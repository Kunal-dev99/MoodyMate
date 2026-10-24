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
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.canonical import canonical_sha256
from app.core.config import Settings
from app.domain import service as domain_service
from app.domain.constants import (
    DOMAIN_SEED_TAG,
    INSTRUMENT_SPEC_V2,
    INSTRUMENT_TYPED_MATURITY,
    INSTRUMENT_TYPED_MATURITY_SHA256,
    INSTRUMENT_TYPED_MATURITY_TITLE,
    INSTRUMENT_V1_SHA256,
    PROJECTABLE_STATUSES,
    PROJECTION_SPECS,
    PROJECTOR_V2,
    PROJECTOR_VERSION,
    SEEDED_INSTRUMENT_TYPES,
    CashflowOrigin,
    CashflowType,
    ColumnKind,
    DomainSchemaCode,
)
from app.governance.constants import WS3_ROLE_PERMISSIONS, FieldType
from app.governance.service import FieldSpec, contract_sha256
from app.main import create_app
from app.tests import terms_kit
from app.tests.conftest import (
    _CALC_CLEANUP,
    _TERMS_CLEANUP,
    alembic_config,
    clean,
    clear_identity_links,
    dev_token,
    insert_tenant,
    script,
    sql,
)
from app.tests.helpers import (
    IMPORTS,
    INSTRUMENTS,
    MAPPINGS,
    RUNS,
    SNAPSHOTS,
    SOURCES,
    activate_version,
    assert_denied,
    cid,
    contract_body,
    events,
    make_snapshot,
    module_version_id,
    project,
    sample,
    sample_rules,
    schema_id,
    seeded_source_id,
    source_body,
    start_run,
    upload,
    validate_import,
    validated_instruments,
    validated_sample,
    without_rate_limits,
)
from app.tests.identity_kit import (
    EXTRA_FLOWS,
    GOLDEN_V1_RUN,
    IDENTITY_TABLES,
    KEEP,
    LINKS,
    RECONCILE,
    TYPED_EDGE_CSV,
    approve_proposal,
    counts_by_pair,
    golden_check,
    identity_counts,
    links_of,
    reconcile,
    run_document_by_key,
    run_outputs,
    schema_versions,
    typed_fields,
    typed_import,
    typed_source,
    without_written,
)
from app.tests.terms_kit import (
    CONSISTENT,
    CURRENCIES,
    EXPECTED_TERMS,
    FX_STATUS,
    INCONSISTENT,
    LATER_CSV,
    RATE_TERMS_CSV,
    RATE_TERMS_RULES,
    SAMPLE_CURRENCIES,
    TERM_VOCABULARIES,
    calculations_have_no_fx_arithmetic,
    check_rate_terms_contract,
    check_stored_terms,
    check_terms_append_only,
    publish_rate_terms,
    term_counts,
    term_rows,
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
    without_rate_limits(api)
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
    # BE-19.1: the domain's schema codes are the union of the 0017 and 0022 (D-38) copies; the
    # 0017 copy itself stays frozen.
    m0022 = migration("0022_credit_domain")
    assert (
        m0017.SCHEMA_CODES == m0022.OLD_SCHEMA_CODES == ("instrument", "position", "cashflow_event")
    )
    assert m0017.SCHEMA_CODES + m0022.SCHEMA_CODES == tuple(c.value for c in DomainSchemaCode)
    assert m0017.CASHFLOW_TYPES == tuple(c.value for c in CashflowType)
    assert m0017.ORIGINS == tuple(c.value for c in CashflowOrigin)
    assert m0017.FIELD_TYPES == tuple(c.value for c in FieldType)
    assert set(m0017.PROJECTABLE) == {s.value for s in PROJECTABLE_STATUSES}
    # BE-32.1: 0031's frozen copies equal the code and the plan (D-61 §3, §6).
    m0031 = migration("0031_rate_terms_currency_fx")
    assert m0031.VOCABULARY_KINDS == tuple(k.value for k in terms_kit.c("TermVocabularyKind"))
    assert (m0031.VOCABULARY_TAG, m0031.CURRENCY_TAG) == (
        terms_kit.c("TERM_VOCABULARY_SEED_TAG"),
        terms_kit.c("CURRENCY_SEED_TAG"),
    )
    frozen_vocabulary: dict[str, set[str]] = {}
    for kind, code, _ in m0031.VOCABULARY:
        frozen_vocabulary.setdefault(kind, set()).add(code)
    assert frozen_vocabulary == TERM_VOCABULARIES
    assert {code for code, _, _ in m0031.CURRENCIES} == SAMPLE_CURRENCIES
    types = sql(db, "SELECT code, name, provisional, seeded_by FROM instrument_type ORDER BY code")
    assert {t["code"]: t["name"] for t in types} == SEEDED_INSTRUMENT_TYPES
    assert all(t["provisional"] and t["seeded_by"] == DOMAIN_SEED_TAG for t in types)
    # No float in the projector's pure parts (as BE-11.2).
    for part in domain_service.PROJECTOR_PARTS:
        assert "float" not in inspect.getsource(getattr(domain_service, part)), part
    # BE-32.4 (D-61 §6): no conversion arithmetic in app/calculations/ (the grep).
    assert calculations_have_no_fx_arithmetic() == []
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

    # BE-31.4 (D-58 §4, ruling Q11): with IDENTITY_LINKS_ENABLED off the projections write no
    # legal entity and no link, and their summaries are unchanged (the golden below).
    state = cast(FastAPI, api.app).state
    links_on = state.settings
    state.settings = links_on.model_copy(update={"IDENTITY_LINKS_ENABLED": False})
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
        # BE-31.3: v1 is byte-identical to the golden taken before Phase 31 (projector 1, the
        # summary with TYPE_NOT_PROJECTED, every payload_sha256 by ordinal).
        golden_check(db, code, body)
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
    assert identity_counts(db) == dict.fromkeys(IDENTITY_TABLES, 0)
    assert projections["position"]["summary"]["new_identities"] == 25
    assert "missing_values" not in projections["instrument"]["summary"]  # v1 (ruling Q9)
    # Switched off, the reconcile is a 409, audited DENIED, and writes nothing.
    c = cid()
    r = api.post(RECONCILE, headers={**steward, "X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "identity_links_disabled"), r.text
    assert_denied(db, c, "identity_links_disabled", action="identity_link.reconcile")
    assert identity_counts(db) == dict.fromkeys(IDENTITY_TABLES, 0)
    state.settings = links_on
    # A fixture ladder run (v1 instruments + the cash flows) before any link exists.
    snapshot = make_snapshot(api, reader, [instruments["id"], imports["cashflow_event"]["id"]])
    assert snapshot.status_code == 201, snapshot.text
    ladder = {
        "dataset_snapshot_id": snapshot.json()["snapshot_id"],
        "module_version_id": module_version_id(api, reader),
        "reporting_date": "2026-12-31",
    }
    v1_run = start_run(api, reader, ladder).json()
    assert v1_run["result_sha256"], v1_run
    # BE-31.3 (a): the run equals the golden captured on the pre-phase code (56dec79) once each
    # row id is its stable key (result_sha256 hashes row ids, which differ per database).
    v1_document = run_document_by_key(db, v1_run["run_id"])
    assert v1_run["status"] == GOLDEN_V1_RUN["status"], v1_run
    assert v1_document == GOLDEN_V1_RUN["document"]
    assert canonical_sha256(v1_document) == GOLDEN_V1_RUN["canonical_sha256"]
    # The reconcile links the historical rows: each position to its instrument and its legal
    # entity (registered now, one per source and code), each cash flow to its instrument.
    first = reconcile(api, steward)
    expected_links = {
        "POSITION_VERSION": sql(
            db,
            "SELECT v.id AS row, 'INSTRUMENT' AS kind, i.id AS target FROM position_version v"
            " JOIN instrument i ON i.business_key = v.instrument_key UNION ALL"
            " SELECT v.id, 'LEGAL_ENTITY', e.id FROM position_version v"
            " JOIN position p ON p.id = v.position_id JOIN legal_entity e"
            " ON e.source_system_id = p.source_system_id AND e.code = v.legal_entity_ref",
        ),
        "CASHFLOW_EVENT": sql(
            db,
            "SELECT e.id AS row, 'INSTRUMENT' AS kind, i.id AS target FROM cashflow_event e"
            " JOIN instrument i ON i.business_key = e.instrument_key",
        ),
    }
    refs = sql(db, "SELECT DISTINCT legal_entity_ref FROM position_version")
    assert identity_counts(db)["legal_entity"] == len(refs) > 0
    for link_kind, link_rows in expected_links.items():
        assert len(link_rows) == {"POSITION_VERSION": 50, "CASHFLOW_EVENT": 25}[link_kind]
        assert links_of(db, link_kind) == {
            (r["row"], r["kind"]): r["target"] for r in link_rows
        }, link_kind
    pairs = counts_by_pair(first)
    for (source_kind, target_kind), n in {
        ("POSITION_VERSION", "INSTRUMENT"): 25,
        ("POSITION_VERSION", "LEGAL_ENTITY"): 25,
        ("CASHFLOW_EVENT", "INSTRUMENT"): 25,
        ("CREDIT_EXPOSURE", "COUNTERPARTY"): 0,
    }.items():
        link_pair = pairs[(source_kind, target_kind)]
        assert (link_pair["rows"], link_pair["linked"], link_pair["links_written"]) == (n, n, n)
    assert first["links_written"] == 75 == identity_counts(db)["domain_identity_link"]
    [row] = sql(db, "SELECT id FROM position_version ORDER BY id LIMIT 1")
    link_page = api.get(LINKS, params={"source_id": str(row["id"])}, headers=reader).json()
    assert {i["target_kind"] for i in link_page["items"]} == {"INSTRUMENT", "LEGAL_ENTITY"}
    # Idempotent: a second reconcile writes nothing and returns the same counts.
    kept = identity_counts(db)
    second = reconcile(api, steward)
    assert second["links_written"] == 0 and without_written(second) == without_written(first)
    assert identity_counts(db) == kept
    # Runs never read links: the same run after the reconcile has the same result_sha256.
    rerun = start_run(api, reader, ladder).json()
    assert (rerun["status"], rerun["result_sha256"]) == (v1_run["status"], v1_run["result_sha256"])
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
    # BE-31.4 (D-58 §4): with the switch on, the projection links its own rows.
    own_links = sql(
        db,
        "SELECT count(*) AS n FROM domain_identity_link l JOIN cashflow_event e"
        " ON e.id = l.source_row_id WHERE e.import_job_id = $1",
        uuid.UUID(own_flows),
    )
    assert own_links[0]["n"] == 25
    assert identity_counts(db)["domain_identity_link"] == kept["domain_identity_link"] + 25

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

    # --- 5. Phase 31: typed maturity (BE-31.2, BE-31.3) and ambiguous keys (BE-31.4) --------
    # The frozen contract: its hash is the code's (ruling Q10), and it is instrument v1 with
    # exactly `maturity_date` DATE and `interest_rate` (11,6).
    frozen = [
        FieldSpec(**dict[str, Any]({k: f[k] for k in KEEP})) for f in INSTRUMENT_TYPED_MATURITY
    ]
    assert contract_sha256(frozen) == INSTRUMENT_TYPED_MATURITY_SHA256
    by_name = sorted(INSTRUMENT_TYPED_MATURITY, key=lambda f: str(f["name"]))
    assert sorted(
        typed_fields(api, steward, schema_id(db, "instrument")), key=lambda f: f["name"]
    ) == [{k: f[k] for k in KEEP} for f in by_name]
    versions_before = schema_versions(db)
    assert {n: v["status"] for n, v in versions_before.items()} == {1: "ACTIVE", 2: "DRAFT"}
    assert versions_before[1]["content_sha256"] == INSTRUMENT_V1_SHA256
    # Published only through the governed path: BREAKING with the mapping a dependent, a
    # second person approves; the new version is ACTIVE at max + 1, v1 DEPRECATED, and the
    # seeded v2 DRAFT is untouched.
    approve_proposal(
        api, steward, approver, db, list(INSTRUMENT_TYPED_MATURITY), INSTRUMENT_TYPED_MATURITY_TITLE
    )
    versions_after = schema_versions(db)
    typed_version = max(versions_before) + 1
    assert {n: v["status"] for n, v in versions_after.items()} == {
        1: "DEPRECATED",
        2: "DRAFT",
        typed_version: "ACTIVE",
    }
    assert versions_after[typed_version]["content_sha256"] == INSTRUMENT_TYPED_MATURITY_SHA256
    assert versions_after[2]["content_sha256"] == versions_before[2]["content_sha256"]
    # Matched by contract hash, not by number (D-57 §2): the same number with another hash is
    # unsupported, and the v1 contract under a new number is projector 1 again.
    vid = versions_after[typed_version]["id"]
    for content, matched in (
        (INSTRUMENT_TYPED_MATURITY_SHA256, (PROJECTOR_V2, INSTRUMENT_SPEC_V2)),
        ("f" * 64, None),
        (INSTRUMENT_V1_SHA256, (PROJECTOR_VERSION, PROJECTION_SPECS[DomainSchemaCode.INSTRUMENT])),
    ):
        pins = domain_service.SchemaPins({}, {vid: typed_version}, {}, {vid: content})
        found = domain_service.DomainService._spec(
            DomainSchemaCode.INSTRUMENT, vid, typed_version, pins
        )
        assert found == matched, content
    # A second instrument source: the sample under the typed contract (its mapping made
    # ACTIVE against it), then the edge cases: one good DATE, three bad dates held by
    # validation, a missing maturity, rates at exactly 6 dp.
    typed = typed_source(api, db, steward)
    typed_sample = typed_import(api, db, steward, approver, typed, sample("instrument.csv"))
    assert typed_sample["status"] == "COMPLETED", typed_sample
    edge = typed_import(api, db, steward, approver, typed, TYPED_EDGE_CSV)
    assert edge["schema_version_id"] == str(vid), edge
    assert edge["manifest"]["quality"]["accepted_records"] == 2, edge["manifest"]
    held = sql(
        db,
        "SELECT DISTINCT r.raw_values ->> 'maturity_date' AS v FROM validation_issue i"
        " JOIN source_record r ON r.id = i.source_record_id"
        " WHERE i.import_job_id = $1 AND i.field = 'maturity_date'",
        uuid.UUID(edge["id"]),
    )
    assert {h["v"] for h in held} == {"2030-02-30", "2030-13-01", "n/a"}
    unchanged = domain_counts(db)
    # Projector v2 switched off: projection_unsupported, audited DENIED, nothing written.
    state.settings = links_on.model_copy(update={"INSTRUMENT_PROJECTOR_V2_ENABLED": False})
    c = cid()
    r = project(api, steward, edge["id"], **{"X-Correlation-ID": c})
    state.settings = links_on
    assert (r.status_code, r.json()["code"]) == (409, "projection_unsupported"), r.text
    assert r.json()["details"]["projector_version"] == PROJECTOR_V2
    denied(db, c, "projection_unsupported")
    # The same version number with a different hash in the code: projection_unsupported.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(
            domain_service,
            "INSTRUMENT_SPECS_BY_HASH",
            {"f" * 64: (PROJECTOR_V2, INSTRUMENT_SPEC_V2)},
        )
        c = cid()
        r = project(api, steward, edge["id"], **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "projection_unsupported"), r.text
    assert r.json()["details"]["schema_version"] == typed_version
    denied(db, c, "projection_unsupported")
    assert domain_counts(db) == unchanged
    r = project(api, steward, edge["id"])
    assert r.status_code == 201, r.text
    body = no_float(r.text)
    assert (body["schema_version"], body["projector_version"]) == (typed_version, PROJECTOR_V2)
    assert body["summary"]["missing_values"] == {"maturity_date": 1}  # ruling Q9
    assert body["summary"]["type_not_projected"] == {}
    assert body["summary"]["records"] == body["summary"]["rows_projected"] == 2
    stored = {
        r["business_key"]: r
        for r in sql(
            db,
            "SELECT i.id, i.business_key, v.maturity_date, v.interest_rate"
            " FROM instrument_version v"
            " JOIN instrument i ON i.id = v.instrument_id WHERE v.import_job_id = $1",
            uuid.UUID(edge["id"]),
        )
    }
    assert set(stored) == {"TM-1", "TM-5"}  # TM-2, TM-3 and TM-4 never projected
    assert stored["TM-1"]["maturity_date"] == date(2030, 6, 30)
    assert stored["TM-5"]["maturity_date"] is None  # missing: NULL, and counted above
    rates = {k: v["interest_rate"] for k, v in stored.items()}
    assert rates == {"TM-1": Decimal("2.123456"), "TM-5": Decimal("0.000001")}
    assert {k: str(v) for k, v in rates.items()} == {"TM-1": "2.123456", "TM-5": "0.000001"}
    [typed_shown] = api.get(f"{INSTRUMENTS}/{stored['TM-1']['id']}", headers=steward).json()[
        "versions"
    ]
    assert (typed_shown["maturity_date"], typed_shown["interest_rate"]) == (
        "2030-06-30",
        "2.123456",
    )
    # A ladder run over a typed-instrument snapshot (the sample under the typed contract)
    # gives the same result as over the v1 one: the same status and outputs. Its
    # result_sha256 differs by the instrument version ids alone, which D-31 §4 hashes (an
    # open question on the Done-when's wording).
    r = project(api, steward, typed_sample["id"])
    assert r.status_code == 201 and r.json()["projector_version"] == PROJECTOR_V2, r.text
    assert r.json()["summary"]["missing_values"] == {}
    snapshot = make_snapshot(api, reader, [typed_sample["id"], imports["cashflow_event"]["id"]])
    assert snapshot.status_code == 201, snapshot.text
    typed_run = start_run(
        api, reader, {**ladder, "dataset_snapshot_id": snapshot.json()["snapshot_id"]}
    ).json()
    assert typed_run["status"] == v1_run["status"], typed_run
    # BE-31.3 (c): a repeat run over the same typed snapshot gives an equal result_sha256.
    typed_rerun = start_run(
        api, reader, {**ladder, "dataset_snapshot_id": snapshot.json()["snapshot_id"]}
    ).json()
    assert typed_rerun["run_id"] != typed_run["run_id"], typed_rerun
    assert (typed_rerun["status"], typed_rerun["result_sha256"]) == (
        typed_run["status"],
        typed_run["result_sha256"],
    )
    typed_outputs = run_outputs(db, typed_run["run_id"])
    assert typed_outputs == run_outputs(db, v1_run["run_id"])
    assert typed_outputs["contributions"] and typed_outputs["gap"]
    versions_used = sql(
        db,
        "SELECT DISTINCT v.import_job_id FROM intermediate_value c JOIN instrument_version v"
        " ON v.id = c.instrument_version_id WHERE c.calculation_run_id = $1",
        uuid.UUID(typed_run["run_id"]),
    )
    assert [str(v["import_job_id"]) for v in versions_used] == [typed_sample["id"]]
    # The sample's instrument keys are now on two source systems: existing links are kept
    # (linked_now_ambiguous); a new cash flow naming such a key is LINK_AMBIGUOUS, one naming
    # no instrument LINK_UNRESOLVED, and neither is linked.
    kept = identity_counts(db)
    r = upload(api, steward, str(imports["cashflow_event"]["source_id"]), EXTRA_FLOWS)
    assert r.status_code == 201, r.text
    extra = validate_import(api, steward, r.json()["id"]).json()
    assert extra["status"] == "COMPLETED", extra
    c = cid()
    assert project(api, steward, extra["id"], **{"X-Correlation-ID": c}).status_code == 201
    [event] = events(db, c)
    details = json.loads(event["details"])
    assert (details["links_written"], details["links_ambiguous"], details["links_unresolved"]) == (
        0,
        1,
        1,
    )
    third = reconcile(api, steward)
    assert third["links_written"] == 0 and identity_counts(db) == kept
    pairs = counts_by_pair(third)
    flow_pair = pairs[("CASHFLOW_EVENT", "INSTRUMENT")]
    assert (
        flow_pair["rows"],
        flow_pair["linked"],
        flow_pair["ambiguous"],
        flow_pair["unresolved"],
    ) == (
        52,
        50,
        1,
        1,
    )
    for link_pair in (flow_pair, pairs[("POSITION_VERSION", "INSTRUMENT")]):
        assert link_pair["linked_now_ambiguous"] == link_pair["linked"] > 0, link_pair
    assert pairs[("POSITION_VERSION", "LEGAL_ENTITY")]["linked_now_ambiguous"] == 0
    assert without_written(reconcile(api, steward)) == without_written(third)
    # BE-32.2: the BE-31.3 v2 summary is unchanged by Phase 32 (no term keys on projector 2).
    assert set(body["summary"]) == {
        "records", "rows_projected", "new_identities", "attribute_values",
        "type_not_projected", "unknown_values", "canonical_only_fields", "missing_values",
    }  # fmt: skip

    # --- 6. Phase 32: rate terms (BE-32.2), effective dating (BE-32.3), FX status (BE-32.4) -
    # The frozen contract (typed maturity + eleven optional terms), matched by hash.
    check_rate_terms_contract()
    terms_hash = terms_kit.c("INSTRUMENT_RATE_TERMS_SHA256")
    projector_v3 = terms_kit.c("PROJECTOR_V3")
    for content, matched in (
        (terms_hash, (projector_v3, terms_kit.c("INSTRUMENT_SPEC_V3"))),
        (INSTRUMENT_TYPED_MATURITY_SHA256, (PROJECTOR_V2, INSTRUMENT_SPEC_V2)),
    ):
        pins = domain_service.SchemaPins({}, {vid: typed_version}, {}, {vid: content})
        found = domain_service.DomainService._spec(
            DomainSchemaCode.INSTRUMENT, vid, typed_version, pins
        )
        assert found == matched, content
    publish_rate_terms(api, steward, approver, db)
    versions_terms = schema_versions(db)
    terms_version = max(versions_after) + 1
    assert {n: v["status"] for n, v in versions_terms.items()} == {
        1: "DEPRECATED",
        2: "DRAFT",
        typed_version: "DEPRECATED",
        terms_version: "ACTIVE",
    }
    assert versions_terms[terms_version]["content_sha256"] == terms_hash
    terms_source = typed_source(api, db, steward, code="rate_terms_instruments")
    rated = typed_import(
        api, db, steward, approver, terms_source, RATE_TERMS_CSV, rules=RATE_TERMS_RULES
    )
    assert rated["status"] == "COMPLETED", rated
    assert rated["manifest"]["quality"]["accepted_records"] == 6, rated["manifest"]
    # Switched off (reversibility): projection_unsupported, audited DENIED, nothing written.
    unchanged = {**domain_counts(db), **term_counts(db)}
    state.settings = links_on.model_copy(update={"INSTRUMENT_TERMS_PROJECTION_ENABLED": False})
    c = cid()
    r = project(api, steward, rated["id"], **{"X-Correlation-ID": c})
    state.settings = links_on
    assert (r.status_code, r.json()["code"]) == (409, "projection_unsupported"), r.text
    assert r.json()["details"]["projector_version"] == projector_v3
    denied(db, c, "projection_unsupported")
    assert {**domain_counts(db), **term_counts(db)} == unchanged
    r = project(api, steward, rated["id"])
    assert r.status_code == 201, r.text
    body = no_float(r.text)
    assert (body["schema_version"], body["projector_version"]) == (terms_version, projector_v3)
    # Every instrument_version is written; the three inconsistent ones have no term row and
    # are counted; the unknown day count is kept as text and counted.
    assert body["summary"]["records"] == body["summary"]["rows_projected"] == 6
    assert body["summary"]["terms_inconsistent"] == len(INCONSISTENT)
    assert body["summary"]["unknown_values"] == {"day_count": 1}
    assert count(db, "instrument_version", uuid.UUID(rated["id"])) == 6
    stored_terms = term_rows(db, rated["id"])
    assert set(stored_terms) == set(CONSISTENT)
    check_stored_terms(stored_terms)
    keys = {
        r["business_key"]: r["id"]
        for r in sql(db, "SELECT id, business_key FROM instrument WHERE business_key LIKE 'RT-%'")
    }
    shown_terms = {}
    for key, instrument_id in keys.items():
        detail = no_float(api.get(f"{INSTRUMENTS}/{instrument_id}", headers=steward).text)
        [v] = detail["versions"]
        assert v["terms_typed"] is True, (key, v)
        assert v["imported_at"] and (detail["effective_on"], detail["effective_version_id"]) == (
            None,
            None,
        )
        shown_terms[key] = v
        if key in INCONSISTENT:
            assert v["terms"] is None and v["effective_from"] is None, key
            continue
        assert v["effective_from"] == "2026-01-01", (key, v)
        terms = v["terms"]
        assert {f: terms[f] for f in EXPECTED_TERMS[key]} == EXPECTED_TERMS[key], key
        assert terms["unknown_fields"] == (["day_count"] if key == "RT-UNK" else []), key
        assert terms["provisional"] is True and terms["rate_unit"] == "percent", key
    assert shown_terms["RT-FIX"]["interest_rate"] == "7.250000"
    # Instruments without the contract show no terms (the typed-maturity TM-1, untyped).
    [tm1] = api.get(f"{INSTRUMENTS}/{stored['TM-1']['id']}", headers=steward).json()["versions"]
    assert (tm1["terms_typed"], tm1["terms"], tm1["effective_from"]) == (False, None, None)
    # BE-32.3: a snapshot pinned before a later import with an earlier effective_from.
    pinned = make_snapshot(api, reader, [rated["id"]])
    assert pinned.status_code == 201, pinned.text
    pinned_path = f"{SNAPSHOTS}/{pinned.json()['snapshot_id']}"
    pinned_before = api.get(pinned_path, headers=reader).json()
    later = typed_import(api, db, steward, approver, terms_source, LATER_CSV)
    r = project(api, steward, later["id"])
    assert r.status_code == 201 and r.json()["summary"]["terms_inconsistent"] == 1, r.text
    assert set(term_rows(db, later["id"])) == {"RT-FIX"}
    fixed = f"{INSTRUMENTS}/{keys['RT-FIX']}"
    first_v, later_v = api.get(fixed, headers=steward).json()["versions"]
    assert (first_v["version"], later_v["version"]) == (1, 2)
    assert (first_v["effective_from"], later_v["effective_from"]) == ("2026-01-01", "2025-06-01")
    assert later_v["imported_at"] > first_v["imported_at"]  # imported later, effective earlier
    for effective_on, picked in (
        ("2026-06-30", first_v["version_id"]),  # by business date: v1, not the later import
        ("2025-12-31", later_v["version_id"]),
        ("2025-01-01", None),
    ):
        got = api.get(fixed, params={"effective_on": effective_on}, headers=reader).json()
        assert (got["effective_on"], got["effective_version_id"]) == (effective_on, picked)
    # Fail closed: RT-FLT's later version is effective from 2026-03-01, but floor > cap writes
    # no term row, so its date is not stored. It is no candidate, and no pick is made rather
    # than v1 (effective 2026-01-01) silently.
    floating = f"{INSTRUMENTS}/{keys['RT-FLT']}"
    flt_v1, flt_v2 = api.get(floating, headers=steward).json()["versions"]
    assert (flt_v1["effective_from"], flt_v1["terms"] is not None) == ("2026-01-01", True)
    assert (flt_v2["terms_typed"], flt_v2["terms"], flt_v2["effective_from"]) == (True, None, None)
    for effective_on in ("2026-06-30", "2026-02-01", "2025-01-01"):
        got = api.get(floating, params={"effective_on": effective_on}, headers=reader).json()
        assert (got["effective_on"], got["effective_version_id"]) == (effective_on, None)
    assert api.get(pinned_path, headers=reader).json() == pinned_before  # D-30
    # BE-32.4: currencies (tagged PROVISIONAL seed) and FX consolidation BLOCKED.
    listed = api.get(CURRENCIES, headers=reader).json()["items"]
    assert {i["code"] for i in listed} == SAMPLE_CURRENCIES and all(
        i["provisional"] for i in listed
    )
    assert api.get(FX_STATUS, headers=reader).json() == {
        "status": "BLOCKED",
        "reason_code": "fx_policy_not_approved",
        "observation_count": 0,
    }
    for read in ("results", "maturity-ladder"):
        ctx = api.get(f"{RUNS}/{v1_run['run_id']}/{read}", headers=reader).json()["context"]
        assert ctx["consolidation"] == "BLOCKED", read
    # The term rows are append-only too (the seeded 0031 tables are checked in the WS1 test).
    check_terms_append_only(db, ("instrument_term_version",))
    # Part 4 checks 0017's own refusal: the runs, links and terms (each guarded by its own
    # migration's downgrade) go first.
    script(db, _CALC_CLEANUP)
    clear_identity_links(db)
    script(db, _TERMS_CLEANUP)

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
        with pytest.raises(Exception, match="domain projections or domain rows exist"):
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
