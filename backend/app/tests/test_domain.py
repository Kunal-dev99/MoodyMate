"""Phase 14 (BE-14.2 – BE-14.4): the WS3 domain projection and the instrument catalogue, in one
essential test (D-26), in four parts:

1. Seed: the WS3 role grants in the database equal WS3_ROLE_PERMISSIONS (and the plan's
   matrix); the frozen copies in migration 0017 equal the code; the PROVISIONAL instrument
   types are seeded and tagged.
2. Projection: each refusal (not_validated, projection_unsupported, the switch off) is a 409
   with its DENIED audit event and writes nothing; the synthetic instrument, position and
   cash-flow imports project one row per accepted record (= the manifest's accepted count),
   a second call writes nothing, every row resolves through `record_lineage` to its import,
   and no stored or returned value is a float. A second instrument import adds versions.
3. Catalogue: filters, `notional` and `payload_sha256` masked for risk_analyst and clear
   for data_steward, and the version -> canonical record -> source row chain of one
   instrument, with the cash flows of its own source only.
4. Guards: UPDATE, DELETE and TRUNCATE are refused on every domain table; an insert into
   another tenant, with another tenant's instrument type, with a schema code that is not
   the import's, or under a projection of another schema code is refused; and the
   downgrade refuses while a projection exists; without one, 0017 -> 0016 -> 0017 removes
   and restores the tagged seed.
"""

import importlib.util
import inspect
import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain import service as domain_service
from app.domain.constants import (
    DOMAIN_SEED_TAG,
    PROJECTABLE_STATUSES,
    SEEDED_INSTRUMENT_TYPES,
    CashflowOrigin,
    CashflowType,
    ColumnKind,
    DomainSchemaCode,
)
from app.governance.constants import WS3_ROLE_PERMISSIONS, FieldType
from app.main import create_app
from app.tests.conftest import alembic_config, clean, dev_token, insert_tenant, sql
from app.tests.helpers import (
    IMPORTS,
    INSTRUMENTS,
    MAPPINGS,
    SOURCES,
    activate_version,
    cid,
    contract_body,
    events,
    project,
    sample,
    sample_rules,
    seeded_source_id,
    source_body,
    upload,
    validate_import,
    validated_instruments,
    validated_sample,
)

pytestmark = pytest.mark.db

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
IMMUTABLE = asyncpg.InsufficientPrivilegeError
MASKED = {"masked": True, "classification": "confidential"}
TABLES = {  # schema code -> its version or event table
    "instrument": "instrument_version",
    "position": "position_version",
    "cashflow_event": "cashflow_event",
}
DOMAIN_TABLES = (
    "domain_projection",
    "instrument_type",
    "instrument",
    "position",
    "instrument_version",
    "position_version",
    "cashflow_event",
    "instrument_attribute_definition",
    "instrument_attribute_value",
)
# The plan's matrix (docs/DELIVERY_PLAN.md Feature 03, D-34), written out from the plan.
PLAN_MATRIX = {
    "platform_admin": {"domain:read", "rule:read", "calc:read"},
    "data_steward": {"domain:read", "domain:project", "rule:read", "calc:read"},
    "risk_analyst": {"domain:read", "rule:read", "rule:propose", "calc:read", "calc:run"},
    "approver": {"domain:read", "rule:read", "rule:approve", "calc:read"},
    "auditor": {"domain:read", "rule:read", "calc:read"},
}


def migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def no_float(text: str) -> Any:
    """Parse JSON, failing on any JSON number with a fraction or exponent."""

    def refuse(value: str) -> Any:
        raise AssertionError(f"a JSON float: {value}")

    return json.loads(text, parse_float=refuse)


def count(db: str, table: str, import_id: Any = None) -> int:
    if import_id is None:
        return int(sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"])
    return int(
        sql(db, f"SELECT count(*) AS n FROM {table} WHERE import_job_id = $1", import_id)[0]["n"]
    )


def domain_counts(db: str) -> dict[str, int]:
    return {t: count(db, t) for t in DOMAIN_TABLES}


def denied(db: str, correlation: str, reason: str) -> None:
    [event] = events(db, correlation)
    assert (event["action"], event["outcome"]) == ("import.project", "DENIED"), dict(event)
    assert json.loads(event["details"]) == {"reason": reason}


def same_source_cash_flows(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    approver: dict[str, str],
    source_id: str,
) -> str:
    """The cash-flow sample as CSV on `source_id`: its contract superseded to cashflow_event,
    a mapping made ACTIVE, the import validated. Returns the import id."""
    records = json.loads(sample("cashflow_event.json"), parse_float=str)["records"]
    columns = list(records[0])
    text = "\n".join([",".join(columns), *(",".join(str(r[c]) for c in columns) for r in records)])
    contract = contract_body(
        db,
        schema="cashflow_event",
        expected_columns=[{"name": c, "required": True} for c in columns],
        business_key_columns=["cashflow_id"],
        control_total_columns=[],
        change_summary="Cash flows from the same source.",
    )
    r = api.post(f"{SOURCES}/{source_id}/contracts", json=contract, headers=steward)
    assert r.status_code == 201, r.text
    contract_id = r.json()["id"]
    r = upload(api, steward, source_id, (text + "\n").encode(), filename="cashflows.csv")
    assert r.status_code == 201, r.text
    import_id = str(r.json()["id"])
    mapping = {
        "code": "core_cashflow",
        "name": "Core banking cash flows (test)",
        "source_contract_id": contract_id,
        "rules": sample_rules(api, db, steward, "cashflow_event", columns),
    }
    r = api.post(MAPPINGS, json=mapping, headers=steward)
    assert r.status_code == 201, r.text
    activate_version(api, steward, approver, r.json()["versions"][0], import_id)
    r = validate_import(api, steward, import_id)
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
    return import_id


def other_schema_import(api: TestClient, db: str, steward: dict[str, str]) -> str:
    """An import of a schema the projector does not support (`collateral_asset`, ACTIVE v1)."""
    sid = sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " SELECT gen_random_uuid(), tenant_id, 'collateral_asset', 'Collateral asset', 'x', true,"
        " 'test' FROM schema_definition WHERE code = 'instrument' RETURNING id",
    )[0]["id"]
    vid = sql(
        db,
        "INSERT INTO schema_version (id, schema_id, version, status, content_sha256, created_by)"
        " VALUES (gen_random_uuid(), $1, 1, 'DRAFT', repeat('a', 64), 'test') RETURNING id",
        sid,
    )[0]["id"]
    sql(
        db,
        "INSERT INTO schema_field (id, schema_version_id, position, name, data_type, required,"
        " classification, extension_eligible, created_by) VALUES (gen_random_uuid(), $1, 1,"
        " 'asset_id', 'STRING', true, 'internal', false, 'test')",
        vid,
    )
    sql(db, "UPDATE schema_version SET status = 'ACTIVE' WHERE id = $1", vid)
    body = source_body(
        db,
        code="collateral_feed",
        contract=contract_body(
            db,
            schema="collateral_asset",
            expected_columns=[{"name": "asset_id", "required": True}],
            business_key_columns=["asset_id"],
            control_total_columns=[],
        ),
    )
    r = api.post(SOURCES, json=body, headers=steward)
    assert r.status_code == 201, r.text
    r = upload(api, steward, r.json()["id"], b"asset_id\nA-1\n")
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


@pytest.mark.ws3
def test_domain_projection_catalogue_and_guards(
    api: TestClient, db: str, artifact_dir: Path
) -> None:
    # --- 1. Seed: the WS3 matrix, the frozen copies, the instrument types (BE-14.2) ------------
    grants = sql(
        db,
        "SELECT r.code AS role, p.code AS permission, rp.seeded_by FROM role_permission rp"
        " JOIN role r ON r.id = rp.role_id JOIN permission p ON p.id = rp.permission_id"
        " WHERE p.seeded_by = $1",
        DOMAIN_SEED_TAG,
    )
    seeded: dict[str, set[str]] = {}
    for g in grants:
        assert g["seeded_by"] == DOMAIN_SEED_TAG
        seeded.setdefault(g["role"], set()).add(g["permission"])
    in_code = {role.value: {p.value for p in perms} for role, perms in WS3_ROLE_PERMISSIONS.items()}
    assert seeded == in_code == PLAN_MATRIX
    m0017 = migration("0017_domain_projection")
    assert {r: set(p) for r, p in m0017.MATRIX.items()} == in_code
    assert set(m0017.PERMISSIONS) == set().union(*in_code.values())
    assert m0017.TAG == DOMAIN_SEED_TAG
    assert m0017.INSTRUMENT_TYPES == SEEDED_INSTRUMENT_TYPES
    assert m0017.SCHEMA_CODES == tuple(c.value for c in DomainSchemaCode)
    assert m0017.CASHFLOW_TYPES == tuple(c.value for c in CashflowType)
    assert m0017.ORIGINS == tuple(c.value for c in CashflowOrigin)
    assert m0017.FIELD_TYPES == tuple(c.value for c in FieldType)
    assert set(m0017.PROJECTABLE) == {s.value for s in PROJECTABLE_STATUSES}
    types = sql(db, "SELECT code, name, provisional, seeded_by FROM instrument_type ORDER BY code")
    assert {t["code"]: t["name"] for t in types} == SEEDED_INSTRUMENT_TYPES
    assert all(t["provisional"] and t["seeded_by"] == DOMAIN_SEED_TAG for t in types)
    # No float in the projector's pure parts (as BE-11.2).
    for part in domain_service.PROJECTOR_PARTS:
        assert "float" not in inspect.getsource(getattr(domain_service, part)), part
    # A defect on the typed path never carries the value (in the message or a cause).
    for kind in (ColumnKind.DATE, ColumnKind.DECIMAL):
        with pytest.raises(ValueError) as defect:
            domain_service.typed(kind, "SENTINEL-2030-99-99")
        assert "SENTINEL" not in str(defect.value), kind
        error = defect.value
        shown = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
        assert "SENTINEL" not in str(shown or ""), kind  # what a traceback would print

    # --- 2. Projection (BE-14.3) -------------------------------------------------------------
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    reader = dev_token(api, "risk_analyst")
    # Not validated: an upload before any mapping (MAPPING_REQUIRED).
    early = upload(api, steward, seeded_source_id(db), sample("instrument.csv"))
    assert early.status_code == 201 and early.json()["status"] == "MAPPING_REQUIRED", early.text
    c = cid()
    r = project(api, steward, early.json()["id"], **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "not_validated"), r.text
    denied(db, c, "not_validated")
    # Another schema: projection_unsupported (checked before validation).
    c = cid()
    r = project(api, steward, other_schema_import(api, db, steward), **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "projection_unsupported"), r.text
    assert r.json()["details"]["schema_code"] == "collateral_asset"
    denied(db, c, "projection_unsupported")
    # An absent (or foreign) import: 404, audited too.
    c = cid()
    r = project(api, steward, str(uuid.uuid4()), **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    denied(db, c, "not_found")
    assert domain_counts(db) == {**dict.fromkeys(DOMAIN_TABLES, 0), "instrument_type": 3}

    instruments, _ = validated_instruments(api, db, steward, approver)
    imports = {
        "instrument": instruments,
        "position": validated_sample(api, db, steward, approver, "position"),
        "cashflow_event": validated_sample(api, db, steward, approver, "cashflow_event"),
    }
    # The switch off: 409, audited, nothing written; reads still work.
    off = Settings(
        ENV="test",
        DATABASE_URL=db,
        ARTIFACT_STORE_DIR=artifact_dir,
        DOMAIN_PROJECTION_ENABLED=False,
    )
    with TestClient(create_app(off)) as switched:
        for imported in imports.values():
            c = cid()
            r = project(switched, steward, imported["id"], **{"X-Correlation-ID": c})
            assert (r.status_code, r.json()["code"]) == (409, "domain_projection_disabled")
            denied(db, c, "domain_projection_disabled")
        assert switched.get(INSTRUMENTS, headers=reader).json()["total"] == 0
    assert domain_counts(db) == {**dict.fromkeys(DOMAIN_TABLES, 0), "instrument_type": 3}

    projections = {}
    for code, imported in imports.items():
        import_id = uuid.UUID(imported["id"])
        accepted = imported["manifest"]["quality"]["accepted_records"]
        assert accepted == 25, imported["manifest"]
        read = f"{IMPORTS}/{imported['id']}/projection"
        got = api.get(read, headers=reader)
        assert (got.status_code, got.json()["code"]) == (404, "projection_not_found")
        c = cid()
        r = project(api, steward, imported["id"], **{"X-Correlation-ID": c})
        assert r.status_code == 201, r.text
        body = no_float(r.text)
        # The read-only GET (domain:read) returns the same body to a role that cannot project.
        assert api.get(read, headers=reader).json() == body
        assert (body["schema_code"], body["schema_version"], body["projector_version"]) == (
            code,
            1,
            1,
        )
        # Conservation: one row per accepted record.
        assert body["projected_count"] == body["accepted_count"] == accepted
        assert body["summary"]["records"] == body["summary"]["rows_projected"] == accepted
        assert count(db, TABLES[code], import_id) == accepted
        assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
            ("import.project", "SUCCESS")
        ]
        before = domain_counts(db)
        # A second call returns the same projection and writes nothing (not even an audit row).
        c = cid()
        again = project(api, steward, imported["id"], **{"X-Correlation-ID": c})
        assert again.status_code == 200 and again.headers["Idempotent-Replayed"] == "true"
        assert again.json() == body
        assert domain_counts(db) == before and events(db, c) == []
        # Every row's canonical record resolves to a record_lineage row of this import, and
        # the row repeats that record's source record and ordinal.
        unresolved = sql(
            db,
            f"SELECT count(*) AS n FROM {TABLES[code]} t WHERE NOT EXISTS ("
            " SELECT 1 FROM record_lineage l JOIN canonical_record c"
            " ON c.id = l.canonical_record_id JOIN source_record s ON s.id = l.source_record_id"
            " JOIN import_batch b ON b.id = s.batch_id"
            " WHERE c.id = t.canonical_record_id AND c.import_job_id = t.import_job_id"
            " AND b.import_job_id = t.import_job_id AND s.id = t.source_record_id"
            " AND s.ordinal = t.source_ordinal AND c.payload_sha256 = t.payload_sha256)",
        )
        assert unresolved[0]["n"] == 0, code
        projections[code] = body
    summary = projections["instrument"]["summary"]
    # instrument v1 maturity_date is free text: never parsed, counted, kept in the record.
    assert summary["type_not_projected"] == {"maturity_date": 25}
    assert (
        sql(db, "SELECT count(*) AS n FROM instrument_version WHERE maturity_date IS NOT NULL")[0][
            "n"
        ]
        == 0
    )
    assert summary["new_identities"] == 25 and summary["attribute_values"] == 25
    assert summary["unknown_values"] == {}
    assert set(summary["canonical_only_fields"]) == {"counterparty_id", "legacy_book_code"}
    assert projections["cashflow_event"]["summary"]["new_identities"] == 0
    # No stored value is a float: NUMERIC reads back as Decimal, JSON holds no fraction.
    for table in DOMAIN_TABLES:
        for row in sql(db, f"SELECT * FROM {table}"):
            for name, value in dict(row).items():
                assert not isinstance(value, float), (table, name)
                if name == "summary":
                    no_float(value)

    # A later import of the same keys adds version 2 of each instrument, updating nothing.
    v1_rows = sql(db, "SELECT * FROM instrument_version ORDER BY id")
    again = upload(api, steward, str(instruments["source_id"]), sample("instrument.csv"))
    assert again.status_code == 201, again.text
    second = validate_import(api, steward, again.json()["id"]).json()
    assert project(api, steward, second["id"]).status_code == 201
    assert count(db, "instrument") == 25
    versions = sql(db, "SELECT version, count(*) AS n FROM instrument_version GROUP BY version")
    assert {v["version"]: v["n"] for v in versions} == {1: 25, 2: 25}
    assert [
        r for r in sql(db, "SELECT * FROM instrument_version ORDER BY id") if r["version"] == 1
    ] == v1_rows
    # The same cash flows from the instruments' own source (its contract superseded to
    # cashflow_event, the sample as CSV): only these link to its instruments.
    source_id = str(instruments["source_id"])
    own_flows = same_source_cash_flows(api, db, steward, approver, source_id)
    assert project(api, steward, own_flows).status_code == 201

    # --- 3. The catalogue (BE-14.4) ----------------------------------------------------------
    page = no_float(api.get(INSTRUMENTS, params={"limit": 200}, headers=reader).text)
    assert page["total"] == 25 and len(page["items"]) == 25
    assert {t["code"] for t in page["instrument_types"]} == set(SEEDED_INSTRUMENT_TYPES)
    assert all(t["provisional"] for t in page["instrument_types"])
    assert all(i["version_count"] == 2 and i["latest_version"] == 2 for i in page["items"])
    for params, expected in (
        (
            {"currency": "EUR"},
            {i["instrument_id"] for i in page["items"] if i["currency"] == "EUR"},
        ),
        (
            {"instrument_type": "LOAN"},
            {i["instrument_id"] for i in page["items"] if i["instrument_type"] == "LOAN"},
        ),
        ({"import_id": instruments["id"]}, {i["instrument_id"] for i in page["items"]}),
        ({"source_id": str(instruments["source_id"])}, {i["instrument_id"] for i in page["items"]}),
        ({"import_id": imports["position"]["id"]}, set()),
    ):
        got = api.get(INSTRUMENTS, params={**params, "limit": 200}, headers=reader).json()
        assert {i["instrument_id"] for i in got["items"]} == expected and got["total"] == len(
            expected
        ), params
    assert 0 < len({i["instrument_id"] for i in page["items"] if i["currency"] == "EUR"}) < 25

    # One instrument with cash flows: the version -> source row chain, masking by role. Cash
    # flows link by source and business key: the other source's events on the key are not
    # listed.
    flows = sql(
        db,
        "SELECT instrument_key, count(*) FILTER (WHERE source_system_id = $1) AS n,"
        " count(*) AS everywhere FROM cashflow_event GROUP BY 1",
        uuid.UUID(source_id),
    )
    key = sorted(flows, key=lambda f: (-f["n"], f["instrument_key"]))[0]["instrument_key"]
    [mine] = [f for f in flows if f["instrument_key"] == key]
    assert 0 < mine["n"] < mine["everywhere"], dict(mine)
    [item] = [i for i in page["items"] if i["business_key"] == key]
    path = f"{INSTRUMENTS}/{item['instrument_id']}"
    clear = no_float(api.get(path, headers=steward).text)
    masked = no_float(api.get(path, headers=reader).text)
    assert len(clear["versions"]) == 2
    for shown, hidden in zip(clear["versions"], masked["versions"], strict=True):
        assert isinstance(shown["notional"], str)  # clear: exact decimal text, not masked
        assert hidden["notional"] == MASKED
        assert hidden["currency"] == shown["currency"]  # public
        assert shown["maturity_date"] is None  # TYPE_NOT_PROJECTED
        assert [a["code"] for a in shown["attributes"]] == ["product_family"]
        assert shown["cash_flows"] and all(
            isinstance(f["amount"], str) for f in shown["cash_flows"]
        )
        assert all(f["amount"] == MASKED for f in hidden["cash_flows"])
        assert len(shown["cash_flows"]) == mine["n"]
        assert {f["import_id"] for f in shown["cash_flows"]} == {own_flows}
        # The payload hash is shown only to a caller cleared for the payload's most sensitive
        # field (confidential here): it could be brute-forced to the masked values.
        stored = sql(
            db,
            "SELECT payload_sha256 FROM instrument_version WHERE id = $1",
            uuid.UUID(shown["version_id"]),
        )[0]["payload_sha256"]
        assert shown["payload_sha256"] == stored
        assert hidden["payload_sha256"] == "masked"
    assert [v["import_id"] for v in clear["versions"]] == [instruments["id"], second["id"]]
    for v in clear["versions"]:
        [chain] = sql(
            db,
            "SELECT s.id AS source_record_id, s.ordinal, s.raw_values::text AS raw,"
            " b.import_job_id, c.payload ->> 'notional' AS notional FROM canonical_record c"
            " JOIN record_lineage l ON l.canonical_record_id = c.id"
            " JOIN source_record s ON s.id = l.source_record_id"
            " JOIN import_batch b ON b.id = s.batch_id WHERE c.id = $1",
            uuid.UUID(v["canonical_record_id"]),
        )
        assert str(chain["import_job_id"]) == v["import_id"]
        assert (str(chain["source_record_id"]), chain["ordinal"]) == (
            v["source_record_id"],
            v["source_ordinal"],
        )
        assert json.loads(chain["raw"])["instrument_id"] == key
        assert chain["notional"] == v["notional"]  # exact text, not rounded

    # --- 4. Guards (BE-14.2) -----------------------------------------------------------------
    for table in DOMAIN_TABLES:
        [row] = sql(db, f"SELECT * FROM {table} ORDER BY id LIMIT 1")
        for statement in (
            f"UPDATE {table} SET created_by = 'tampered' WHERE id = $1",
            f"UPDATE {table} SET tenant_id = gen_random_uuid() WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(IMMUTABLE, match="append-only"):
                sql(db, statement, row["id"])
        with pytest.raises(IMMUTABLE, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
        assert sql(db, f"SELECT * FROM {table} WHERE id = $1", row["id"]) == [row], table
    # Same tenant: a copy of an event under another tenant is refused.
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    with pytest.raises(IMMUTABLE, match="tenant"):
        sql(
            db,
            "INSERT INTO cashflow_event SELECT * FROM (SELECT"
            " (jsonb_populate_record(NULL::cashflow_event, to_jsonb(e)"
            " || jsonb_build_object('id', gen_random_uuid(), 'tenant_id', $1::uuid,"
            " 'canonical_record_id', gen_random_uuid()))).* FROM cashflow_event e LIMIT 1) x",
            other,
        )

    def copy(table: str, changes: str, where: str = "true") -> str:
        """INSERT a copy of one row of `table` with a new id and these jsonb changes."""
        return (
            f"INSERT INTO {table} SELECT * FROM (SELECT (jsonb_populate_record(NULL::{table},"
            f" to_jsonb(t) || jsonb_build_object('id', gen_random_uuid(), {changes}))).*"
            f" FROM {table} t WHERE {where} LIMIT 1) x"
        )

    # An instrument type of another tenant is refused on a version.
    foreign_type = sql(
        db,
        "INSERT INTO instrument_type (id, tenant_id, code, name, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'LOAN', 'Loan', true, 'test') RETURNING id",
        other,
    )[0]["id"]
    with pytest.raises(IMMUTABLE, match="instrument type of its tenant"):
        sql(db, copy("instrument_version", "'instrument_type_id', $1::uuid"), foreign_type)
    # A projection whose schema code is not its import's pinned schema is refused.
    with pytest.raises(IMMUTABLE, match="not the import's pinned schema"):
        sql(
            db,
            copy("domain_projection", "'schema_code', 'position'", "schema_code = 'instrument'"),
        )
    # A row under a projection of another schema code is refused.
    with pytest.raises(IMMUTABLE, match="must belong to a cashflow_event projection"):
        sql(
            db,
            copy("cashflow_event", "'domain_projection_id', $1::uuid"),
            uuid.UUID(projections["instrument"]["projection_id"]),
        )

    # The downgrade refuses while a projection exists, in one transaction: nothing changes.
    head = str(sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"])
    # Head is 0017 or a later WS3 revision (0018, Phase 15), whose downgrade passes without
    # calculation rows; 0017's own refusal is what stops this one.
    assert head == str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())
    before = domain_counts(db)
    try:
        with pytest.raises(Exception, match="this downgrade is refused"):
            command.downgrade(alembic_config(db), "0016")
        assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == head
        assert domain_counts(db) == before
        # Without domain rows: the tables and the tagged seed go, and come back.
        clean(db)
        command.downgrade(alembic_config(db), "0016")
        assert sql(db, "SELECT to_regclass('instrument_version') AS t")[0]["t"] is None
        assert (
            sql(db, "SELECT count(*) AS n FROM permission WHERE code LIKE 'domain:%'")[0]["n"] == 0
        )
    finally:
        command.upgrade(alembic_config(db), "head")
    assert count(db, "instrument_type") == 3
    grants = sql(
        db, "SELECT count(*) AS n FROM role_permission WHERE seeded_by = $1", DOMAIN_SEED_TAG
    )
    assert grants[0]["n"] == sum(len(p) for p in PLAN_MATRIX.values())
