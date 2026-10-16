"""Phase 5: controlled schema change — proposals (BE-5.1/5.2), maker-checker approval
(BE-5.3) and revert (BE-5.4)."""

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.tokens import DevTokens
from app.governance.constants import DEV_ISSUER
from app.main import create_app
from app.tests.conftest import dev_token, insert_user, sql
from app.tests.test_schema_registry import schema_id

pytestmark = pytest.mark.db

BASE = "/api/v1/schema-change-proposals"


def token_for(db: str, subject: str, roles: tuple[str, ...]) -> dict[str, str]:
    """A dev token for a user with exactly these roles (dev personas hold one role each)."""
    insert_user(db, DEV_ISSUER, subject, roles=roles)
    settings = Settings(ENV="test")
    token = DevTokens(settings.AUTH_DEV_SECRET, settings.AUTH_DEV_TOKEN_TTL_MINUTES).issue(
        subject, f"{subject}@example.test", subject
    )
    return {"Authorization": f"Bearer {token}"}


def fields_of(api: TestClient, sid: Any, version: int) -> list[dict[str, Any]]:
    versions = api.get(f"/api/v1/schemas/{sid}/versions", headers=dev_token(api, "auditor")).json()
    [v] = [v for v in versions["items"] if v["version"] == version]
    keep = (
        "name",
        "data_type",
        "required",
        "unit",
        "precision",
        "scale",
        "classification",
        "extension_eligible",
        "description",
    )
    return [{k: f[k] for k in keep} for f in v["fields"]]


def propose(
    api: TestClient, headers: dict[str, str], sid: Any, fields: list[dict[str, Any]], **kw: Any
) -> Any:
    body = {"schema_id": str(sid), "title": kw.pop("title", "Test change"), "fields": fields, **kw}
    return api.post(BASE, json=body, headers=headers)


@pytest.fixture
def steward(api: TestClient) -> dict[str, str]:
    return dev_token(api, "data_steward")


@pytest.fixture
def approver(api: TestClient) -> dict[str, str]:
    return dev_token(api, "approver")


def removal_proposal(api: TestClient, db: str, steward: dict[str, str]) -> Any:
    """Instrument v1 minus `counterparty_id`: a BREAKING change."""
    sid = schema_id(db, "instrument")
    fields = [f for f in fields_of(api, sid, 1) if f["name"] != "counterparty_id"]
    r = propose(api, steward, sid, fields, title="Drop counterparty_id")
    assert r.status_code == 201, r.text
    return r.json()


# --- BE-5.2 create, read, impact analysis --------------------------------------------------


def test_create_proposal_creates_a_draft_version(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    p = removal_proposal(api, db, steward)
    assert p["status"] == "DRAFT"
    assert p["base_version"]["version"] == 1 and p["base_version"]["status"] == "ACTIVE"
    assert p["proposed_version"]["version"] == 3 and p["proposed_version"]["status"] == "DRAFT"
    assert p["impact"] is None and p["proposer"].startswith("user:")
    got = api.get(f"{BASE}/{p['id']}", headers=dev_token(api, "risk_analyst"))
    assert got.status_code == 200 and got.json()["id"] == p["id"]


def test_removal_reports_breaking_and_lists_seeded_dependents(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    p = removal_proposal(api, db, steward)
    r = api.post(f"{BASE}/{p['id']}/impact-analysis", headers=steward)
    assert r.status_code == 200, r.text
    impact = r.json()["impact"]
    assert impact["overall"] == "BREAKING"
    assert {(c["field"], c["change"], c["compatibility"]) for c in impact["changes"]} == {
        ("counterparty_id", "REMOVED", "BREAKING")
    }
    deps = {(d["dependent_type"], d["dependent_ref"]): d for d in impact["dependents"]}
    # Direct dependents of instrument...
    assert ("SCHEMA", "position") in deps and (
        "MAPPING",
        "core_banking_instrument (illustrative)",
    ) in deps
    # ...and what depends on those schemas in turn (the walk), with the path it took.
    module = deps[("MODULE", "liquidity_gap (illustrative)")]
    assert module["depth"] == 2 and module["via"][0] == "instrument"
    assert ("REPORT", "maturity_ladder (illustrative)") in deps


def test_create_validates_the_contract(api: TestClient, db: str, steward: dict[str, str]) -> None:
    sid = schema_id(db, "instrument")
    v1 = fields_of(api, sid, 1)
    cases = [
        (v1 + [dict(v1[0])], "duplicate_field"),
        ([{**v1[0], "classification": "top-secret"}], "unknown_classification"),
        (v1, "no_change"),
        ([{**v1[0], "precision": 4, "scale": 6}], None),  # scale > precision: request validation
    ]
    for fields, code in cases:
        r = propose(api, steward, sid, fields)
        assert r.status_code == 422, r.text
        if code:
            assert r.json()["code"] == code


def test_proposing_needs_schema_propose(api: TestClient, db: str) -> None:
    sid = schema_id(db, "instrument")
    r = propose(api, dev_token(api, "risk_analyst"), sid, fields_of(api, sid, 1)[:-1])
    assert r.status_code == 403


# --- BE-5.3 submit and maker-checker approval ---------------------------------------------------


def test_submit_without_impact_analysis_is_409(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    p = removal_proposal(api, db, steward)
    r = api.post(f"{BASE}/{p['id']}/submit", json={}, headers=steward)
    assert r.status_code == 409 and r.json()["code"] == "impact_analysis_required"


def test_approval_without_impact_analysis_is_409(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> None:
    p = removal_proposal(api, db, steward)
    # Force the state a buggy client or a direct DB edit could produce.
    sql(
        db,
        "UPDATE schema_change_proposal SET status = 'SUBMITTED' WHERE id = $1",
        uuid.UUID(p["id"]),
    )
    r = api.post(f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 409 and r.json()["code"] == "impact_analysis_required"


def submitted(api: TestClient, db: str, steward: dict[str, str]) -> Any:
    p = removal_proposal(api, db, steward)
    assert api.post(f"{BASE}/{p['id']}/impact-analysis", headers=steward).status_code == 200
    r = api.post(
        f"{BASE}/{p['id']}/submit", json={"regression_evidence_ref": "CI run 42"}, headers=steward
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SUBMITTED"
    return r.json()


def test_proposer_cannot_approve_own_proposal(api: TestClient, db: str) -> None:
    both = token_for(db, "dev:maker-checker", ("data_steward", "approver"))
    p = submitted(api, db, both)
    r = api.post(f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=both)
    assert r.status_code == 403 and r.json()["code"] == "self_approval"
    assert api.get(f"{BASE}/{p['id']}", headers=both).json()["status"] == "SUBMITTED"


def test_approval_needs_schema_approve(api: TestClient, db: str, steward: dict[str, str]) -> None:
    p = submitted(api, db, steward)
    r = api.post(
        f"{BASE}/{p['id']}/approval",
        json={"decision": "APPROVE"},
        headers=dev_token(api, "platform_admin"),
    )
    assert r.status_code == 403  # segregation of duties: platform_admin cannot approve


def test_valid_approval_activates_new_version(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> None:
    p = submitted(api, db, steward)
    base_hash = sql(
        db,
        "SELECT content_sha256 FROM schema_version WHERE id = $1",
        uuid.UUID(p["base_version"]["id"]),
    )[0]["content_sha256"]
    base_fields = sql(
        db,
        "SELECT count(*) AS n FROM schema_field WHERE schema_version_id = $1",
        uuid.UUID(p["base_version"]["id"]),
    )[0]["n"]
    cid = f"test-{uuid.uuid4().hex[:10]}"
    r = api.post(
        f"{BASE}/{p['id']}/approval",
        json={"decision": "APPROVE", "comment": "Checked with consumers"},
        headers={**approver, "X-Correlation-ID": cid},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPLIED" and body["decided_by"].startswith("user:")
    rows = {
        r["id"]: r
        for r in sql(
            db,
            "SELECT id, status, content_sha256, effective_from FROM schema_version"
            " WHERE schema_id = $1",
            uuid.UUID(p["schema_id"]),
        )
    }
    new, old = (
        rows[uuid.UUID(p["proposed_version"]["id"])],
        rows[uuid.UUID(p["base_version"]["id"])],
    )
    assert new["status"] == "ACTIVE" and new["effective_from"] is not None
    assert old["status"] == "DEPRECATED"
    # The old contract is untouched.
    assert old["content_sha256"] == base_hash
    assert (
        sql(db, "SELECT count(*) AS n FROM schema_field WHERE schema_version_id = $1", old["id"])[
            0
        ]["n"]
        == base_fields
    )
    # Audited in the same transaction, with the approver as actor.
    actions = {
        e["action"]: e for e in sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    }
    assert {"schema_change.approve", "schema_version.activate", "schema_version.deprecate"} <= set(
        actions
    )
    assert actions["schema_change.approve"]["outcome"] == "SUCCESS"
    assert json.loads(actions["schema_change.approve"]["details"])["overall"] == "BREAKING"
    # The catalogue now shows the new version in effect.
    summary = next(
        s
        for s in api.get("/api/v1/schemas", headers=approver).json()["items"]
        if s["code"] == "instrument"
    )
    assert summary["effective_version"]["version"] == 3


def test_rejection_closes_the_proposal(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> None:
    p = submitted(api, db, steward)
    r = api.post(f"{BASE}/{p['id']}/approval", json={"decision": "REJECT"}, headers=approver)
    assert r.status_code == 422  # a rejection needs a reason
    r = api.post(
        f"{BASE}/{p['id']}/approval",
        json={"decision": "REJECT", "comment": "Consumers not ready"},
        headers=approver,
    )
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert r.json()["proposed_version"]["status"] == "REJECTED"
    assert (
        api.post(
            f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=approver
        ).status_code
        == 409
    )


def test_stale_proposal_is_superseded(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> None:
    first, second = submitted(api, db, steward), submitted(api, db, steward)
    assert (
        api.post(
            f"{BASE}/{first['id']}/approval", json={"decision": "APPROVE"}, headers=approver
        ).status_code
        == 200
    )
    # Applying the first supersedes the second at once; approving it is then a plain 409.
    assert api.get(f"{BASE}/{second['id']}", headers=approver).json()["status"] == "SUPERSEDED"
    r = api.post(f"{BASE}/{second['id']}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 409 and r.json()["code"] == "invalid_status"


def test_activation_rejects_a_tampered_contract_hash(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> None:
    p = submitted(api, db, steward)
    # The proposed version is still DRAFT, so its fields can change underneath the proposal.
    sql(
        db,
        "UPDATE schema_field SET required = NOT required"
        " WHERE schema_version_id = $1 AND name = 'currency'",
        uuid.UUID(p["proposed_version"]["id"]),
    )
    r = api.post(f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 409 and r.json()["code"] == "contract_changed"


def test_disabled_setting_makes_the_registry_read_only(db: str) -> None:
    app = create_app(Settings(ENV="test", DATABASE_URL=db, SCHEMA_CHANGE_ENABLED=False))
    with TestClient(app) as c:
        sid = schema_id(db, "instrument")
        r = propose(c, dev_token(c, "data_steward"), sid, fields_of(c, sid, 1)[:-1])
        assert r.status_code == 403 and r.json()["code"] == "schema_change_disabled"
        assert c.get(BASE, headers=dev_token(c, "risk_analyst")).status_code == 200


# --- BE-5.4 revert ------------------------------------------------------------------------------


def test_revert_reinstates_the_prior_contract_as_a_new_version(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> None:
    p = submitted(api, db, steward)
    original_hash = sql(
        db,
        "SELECT content_sha256 FROM schema_version WHERE id = $1",
        uuid.UUID(p["base_version"]["id"]),
    )[0]["content_sha256"]
    assert (
        api.post(
            f"{BASE}/{p['id']}/approval", json={"decision": "APPROVE"}, headers=approver
        ).status_code
        == 200
    )
    count_before = sql(
        db,
        "SELECT count(*) AS n FROM schema_version WHERE schema_id = $1",
        uuid.UUID(p["schema_id"]),
    )[0]["n"]

    r = api.post(f"{BASE}/{p['id']}/revert", json={}, headers=steward)
    assert r.status_code == 201, r.text
    revert = r.json()
    assert revert["reverts_proposal_id"] == p["id"] and revert["status"] == "DRAFT"
    impact = api.post(f"{BASE}/{revert['id']}/impact-analysis", headers=steward).json()["impact"]
    assert impact["overall"] == "ADDITIVE"  # counterparty_id comes back as an optional field
    assert api.post(f"{BASE}/{revert['id']}/submit", json={}, headers=steward).status_code == 200
    assert (
        api.post(
            f"{BASE}/{revert['id']}/approval", json={"decision": "APPROVE"}, headers=approver
        ).status_code
        == 200
    )

    active = sql(
        db,
        "SELECT content_sha256, version FROM schema_version"
        " WHERE schema_id = $1 AND status = 'ACTIVE'",
        uuid.UUID(p["schema_id"]),
    )
    assert len(active) == 1 and active[0]["content_sha256"] == original_hash
    assert active[0]["version"] == 4
    count_after = sql(
        db,
        "SELECT count(*) AS n FROM schema_version WHERE schema_id = $1",
        uuid.UUID(p["schema_id"]),
    )[0]["n"]
    assert count_after == count_before + 1


def test_only_an_applied_proposal_can_be_reverted(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    p = removal_proposal(api, db, steward)
    r = api.post(f"{BASE}/{p['id']}/revert", json={}, headers=steward)
    assert r.status_code == 409 and r.json()["code"] == "not_applied"


def test_list_filters_by_status(api: TestClient, db: str, steward: dict[str, str]) -> None:
    submitted(api, db, steward)
    removal_proposal(api, db, steward)
    listed = api.get(
        BASE, params={"status": "SUBMITTED"}, headers=dev_token(api, "approver")
    ).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "SUBMITTED"
