"""WS1 verification suite (BE-6.3). Run on its own with `pytest -m ws1`; CI runs it as a
separate step and the route x role matrix is printed in the test report.

1. Authorization matrix: every /api/v1 route x every seeded role -> 2xx/4xx as declared,
   403 exactly where the role lacks the route's permission; 401 for every route without a
   token.
2. Cross-tenant isolation: a user of another tenant sees and changes nothing here, and
   another tenant's resource is the same 404 as a random id (WS3 instruments and the
   projection included, Phase 14; dataset snapshots, runs and their results, Phase 15).
3. Audit completeness: every mutating route is listed and emits its audit event.
4. Migrations: base -> head round trip with the seed; the downgrade refuses while user data
   or audit rows exist.
"""

import uuid
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.auth import AUTHENTICATED, PUBLIC_PATHS, declared_permission
from app.governance.constants import ROLE_PERMISSIONS, Role
from app.tests.conftest import WS1_MATRIX, alembic_config, clean, dev_token, insert_tenant, sql
from app.tests.helpers import (
    FIELD_KEYS,
    IMPORTS,
    INSTRUMENTS,
    MODULES,
    PROPOSALS,
    RUNS,
    SNAPSHOTS,
    SOURCES,
    blocked_run,
    cid,
    contract_body,
    events,
    module_version_id,
    projected_instruments,
    removal_proposal,
    schema_id,
    seeded_source_id,
    source_body,
    token_for,
    upload,
    validated_sample,
)

# BE-18.3: every test here (matrix, isolation, audit, round trip) is in the WS3 suite too.
pytestmark = [pytest.mark.db, pytest.mark.ws1, pytest.mark.ws3]

ROLES = [r.value for r in Role]
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
ALLOWED_OUTCOMES = {200, 201, 404, 409, 422}  # a permitted role is never refused


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


def fill(path: str, ids: dict[str, str] | None = None) -> str:
    """A path template with its parameters filled from `ids`, else by random (absent) ids."""
    for name, value in (ids or {}).items():
        path = path.replace("{" + name + "}", value)
    while "{" in path:
        start, end = path.index("{"), path.index("}")
        path = path[:start] + str(uuid.uuid4()) + path[end + 1 :]
    return path + ("?from=1&to=2" if path.endswith("/diff") and ids else "")


# Phase 10: a clean CSV for the seeded core_banking contract and a mapping that validates ok.
WS1_CSV = (
    b"instrument_id,instrument_type,currency,notional\nI-1,BOND,EUR,100.00\nI-2,LOAN,USD,250.50\n"
)


def _rule(source: str, target: str, code: str = "COPY") -> dict[str, Any]:
    return {
        "source_column": source,
        "target_field": target,
        "transform": {"code": code, "params": {}},
    }


WS1_RULES = [
    _rule("instrument_id", "instrument_id"),
    _rule("instrument_type", "instrument_type"),
    _rule("currency", "currency"),
    _rule("notional", "notional", "PARSE_DECIMAL"),
]


def ws1_mapping_body(db: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    contract = sql(
        db,
        "SELECT c.id FROM source_contract c"
        " JOIN source_profile p ON p.id = c.source_profile_id"
        " JOIN source_system s ON s.id = p.source_system_id"
        " WHERE s.code = 'core_banking' AND c.status <> 'SUPERSEDED'",
    )[0]["id"]
    return {
        "code": "ws1_check",
        "name": "WS1 check",
        "source_contract_id": str(contract),
        "rules": rules,
    }


# --- 1. Authorization matrix ---------------------------------------------------------------------


def test_route_by_role_authorization_matrix(api: TestClient, db: str) -> None:
    assert {r["code"] for r in sql(db, "SELECT code FROM role")} == set(ROLES)
    routes = protected_routes(api)
    # Without a token every protected route is 401.
    for method, path, _ in routes:
        r = api.request(method, fill(path), json={} if method in MUTATING else None)
        assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a token"

    # Existing ids, so GET routes answer 2xx for permitted roles (the plan's 2xx side).
    steward = dev_token(api, "data_steward")
    proposal = removal_proposal(api, db, steward)
    # Phase 14: a validated, projected import (its mapping ACTIVE) and one of its instruments.
    projected = projected_instruments(api, db, steward, dev_token(api, "approver"))
    imported = api.get(f"{IMPORTS}/{projected['import_id']}", headers=steward)
    assert imported.status_code == 200, imported.text
    # Phase 15: a snapshot of that import and a run on it (BLOCKED: no cash flows), so the
    # calculation reads answer 2xx.
    calculated = blocked_run(api, projected["import_id"])
    ids = {
        "schema_id": str(schema_id(db, "instrument")),
        "proposal_id": proposal["id"],
        "user_id": api.get("/api/v1/me", headers=dev_token(api, "auditor")).json()["user_id"],
        "source_id": seeded_source_id(db),
        "import_id": imported.json()["id"],
        "mapping_id": projected["mapping_id"],
        "batch_id": imported.json()["batches"][0]["id"],  # Phase 11: GET /quarantine/{batch_id}
        "instrument_id": projected["instrument_id"],  # Phase 14: GET /instruments/{id}
        "snapshot_id": calculated["snapshot_id"],  # Phase 15: GET /dataset-snapshots/{id}
        "run_id": calculated["run_id"],  # Phase 15: GET /calculation-runs/{id}[/...]
    }
    tokens = {role: dev_token(api, role) for role in ROLES}
    failures = []
    successes = dict.fromkeys(ROLES, 0)
    for method, path, permission in routes:
        row: dict[str, Any] = {"route": f"{method} {path}", "permission": permission}
        for role in ROLES:
            allowed = permission == AUTHENTICATED or permission in ROLE_PERMISSIONS[Role(role)]
            r = api.request(
                method,
                fill(path, ids),
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
    # BE-9.3: the profile route is declared import:read and every seeded role reads it.
    [profile] = [r for r in WS1_MATRIX if r["route"] == "GET /api/v1/imports/{import_id}/profile"]
    assert profile["permission"] == "import:read"
    assert all(profile[role] == 200 for role in ROLES), profile
    # BE-14.4: the catalogue routes and the projection read are domain:read and 2xx for every
    # seeded role; BE-14.3:
    # the projection is domain:project, 2xx (the existing projection) for data_steward only.
    for route in (
        "GET /api/v1/instruments",
        "GET /api/v1/instruments/{instrument_id}",
        "GET /api/v1/imports/{import_id}/projection",
    ):
        [row] = [r for r in WS1_MATRIX if r["route"] == route]
        assert row["permission"] == "domain:read"
        assert all(row[role] == 200 for role in ROLES), row
    [projection] = [
        r for r in WS1_MATRIX if r["route"] == "POST /api/v1/imports/{import_id}/projection"
    ]
    assert projection["permission"] == "domain:project"
    assert {role: projection[role] for role in ROLES} == {
        role: 200 if role == "data_steward" else 403 for role in ROLES
    }
    # BE-15.5: every calculation read is 2xx for every seeded role (calc:read, and rule:read
    # for the module registry); BE-15.3/15.4: the two writes are calc:run, risk_analyst only
    # (its empty body is a 422, everyone else a 403).
    for route, permission in (
        ("GET /api/v1/calculation-modules", "rule:read"),
        ("GET /api/v1/dataset-snapshots", "calc:read"),
        ("GET /api/v1/dataset-snapshots/{snapshot_id}", "calc:read"),
        ("GET /api/v1/calculation-runs", "calc:read"),
        ("GET /api/v1/calculation-runs/{run_id}", "calc:read"),
        ("GET /api/v1/calculation-runs/{run_id}/results", "calc:read"),
        ("GET /api/v1/calculation-runs/{run_id}/maturity-ladder", "calc:read"),
    ):
        [row] = [r for r in WS1_MATRIX if r["route"] == route]
        assert row["permission"] == permission, route
        assert all(row[role] == 200 for role in ROLES), row
    for route in ("POST /api/v1/dataset-snapshots", "POST /api/v1/calculation-runs"):
        [row] = [r for r in WS1_MATRIX if r["route"] == route]
        assert row["permission"] == "calc:run", route
        assert {role: row[role] for role in ROLES} == {
            role: 422 if role == "risk_analyst" else 403 for role in ROLES
        }, row
    assert len(WS1_MATRIX) >= 12


# --- 2. Cross-tenant isolation -------------------------------------------------------------------


def test_another_tenant_sees_and_changes_nothing_here(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    here = removal_proposal(api, db, steward)
    instrument = schema_id(db, "instrument")
    here_user = api.get("/api/v1/me", headers=dev_token(api, "auditor")).json()["user_id"]
    here_source = seeded_source_id(db)
    here_import = upload(api, steward, here_source, b"instrument_id\nA\n").json()["id"]
    # Phase 14: a projected import and one of its instruments.
    here_projected = projected_instruments(api, db, steward, dev_token(api, "approver"))
    here_instrument = here_projected["instrument_id"]
    # Phase 15: a snapshot and a run on it.
    here_calculated = blocked_run(api, here_projected["import_id"])
    here_module = sql(
        db, "SELECT id FROM module_version WHERE implementation_status = 'AVAILABLE'"
    )[0]["id"]

    other_tenant = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    outsider = token_for(
        db,
        "dev:outsider",
        ("platform_admin", "auditor", "data_steward", "approver", "risk_analyst"),
        tenant_id=other_tenant,
    )
    # Lists hold only the outsider's own tenant.
    users = api.get("/api/v1/users", headers=outsider).json()["items"]
    assert len(users) == 1 and users[0]["display_name"].endswith("dev:outsider")
    for path in (
        "/api/v1/schemas",
        PROPOSALS,
        SOURCES,
        IMPORTS,
        # Phase 15: the new-run panel's filters stay in the caller's tenant (this tenant has
        # a projected instrument import).
        f"{IMPORTS}?projected=true&schema_code=instrument",
        INSTRUMENTS,
        SNAPSHOTS,
        RUNS,
        MODULES,
    ):
        assert api.get(path, headers=outsider).json()["total"] == 0, path
    audit = api.get("/api/v1/audit-events", params={"limit": 200}, headers=outsider).json()
    theirs = {
        str(r["id"])
        for r in sql(db, "SELECT id FROM audit_event WHERE tenant_id = $1", other_tenant)
    }
    assert audit["items"] and {e["id"] for e in audit["items"]} <= theirs

    # Reads by id: exactly the 404 an id that does not exist gets (no existence oracle).
    def without_cid(r: Any) -> dict[str, Any]:
        return {k: v for k, v in r.json().items() if k != "correlation_id"}

    for real, random_id in (
        (f"{SOURCES}/{here_source}", f"{SOURCES}/{uuid.uuid4()}"),
        (f"{IMPORTS}/{here_import}", f"{IMPORTS}/{uuid.uuid4()}"),
        (f"{IMPORTS}/{here_import}/profile", f"{IMPORTS}/{uuid.uuid4()}/profile"),
        # Phase 12: drift and reconciliation reads are scoped through the import.
        (f"{IMPORTS}/{here_import}/drift", f"{IMPORTS}/{uuid.uuid4()}/drift"),
        (
            f"{IMPORTS}/{here_import}/reconciliation",
            f"{IMPORTS}/{uuid.uuid4()}/reconciliation",
        ),
        (f"/api/v1/schemas/{instrument}/versions", f"/api/v1/schemas/{uuid.uuid4()}/versions"),
        (
            f"/api/v1/schemas/{instrument}/diff?from=1&to=2",
            f"/api/v1/schemas/{uuid.uuid4()}/diff?from=1&to=2",
        ),
        (f"{PROPOSALS}/{here['id']}", f"{PROPOSALS}/{uuid.uuid4()}"),
        (f"/api/v1/users/{here_user}", f"/api/v1/users/{uuid.uuid4()}"),
        # Phase 14: the instrument catalogue and the projection read.
        (f"{INSTRUMENTS}/{here_instrument}", f"{INSTRUMENTS}/{uuid.uuid4()}"),
        (
            f"{IMPORTS}/{here_projected['import_id']}/projection",
            f"{IMPORTS}/{uuid.uuid4()}/projection",
        ),
        # Phase 15: snapshots, runs, their results and their ladder.
        (f"{SNAPSHOTS}/{here_calculated['snapshot_id']}", f"{SNAPSHOTS}/{uuid.uuid4()}"),
        (f"{RUNS}/{here_calculated['run_id']}", f"{RUNS}/{uuid.uuid4()}"),
        (f"{RUNS}/{here_calculated['run_id']}/results", f"{RUNS}/{uuid.uuid4()}/results"),
        (
            f"{RUNS}/{here_calculated['run_id']}/maturity-ladder",
            f"{RUNS}/{uuid.uuid4()}/maturity-ladder",
        ),
    ):
        mine, absent = api.get(real, headers=outsider), api.get(random_id, headers=outsider)
        assert mine.status_code == absent.status_code == 404, real
        assert without_cid(mine) == without_cid(absent), real

    # Writes by id and against this tenant's schema/source find nothing either.
    for action, body in (
        ("impact-analysis", None),
        ("submit", {}),
        ("approval", {"decision": "APPROVE"}),
        ("revert", {}),
    ):
        r = api.post(f"{PROPOSALS}/{here['id']}/{action}", json=body, headers=outsider)
        assert r.status_code == 404, action
    fields = here["proposed_fields"]
    r = api.post(
        PROPOSALS,
        json={"schema_id": str(instrument), "title": "Cross tenant", "fields": fields},
        headers=outsider,
    )
    assert r.status_code == 404
    r = api.post(f"{SOURCES}/{here_source}/contracts", json=contract_body(db), headers=outsider)
    assert r.status_code == 404
    r = api.post(SOURCES, json=source_body(db, code="outsider_feed"), headers=outsider)
    assert (r.status_code, r.json()["message"]) == (404, "Schema not found.")
    # Phase 14: projecting this tenant's import is the same 404 as an absent one, audited.
    for import_id in (here_projected["import_id"], here_import):
        correlation = cid()
        r = api.post(
            f"{IMPORTS}/{import_id}/projection",
            headers={**outsider, "X-Correlation-ID": correlation},
        )
        assert (r.status_code, r.json()["code"]) == (404, "not_found")
        [denied] = events(db, correlation)
        assert (denied["outcome"], str(denied["tenant_id"])) == ("DENIED", str(other_tenant))
    # Phase 15: a snapshot of this tenant's import, and a run on this tenant's snapshot, are
    # the same 404 as absent ones, audited DENIED.
    for import_id in (here_projected["import_id"], str(uuid.uuid4())):
        correlation = cid()
        r = api.post(
            SNAPSHOTS,
            json={"import_ids": [import_id]},
            headers={**outsider, "X-Correlation-ID": correlation},
        )
        assert (r.status_code, r.json()["code"]) == (404, "not_found")
        [denied] = events(db, correlation)
        assert (denied["action"], denied["outcome"]) == ("dataset_snapshot.create", "DENIED")
    for snapshot_id in (here_calculated["snapshot_id"], str(uuid.uuid4())):
        correlation = cid()
        body = {
            "dataset_snapshot_id": snapshot_id,
            "module_version_id": str(here_module),
            "reporting_date": "2026-12-31",
        }
        r = api.post(
            RUNS,
            json=body,
            headers={**outsider, "X-Correlation-ID": correlation, "Idempotency-Key": cid()},
        )
        assert (r.status_code, r.json()["code"]) == (404, "not_found")
        [denied] = events(db, correlation)
        assert (denied["action"], denied["outcome"]) == ("calculation_run.create", "DENIED")
    correlation = cid()
    r = upload(api, outsider, here_source, b"instrument_id\nA\n", cid=correlation)
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    [denied] = events(db, correlation)
    assert denied["outcome"] == "DENIED"
    for table in (
        "schema_change_proposal",
        "source_system",
        "import_job",
        "domain_projection",
        "instrument",
        "instrument_version",
        "dataset_snapshot",
        "calculation_run",
        "rule_set",
    ):
        n = sql(db, f"SELECT count(*) AS n FROM {table} WHERE tenant_id = $1", other_tenant)
        assert n[0]["n"] == 0, table

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


# --- 3. Audit completeness -----------------------------------------------------------------------

# Every mutating route and the audit action it must record. A new mutating route fails this
# test until it is added here and exercised below.
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
    "POST /api/v1/mappings": "mapping.create",
    "POST /api/v1/mappings/{mapping_id}/versions": "mapping_version.create",
    "POST /api/v1/imports/{import_id}/mappings/validate": "mapping.validate",
    "POST /api/v1/mappings/{mapping_id}/versions/{version}/submit": "mapping_version.submit",
    "POST /api/v1/mappings/{mapping_id}/versions/{version}/approval": "mapping.approve",
    "POST /api/v1/imports/{import_id}/validate": "import.validate",
    "POST /api/v1/quarantine/{batch_id}/items/{quarantine_id}/disposition": (
        "quarantine.disposition"
    ),
    "POST /api/v1/drift-events/{drift_event_id}/review": "drift_event.review",
    # Phase 14 (BE-14.3, BE-14.5): projection into the WS3 domain.
    "POST /api/v1/imports/{import_id}/projection": "import.project",
    # Phase 15 (BE-15.3, BE-15.4): dataset snapshots and calculation runs.
    "POST /api/v1/dataset-snapshots": "dataset_snapshot.create",
    "POST /api/v1/calculation-runs": "calculation_run.create",
}


def test_every_mutating_route_is_listed_and_emits_an_audit_event(api: TestClient, db: str) -> None:
    mutating = {
        f"{m} {route.path}"
        for route in api.app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1")
        for m in route.methods
        if m in MUTATING
    }
    assert mutating == set(AUDITED)
    called: set[str] = set()

    def check(route: str, correlation: str) -> None:
        called.add(route)
        actions = [e["action"] for e in events(db, correlation)]
        assert AUDITED[route] in actions, f"{route} recorded {actions}"

    def call(route: str, headers: dict[str, str], path: str | None = None, body: Any = None) -> Any:
        correlation = cid()
        method, template = route.split(" ", 1)
        r = api.request(
            method,
            path or template,
            json=body,
            headers={**headers, "X-Correlation-ID": correlation},
        )
        assert r.status_code < 300, f"{route}: {r.status_code} {r.text}"
        check(route, correlation)
        return r.json()

    call("POST /api/v1/dev/sessions", {}, body={"role": "auditor"})
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    instrument = schema_id(db, "instrument")
    v1 = api.get(f"/api/v1/schemas/{instrument}/versions", headers=steward).json()["items"][0]
    fields = [
        {k: f[k] for k in FIELD_KEYS} for f in v1["fields"] if f["name"] != "legacy_book_code"
    ]
    p = call(
        "POST /api/v1/schema-change-proposals",
        steward,
        body={"schema_id": str(instrument), "title": "Audit check", "fields": fields},
    )
    base = f"{PROPOSALS}/{p['id']}"
    call(f"POST {PROPOSALS}/{{proposal_id}}/impact-analysis", steward, f"{base}/impact-analysis")
    call(f"POST {PROPOSALS}/{{proposal_id}}/submit", steward, f"{base}/submit", {})
    approval = {"decision": "APPROVE"}
    call(f"POST {PROPOSALS}/{{proposal_id}}/approval", approver, f"{base}/approval", approval)
    call(f"POST {PROPOSALS}/{{proposal_id}}/revert", steward, f"{base}/revert", {})
    source = call("POST /api/v1/sources", steward, body=source_body(db, code="audit_check_feed"))
    call(
        "POST /api/v1/sources/{source_id}/contracts",
        steward,
        f"{SOURCES}/{source['id']}/contracts",
        contract_body(db, schema="position"),
    )
    correlation = cid()
    r = upload(api, steward, source["id"], b"Instrument ID\nA\n", cid=correlation)
    assert r.status_code == 201, r.text
    check("POST /api/v1/imports", correlation)
    # Phase 10: create, a second version, validate it against a clean import, submit, approve.
    m = call("POST /api/v1/mappings", steward, body=ws1_mapping_body(db, WS1_RULES[:-1]))
    v2 = call(
        "POST /api/v1/mappings/{mapping_id}/versions",
        steward,
        f"/api/v1/mappings/{m['id']}/versions",
        {"rules": WS1_RULES},
    )
    clean_import = upload(api, steward, seeded_source_id(db), WS1_CSV)
    assert clean_import.status_code == 201, clean_import.text
    validated = call(
        "POST /api/v1/imports/{import_id}/mappings/validate",
        steward,
        f"/api/v1/imports/{clean_import.json()['id']}/mappings/validate",
        {"mapping_version_id": v2["id"]},
    )
    assert validated["ok"], validated
    versions = f"/api/v1/mappings/{m['id']}/versions/2"
    call(
        "POST /api/v1/mappings/{mapping_id}/versions/{version}/submit",
        steward,
        f"{versions}/submit",
        {},
    )
    call(
        "POST /api/v1/mappings/{mapping_id}/versions/{version}/approval",
        approver,
        f"{versions}/approval",
        {"decision": "APPROVE"},
    )
    # Phase 11: validate an import with a bad currency (QUARANTINED), then review its item.
    held = upload(api, steward, seeded_source_id(db), WS1_CSV + b"I-3,BOND,EURO,1.00\n")
    assert held.status_code == 201, held.text
    validated_import = call(
        "POST /api/v1/imports/{import_id}/validate",
        steward,
        f"/api/v1/imports/{held.json()['id']}/validate",
    )
    assert validated_import["status"] == "QUARANTINED", validated_import
    batch = validated_import["batches"][0]["id"]
    [item] = api.get(f"/api/v1/quarantine/{batch}", headers=steward).json()["items"]
    call(
        "POST /api/v1/quarantine/{batch_id}/items/{quarantine_id}/disposition",
        steward,
        f"/api/v1/quarantine/{batch}/items/{item['id']}/disposition",
        {"status": "UNDER_REVIEW"},
    )
    # Phase 12: the clean import, validated, is the drift baseline; a file with one more
    # column raises COLUMN_ADDED, which the steward reviews.
    done = api.post(f"{IMPORTS}/{clean_import.json()['id']}/validate", headers=steward)
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED", done.text
    # Phase 14: a validated import projected into the domain. The schema change above left
    # `instrument` v3 ACTIVE, which projector v1 refuses (D-29), so a cash-flow import is used.
    cash_flows = validated_sample(api, db, steward, approver, "cashflow_event")
    projected = call(
        "POST /api/v1/imports/{import_id}/projection",
        steward,
        f"{IMPORTS}/{cash_flows['id']}/projection",
    )
    assert projected["projected_count"] == projected["accepted_count"] == 25, projected
    # Phase 15: a snapshot of the projected cash flows and a run on it (BLOCKED: no
    # instruments in the snapshot, still a 201 and audited).
    analyst = dev_token(api, "risk_analyst")
    snapshot = call(
        "POST /api/v1/dataset-snapshots", analyst, body={"import_ids": [cash_flows["id"]]}
    )
    module = module_version_id(api, analyst)  # maturity_ladder: PLANNED modules never run
    run = call(
        "POST /api/v1/calculation-runs",
        {**analyst, "Idempotency-Key": cid()},
        body={
            "dataset_snapshot_id": snapshot["snapshot_id"],
            "module_version_id": module,
            "reporting_date": "2026-12-31",
        },
    )
    assert run["status"] == "BLOCKED", run
    # Phase 18 (BE-18.1): a PLANNED module version never runs: 409, audited DENIED, no run.
    correlation = cid()
    r = api.post(
        RUNS,
        json={
            "dataset_snapshot_id": snapshot["snapshot_id"],
            "module_version_id": module_version_id(api, analyst, "alm_irrbb"),
            "reporting_date": "2026-12-31",
        },
        headers={**analyst, "Idempotency-Key": cid(), "X-Correlation-ID": correlation},
    )
    assert (r.status_code, r.json()["code"]) == (409, "module_not_available"), r.text
    assert r.json()["details"]["reason"] == "planned"
    [denied] = events(db, correlation)
    assert (denied["action"], denied["outcome"]) == ("calculation_run.create", "DENIED")
    wider = upload(
        api,
        steward,
        seeded_source_id(db),
        b"instrument_id,instrument_type,currency,notional,extra\nI-1,BOND,EUR,100.00,x\n",
    )
    assert wider.status_code == 201, wider.text
    drift = api.get(f"{IMPORTS}/{wider.json()['id']}/drift", headers=steward).json()["items"]
    [added] = [e for e in drift if e["drift_type"] == "COLUMN_ADDED"]
    call(
        "POST /api/v1/drift-events/{drift_event_id}/review",
        steward,
        f"/api/v1/drift-events/{added['id']}/review",
        {"outcome": "ACKNOWLEDGED", "comment": "Expected: a new optional column."},
    )
    # Every listed route was exercised here: listing a route without driving it fails.
    assert called == set(AUDITED)


# --- 4. Migrations -------------------------------------------------------------------------------


# BE-13.2: the WS2 suite's base -> head round trip is this test (one test, both suites).
@pytest.mark.ws2
def test_migration_round_trip_and_downgrade_guards(api: TestClient, db: str) -> None:
    head = str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())

    def revision() -> str:
        return str(sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"])

    # User data blocks the downgrade, in one transaction: nothing is removed or downgraded.
    steward = dev_token(api, "data_steward")
    assert upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n").status_code == 201
    tables = ("data_profile_snapshot", "import_job", "source_record", "audit_event")
    before = {t: sql(db, f"SELECT * FROM {t} ORDER BY id") for t in tables}
    try:
        with pytest.raises(Exception, match="data_profile_snapshot has rows"):
            command.downgrade(alembic_config(db), "base")
        assert revision() == head
        assert {t: sql(db, f"SELECT * FROM {t} ORDER BY id") for t in tables} == before

        # Without user data, audit rows still block it unless the loss is acknowledged.
        clean(db)
        assert sql(db, "SELECT count(*) AS n FROM audit_event")[0]["n"] > 0
        guarded = alembic_config(db)
        guarded.attributes.pop("allow_audit_loss", None)
        with pytest.raises(Exception, match="refusing to drop the audit trail"):
            command.downgrade(guarded, "base")
        assert revision() == head
    finally:
        command.upgrade(alembic_config(db), "head")

    # base -> head with the seed.
    cfg = alembic_config(db)
    command.downgrade(cfg, "base")
    assert sql(db, "SELECT to_regclass('schema_version') AS t")[0]["t"] is None
    command.upgrade(cfg, "head")
    assert revision() == head
    seeded = sql(db, "SELECT count(*) AS n FROM schema_version WHERE seeded_by IS NOT NULL")
    assert seeded[0]["n"] == 4
    assert (
        sql(db, "SELECT count(*) AS n FROM source_system WHERE seeded_by IS NOT NULL")[0]["n"] == 1
    )
