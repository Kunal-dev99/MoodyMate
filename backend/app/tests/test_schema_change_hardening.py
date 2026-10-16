"""Phase 5 verification follow-ups: defects found by the independent review of BE-5.x."""

import uuid
from typing import Any

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.governance.service import diff_fields
from app.main import create_app
from app.tests.conftest import alembic_config, dev_token, sql
from app.tests.test_schema_change import (
    BASE,
    fields_of,
    propose,
    removal_proposal,
    submitted,
    token_for,
)
from app.tests.test_schema_registry import schema_id, spec

pytestmark = pytest.mark.db

DEC = {"data_type": "DECIMAL"}


def events(db: str, action: str, resource_id: str) -> list[Any]:
    return sql(
        db,
        "SELECT * FROM audit_event WHERE action = $1 AND resource_id = $2 ORDER BY occurred_at",
        action,
        resource_id,
    )


# --- 1. Fixed-precision decimals ---


def changes(old: list[Any], new: list[Any]) -> set[tuple[str, str | None, str]]:
    return {(c.change, c.attribute, c.compatibility) for c in diff_fields(old, new).changes}


@pytest.mark.parametrize(("precision", "scale"), [(18, None), (None, None), (None, 6)])
def test_dropping_a_decimal_bound_is_breaking(precision: int | None, scale: int | None) -> None:
    old = [spec(**DEC, precision=18, scale=6)]
    new = [spec(**DEC, precision=precision, scale=scale)]
    assert changes(old, new) == {("MODIFIED", "precision_scale", "BREAKING")}


def test_unbounded_size_on_other_types_is_still_a_widening() -> None:
    assert changes([spec(precision=10)], [spec()]) == {
        ("MODIFIED", "precision_scale", "COMPATIBLE")
    }


@pytest.mark.parametrize(("precision", "scale"), [(None, 2), (10, None), (None, None)])
def test_decimal_fields_need_precision_and_scale(
    api: TestClient, db: str, precision: int | None, scale: int | None
) -> None:
    sid = schema_id(db, "instrument")
    fields = fields_of(api, sid, 1)
    notional = next(f for f in fields if f["name"] == "notional")
    notional.update(precision=precision, scale=scale)
    r = propose(api, dev_token(api, "data_steward"), sid, fields)
    assert r.status_code == 422


def test_extension_eligible_must_be_explicit(api: TestClient, db: str) -> None:
    sid = schema_id(db, "instrument")
    fields = fields_of(api, sid, 1)[:-1]
    del fields[0]["extension_eligible"]
    assert propose(api, dev_token(api, "data_steward"), sid, fields).status_code == 422


# --- 2. Refused maker-checker attempts are audited ---


def test_self_approval_attempt_is_audited(api: TestClient, db: str) -> None:
    both = token_for(db, "dev:both-roles", ("data_steward", "approver"))
    p = submitted(api, db, both)
    r = api.post(f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=both)
    assert r.status_code == 403
    [e] = events(db, "schema_change.approve", p["id"])
    assert (e["outcome"], e["resource_id"]) == ("DENIED", p["id"])
    assert "self_approval" in e["details"]


def test_submit_by_someone_else_is_audited(api: TestClient, db: str) -> None:
    p = removal_proposal(api, db, dev_token(api, "data_steward"))
    other = token_for(db, "dev:other-steward", ("data_steward",))
    api.post(f"{BASE}/{p['id']}/impact-analysis", headers=other)
    r = api.post(f"{BASE}/{p['id']}/submit", json={}, headers=other)
    assert r.status_code == 403
    [e] = events(db, "schema_change.submit", p["id"])
    assert e["outcome"] == "DENIED" and "not_proposer" in e["details"]


@pytest.mark.parametrize("route", ["impact-analysis", "submit", "approval", "revert"])
def test_switch_blocks_every_write_and_is_audited(db: str, route: str) -> None:
    with TestClient(create_app(Settings(ENV="test", DATABASE_URL=db))) as on:
        p = removal_proposal(on, db, dev_token(on, "data_steward"))
    app = create_app(Settings(ENV="test", DATABASE_URL=db, SCHEMA_CHANGE_ENABLED=False))
    with TestClient(app) as off:
        role = "approver" if route == "approval" else "data_steward"
        body = {"decision": "APPROVE"} if route == "approval" else {}
        cid = f"test-{uuid.uuid4().hex[:10]}"
        headers = {**dev_token(off, role), "X-Correlation-ID": cid}
        r = off.post(f"{BASE}/{p['id']}/{route}", json=body, headers=headers)
    assert r.status_code == 403 and r.json()["code"] == "schema_change_disabled"
    [e] = sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    assert e["outcome"] == "DENIED" and "schema_change_disabled" in e["details"]


# --- 3. Applying a change supersedes the others at once ---


def test_applying_supersedes_other_open_proposals(api: TestClient, db: str) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    first, second = submitted(api, db, steward), submitted(api, db, steward)
    draft = removal_proposal(api, db, steward)
    assert (
        api.post(
            f"{BASE}/{first['id']}/approval", json={"decision": "APPROVE"}, headers=approver
        ).status_code
        == 200
    )
    for other in (second, draft):
        got = api.get(f"{BASE}/{other['id']}", headers=approver).json()
        assert got["status"] == "SUPERSEDED" and got["proposed_version"]["status"] == "REJECTED"
    # Nothing left to decide or analyse: a plain 409, and no write.
    before = sql(db, "SELECT count(*) AS n FROM audit_event")[0]["n"]
    r = api.post(f"{BASE}/{second['id']}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 409 and r.json()["code"] == "invalid_status"
    assert api.post(f"{BASE}/{draft['id']}/impact-analysis", headers=steward).status_code == 409
    assert sql(db, "SELECT count(*) AS n FROM audit_event")[0]["n"] == before


# --- 4. Decided proposals are immutable in the database ---


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE schema_change_proposal SET status = 'SUBMITTED' WHERE id = $1",
        "UPDATE schema_change_proposal SET decision_comment = 'edited' WHERE id = $1",
        "DELETE FROM schema_change_proposal WHERE id = $1",
    ],
)
def test_decided_proposals_cannot_be_edited(api: TestClient, db: str, statement: str) -> None:
    p = submitted(api, db, dev_token(api, "data_steward"))
    api.post(
        f"{BASE}/{p['id']}/approval",
        json={"decision": "APPROVE"},
        headers=dev_token(api, "approver"),
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="immutable"):
        sql(db, statement, uuid.UUID(p["id"]))


def test_open_proposals_cannot_be_deleted_or_truncated(api: TestClient, db: str) -> None:
    p = removal_proposal(api, db, dev_token(api, "data_steward"))
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        sql(db, "DELETE FROM schema_change_proposal WHERE id = $1", uuid.UUID(p["id"]))
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        sql(db, "TRUNCATE schema_change_proposal CASCADE")


def test_proposal_downgrade_refuses_while_proposals_exist(api: TestClient, db: str) -> None:
    removal_proposal(api, db, dev_token(api, "data_steward"))
    try:
        with pytest.raises(Exception, match="schema_change_proposal has rows"):
            command.downgrade(alembic_config(db), "0007")
    finally:
        command.upgrade(alembic_config(db), "head")


# --- 5. Reverts ---


def test_an_applied_change_is_reverted_at_most_once(api: TestClient, db: str) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    p = submitted(api, db, steward)
    api.post(f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert api.post(f"{BASE}/{p['id']}/revert", json={}, headers=steward).status_code == 201
    r = api.post(f"{BASE}/{p['id']}/revert", json={}, headers=steward)
    assert r.status_code == 409 and r.json()["code"] == "already_reverted"


# --- 6. Dependents are listed once, with every path ---


def test_dependents_are_deduplicated_with_all_paths(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    p = removal_proposal(api, db, steward)
    impact = api.post(f"{BASE}/{p['id']}/impact-analysis", headers=steward).json()["impact"]
    keys = [(d["dependent_type"], d["dependent_ref"]) for d in impact["dependents"]]
    assert len(keys) == len(set(keys)) == 5
    gap = next(
        d for d in impact["dependents"] if d["dependent_ref"] == "liquidity_gap (illustrative)"
    )
    assert sorted(path[1] for path in gap["paths"]) == ["cashflow_event", "position"]
    assert gap["depth"] == 2
