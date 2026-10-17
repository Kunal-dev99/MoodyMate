"""WS2 verification suite (BE-13.2). Run on its own with `pytest -m ws2 -v -s`; CI runs it
as a separate step and the WS2 route x role matrix is printed in the test report.

One test (D-26: the suite stays under 50 tests), in four parts that share one prepared
fixture, the end-to-end run itself:

1. End to end: a steward registers a CSV source and supersedes its contract, uploads (the
   profile is taken), maps (v1, v2, dry run, submit), an approver activates the mapping,
   the steward validates (COMPLETED, then QUARANTINED) and the reconciliation is read; a
   quarantine item is reviewed and a drift alert is acknowledged. Every mutating step is
   first tried by every other seeded role (403 `forbidden` with the declared
   `required_permission`, nothing done), then done by the one role allowed (2xx), and must
   record its `AUDITED` action (audit completeness: every WS2 mutating route is in
   `AUDITED`, and every WS2 `AUDITED` route is called).
2. Row conservation: every source row of a validated import is accepted (lineage) or held
   (a quarantine item), exactly once, and the manifest and reconciliation agree.
3. Matrix: every WS2 read route x every seeded role on the prepared fixture: 2xx for the
   roles allowed, 403 `forbidden` with `required_permission` for the others.
4. Cross-tenant isolation for sources, imports, mappings, quarantine and drift: another
   tenant's user reads and changes nothing here (the same 404 as a random id), lists hold
   only the caller's tenant's items, and nothing is written into the other tenant.

The base -> head migration round trip is the WS1 suite's
`test_migration_round_trip_and_downgrade_guards`, marked `ws2` as well, so `pytest -m ws2`
runs it too.
"""

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.governance.constants import DEFAULT_TENANT_ID, WS2_ROLE_PERMISSIONS, Role
from app.tests.conftest import WS2_MATRIX, dev_token, insert_tenant, sql
from app.tests.helpers import (
    IMPORTS,
    INSTRUMENT_RULES,
    MAPPINGS,
    SOURCES,
    cid,
    contract_body,
    events,
    sample,
    source_body,
    token_for,
    upload,
)
from app.tests.test_ws1_verification import AUDITED, MUTATING, ROLES, fill, protected_routes

pytestmark = [pytest.mark.db, pytest.mark.ws2]

WS2_PERMISSIONS = frozenset().union(*WS2_ROLE_PERMISSIONS.values())
Send = Callable[[dict[str, str]], Response]


class Run:
    """The WS2 routes, the seeded roles' tokens and what the suite has proved so far."""

    def __init__(self, api: TestClient, db: str) -> None:
        self.api, self.db = api, db
        self.routes = {
            f"{m} {p}": perm for m, p, perm in protected_routes(api) if perm in WS2_PERMISSIONS
        }
        self.tokens = {role: dev_token(api, role) for role in ROLES}
        self.rows: dict[str, dict[str, Any]] = {}
        self.called: set[str] = set()

    def allowed(self, route: str) -> set[str]:
        perm = self.routes[route]
        return {r for r in ROLES if perm in WS2_ROLE_PERMISSIONS[Role(r)]}

    def row(self, route: str) -> dict[str, Any]:
        return self.rows.setdefault(route, {"route": route, "permission": self.routes[route]})

    def refused(self, route: str, role: str, r: Response) -> None:
        body = r.json()
        proved = (
            r.status_code == 403
            and body.get("code") == "forbidden"
            and body.get("details", {}).get("required_permission") == self.routes[route]
        )
        assert proved, f"{route} as {role}: {r.status_code} {body}, expected 403 forbidden"
        self.row(route)[role] = r.status_code

    def step(self, route: str, actor: str, send: Send) -> Any:
        """A mutating route: refused for every other role, then done by `actor` (2xx) with
        its audit action recorded."""
        assert route in self.routes, f"{route} is not a WS2 route"
        assert self.allowed(route) == {actor}, f"{route}: allowed {self.allowed(route)}"
        for role in ROLES:
            if role != actor:
                self.refused(route, role, send({**self.tokens[role], "X-Correlation-ID": cid()}))
        correlation = cid()
        r = send({**self.tokens[actor], "X-Correlation-ID": correlation})
        assert r.status_code < 300, f"{route} as {actor}: {r.status_code} {r.text}"
        self.row(route)[actor] = r.status_code
        actions = [e["action"] for e in events(self.db, correlation)]
        assert AUDITED.get(route) in actions, f"{route} recorded {actions}"
        self.called.add(route)
        return r.json()

    def post(self, route: str, actor: str, path: str, body: Any = None) -> Any:
        return self.step(route, actor, lambda h: self.api.post(path, json=body, headers=h))


def conserved(db: str, import_id: str) -> dict[str, int]:
    """Each source row of the import is accepted (lineage) or held (quarantine), once."""
    rows = sql(
        db,
        "SELECT r.ordinal,"
        " (SELECT count(*) FROM record_lineage l WHERE l.source_record_id = r.id) AS accepted,"
        " (SELECT count(*) FROM quarantine_item q WHERE q.source_record_id = r.id) AS held"
        " FROM source_record r JOIN import_batch b ON b.id = r.batch_id"
        " WHERE b.import_job_id = $1 ORDER BY r.ordinal",
        uuid.UUID(import_id),
    )
    unconserved = [r["ordinal"] for r in rows if r["accepted"] + r["held"] != 1]
    assert not unconserved, f"rows not in exactly one outcome: {unconserved}"
    accepted = sum(r["accepted"] for r in rows)
    return {"rows": len(rows), "accepted": accepted, "held": len(rows) - accepted}


def test_ws2_end_to_end_matrix_isolation_and_audit(api: TestClient, db: str) -> None:
    run = Run(api, db)
    steward = run.tokens["data_steward"]

    # --- 1. End to end, each mutating step refused for the other roles -----------------------
    source = run.post(
        "POST /api/v1/sources", "data_steward", SOURCES, source_body(db, code="ws2_e2e_feed")
    )
    seeded_columns = json.loads(
        sql(db, "SELECT expected_columns FROM source_contract WHERE seeded_by IS NOT NULL")[0][
            "expected_columns"
        ]
    )
    contract = run.post(
        "POST /api/v1/sources/{source_id}/contracts",
        "data_steward",
        f"{SOURCES}/{source['id']}/contracts",
        contract_body(
            db,
            expected_columns=seeded_columns,
            business_key_columns=["instrument_id"],
            control_total_columns=["notional"],
            change_summary="The delivered file's columns.",
        ),
    )
    assert (contract["version"], contract["supersedes_id"]) == (2, source["contracts"][0]["id"])

    def upload_step(data: bytes) -> Any:
        return run.step(
            "POST /api/v1/imports",
            "data_steward",
            lambda h: upload(api, h, source["id"], data, filename="instrument.csv"),
        )

    clean = upload_step(sample("instrument.csv"))
    assert (clean["status"], clean["row_count"]) == ("MAPPING_REQUIRED", 25), clean
    profile = api.get(f"{IMPORTS}/{clean['id']}/profile", headers=steward)
    assert profile.status_code == 200 and profile.json()["row_count"] == 25, profile.text

    mapping = run.post(
        "POST /api/v1/mappings",
        "data_steward",
        MAPPINGS,
        {
            "code": "ws2_e2e",
            "name": "WS2 end to end",
            "source_contract_id": contract["id"],
            "rules": INSTRUMENT_RULES[:-1],
        },
    )
    v2 = run.post(
        "POST /api/v1/mappings/{mapping_id}/versions",
        "data_steward",
        f"{MAPPINGS}/{mapping['id']}/versions",
        {"rules": INSTRUMENT_RULES},
    )
    dry_run = run.post(
        "POST /api/v1/imports/{import_id}/mappings/validate",
        "data_steward",
        f"{IMPORTS}/{clean['id']}/mappings/validate",
        {"mapping_version_id": v2["id"]},
    )
    assert dry_run["ok"], dry_run
    versions = f"{MAPPINGS}/{mapping['id']}/versions/{v2['version']}"
    run.post(
        "POST /api/v1/mappings/{mapping_id}/versions/{version}/submit",
        "data_steward",
        f"{versions}/submit",
        {},
    )
    active = run.post(
        "POST /api/v1/mappings/{mapping_id}/versions/{version}/approval",
        "approver",
        f"{versions}/approval",
        {"decision": "APPROVE"},
    )
    assert active["status"] == "ACTIVE", active

    def validate_step(import_id: str) -> Any:
        return run.post(
            "POST /api/v1/imports/{import_id}/validate",
            "data_steward",
            f"{IMPORTS}/{import_id}/validate",
        )

    done = validate_step(clean["id"])
    assert (done["status"], done["mapping_version_id"]) == ("COMPLETED", v2["id"]), done
    mixed = upload_step(sample("instrument__mixed_errors.csv"))
    held = validate_step(mixed["id"])
    assert held["status"] == "QUARANTINED", held

    def reconciliation(import_id: str) -> Any:
        r = api.get(f"{IMPORTS}/{import_id}/reconciliation", headers=steward)
        assert r.status_code == 200, r.text
        return r.json()

    assert reconciliation(clean["id"])["outcome"] == "MATCHED"
    recon = reconciliation(mixed["id"])
    assert recon["outcome"] == "REVIEW_REQUIRED", recon

    batch = held["batches"][0]["id"]
    items = api.get(f"/api/v1/quarantine/{batch}", headers=steward).json()["items"]
    item = next(i for i in items if i["status"] == "OPEN")
    reviewed = run.post(
        "POST /api/v1/quarantine/{batch_id}/items/{quarantine_id}/disposition",
        "data_steward",
        f"/api/v1/quarantine/{batch}/items/{item['id']}/disposition",
        {"status": "UNDER_REVIEW"},
    )
    assert reviewed["status"] == "UNDER_REVIEW", reviewed

    wider = upload_step(sample("instrument__unknown_extra_column.csv"))
    drift = api.get(f"{IMPORTS}/{wider['id']}/drift", headers=steward).json()["items"]
    [added] = [e for e in drift if e["drift_type"] == "COLUMN_ADDED"]
    acknowledged = run.post(
        "POST /api/v1/drift-events/{drift_event_id}/review",
        "data_steward",
        f"/api/v1/drift-events/{added['id']}/review",
        {"outcome": "ACKNOWLEDGED", "comment": "Expected: a new optional column."},
    )
    assert acknowledged["status"] == "ACKNOWLEDGED", acknowledged

    # Audit completeness: every WS2 mutating route is AUDITED and was driven above.
    ws2_mutating = {r for r in run.routes if r.split(" ", 1)[0] in MUTATING}
    assert ws2_mutating <= set(AUDITED), ws2_mutating - set(AUDITED)
    assert run.called == ws2_mutating == set(AUDITED) & set(run.routes)

    # --- 2. Row conservation -----------------------------------------------------------------
    for validated, outcome in ((done, (25, 25, 0)), (held, (25, 20, 5))):
        stored = conserved(db, validated["id"])
        assert (stored["rows"], stored["accepted"], stored["held"]) == outcome, stored
        quality = validated["manifest"]["quality"]
        assert quality["row_count"] == stored["rows"]
        assert quality["accepted_records"] == stored["accepted"]
        assert quality["quarantined_records"] + quality["rejected_records"] == stored["held"]
        [count] = [i for i in reconciliation(validated["id"])["items"] if i["check"] == "ROW_COUNT"]
        assert int(count["source"]) == int(count["target"]) + int(count["held"]), count

    # --- 3. Matrix: every WS2 read route x every seeded role on the prepared fixture ---------
    ids = {
        "source_id": source["id"],
        "import_id": mixed["id"],
        "mapping_id": mapping["id"],
        "batch_id": batch,
    }
    reads = sorted(r for r in run.routes if r.split(" ", 1)[0] not in MUTATING)
    for route in reads:
        method, template = route.split(" ", 1)
        allowed = run.allowed(route)
        for role in ROLES:
            r = api.request(method, fill(template, ids), headers=run.tokens[role])
            if role in allowed:
                assert r.status_code < 300, f"{route} as {role}: {r.status_code} {r.text}"
                run.row(route)[role] = r.status_code
            else:
                run.refused(route, role, r)
    assert set(run.rows) == set(run.routes), set(run.routes) - set(run.rows)
    for route in sorted(run.rows):
        assert set(run.rows[route]) == {"route", "permission", *ROLES}, run.rows[route]
        WS2_MATRIX.append(run.rows[route])

    # --- 4. Cross-tenant isolation -------------------------------------------------------------
    other = insert_tenant(db, f"ws2-other-{uuid.uuid4().hex[:6]}")
    foreign = sql(
        db,
        "INSERT INTO source_system (id, tenant_id, code, name, kind, created_by)"
        " VALUES (gen_random_uuid(), $1, 'outsider_feed', 'Outsider', 'FILE_DROP', 'test')"
        " RETURNING id",
        other,
    )[0]["id"]
    sql(
        db,
        "INSERT INTO source_profile (id, source_system_id, format, encoding, delimiter, owner,"
        " default_classification, provisional, created_by) VALUES (gen_random_uuid(), $1,"
        " 'CSV', 'UTF-8', ',', 'Outsider team', 'confidential', true, 'test')",
        foreign,
    )
    outsider = token_for(db, "dev:ws2-outsider", tuple(ROLES), tenant_id=other)

    # Lists hold only the caller's tenant's items, both ways.
    theirs = api.get(SOURCES, headers=outsider).json()
    assert [i["id"] for i in theirs["items"]] == [str(foreign)], theirs
    for path in (IMPORTS, MAPPINGS):
        assert api.get(path, headers=outsider).json()["total"] == 0, path
    tenant_of = {
        SOURCES: "SELECT tenant_id FROM source_system WHERE id = $1",
        IMPORTS: "SELECT tenant_id FROM import_job WHERE id = $1",
        MAPPINGS: "SELECT tenant_id FROM field_mapping WHERE id = $1",
        f"/api/v1/quarantine/{batch}": "SELECT j.tenant_id FROM quarantine_item q"
        " JOIN import_job j ON j.id = q.import_job_id WHERE q.id = $1",
        f"{IMPORTS}/{wider['id']}/drift": "SELECT tenant_id FROM data_drift_event WHERE id = $1",
    }
    for path, query in tenant_of.items():
        listed = api.get(path, params={"limit": 200}, headers=steward).json()["items"]
        assert listed, f"{path}: nothing listed"
        for i in listed:
            owner = sql(db, query, uuid.UUID(i["id"]))[0]["tenant_id"]
            assert owner == DEFAULT_TENANT_ID, f"{path}: {i['id']} belongs to {owner}"
    assert api.get(f"{SOURCES}/{foreign}", headers=steward).status_code == 404

    # Reads by id: exactly the 404 a random id gets (no existence oracle).
    def without_cid(r: Response) -> dict[str, Any]:
        return {k: v for k, v in r.json().items() if k != "correlation_id"}

    for route in reads:
        template = route.split(" ", 1)[1]
        if "{" not in template:
            continue
        mine = api.get(fill(template, ids), headers=outsider)
        absent = api.get(fill(template), headers=outsider)
        assert mine.status_code == absent.status_code == 404, f"{route}: {mine.status_code}"
        assert without_cid(mine) == without_cid(absent), route

    # Writes against this tenant's sources, imports, mappings, quarantine and drift: 404.
    base = f"{MAPPINGS}/{mapping['id']}/versions"
    for path, body in (
        (f"{SOURCES}/{source['id']}/contracts", contract_body(db)),
        (f"{IMPORTS}/{wider['id']}/validate", None),
        (f"{IMPORTS}/{wider['id']}/mappings/validate", {"mapping_version_id": v2["id"]}),
        (base, {"rules": INSTRUMENT_RULES}),
        (f"{base}/1/submit", {}),
        (f"{base}/1/approval", {"decision": "APPROVE"}),
        (
            f"/api/v1/quarantine/{batch}/items/{item['id']}/disposition",
            {"status": "OPEN"},
        ),
        (
            f"/api/v1/drift-events/{added['id']}/review",
            {"outcome": "ACKNOWLEDGED", "comment": "Outsider."},
        ),
    ):
        r = api.post(path, json=body, headers=outsider)
        assert (r.status_code, r.json()["code"]) == (404, "not_found"), f"{path}: {r.text}"
    r = upload(api, outsider, source["id"], sample("instrument.csv"))
    assert (r.status_code, r.json()["code"]) == (404, "not_found"), r.text
    for table, expected in (
        ("source_system", 1),
        ("import_job", 0),
        ("field_mapping", 0),
        ("reconciliation_run", 0),
        ("data_drift_event", 0),
    ):
        n = sql(db, f"SELECT count(*) AS n FROM {table} WHERE tenant_id = $1", other)[0]["n"]
        assert n == expected, f"{table}: {n} rows in the other tenant"
    # And nothing here moved: the item and the drift event are as the steward left them.
    for table, row_id, status in (
        ("quarantine_item", item["id"], "UNDER_REVIEW"),
        ("data_drift_event", added["id"], "ACKNOWLEDGED"),
    ):
        [now] = sql(db, f"SELECT status FROM {table} WHERE id = $1", uuid.UUID(row_id))
        assert now["status"] == status, f"{table} moved to {now['status']}"
