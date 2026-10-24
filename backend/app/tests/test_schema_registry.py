"""Schema registry and controlled change (Phases 4-5): propose -> impact analysis -> submit
-> approval by a different person -> apply; self-approval refused and audited; ACTIVE
contracts and decided proposals are immutable in the database."""

import json
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.tests.canonical_kit import check_schema_contract_hashes
from app.tests.conftest import dev_token, sql
from app.tests.helpers import PROPOSALS, cid, events, removal_proposal, token_for, version_id

pytestmark = pytest.mark.db

IMMUTABLE = asyncpg.InsufficientPrivilegeError


def test_breaking_change_needs_analysis_and_a_second_person(api: TestClient, db: str) -> None:
    both = token_for(db, "dev:maker-checker", ("data_steward", "approver"))
    p = removal_proposal(api, db, both)
    base = f"{PROPOSALS}/{p['id']}"
    assert (p["status"], p["proposed_version"]["status"]) == ("DRAFT", "DRAFT")
    r = api.post(f"{base}/submit", json={}, headers=both)
    assert (r.status_code, r.json()["code"]) == (409, "impact_analysis_required")
    impact = api.post(f"{base}/impact-analysis", headers=both).json()["impact"]
    assert impact["overall"] == "BREAKING"
    assert {(c["field"], c["change"]) for c in impact["changes"]} == {
        ("counterparty_id", "REMOVED")
    }
    r = api.post(f"{base}/submit", json={"regression_evidence_ref": "CI run 42"}, headers=both)
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED", r.text

    # Maker-checker: the proposer may not approve, even holding schema:approve (audited).
    correlation = cid()
    r = api.post(
        f"{base}/approval",
        json={"decision": "APPROVE"},
        headers={**both, "X-Correlation-ID": correlation},
    )
    assert (r.status_code, r.json()["code"]) == (403, "self_approval")
    [denied] = events(db, correlation)
    assert (denied["action"], denied["outcome"]) == ("schema_change.approve", "DENIED")
    assert "self_approval" in denied["details"]
    # Segregation of duties: platform_admin holds no schema:approve.
    r = api.post(
        f"{base}/approval", json={"decision": "APPROVE"}, headers=dev_token(api, "platform_admin")
    )
    assert r.status_code == 403
    assert api.get(base, headers=both).json()["status"] == "SUBMITTED"

    old_id = uuid.UUID(p["base_version"]["id"])
    old_hash = sql(db, "SELECT content_sha256 FROM schema_version WHERE id = $1", old_id)
    correlation = cid()
    r = api.post(
        f"{base}/approval",
        json={"decision": "APPROVE", "comment": "Checked with consumers"},
        headers={**dev_token(api, "approver"), "X-Correlation-ID": correlation},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPLIED"
    assert r.json()["proposed_version"]["status"] == "ACTIVE"
    rows = {
        r["id"]: r
        for r in sql(
            db,
            "SELECT id, status, content_sha256 FROM schema_version WHERE schema_id = $1",
            uuid.UUID(p["schema_id"]),
        )
    }
    assert rows[uuid.UUID(p["proposed_version"]["id"])]["status"] == "ACTIVE"
    assert rows[old_id]["status"] == "DEPRECATED"
    assert rows[old_id]["content_sha256"] == old_hash[0]["content_sha256"]  # untouched
    actions = {e["action"]: e for e in events(db, correlation)}
    assert {"schema_change.approve", "schema_version.activate", "schema_version.deprecate"} <= set(
        actions
    )
    assert json.loads(actions["schema_change.approve"]["details"])["overall"] == "BREAKING"


def test_active_contracts_and_decided_proposals_are_immutable(api: TestClient, db: str) -> None:
    active = version_id(db, "instrument", 1)
    before = sql(db, "SELECT * FROM schema_field WHERE schema_version_id = $1 ORDER BY id", active)
    for statement in (
        "UPDATE schema_field SET data_type = 'INTEGER' WHERE schema_version_id = $1",
        "UPDATE schema_field SET description = 'edited' WHERE schema_version_id = $1",
        "DELETE FROM schema_field WHERE schema_version_id = $1",
        "INSERT INTO schema_field (id, schema_version_id, position, name, data_type, required,"
        " classification, extension_eligible, created_by) VALUES (gen_random_uuid(), $1, 99,"
        " 'smuggled', 'STRING', false, 'public', false, 'test')",
        "UPDATE schema_version SET content_sha256 = repeat('0', 64) WHERE id = $1",
        "UPDATE schema_version SET status = 'DRAFT' WHERE id = $1",
        "DELETE FROM schema_version WHERE id = $1",
    ):
        with pytest.raises(IMMUTABLE, match="immutable"):
            sql(db, statement, active)
    for table in ("schema_field", "schema_version", "schema_definition"):
        with pytest.raises(IMMUTABLE, match="immutable"):
            sql(db, f"TRUNCATE {table} CASCADE")
    assert (
        sql(db, "SELECT status FROM schema_version WHERE id = $1", active)[0]["status"] == "ACTIVE"
    )
    after = sql(db, "SELECT * FROM schema_field WHERE schema_version_id = $1 ORDER BY id", active)
    assert after == before

    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    open_one = removal_proposal(api, db, steward)
    with pytest.raises(IMMUTABLE):
        sql(db, "DELETE FROM schema_change_proposal WHERE id = $1", uuid.UUID(open_one["id"]))
    with pytest.raises(IMMUTABLE):
        sql(db, "TRUNCATE schema_change_proposal CASCADE")
    base = f"{PROPOSALS}/{open_one['id']}"
    assert api.post(f"{base}/impact-analysis", headers=steward).status_code == 200
    assert api.post(f"{base}/submit", json={}, headers=steward).status_code == 200
    r = api.post(f"{base}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200, r.text
    for statement in (
        "UPDATE schema_change_proposal SET status = 'SUBMITTED' WHERE id = $1",
        "UPDATE schema_change_proposal SET decision_comment = 'edited' WHERE id = $1",
        "DELETE FROM schema_change_proposal WHERE id = $1",
    ):
        with pytest.raises(IMMUTABLE, match="immutable"):
            sql(db, statement, uuid.UUID(open_one["id"]))

    # Phase 29 (BE-29.1, D-52 §4): every stored contract hash, the applied one included,
    # re-verifies under its policy, the service and the migrations' frozen copies.
    check_schema_contract_hashes(db)
