"""WS1 verification suite (BE-6.3). Run on its own with `pytest -m ws1`; CI runs it as a
separate step and the route x role matrix is printed in the test report.

1. Authorization matrix: every /api/v1 route x every seeded role -> 2xx/4xx as declared,
   403 exactly where the role lacks the route's permission.
2. Cross-tenant isolation: a user of another tenant sees and changes nothing here.
3. Breaking-change approval: analysis, maker-checker approval, activation.
4. Audit completeness: every mutating route emits an audit event.
5. Migration round trip base -> head (with the seeded data).
"""

import uuid
from typing import Any

import pytest
from alembic import command
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.auth import AUTHENTICATED, PUBLIC_PATHS, declared_permission
from app.governance.constants import ROLE_PERMISSIONS, Role
from app.tests.conftest import WS1_MATRIX, alembic_config, dev_token, insert_tenant, sql
from app.tests.test_imports import upload
from app.tests.test_schema_change import (
    BASE,
    removal_proposal,
    submitted,
    token_for,
)
from app.tests.test_schema_registry import schema_id
from app.tests.test_sources import contract_body, source_body

pytestmark = [pytest.mark.db, pytest.mark.ws1]

ROLES = [r.value for r in Role]
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def protected_routes(api: TestClient) -> list[tuple[str, str, str]]:
    """(method, path template, declared permission) for every protected /api/v1 route."""
    found = []
    for route in api.app.routes:  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1"):
            continue
        if route.path in PUBLIC_PATHS:
            continue
        permission = declared_permission(route)
        assert permission, f"{route.path} declares no permission"  # also enforced at runtime
        for method in sorted(route.methods):
            found.append((method, route.path, permission))
    return sorted(found, key=lambda r: (r[1], r[0]))


def concrete(path: str) -> str:
    """A path template with every parameter filled by a random (absent) id."""
    while "{" in path:
        start, end = path.index("{"), path.index("}")
        path = path[:start] + str(uuid.uuid4()) + path[end + 1 :]
    return path


# --- 1. Authorization matrix ---


ALLOWED_OUTCOMES = {200, 201, 404, 409, 422}  # a permitted role is never refused


def real_ids(api: TestClient, db: str) -> dict[str, str]:
    """Existing ids, so GET routes answer 2xx for permitted roles (the plan's 2xx side)."""
    proposal = removal_proposal(api, db, dev_token(api, "data_steward"))
    auditor = api.get("/api/v1/me", headers=dev_token(api, "auditor")).json()
    source = sql(db, "SELECT id FROM source_system WHERE code = 'core_banking'")[0]["id"]
    imported = upload(api, dev_token(api, "data_steward"), str(source), b"instrument_id\nA\n")
    assert imported.status_code == 201, imported.text
    return {
        "schema_id": str(schema_id(db, "instrument")),
        "proposal_id": proposal["id"],
        "user_id": auditor["user_id"],
        "source_id": str(source),
        "import_id": imported.json()["id"],
    }


def concrete_with(path: str, ids: dict[str, str]) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", value)
    return path + ("?from=1&to=2" if path.endswith("/diff") else "")


def test_seeded_roles_are_the_roles_tested(db: str) -> None:
    seeded = {r["code"] for r in sql(db, "SELECT code FROM role")}
    assert seeded == set(ROLES)


def test_route_by_role_authorization_matrix(api: TestClient, db: str) -> None:
    ids = real_ids(api, db)
    tokens = {role: dev_token(api, role) for role in ROLES}
    failures = []
    successes = dict.fromkeys(ROLES, 0)
    for method, path, permission in protected_routes(api):
        row: dict[str, Any] = {"route": f"{method} {path}", "permission": permission}
        for role in ROLES:
            allowed = permission == AUTHENTICATED or permission in ROLE_PERMISSIONS[Role(role)]
            r = api.request(
                method,
                concrete_with(path, ids),
                headers=tokens[role],
                json={} if method in MUTATING else None,
            )
            row[role] = r.status_code
            if allowed:
                # Any refusal at all (401, 403 for whatever reason) is a failure.
                if r.status_code not in ALLOWED_OUTCOMES:
                    failures.append(f"{method} {path} as {role}: {r.status_code}, expected allowed")
                successes[role] += r.status_code < 300
            else:
                body = r.json()
                proved = (
                    r.status_code == 403
                    and body.get("code") == "forbidden"
                    and body.get("details", {}).get("required_permission") == permission
                )
                if not proved:
                    failures.append(f"{method} {path} as {role}: {r.status_code} {body}")
        WS1_MATRIX.append(row)
    assert not failures, "\n".join(failures)
    # Every role really uses the routes it may use, not only gets past the check.
    assert all(successes.values()), successes
    assert len(WS1_MATRIX) >= 12


def test_matrix_covers_every_route_without_a_token(api: TestClient) -> None:
    for method, path, _ in protected_routes(api):
        r = api.request(method, concrete(path), json={} if method in MUTATING else None)
        assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a token"


# --- 2. Cross-tenant isolation ---


def test_another_tenant_sees_and_changes_nothing_here(api: TestClient, db: str) -> None:
    here = removal_proposal(api, db, dev_token(api, "data_steward"))
    instrument = schema_id(db, "instrument")
    here_user = api.get("/api/v1/me", headers=dev_token(api, "auditor")).json()["user_id"]

    other_tenant = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    outsider = token_for(
        db,
        "dev:outsider",
        ("platform_admin", "auditor", "data_steward", "approver"),
        tenant_id=other_tenant,
    )
    # Lists hold only the outsider's own tenant.
    users = api.get("/api/v1/users", headers=outsider).json()["items"]
    assert len(users) == 1 and users[0]["display_name"].endswith("dev:outsider")
    assert api.get("/api/v1/schemas", headers=outsider).json()["total"] == 0
    assert api.get(BASE, headers=outsider).json()["total"] == 0
    audit = api.get("/api/v1/audit-events", params={"limit": 200}, headers=outsider).json()
    theirs = {
        str(r["id"])
        for r in sql(db, "SELECT id FROM audit_event WHERE tenant_id = $1", other_tenant)
    }
    assert audit["items"] and {e["id"] for e in audit["items"]} <= theirs
    # Direct reads and writes by id: not found, exactly as for an id that does not exist.
    assert api.get("/api/v1/sources", headers=outsider).json()["total"] == 0
    here_source = sql(db, "SELECT id FROM source_system WHERE code = 'core_banking'")[0]["id"]
    here_import = upload(
        api, dev_token(api, "data_steward"), str(here_source), b"instrument_id\nA\n"
    ).json()["id"]
    assert api.get("/api/v1/imports", headers=outsider).json()["total"] == 0
    for path in (
        f"/api/v1/imports/{here_import}",
        f"/api/v1/sources/{here_source}",
        f"/api/v1/users/{here_user}",
        f"/api/v1/schemas/{instrument}/versions",
        f"/api/v1/schemas/{instrument}/diff?from=1&to=2",
        f"{BASE}/{here['id']}",
    ):
        assert api.get(path, headers=outsider).status_code == 404, path
    for action, body in (
        ("impact-analysis", None),
        ("submit", {}),
        ("approval", {"decision": "APPROVE"}),
        ("revert", {}),
    ):
        r = api.post(f"{BASE}/{here['id']}/{action}", json=body, headers=outsider)
        assert r.status_code == 404, action
    fields = api.get(f"{BASE}/{here['id']}", headers=dev_token(api, "auditor")).json()[
        "proposed_fields"
    ]
    r = api.post(
        BASE,
        json={"schema_id": str(instrument), "title": "Cross tenant", "fields": fields},
        headers=outsider,
    )
    assert r.status_code == 404
    assert (
        sql(
            db,
            "SELECT count(*) AS n FROM schema_change_proposal WHERE tenant_id = $1",
            other_tenant,
        )[0]["n"]
        == 0
    )

    # And the other way round: this tenant sees nothing of the outsider's.
    foreign = sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'outsider_only', 'Outsider', 'x', false, 'test')"
        " RETURNING id",
        other_tenant,
    )[0]["id"]
    insider = dev_token(api, "platform_admin")
    assert "outsider_only" not in api.get("/api/v1/schemas", headers=insider).text
    assert api.get(f"/api/v1/schemas/{foreign}/versions", headers=insider).status_code == 404
    users_here = api.get("/api/v1/users", params={"limit": 200}, headers=insider).json()["items"]
    assert all(not u["display_name"].endswith("dev:outsider") for u in users_here)


# --- 3. Breaking-change approval ---


def test_breaking_change_needs_analysis_and_a_second_person(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    draft = removal_proposal(api, db, steward)
    assert api.post(f"{BASE}/{draft['id']}/submit", json={}, headers=steward).status_code == 409
    both = token_for(db, "dev:maker-and-checker", ("data_steward", "approver"))
    mine = submitted(api, db, both)
    assert mine["impact"]["overall"] == "BREAKING"
    r = api.post(f"{BASE}/{mine['id']}/approval", json={"decision": "APPROVE"}, headers=both)
    assert r.status_code == 403
    r = api.post(
        f"{BASE}/{mine['id']}/approval",
        json={"decision": "APPROVE"},
        headers=dev_token(api, "approver"),
    )
    assert r.status_code == 200 and r.json()["proposed_version"]["status"] == "ACTIVE"


# --- 4. Audit completeness ---

# Every mutating route and the audit action it must record. A new mutating route fails
# test_every_mutating_route_is_listed until it is added here and exercised below.
AUDITED = {
    "POST /api/v1/dev/sessions": "auth.dev_session.create",
    "POST /api/v1/schema-change-proposals": "schema_change.create",
    "POST /api/v1/schema-change-proposals/{proposal_id}/impact-analysis": (
        "schema_change.impact_analysis"
    ),
    "POST /api/v1/schema-change-proposals/{proposal_id}/submit": "schema_change.submit",
    "POST /api/v1/schema-change-proposals/{proposal_id}/approval": "schema_change.approve",
    "POST /api/v1/schema-change-proposals/{proposal_id}/revert": "schema_change.revert",
    "POST /api/v1/sources": "source.create",
    "POST /api/v1/sources/{source_id}/contracts": "source_contract.create",
    "POST /api/v1/imports": "import.create",
}


def test_every_mutating_route_is_listed(api: TestClient) -> None:
    mutating = {
        f"{m} {route.path}"
        for route in api.app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1")
        for m in route.methods
        if m in MUTATING
    }
    assert mutating == set(AUDITED)


def test_every_mutating_route_emits_an_audit_event(api: TestClient, db: str) -> None:
    called: set[str] = set()

    def call(route: str, headers: dict[str, str], path: str | None = None, body: Any = None) -> Any:
        called.add(route)
        cid = f"ws1-{uuid.uuid4().hex[:10]}"
        method, template = route.split(" ", 1)
        r = api.request(
            method, path or template, json=body, headers={**headers, "X-Correlation-ID": cid}
        )
        assert r.status_code < 300, f"{route}: {r.status_code} {r.text}"
        actions = [
            e["action"]
            for e in sql(db, "SELECT action FROM audit_event WHERE correlation_id = $1", cid)
        ]
        assert AUDITED[route] in actions, f"{route} recorded {actions}"
        return r.json()

    call("POST /api/v1/dev/sessions", {}, body={"role": "auditor"})
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    instrument = schema_id(db, "instrument")
    v1 = api.get(f"/api/v1/schemas/{instrument}/versions", headers=steward).json()["items"][0]
    keep = (
        "name",
        "data_type",
        "required",
        "unit",
        "precision",
        "scale",
        "classification",
        "extension_eligible",
    )
    fields = [{k: f[k] for k in keep} for f in v1["fields"] if f["name"] != "legacy_book_code"]
    p = call(
        "POST /api/v1/schema-change-proposals",
        steward,
        body={"schema_id": str(instrument), "title": "Audit check", "fields": fields},
    )
    base = f"{BASE}/{p['id']}"
    call(
        "POST /api/v1/schema-change-proposals/{proposal_id}/impact-analysis",
        steward,
        f"{base}/impact-analysis",
    )
    call("POST /api/v1/schema-change-proposals/{proposal_id}/submit", steward, f"{base}/submit", {})
    call(
        "POST /api/v1/schema-change-proposals/{proposal_id}/approval",
        approver,
        f"{base}/approval",
        {"decision": "APPROVE"},
    )
    call("POST /api/v1/schema-change-proposals/{proposal_id}/revert", steward, f"{base}/revert", {})
    source = call(
        "POST /api/v1/sources",
        steward,
        body=source_body(db, code="audit_check_feed"),
    )
    call(
        "POST /api/v1/sources/{source_id}/contracts",
        steward,
        f"/api/v1/sources/{source['id']}/contracts",
        contract_body(db, schema="position"),
    )
    called.add("POST /api/v1/imports")
    imported_cid = f"ws1-{uuid.uuid4().hex[:10]}"
    r = upload(api, steward, source["id"], b"Instrument ID\nA\n", cid=imported_cid)
    assert r.status_code == 201, r.text
    actions = [
        e["action"]
        for e in sql(db, "SELECT action FROM audit_event WHERE correlation_id = $1", imported_cid)
    ]
    assert AUDITED["POST /api/v1/imports"] in actions, actions
    # Every listed route was exercised here: listing a route without driving it fails.
    assert called == set(AUDITED)


# --- 5. Migration round trip ---


def test_migration_round_trip_base_to_head(db: str) -> None:
    cfg = alembic_config(db)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    seeded = sql(db, "SELECT count(*) AS n FROM schema_version WHERE seeded_by IS NOT NULL")
    assert seeded[0]["n"] == 4
