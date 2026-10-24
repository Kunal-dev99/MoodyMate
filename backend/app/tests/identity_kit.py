"""Phase 31 (BE-31.1 – BE-31.5) helpers for the folds in test_domain.py, test_credit.py and
test_ws1_verification.py (D-26: no new test function, budget 49 -> 49).

- `GOLDEN_V1`: the v1 projection of the three synthetic samples (each summary, every
  `payload_sha256` by source ordinal), taken on the projector before Phase 31
  (app/tests/golden/domain_v1_projection.json). v1 behaviour must stay byte-identical (D-57 §1).
- `typed_fields` / `publish_typed_instrument`: the typed-maturity contract (D-57 option B: v1
  with `maturity_date` DATE and `interest_rate` NUMERIC(11,6), nothing else) proposed through
  the existing governed path and approved by a second person.
- `typed_source` / `typed_import`: an instrument CSV on its own instrument source after
  publication: the import pins the new ACTIVE schema version, so the first one needs a mapping
  for it (D-18 §4); validated.
- `links_of`, `reconcile`, `identity_shape`, `check_identity_append_only`: identity-link reads
  and the 0030 guards (D-58).
"""

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.tests.conftest import dev_token, sql
from app.tests.helpers import (
    INSTRUMENT_RULES,
    MAPPINGS,
    PROPOSALS,
    SOURCES,
    activate_version,
    contract_body,
    project,
    schema_id,
    source_body,
    upload,
    validate_import,
    validated_sample,
)

LEGAL_ENTITIES = "/api/v1/legal-entities"
LINKS = "/api/v1/identity-links"
RECONCILE = f"{LINKS}/reconcile"
IDENTITY_TABLES = ("legal_entity", "domain_identity_link")
GOLDEN_V1: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parent / "golden" / "domain_v1_projection.json").read_text()
)
KEEP = (
    "name",
    "data_type",
    "required",
    "unit",
    "precision",
    "scale",
    "classification",
    "extension_eligible",
)
# The edge cases of BE-31.3 on the typed contract: one good DATE, three bad dates held by
# validation, a missing maturity (NULL, counted), and interest rates at exactly 6 dp.
TYPED_EDGE_CSV = (
    b"instrument_id,instrument_type,currency,counterparty_id,product_family,notional,"
    b"interest_rate,maturity_date,book_code\n"
    b"TM-1,LOAN,EUR,CP-1,CORPORATE,1000.00,2.123456,2030-06-30,BK-1\n"
    b"TM-2,LOAN,EUR,CP-1,CORPORATE,1000.00,1.000001,2030-02-30,BK-1\n"
    b"TM-3,LOAN,EUR,CP-1,CORPORATE,1000.00,1.000001,2030-13-01,BK-1\n"
    b"TM-4,LOAN,EUR,CP-1,CORPORATE,1000.00,1.000001,n/a,BK-1\n"
    b"TM-5,LOAN,EUR,CP-1,CORPORATE,1000.00,0.000001,,BK-1\n"
)


def golden_check(db: str, code: str, body: dict[str, Any]) -> None:
    """A v1 import's projection equals the pre-phase golden (BE-31.3)."""
    table = {
        "instrument": "instrument_version",
        "position": "position_version",
        "cashflow_event": "cashflow_event",
    }[code]
    golden = GOLDEN_V1[code]
    assert (body["projector_version"], body["schema_version"]) == (1, 1), code
    assert body["summary"] == golden["summary"], code
    rows = sql(
        db,
        f"SELECT payload_sha256 FROM {table} WHERE import_job_id = $1 ORDER BY source_ordinal",
        uuid.UUID(body["import_id"]),
    )
    assert [r["payload_sha256"] for r in rows] == golden["payload_sha256"], code


def version_fields(api: TestClient, headers: dict[str, str], sid: Any, version: int) -> list[Any]:
    items = api.get(f"/api/v1/schemas/{sid}/versions", headers=headers).json()["items"]
    [v] = [v for v in items if v["version"] == version]
    return [{k: f[k] for k in KEEP} for f in v["fields"]]


def typed_fields(api: TestClient, headers: dict[str, str], sid: Any) -> list[dict[str, Any]]:
    """instrument v1 with `maturity_date` DATE and `interest_rate` (11,6); nothing else."""
    fields = version_fields(api, headers, sid, 1)
    names = [f["name"] for f in fields]
    assert {"maturity_date", "interest_rate"} <= set(names), names
    for f in fields:
        if f["name"] == "maturity_date":
            f.update(data_type="DATE", precision=None, scale=None)
        elif f["name"] == "interest_rate":
            f.update(data_type="DECIMAL", precision=11, scale=6)
    return fields


def schema_versions(db: str, code: str = "instrument") -> dict[int, dict[str, Any]]:
    return {
        r["version"]: dict(r)
        for r in sql(
            db,
            "SELECT v.id, v.version, v.status, v.content_sha256 FROM schema_version v"
            " JOIN schema_definition d ON d.id = v.schema_id WHERE d.code = $1",
            code,
        )
    }


def approve_proposal(
    api: TestClient,
    steward: dict[str, str],
    approver: dict[str, str],
    db: str,
    fields: list[dict[str, Any]],
    title: str,
) -> dict[str, Any]:
    """Propose, analyse (BREAKING, the core_instrument mapping a dependent), submit, and approve
    by a second person. Returns the APPLIED proposal."""
    sid = schema_id(db, "instrument")
    r = api.post(
        PROPOSALS, json={"schema_id": str(sid), "title": title, "fields": fields}, headers=steward
    )
    assert r.status_code == 201, r.text
    base = f"{PROPOSALS}/{r.json()['id']}"
    impact = api.post(f"{base}/impact-analysis", headers=steward).json()["impact"]
    assert impact["overall"] == "BREAKING", impact
    assert any(
        d["dependent_type"] == "MAPPING" and d["dependent_ref"] == "core_instrument"
        for d in impact["dependents"]
    ), impact["dependents"]
    assert api.post(f"{base}/submit", json={}, headers=steward).status_code == 200
    # Maker-checker: the proposer cannot approve; a second person does.
    r = api.post(f"{base}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200 and r.json()["status"] == "APPLIED", r.text
    return dict(r.json())


TYPED_COLUMNS = (
    "instrument_id",
    "instrument_type",
    "currency",
    "counterparty_id",
    "product_family",
    "notional",
    "interest_rate",
    "maturity_date",
    "book_code",
)


def typed_source(
    api: TestClient, db: str, steward: dict[str, str], code: str = "typed_instruments"
) -> dict[str, Any]:
    """A second instrument CSV source (its own contract: the sample's columns, keyed by
    `instrument_id`). Its instrument keys repeat the seeded source's: two source systems."""
    contract = contract_body(
        db,
        expected_columns=[{"name": c, "required": True} for c in TYPED_COLUMNS],
        business_key_columns=["instrument_id"],
        control_total_columns=[],
    )
    r = api.post(SOURCES, json=source_body(db, code=code, contract=contract), headers=steward)
    assert r.status_code == 201, r.text
    return dict(r.json())


def typed_import(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    approver: dict[str, str],
    source: dict[str, Any],
    data: bytes,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`data` on `source` after a publication: the import pins the new ACTIVE schema version,
    so the first one needs a mapping for that version (D-18 §4). Validated."""
    r = upload(api, steward, source["id"], data, filename="typed.csv")
    assert r.status_code == 201, r.text
    imported = r.json()
    if imported["status"] == "MAPPING_REQUIRED":
        mapping = {
            "code": f"{source['code']}_map",
            "name": f"{source['code']} (test)",
            "source_contract_id": source["current_contract"]["id"],
            "rules": rules or INSTRUMENT_RULES,
        }
        r = api.post(MAPPINGS, json=mapping, headers=steward)
        assert r.status_code == 201, r.text
        activate_version(api, steward, approver, r.json()["versions"][0], imported["id"])
    r = validate_import(api, steward, imported["id"])
    assert r.status_code == 200, r.text
    return dict(r.json())


def links_of(db: str, kind: str) -> dict[tuple[Any, str], Any]:
    """(source_row_id, target_kind) -> target_id of every link from rows of this kind."""
    return {
        (r["source_row_id"], r["target_kind"]): r["target_id"]
        for r in sql(
            db,
            "SELECT source_row_id, target_kind, target_id FROM domain_identity_link"
            " WHERE source_kind = $1",
            kind,
        )
    }


def counts_by_pair(body: dict[str, Any]) -> dict[tuple[str, str], dict[str, int]]:
    return {(c["source_kind"], c["target_kind"]): c for c in body["counts"]}


def reconcile(api: TestClient, headers: dict[str, str], **kw: str) -> dict[str, Any]:
    r = api.post(RECONCILE, headers={**headers, **kw})
    assert r.status_code == 200, r.text
    body = dict(r.json())
    for c in body["counts"]:  # conservation: every row examined lands in one bucket
        assert c["rows"] == c["linked"] + c["unresolved"] + c["ambiguous"] + c["no_key"], c
        assert 0 <= c["linked_now_ambiguous"] <= c["linked"] and c["links_written"] <= c["linked"]
    assert body["links_written"] == sum(c["links_written"] for c in body["counts"])
    return body


def without_written(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: v for k, v in c.items() if k != "links_written"} for c in body["counts"]]


def identity_counts(db: str) -> dict[str, int]:
    return {t: int(sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"]) for t in IDENTITY_TABLES}


def check_identity_append_only(db: str) -> None:
    """UPDATE, DELETE and TRUNCATE are refused on both 0030 tables (BE-31.1)."""
    for table in IDENTITY_TABLES:
        [row] = sql(db, f"SELECT * FROM {table} ORDER BY id LIMIT 1")
        for statement in (
            f"UPDATE {table} SET created_by = 'tampered' WHERE id = $1",
            f"UPDATE {table} SET tenant_id = gen_random_uuid() WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
                sql(db, statement, row["id"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
        assert sql(db, f"SELECT * FROM {table} WHERE id = $1", row["id"]) == [row], table


def identity_shape(db: str, tables: tuple[str, ...]) -> list[Any]:
    """The tables' columns, constraints and triggers, as the catalog states them."""
    shape: list[Any] = []
    for table in tables:
        shape += sql(
            db,
            "SELECT table_name, column_name, data_type, is_nullable, column_default,"
            " numeric_precision, numeric_scale FROM information_schema.columns"
            " WHERE table_name = $1 ORDER BY ordinal_position",
            table,
        )
        shape += sql(
            db,
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint"
            " WHERE conrelid = $1::regclass ORDER BY conname",
            table,
        )
        shape += sql(
            db,
            "SELECT tgname, pg_get_triggerdef(oid) AS d FROM pg_trigger"
            " WHERE tgrelid = $1::regclass AND NOT tgisinternal ORDER BY tgname",
            table,
        )
    return [dict(r) for r in shape]


def projected_legal_entity(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> str:
    """The position sample projected: its legal entity codes become identities (D-58 §2).
    Returns one legal_entity_id of the caller's tenant."""
    positions = validated_sample(api, db, steward, approver, "position")
    assert project(api, steward, positions["id"]).status_code == 201
    page = api.get(LEGAL_ENTITIES, headers=dev_token(api, "auditor")).json()
    assert page["total"] >= 1, page
    return str(page["items"][0]["legal_entity_id"])


# BE-31.4: two cash flows on the cash-flow sample's own source, projected once the sample's
# instrument keys exist on two source systems: one names such a key (LINK_AMBIGUOUS), the
# other a key no instrument has (LINK_UNRESOLVED). Neither is linked.
EXTRA_FLOWS = b"""{
  "marker": "ILLUSTRATIVE / SYNTHETIC identity-link fixture. Not client data.",
  "records": [
    {"cashflow_id": "CF-AMB-1", "instrument_id": "INS-000001", "due_date": "2027-09-02", "amount": 100.00, "currency": "EUR", "cashflow_type": "FEE", "origin": "CONTRACTUAL"},
    {"cashflow_id": "CF-UNR-1", "instrument_id": "INS-NOT-HELD", "due_date": "2027-09-02", "amount": 100.00, "currency": "EUR", "cashflow_type": "FEE", "origin": "CONTRACTUAL"}
  ]
}
"""  # noqa: E501


def run_outputs(db: str, run_id: str) -> dict[str, list[Any]]:
    """A run's stored outputs as `result_sha256` hashes them (D-31 §4), with each contribution's
    instrument version named by its instrument's business key instead of its row id: two
    instrument imports of the same instruments have different version rows, so their runs'
    hashes differ by those ids alone."""
    rid = uuid.UUID(run_id)
    return {
        "gap": [
            dict(r)
            for r in sql(
                db,
                "SELECT sequence, currency, bucket_code, bucket_sequence, inflows, outflows,"
                " net_gap, cumulative_gap, inflows_contractual, inflows_behavioural,"
                " outflows_contractual, outflows_behavioural, contribution_count"
                " FROM liquidity_gap_result WHERE calculation_run_id = $1 ORDER BY sequence",
                rid,
            )
        ],
        "contributions": [
            dict(r)
            for r in sql(
                db,
                "SELECT c.sequence, c.code, c.value, c.unit, c.bucket_code, c.direction, c.origin,"
                " c.days_after_reporting_date, c.rule_version_ids::text AS rules,"
                " c.cashflow_event_id, i.business_key AS instrument FROM intermediate_value c"
                " LEFT JOIN instrument_version v ON v.id = c.instrument_version_id"
                " LEFT JOIN instrument i ON i.id = v.instrument_id"
                " WHERE c.calculation_run_id = $1 ORDER BY c.sequence",
                rid,
            )
        ],
        "warnings": [
            dict(r)
            for r in sql(
                db,
                "SELECT sequence, code, severity, entity_type, entity_id, details::text AS d"
                " FROM calculation_warning WHERE calculation_run_id = $1 ORDER BY sequence",
                rid,
            )
        ],
    }
