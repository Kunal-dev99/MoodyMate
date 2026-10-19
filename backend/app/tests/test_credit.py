"""Phase 19 (BE-19.1 - BE-19.5, Feature 04, D-37, D-38, D-43): counterparty and credit-exposure
data in the domain, in one essential test (D-26), in five parts:

1. Seed (BE-19.1): migration 0022 seeds the ACTIVE `counterparty` v1 and `credit_exposure` v1
   schemas (D-37 §1, classified per D-43 §1) with their `schema_dependency` rows, and the
   PROVISIONAL `credit_vocabulary` (D-37 §5, neutral grade codes); every seed row carries the
   `0022_credit_domain` tag.
2. Samples and projection (BE-19.2, BE-19.3): both synthetic samples cover the plan's cases and
   validate to COMPLETED with `accepted_count` = the row count; with
   CREDIT_PROJECTION_ENABLED off each is a 409 `projection_unsupported`, audited DENIED, with
   nothing written; on, each projects one row per accepted record, a second call writes
   nothing, every row resolves through `record_lineage` to its import, and no stored value is
   a float (INTEGER DPD, BOOLEAN default flag).
3. Snapshots (BE-19.4): a credit snapshot (counterparty + credit_exposure) gets the same id for
   the same members, its content hash is the Feature 03 formula, and a duplicated counterparty
   key is a 409 `ambiguous_business_key`.
4. Reads (BE-19.5): vocabularies, the counterparty and exposure catalogues with filters,
   `counterparty_name` and `drawn_amount` masked for risk_analyst and clear for data_steward,
   and one exposure version -> canonical record -> source row.
5. Guards (BE-19.1): UPDATE, DELETE and TRUNCATE are refused on the four tables and on
   `credit_vocabulary`; the same-tenant and canonical-repeat insert checks hold; an unknown
   sixth `schema_code` is refused by both widened CHECKs; the downgrade refuses while a credit
   projection exists; and 0021 -> head -> 0021 -> head round-trips the tables and the seed.
"""

import csv
import hashlib
import importlib.util
import io
import json
import uuid
from datetime import date
from decimal import Decimal
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
from app.domain.constants import CreditVocabularyKind
from app.main import create_app
from app.tests.conftest import alembic_config, clean, dev_token, insert_tenant, script, sql
from app.tests.credit_kit import (
    COUNTERPARTIES,
    CREDIT_SAMPLES,
    EXPOSURES,
    VOCABULARIES,
    validated_credit,
)
from app.tests.helpers import (
    SNAPSHOTS,
    cid,
    events,
    make_snapshot,
    project,
    sample,
)

pytestmark = [pytest.mark.db, pytest.mark.ws3]

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
TAG = "0022_credit_domain"
IMMUTABLE = asyncpg.InsufficientPrivilegeError
MASKED = {"masked": True, "classification": "confidential"}
CREDIT_CODES = ("counterparty", "credit_exposure")
VERSION_TABLES = {
    "counterparty": "counterparty_version",
    "credit_exposure": "credit_exposure_version",
}
CREDIT_TABLES = (
    "counterparty",
    "counterparty_version",
    "credit_exposure",
    "credit_exposure_version",
)
GUARDED_TABLES = (*CREDIT_TABLES, "credit_vocabulary")
DUP_COUNTRIES = ("DE", "US")
# D-37 §1 types and D-43 §1 classifications, written out from the decisions.
FIELD_TYPES = {
    "counterparty": {"counterparty_id": "STRING", "counterparty_name": "STRING"},
    "credit_exposure": {
        "exposure_id": "STRING",
        "counterparty_id": "STRING",
        "as_of_date": "DATE",
        "drawn_amount": "DECIMAL",
        "undrawn_amount": "DECIMAL",
        "days_past_due": "INTEGER",
        "default_flag": "BOOLEAN",
        "origination_date": "DATE",
        "maturity_date": "DATE",
        "effective_interest_rate": "DECIMAL",
        "collateral_value": "DECIMAL",
    },
}
CLASSES = {
    "counterparty": {
        "counterparty_id": "internal",
        "counterparty_name": "confidential",
        "segment": "internal",
        "country": "public",
    },
    "credit_exposure": {
        "exposure_id": "internal",
        "counterparty_id": "internal",
        "currency": "public",
        "facility_type": "internal",
        "drawn_amount": "confidential",
        "undrawn_amount": "confidential",
        "days_past_due": "internal",
        "default_flag": "internal",
        "origination_grade": "internal",
        "effective_interest_rate": "internal",
        "collateral_type": "internal",
        "collateral_value": "confidential",
    },
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


def rows_of(name: str) -> list[dict[str, str]]:
    """A synthetic CSV sample's data rows (the D-24 marker line skipped)."""
    text = sample(name).decode()
    assert text.startswith("# ILLUSTRATIVE / SYNTHETIC"), name
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    return list(csv.DictReader(io.StringIO(body)))


def count(db: str, table: str, import_id: Any = None) -> int:
    if import_id is None:
        return int(sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"])
    return int(
        sql(db, f"SELECT count(*) AS n FROM {table} WHERE import_job_id = $1", import_id)[0]["n"]
    )


def credit_counts(db: str) -> dict[str, int]:
    return {t: count(db, t) for t in (*CREDIT_TABLES, "domain_projection")}


def revision(db: str) -> str:
    return str(sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"])


def copy(table: str, changes: str, where: str = "true") -> str:
    """INSERT a copy of one row of `table` with a new id and these jsonb changes."""
    return (
        f"INSERT INTO {table} SELECT * FROM (SELECT (jsonb_populate_record(NULL::{table},"
        f" to_jsonb(t) || jsonb_build_object('id', gen_random_uuid(), {changes}))).*"
        f" FROM {table} t WHERE {where} LIMIT 1) x"
    )


def check_refuses(db: str, table: str, where: str) -> None:
    """An unknown sixth schema_code in `table` is refused by its CHECK itself: the row's user
    triggers are lifted inside one transaction that is never committed."""
    with pytest.raises(asyncpg.CheckViolationError):
        insert = copy(table, "'schema_code', 'collateral_asset'", where)
        script(db, f"BEGIN; ALTER TABLE {table} DISABLE TRIGGER USER; {insert}; ROLLBACK;")
    assert count(db, table) > 0 and not sql(
        db, f"SELECT 1 FROM {table} WHERE schema_code = 'collateral_asset'"
    )


@pytest.mark.ws3
def test_credit_domain_projection_snapshots_reads_and_guards(
    api: TestClient, db: str, artifact_dir: Path
) -> None:
    # --- 1. Seed (BE-19.1) -------------------------------------------------------------------
    m0022 = migration("0022_credit_domain")
    assert (m0022.revision, m0022.down_revision, m0022.TAG) == ("0022", "0021", TAG)
    for code in CREDIT_CODES:
        [version] = sql(
            db,
            "SELECT v.id, v.status, d.seeded_by AS d_tag, v.seeded_by AS v_tag"
            " FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id"
            " WHERE d.code = $1",
            code,
        )
        assert (version["status"], version["d_tag"], version["v_tag"]) == ("ACTIVE", TAG, TAG)
        fields = {
            f["name"]: f
            for f in sql(
                db, "SELECT * FROM schema_field WHERE schema_version_id = $1", version["id"]
            )
        }
        assert all(f["seeded_by"] == TAG for f in fields.values()), code
        assert {n: fields[n]["data_type"] for n in FIELD_TYPES[code]} == FIELD_TYPES[code]
        assert {n: fields[n]["classification"] for n in CLASSES[code]} == CLASSES[code]
        deps = sql(
            db,
            "SELECT s.seeded_by FROM schema_dependency s JOIN schema_definition d"
            " ON d.id = s.schema_id WHERE d.code = $1",
            code,
        )
        assert deps and all(d["seeded_by"] == TAG for d in deps), code
    exposure_fields = {
        f["name"]: f
        for f in sql(
            db,
            "SELECT f.* FROM schema_field f JOIN schema_version v ON v.id = f.schema_version_id"
            " JOIN schema_definition d ON d.id = v.schema_id WHERE d.code = 'credit_exposure'",
        )
    }
    assert exposure_fields["maturity_date"]["required"] is True
    assert exposure_fields["instrument_id"]["required"] is False
    vocabulary = sql(db, "SELECT * FROM credit_vocabulary")
    assert vocabulary and all(v["seeded_by"] == TAG for v in vocabulary)
    assert {v["vocabulary"] for v in vocabulary} == {k.value for k in CreditVocabularyKind}
    grades = {v["code"]: v for v in vocabulary if v["vocabulary"] == "rating_grade"}
    assert set(grades) == {*(f"G0{i}" for i in range(1, 9)), "D"}  # neutral codes only
    assert [c for c, g in grades.items() if g["is_default_grade"]] == ["D"]
    assert sorted(grades, key=lambda c: grades[c]["rank"]) == [
        *sorted(grades)[1:],
        "D",
    ]  # G01 best, D last

    # --- 2. Samples and projection (BE-19.2, BE-19.3) ------------------------------------------
    counterparties, exposures = (
        rows_of(CREDIT_SAMPLES["counterparty"]),
        rows_of(CREDIT_SAMPLES["credit_exposure"]),
    )
    assert 6 <= len(counterparties) <= 10 and len({r["segment"] for r in counterparties}) == 4
    assert 12 <= len(exposures) <= 16
    assert {r["currency"] for r in exposures} == {"EUR", "USD"}
    assert {"0", "35", "95"} <= {r["days_past_due"] for r in exposures}
    assert "true" in {r["default_flag"] for r in exposures}
    known = {
        kind: {v["code"] for v in vocabulary if v["vocabulary"] == kind}
        for kind in ("collateral_type", "facility_type", "rating_grade")
    }
    collateral = {r["collateral_type"] for r in exposures if r["collateral_type"]}
    assert {"CASH", "REAL_ESTATE"} <= collateral and collateral - known["collateral_type"]
    assert any(r["origination_grade"] not in known["rating_grade"] for r in exposures)
    assert any(r["maturity_date"] < r["as_of_date"] for r in exposures)  # ISO dates: text order
    assert {r["counterparty_id"] for r in exposures} - {
        r["counterparty_id"] for r in counterparties
    }
    assert any("GUARANTEE" in r["facility_type"].upper() for r in exposures)
    assert any(
        "REVOLV" in r["facility_type"].upper() and Decimal(r["undrawn_amount"] or "0") > 0
        for r in exposures
    )

    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    analyst = dev_token(api, "risk_analyst")
    imports = {code: validated_credit(api, db, steward, approver, code) for code in CREDIT_CODES}
    sizes = {"counterparty": len(counterparties), "credit_exposure": len(exposures)}
    for code, imported in imports.items():
        assert imported["manifest"]["quality"]["accepted_records"] == sizes[code], imported

    # The switch off: the pre-Feature-04 projection_unsupported, audited, nothing written.
    off = Settings(
        ENV="test",
        DATABASE_URL=db,
        ARTIFACT_STORE_DIR=artifact_dir,
        CREDIT_PROJECTION_ENABLED=False,
    )
    with TestClient(create_app(off)) as switched:
        for code, imported in imports.items():
            c = cid()
            r = project(switched, steward, imported["id"], **{"X-Correlation-ID": c})
            assert (r.status_code, r.json()["code"]) == (409, "projection_unsupported"), r.text
            assert r.json()["details"]["schema_code"] == code
            [event] = events(db, c)
            assert (event["action"], event["outcome"]) == ("import.project", "DENIED")
            assert json.loads(event["details"])["reason"] == "projection_unsupported"
        assert switched.get(COUNTERPARTIES, headers=analyst).json()["total"] == 0
    assert credit_counts(db) == dict.fromkeys((*CREDIT_TABLES, "domain_projection"), 0)

    projections: dict[str, dict[str, Any]] = {}
    for code, imported in imports.items():
        import_id = uuid.UUID(imported["id"])
        c = cid()
        r = project(api, steward, imported["id"], **{"X-Correlation-ID": c})
        assert r.status_code == 201, r.text
        body = no_float(r.text)
        assert (body["schema_code"], body["schema_version"], body["projector_version"]) == (
            code,
            1,
            1,
        )
        assert body["projected_count"] == body["accepted_count"] == sizes[code]
        assert count(db, VERSION_TABLES[code], import_id) == sizes[code]
        assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
            ("import.project", "SUCCESS")
        ]
        before = credit_counts(db)
        c = cid()
        again = project(api, steward, imported["id"], **{"X-Correlation-ID": c})
        assert again.status_code == 200 and again.headers["Idempotent-Replayed"] == "true"
        assert again.json() == body and credit_counts(db) == before and events(db, c) == []
        unresolved = sql(
            db,
            f"SELECT count(*) AS n FROM {VERSION_TABLES[code]} t WHERE NOT EXISTS ("
            " SELECT 1 FROM record_lineage l JOIN canonical_record c"
            " ON c.id = l.canonical_record_id JOIN source_record s ON s.id = l.source_record_id"
            " JOIN import_batch b ON b.id = s.batch_id"
            " WHERE c.id = t.canonical_record_id AND c.import_job_id = t.import_job_id"
            " AND b.import_job_id = t.import_job_id AND s.id = t.source_record_id"
            " AND s.ordinal = t.source_ordinal AND c.payload_sha256 = t.payload_sha256)",
        )
        assert unresolved[0]["n"] == 0, code
        projections[code] = body
    assert count(db, "counterparty") == len({r["counterparty_id"] for r in counterparties})
    assert count(db, "credit_exposure") == len({r["exposure_id"] for r in exposures})
    for table in (*CREDIT_TABLES, "domain_projection"):
        for row in sql(db, f"SELECT * FROM {table}"):
            for name, value in dict(row).items():
                assert not isinstance(value, float), (table, name)
                if name == "summary":
                    no_float(value)
    typed = sql(
        db,
        "SELECT days_past_due, default_flag, drawn_amount, maturity_date"
        " FROM credit_exposure_version",
    )
    assert all(isinstance(t["days_past_due"], int) for t in typed if t["days_past_due"] is not None)
    assert {type(t["default_flag"]) for t in typed} == {bool}
    assert all(
        isinstance(t["drawn_amount"], Decimal) for t in typed if t["drawn_amount"] is not None
    )
    assert all(isinstance(t["maturity_date"], date) for t in typed)
    assert {t["days_past_due"] for t in typed} >= {0, 35, 95}

    # --- 3. Snapshots (BE-19.4) ----------------------------------------------------------------
    cp_id, ce_id = imports["counterparty"]["id"], imports["credit_exposure"]["id"]
    c = cid()
    r = make_snapshot(api, analyst, [cp_id, ce_id], **{"X-Correlation-ID": c})
    assert r.status_code == 201, r.text
    snapshot = no_float(r.text)
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
        ("dataset_snapshot.create", "SUCCESS")
    ]
    again = make_snapshot(api, analyst, [ce_id, cp_id])
    assert again.status_code == 200 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["snapshot_id"] == snapshot["snapshot_id"] and again.json() == snapshot
    manifests = {
        str(m["import_job_id"]): m["manifest_sha256"]
        for m in sql(
            db,
            "SELECT import_job_id, manifest_sha256 FROM import_manifest"
            " WHERE import_job_id = ANY($1::uuid[])",
            [uuid.UUID(cp_id), uuid.UUID(ce_id)],
        )
    }
    members = [
        {"schema_code": code, "import_id": i, "manifest_sha256": manifests[i]}
        for code, i in (("counterparty", cp_id), ("credit_exposure", ce_id))
    ]
    text = json.dumps(members, sort_keys=True, separators=(",", ":"))  # already by schema code
    assert snapshot["content_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert [(m["schema_code"], m["import_id"]) for m in snapshot["members"]] == [
        ("counterparty", cp_id),
        ("credit_exposure", ce_id),
    ]
    assert api.get(f"{SNAPSHOTS}/{snapshot['snapshot_id']}", headers=approver).json() == snapshot
    # One counterparty key twice in one import (the contract's key is id + country).
    first = counterparties[0]
    buffer = io.StringIO()
    buffer.write("# ILLUSTRATIVE / SYNTHETIC duplicate key fixture. Not client data.\n")
    writer = csv.DictWriter(buffer, fieldnames=list(first), lineterminator="\n")
    writer.writeheader()
    for country in DUP_COUNTRIES:
        writer.writerow({**first, "counterparty_id": "CP-DUP", "country": country})
    data = buffer.getvalue()
    duplicated = validated_credit(
        api,
        db,
        steward,
        approver,
        "counterparty",
        data.encode(),
        source_code="dup_counterparties",
        key=["counterparty_id", "country"],
    )
    assert project(api, steward, duplicated["id"]).status_code == 201
    c = cid()
    r = make_snapshot(api, analyst, [duplicated["id"], ce_id], **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "ambiguous_business_key"), r.text
    [event] = events(db, c)
    assert (event["action"], event["outcome"]) == ("dataset_snapshot.create", "DENIED")

    # --- 4. Reads (BE-19.5) --------------------------------------------------------------------
    vocab = no_float(api.get(VOCABULARIES, headers=analyst).text)["items"]
    assert {(v["vocabulary"], v["code"]) for v in vocab} == {
        (v["vocabulary"], v["code"]) for v in vocabulary
    }
    assert all(v["provisional"] for v in vocab)
    assert [v["code"] for v in vocab if v["is_default_grade"]] == ["D"]

    page = no_float(api.get(COUNTERPARTIES, params={"limit": 200}, headers=analyst).text)
    keys = {r["counterparty_id"] for r in counterparties} | {"CP-DUP"}
    assert page["total"] == len(keys) == len(page["items"])
    assert all(i["counterparty_name"] == MASKED for i in page["items"])
    # The duplicate copies the first counterparty's segment; a filter matches any version.
    segment, country = first["segment"], first["country"]
    dup_country = {"CP-DUP"} if country in DUP_COUNTRIES else set()
    for params, expected in (
        (
            {"segment": segment},
            {r["counterparty_id"] for r in counterparties if r["segment"] == segment} | {"CP-DUP"},
        ),
        (
            {"country": country},
            {r["counterparty_id"] for r in counterparties if r["country"] == country} | dup_country,
        ),
        ({"import_id": cp_id}, {r["counterparty_id"] for r in counterparties}),
        ({"import_id": duplicated["id"]}, {"CP-DUP"}),
        ({"import_id": ce_id}, set()),
    ):
        got = api.get(COUNTERPARTIES, params={**params, "limit": 200}, headers=analyst).json()
        assert {i["business_key"] for i in got["items"]} == expected, params
        assert got["total"] == len(expected), params
    source_of = {i["business_key"]: i["source_id"] for i in page["items"]}
    got = api.get(COUNTERPARTIES, params={"source_id": source_of["CP-DUP"]}, headers=analyst)
    assert {i["business_key"] for i in got.json()["items"]} == {"CP-DUP"}

    # One counterparty with exposures: its versions, and its exposures by business key.
    named = {r["counterparty_id"] for r in exposures}
    [item] = [i for i in page["items"] if i["business_key"] == sorted(named & keys)[0]]
    path = f"{COUNTERPARTIES}/{item['counterparty_id']}"
    clear, hidden = (no_float(api.get(path, headers=h).text) for h in (steward, analyst))
    assert len(clear["versions"]) == 1
    assert isinstance(clear["versions"][0]["counterparty_name"], str)
    assert hidden["versions"][0]["counterparty_name"] == MASKED
    assert hidden["versions"][0]["country"] == clear["versions"][0]["country"]  # public
    expected_exposures = {
        r["exposure_id"] for r in exposures if r["counterparty_id"] == item["business_key"]
    }
    assert {e["business_key"] for e in clear["exposure_versions"]} == expected_exposures
    assert all(e["counterparty_key"] == item["business_key"] for e in clear["exposure_versions"])
    assert all(e["drawn_amount"] == MASKED for e in hidden["exposure_versions"])

    listed = no_float(api.get(EXPOSURES, params={"limit": 200}, headers=analyst).text)
    assert listed["total"] == len(exposures) == len(listed["items"])
    assert all(i["drawn_amount"] in (MASKED, None) for i in listed["items"])
    for params, expected in (
        ({"currency": "USD"}, {r["exposure_id"] for r in exposures if r["currency"] == "USD"}),
        ({"counterparty_key": item["business_key"]}, expected_exposures),
        ({"import_id": ce_id}, {r["exposure_id"] for r in exposures}),
        ({"import_id": cp_id}, set()),
    ):
        got = api.get(EXPOSURES, params={**params, "limit": 200}, headers=analyst).json()
        assert {i["business_key"] for i in got["items"]} == expected, params
    # One exposure: drawn_amount masked by role; version -> canonical record -> source row.
    drawn = next(r for r in exposures if r["drawn_amount"])
    [exposure] = [i for i in listed["items"] if i["business_key"] == drawn["exposure_id"]]
    path = f"{EXPOSURES}/{exposure['credit_exposure_id']}"
    clear, hidden = (no_float(api.get(path, headers=h).text) for h in (steward, analyst))
    [shown], [masked] = clear["versions"], hidden["versions"]
    assert isinstance(shown["drawn_amount"], str) and masked["drawn_amount"] == MASKED
    assert Decimal(shown["drawn_amount"]) == Decimal(drawn["drawn_amount"])
    assert masked["currency"] == shown["currency"] == drawn["currency"]  # public
    assert masked["days_past_due"] == shown["days_past_due"] == int(drawn["days_past_due"])
    assert masked["default_flag"] == shown["default_flag"] == (drawn["default_flag"] == "true")
    assert masked["payload_sha256"] == "masked" and len(shown["payload_sha256"]) == 64
    assert shown["import_id"] == ce_id
    [chain] = sql(
        db,
        "SELECT s.id AS source_record_id, s.ordinal, s.raw_values::text AS raw,"
        " b.import_job_id, c.payload ->> 'drawn_amount' AS drawn FROM canonical_record c"
        " JOIN record_lineage l ON l.canonical_record_id = c.id"
        " JOIN source_record s ON s.id = l.source_record_id"
        " JOIN import_batch b ON b.id = s.batch_id WHERE c.id = $1",
        uuid.UUID(shown["canonical_record_id"]),
    )
    assert str(chain["import_job_id"]) == ce_id
    assert (str(chain["source_record_id"]), chain["ordinal"]) == (
        shown["source_record_id"],
        shown["source_ordinal"],
    )
    assert json.loads(chain["raw"])["exposure_id"] == drawn["exposure_id"]
    assert chain["drawn"] == shown["drawn_amount"]  # exact text, not rounded
    # Absent ids: 404 not_found (another tenant's id is the WS1 isolation test's).
    for absent in (f"{COUNTERPARTIES}/{uuid.uuid4()}", f"{EXPOSURES}/{uuid.uuid4()}"):
        r = api.get(absent, headers=analyst)
        assert (r.status_code, r.json()["code"]) == (404, "not_found"), absent

    # --- 5. Guards (BE-19.1) -------------------------------------------------------------------
    for table in GUARDED_TABLES:
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
    # Same tenant, and a canonical record of the projection's own import (the 0017 checks).
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    for table in ("counterparty_version", "credit_exposure_version"):
        with pytest.raises(IMMUTABLE):
            sql(db, copy(table, "'tenant_id', $1::uuid"), other)
        with pytest.raises(IMMUTABLE, match="canonical record"):
            sql(db, copy(table, "'canonical_record_id', gen_random_uuid()"))
    with pytest.raises(IMMUTABLE, match="must belong to a counterparty projection"):
        sql(
            db,
            copy("counterparty_version", "'domain_projection_id', $1::uuid"),
            uuid.UUID(projections["credit_exposure"]["projection_id"]),
        )
    # An unknown sixth schema_code is still refused by both widened CHECKs (D-38).
    check_refuses(db, "domain_projection", "schema_code = 'counterparty'")
    check_refuses(db, "dataset_snapshot_member", "schema_code = 'credit_exposure'")

    # The downgrade refuses while a credit projection exists: nothing changes.
    head = str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())
    assert revision(db) == head
    before = credit_counts(db)
    try:
        with pytest.raises(Exception, match="refused"):
            command.downgrade(alembic_config(db), "0021")
        assert revision(db) == head and credit_counts(db) == before
        # Without credit rows: 0021 -> head -> 0021 -> head.
        clean(db)
        for _ in range(2):
            command.downgrade(alembic_config(db), "0021")
            assert revision(db) == "0021"
            gone = sql(
                db,
                "SELECT to_regclass('counterparty_version') AS a,"
                " to_regclass('credit_exposure_version') AS b, to_regclass('counterparty') AS c,"
                " to_regclass('credit_exposure') AS d, to_regclass('credit_vocabulary') AS e",
            )[0]
            assert set(dict(gone).values()) == {None}
            assert not sql(
                db,
                "SELECT 1 FROM schema_definition WHERE code IN ('counterparty', 'credit_exposure')",
            )
            for constraint in (
                "ck_domain_projection_schema_code",
                "ck_dataset_snapshot_member_schema_code",
            ):
                [narrow] = sql(
                    db,
                    "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = $1",
                    constraint,
                )
                assert "counterparty" not in narrow["d"] and "cashflow_event" in narrow["d"]
            command.upgrade(alembic_config(db), "head")
            assert revision(db) == head
            assert count(db, "credit_vocabulary") == len(vocabulary)
            assert (
                sql(
                    db,
                    "SELECT count(*) AS n FROM schema_version v JOIN schema_definition d"
                    " ON d.id = v.schema_id WHERE v.seeded_by = $1 AND v.status = 'ACTIVE'",
                    TAG,
                )[0]["n"]
                == 2
            )
    finally:
        if revision(db) != head:
            command.upgrade(alembic_config(db), "head")
