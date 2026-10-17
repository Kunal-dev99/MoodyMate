"""Phase 11 (BE-11.1 – BE-11.3): validation and canonical records, in three essential tests.

1. DB guards: every new table refuses UPDATE/DELETE/TRUNCATE (quarantine_item: status moves
   only, along the D-20 table), the seed and frozen copies match the code, and the
   downgrade refuses while validation results exist.
2. The engine (no DB): every synthetic fixture gives exactly its (row, field, code) issues
   and status; source rows = accepted + quarantined + rejected for every fixture and for
   generated rows; canonical payloads hold strings only, never floats.
3. POST /imports/{id}/validate and GET /imports/{id}/issues: 409 mapping_required, each final
   status with its fixture, 409 already_validated, identical payload hashes for the same
   artifact in two imports, issue filters and pagination, and the BE-10.4 carry-over: a
   mapping dry run writes no canonical record.
"""

import importlib.util
import inspect
import json
import random
import uuid
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.governance.constants import FieldType
from app.ingestion import service as ingestion_service
from app.ingestion.adapters import AdapterError, AdapterOptions, Limits, parse_upload
from app.ingestion.constants import (
    DEFAULT_VALIDATION_RULES,
    QUARANTINE_TRANSITIONS,
    RowOutcome,
    SourceFormat,
    ValidationCode,
)
from app.ingestion.service import (
    MappedRule,
    RowResult,
    profile_entries,
    profile_rules_sha256,
    validate_rows,
)
from app.mapping.transforms import TargetField
from app.tests.conftest import alembic_config, dev_token, sql
from app.tests.helpers import (
    IMPORTS,
    SAMPLE_DIR,
    cid,
    events,
    import_sample,
    instrument_mapping,
    sample,
    validate_import,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
IMMUTABLE = asyncpg.InsufficientPrivilegeError
C = ValidationCode


def migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def counts(db: str, import_id: Any) -> dict[str, int]:
    return {
        "canonical": sql(
            db, "SELECT count(*) AS n FROM canonical_record WHERE import_job_id = $1", import_id
        )[0]["n"],
        "lineage": sql(
            db,
            "SELECT count(*) AS n FROM record_lineage l JOIN canonical_record c"
            " ON c.id = l.canonical_record_id WHERE c.import_job_id = $1",
            import_id,
        )[0]["n"],
        "items": sql(
            db, "SELECT count(*) AS n FROM quarantine_item WHERE import_job_id = $1", import_id
        )[0]["n"],
    }


# --- 1. DB guards, seed, frozen copies and the downgrade refusal (BE-11.1) ------------------------


def test_validation_tables_are_guarded_in_the_database(api: TestClient, db: str) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    first = import_sample(api, steward, db, sample("instrument.csv"))
    instrument_mapping(api, db, steward, approver, first["id"])
    held = import_sample(api, steward, db, sample("instrument__mixed_errors.csv"))
    assert validate_import(api, steward, held["id"]).json()["status"] == "QUARANTINED"
    job = uuid.UUID(held["id"])

    # The seed and the code agree (D-11 frozen copies).
    m15 = migration("0015_validation_tables")
    assert {c.value: (s.value, d.value) for c, (s, d) in DEFAULT_VALIDATION_RULES.items()} == {
        c: tuple(v) for c, v in m15.DEFAULT_RULES.items()
    }
    assert set(m15.DEFAULT_RULES) == {c.value for c in ValidationCode}
    assert {k.value: sorted(v) for k, v in QUARANTINE_TRANSITIONS.items()} == {
        k: sorted(v) for k, v in m15.QUARANTINE_TRANSITIONS.items()
    }
    [seeded] = sql(db, "SELECT * FROM validation_profile")
    rules = json.loads(seeded["rules"])
    assert (seeded["code"], seeded["version"], seeded["provisional"]) == ("default", 1, True)
    assert seeded["seeded_by"] == "0015_validation_profile_seed"
    assert seeded["content_sha256"] == profile_rules_sha256(rules) == m15.profile_sha256(rules)

    # Append-only tables: UPDATE, DELETE and TRUNCATE are refused, with no bypass setting.
    one = {
        t: sql(db, f"SELECT id FROM {t} LIMIT 1")[0]["id"]
        for t in (
            "validation_profile",
            "validation_issue",
            "canonical_record",
            "record_lineage",
            "import_manifest",
        )
    }
    for table, row_id in one.items():
        for statement in (
            f"UPDATE {table} SET created_by = 'x' WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(IMMUTABLE, match="append-only"):
                sql(db, statement, row_id)
        with pytest.raises(IMMUTABLE, match="append-only"):
            sql(db, f"TRUNCATE {table} CASCADE")

    # quarantine_item: a status move along the table is allowed; everything else is refused.
    item = sql(db, "SELECT * FROM quarantine_item WHERE import_job_id = $1 LIMIT 1", job)[0]
    other_record = sql(
        db,
        "SELECT r.id FROM source_record r JOIN import_batch b ON b.id = r.batch_id"
        " WHERE b.import_job_id = $1 AND r.id <> $2 LIMIT 1",
        job,
        item["source_record_id"],
    )[0]["id"]
    refused = [
        ("UPDATE quarantine_item SET source_record_id = $2 WHERE id = $1", other_record),
        ("UPDATE quarantine_item SET reason_codes = '[]' WHERE id = $1", None),
        ("UPDATE quarantine_item SET owner = 'someone else' WHERE id = $1", None),
        (
            "UPDATE quarantine_item SET status = 'RESOLVED', resolved_by_import_id = $2"
            " WHERE id = $1",
            job,
        ),
        ("UPDATE quarantine_item SET status = 'RETRY_READY' WHERE id = $1", None),
        ("DELETE FROM quarantine_item WHERE id = $1", None),
    ]
    for statement, arg in refused:
        with pytest.raises(IMMUTABLE):
            sql(db, statement, item["id"], *([arg] if arg is not None else []))
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE quarantine_item CASCADE")
    sql(db, "UPDATE quarantine_item SET status = 'UNDER_REVIEW' WHERE id = $1", item["id"])
    after = sql(db, "SELECT * FROM quarantine_item WHERE id = $1", item["id"])[0]
    assert after["status"] == "UNDER_REVIEW"
    assert {k: v for k, v in after.items() if k not in ("status", "updated_at")} == {
        k: v for k, v in item.items() if k not in ("status", "updated_at")
    }
    # Inserts: an item starts OPEN or REJECTED; a canonical record carries the pinned versions;
    # enum CHECKs hold.
    with pytest.raises(IMMUTABLE, match="OPEN or REJECTED"):
        sql(
            db,
            "INSERT INTO quarantine_item (id, import_job_id, batch_id, source_record_id, ordinal,"
            " reason_codes, owner, status, created_by) SELECT gen_random_uuid(), $1, batch_id,"
            " gen_random_uuid(), 1, '[]', 'x', 'CORRECTED', 't' FROM quarantine_item"
            " WHERE id = $2",
            job,
            item["id"],
        )
    with pytest.raises(IMMUTABLE, match="pinned"):
        sql(
            db,
            "INSERT INTO canonical_record (id, import_job_id, schema_version_id,"
            " mapping_version_id, source_record_id, ordinal, payload, payload_sha256, created_by)"
            " SELECT gen_random_uuid(), import_job_id, schema_version_id, mapping_version_id, $2,"
            " 99, payload, payload_sha256, 't' FROM canonical_record WHERE import_job_id = $1"
            " LIMIT 1",
            job,
            other_record,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        sql(
            db,
            "INSERT INTO validation_issue (id, import_job_id, sequence, code, severity,"
            " disposition, validation_profile_id, message, created_by) VALUES"
            " (gen_random_uuid(), $1, 999, 'X', 'FATAL', 'FILE_BLOCKED', $2, 'm', 't')",
            job,
            seeded["id"],
        )
    with pytest.raises(asyncpg.CheckViolationError, match="ck_validation_issue_code"):
        sql(  # Review P11-1: the issue code is an enum too.
            db,
            "INSERT INTO validation_issue (id, import_job_id, sequence, code, severity,"
            " disposition, validation_profile_id, message, created_by) VALUES"
            " (gen_random_uuid(), $1, 998, 'X', 'BLOCKER', 'FILE_BLOCKED', $2, 'm', 't')",
            job,
            seeded["id"],
        )

    # The downgrade refuses while results exist, and changes nothing. (Since Phase 12 a
    # validated import also has a reconciliation run, so 0016 refuses first; the 0015
    # refusal itself is checked in test_reconciliation once the Phase 12 rows are gone.)
    tables = ("canonical_record", "quarantine_item", "validation_issue", "import_manifest")
    before = {t: sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in tables}
    head = sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"]
    try:
        with pytest.raises(Exception, match=r"they are kept \(D-20\), so this downgrade"):
            command.downgrade(alembic_config(db), "0014")
        assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == head
        assert {t: sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in tables} == before
    finally:
        command.upgrade(alembic_config(db), "head")


# --- 2. The engine on every fixture, the conservation invariant, no floats (BE-11.2) --------------

M0007 = migration("0007_schema_seed")
# dataset -> (extension, format, seeded v1 fields, business key)
DATASETS: dict[str, tuple[str, SourceFormat, list[dict[str, Any]], str]] = {
    "instrument": ("csv", SourceFormat.CSV, M0007.INSTRUMENT_V1, "instrument_id"),
    "position": ("xlsx", SourceFormat.XLSX, M0007.POSITION_V1, "position_id"),
    "cashflow_event": ("json", SourceFormat.JSON, M0007.CASHFLOW_EVENT_V1, "cashflow_id"),
}
# dataset -> (date field, decimal field, required field dropped / emptied)
DEFECT_FIELDS = {
    "instrument": ("maturity_date", "notional", "notional"),
    "position": ("as_of_date", "balance", "balance"),
    "cashflow_event": ("due_date", "amount", "due_date"),
}


Issue = tuple[int | None, str, C]


def expected(dataset: str, variant: str) -> tuple[str, set[Issue]]:
    """The status and exact (row, field or column, code) issues of each fixture."""
    key = DATASETS[dataset][3]
    day, amount, required = DEFECT_FIELDS[dataset]
    one: dict[str, set[Issue]] = {
        "bad_date": {(3, day, C.INVALID_DATE)},
        "over_scale_decimal": {(4, amount, C.SCALE_EXCEEDED)},
        "duplicate_business_key": {(5, key, C.DUPLICATE_BUSINESS_KEY)},
        "bad_currency_code": {(6, "currency", C.INVALID_CURRENCY_CODE)},
    }
    if variant == "":
        return "COMPLETED", set()
    if variant in one:
        return "QUARANTINED", one[variant]
    if variant == "mixed_errors":
        return "QUARANTINED", set().union(*one.values()) | {(8, required, C.REQUIRED_VALUE_MISSING)}
    if variant == "unknown_extra_column":
        return "COMPLETED_WITH_WARNINGS", {(None, "legacy_flag", C.UNMAPPED_COLUMN)}
    assert variant == "missing_required_column", variant
    return "FAILED", {(None, required, C.REQUIRED_COLUMN_MISSING)}


def plan(dataset: str) -> tuple[list[MappedRule], dict[str, TargetField], dict[str, Any]]:
    """The dataset's mapping, built once from the clean file's header (as an approved mapping
    is): decimals parsed, dates parsed, every column mapped."""
    ext, fmt, seeded, _ = DATASETS[dataset]
    fields = {
        f["name"]: TargetField(
            f["name"],
            FieldType(f["data_type"]),
            f["required"],
            f["precision"],
            f["scale"],
            f["classification"],
        )
        for f in seeded
    }
    columns = parse_upload(sample(f"{dataset}.{ext}"), fmt, AdapterOptions(), Limits()).columns
    rules = []
    for position, column in enumerate(columns, start=1):
        target = "legacy_book_code" if column == "book_code" else column
        kind, params = "COPY", {}
        if fields[target].data_type == FieldType.DECIMAL:
            kind = "PARSE_DECIMAL"
        if fields[target].data_type == FieldType.DATE or target == "maturity_date":
            kind, params = "PARSE_DATE", {"format": "YYYY-MM-DD"}
        rules.append(MappedRule(position, column, target, "MAP", kind, params, "ALLOW_NULL", None))
    return rules, fields, {f["name"]: f["unit"] for f in seeded}


def run(dataset: str, rows: list[Any], columns: list[str]) -> tuple[Any, list[RowResult]]:
    rules, fields, units = plan(dataset)
    entries = profile_entries(
        {
            c.value: {"severity": s, "disposition": d}
            for c, (s, d) in DEFAULT_VALIDATION_RULES.items()
        }
    )
    return validate_rows(rules, fields, units, columns, [DATASETS[dataset][3]], entries, rows)


def assert_typed_strings(results: list[RowResult], fields: dict[str, TargetField]) -> None:
    """D-22: payloads hold strings or null only (JSON round trip: no number, no float), and
    decimals carry exactly their field's scale."""
    for result in results:
        if result.payload is None:
            continue
        decoded = json.loads(json.dumps(result.payload))
        assert all(v is None or isinstance(v, str) for v in decoded.values()), decoded
        for name, value in decoded.items():
            if value is not None and fields[name].data_type == FieldType.DECIMAL:
                assert -int(Decimal(value).as_tuple().exponent) == fields[name].scale, name


def test_engine_fixtures_invariant_and_no_floats() -> None:
    files = sorted(p.name for p in SAMPLE_DIR.iterdir() if p.suffix != "")
    seen = 0
    for dataset, (ext, fmt, _, _) in DATASETS.items():
        _, fields, _ = plan(dataset)
        for name in [
            f for f in files if f.startswith(f"{dataset}.") or f.startswith(f"{dataset}__")
        ]:
            variant = name.removesuffix(f".{ext}").partition("__")[2]
            if variant == "non_utf8":  # refused at upload: never an import (D-25 §3)
                with pytest.raises(AdapterError) as refused:
                    parse_upload(sample(name), fmt, AdapterOptions(), Limits())
                assert refused.value.code == "encoding_not_utf8"
                continue
            parsed = parse_upload(sample(name), fmt, AdapterOptions(), Limits())
            rows = [
                (
                    r.ordinal,
                    r.values,
                    r.extras,
                    [{"code": f.code, "column": f.column} for f in r.flags],
                )
                for r in parsed.rows
            ]
            validator, results = run(dataset, rows, parsed.columns)
            found = {
                (f.ordinal, f.field or f.source_column, f.code)
                for f in [*validator.findings, *(f for r in results for f in r.findings)]
            }
            status, issues = expected(dataset, variant)
            assert (validator.status().value, found) == (status, issues), name
            # Conservation: every row has exactly one outcome.
            outcomes = [r.outcome for r in results]
            assert len(outcomes) == len(parsed.rows) == 25, name
            assert validator.accepted + validator.quarantined + validator.rejected == 25, name
            assert outcomes.count(RowOutcome.ACCEPTED) == validator.accepted, name
            assert all((r.payload is not None) == (r.outcome == "ACCEPTED") for r in results)
            assert_typed_strings(results, fields)
            seen += 1
    assert seen == 24  # 3 clean files + 21 bad variants that parse (non-UTF-8 is refused)

    # Property: generated rows with random defects always conserve rows, keep strings only,
    # and are deterministic (the same rows give the same payload hashes).
    rng = random.Random(20261017)  # noqa: S311 - seeded, reproducible test data
    columns = parse_upload(
        sample("instrument.csv"), SourceFormat.CSV, AdapterOptions(), Limits()
    ).columns
    _, fields, _ = plan("instrument")
    for _ in range(25):
        rows = []
        for ordinal in range(1, rng.randint(1, 60) + 1):
            values = {
                "instrument_id": f"I-{rng.randint(1, 30)}" if rng.random() > 0.05 else " ",
                "instrument_type": rng.choice(["LOAN", "BOND", ""]),
                "currency": rng.choice(["EUR", "USD", "EURO", "eur", ""]),
                "counterparty_id": "CP-1",
                "product_family": "RETAIL",
                "notional": rng.choice(["1.5", "1.234", "x", "", "-0", "1" * 19, "12.30"]),
                "interest_rate": rng.choice(["", "0.1", "1e3"]),
                "maturity_date": rng.choice(["2030-01-31", "2030-02-30", ""]),
                "book_code": "BK-1",
            }
            flags: list[dict[str, Any]] = []
            extras: list[str] = []
            roll = rng.random()
            if roll < 0.05:
                values, flags = {}, [{"code": "BLANK_ROW", "column": None}]
            elif roll < 0.1:
                extras, flags = ["spill"], [{"code": "EXTRA_CELLS", "column": len(columns) + 1}]
            elif roll < 0.15:
                values.pop("book_code")
                flags = [{"code": "MISSING_CELLS", "column": len(columns)}]
            rows.append((ordinal, values, extras, flags))
        validator, results = run("instrument", rows, columns)
        assert validator.accepted + validator.quarantined + validator.rejected == len(rows)
        assert [r.ordinal for r in results] == [r[0] for r in rows]
        assert_typed_strings(results, fields)
        again = run("instrument", rows, columns)[1]
        assert [r.payload for r in again] == [r.payload for r in results]

    # No float anywhere in the engine (the transform engine is checked by BE-10.2's test).
    for part in ingestion_service.VALIDATOR_PARTS:
        assert "float" not in inspect.getsource(getattr(ingestion_service, part)), part


# --- 3. The validate endpoint, statuses, issues and identical hashes (BE-11.3) --------------------


def test_validate_statuses_issues_and_identical_payloads(api: TestClient, db: str) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    early = import_sample(api, steward, db, sample("instrument.csv"))
    assert early["status"] == "MAPPING_REQUIRED"

    # No usable mapping version: 409 mapping_required (audited), nothing written.
    c = cid()
    r = validate_import(api, steward, early["id"], **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "mapping_required"), r.text
    assert (
        api.get(f"{IMPORTS}/{early['id']}", headers=steward).json()["status"] == "MAPPING_REQUIRED"
    )
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [("import.validate", "DENIED")]

    # BE-10.4 carry-over: the dry runs that activate the mapping write no canonical record.
    mapping = instrument_mapping(api, db, steward, approver, early["id"])
    assert sql(db, "SELECT count(*) AS n FROM canonical_record")[0]["n"] == 0
    assert sql(db, "SELECT count(*) AS n FROM mapping_validation")[0]["n"] == 1

    # Each final status with its fixture; the pinned mapping version; rows conserved.
    for fixture, status, issues in (
        ("instrument.csv", "COMPLETED", 0),
        ("instrument__unknown_extra_column.csv", "COMPLETED_WITH_WARNINGS", 1),
        ("instrument__mixed_errors.csv", "QUARANTINED", 5),
        ("instrument__missing_required_column.csv", "FAILED", 1),
    ):
        imported = import_sample(api, steward, db, sample(fixture))
        c = cid()
        r = validate_import(api, steward, imported["id"], **{"X-Correlation-ID": c})
        assert r.status_code == 200, r.text
        body = r.json()
        quality = body["manifest"]["quality"]
        assert (body["status"], body["mapping_version_id"]) == (status, mapping["id"]), fixture
        assert quality["row_count"] == body["row_count"] == 25
        assert (
            quality["accepted_records"]
            + quality["quarantined_records"]
            + quality["rejected_records"]
            == 25
        )
        stored = counts(db, uuid.UUID(imported["id"]))
        assert stored["canonical"] == stored["lineage"] == quality["accepted_records"], fixture
        assert stored["items"] == quality["quarantined_records"] + quality["rejected_records"]
        listed = api.get(f"{IMPORTS}/{imported['id']}/issues", headers=steward).json()
        assert listed["total"] == issues, fixture
        # Phase 12: validation also records its reconciliation, in the same transaction.
        assert sorted((e["action"], e["outcome"]) for e in events(db, c)) == [
            ("import.reconcile", "SUCCESS"),
            ("import.validate", "SUCCESS"),
        ]
        if status == "FAILED":  # a file-level BLOCKER: nothing accepted, every row held
            assert quality["rejected_records"] == 25 and stored["canonical"] == 0
            assert listed["items"][0]["code"] == "REQUIRED_COLUMN_MISSING"
        # Validated once: a second call is 409 already_validated, audited, nothing changes.
        c = cid()
        again = validate_import(api, steward, imported["id"], **{"X-Correlation-ID": c})
        assert (again.status_code, again.json()["code"]) == (409, "already_validated")
        assert [e["outcome"] for e in events(db, c)] == ["DENIED"]
        assert counts(db, uuid.UUID(imported["id"])) == stored

    # The early MAPPING_REQUIRED import validates now, and gives the same payload hashes as
    # the clean import of the same artifact (D-22), as does the dataset hash.
    assert validate_import(api, steward, early["id"]).json()["status"] == "COMPLETED"
    clean_imports = sql(
        db,
        "SELECT j.id FROM import_job j JOIN import_artifact a ON a.import_job_id = j.id"
        " WHERE j.status = 'COMPLETED' ORDER BY j.created_at",
    )
    assert len(clean_imports) == 2
    hashes = [
        [
            (row["ordinal"], row["payload_sha256"])
            for row in sql(
                db,
                "SELECT ordinal, payload_sha256 FROM canonical_record WHERE import_job_id = $1"
                " ORDER BY ordinal",
                j["id"],
            )
        ]
        for j in clean_imports
    ]
    assert hashes[0] == hashes[1] and len(hashes[0]) == 25
    manifests = [
        api.get(f"{IMPORTS}/{j['id']}", headers=steward).json()["manifest"] for j in clean_imports
    ]
    assert manifests[0]["canonical_sha256"] == manifests[1]["canonical_sha256"]
    # Typed strings in the database too: no JSON number in any canonical payload.
    kinds = sql(
        db,
        "SELECT DISTINCT jsonb_typeof(v) AS t FROM canonical_record, jsonb_each(payload) e(k, v)",
    )
    assert {k["t"] for k in kinds} <= {"string", "null"}

    # Issues: filters by severity, code and field (schema field or source column), pages.
    mixed = sql(
        db,
        "SELECT j.id FROM import_job j WHERE j.status = 'QUARANTINED' LIMIT 1",
    )[0]["id"]
    base = f"{IMPORTS}/{mixed}/issues"

    def page(**params: Any) -> Any:
        r = api.get(base, params=params, headers=dev_token(api, "risk_analyst"))
        assert r.status_code == 200, r.text
        return r.json()

    everything = page()
    assert [i["row"] for i in everything["items"]] == [3, 4, 5, 6, 8]
    assert everything["severity_counts"] == {"ERROR": 5}
    assert all(
        set(i) >= {"code", "severity", "disposition", "field", "message"}
        for i in everything["items"]
    )
    assert page(severity="ERROR")["total"] == 5 and page(severity="WARNING")["total"] == 0
    assert [i["row"] for i in page(code="SCALE_EXCEEDED")["items"]] == [4]
    assert [i["code"] for i in page(field="notional")["items"]] == [
        "SCALE_EXCEEDED",
        "REQUIRED_VALUE_MISSING",
    ]
    assert [i["row"] for i in page(field="currency", severity="ERROR")["items"]] == [6]
    second = page(limit=2, offset=2)
    assert (second["total"], [i["row"] for i in second["items"]]) == (5, [5, 6])
    assert api.get(base, params={"code": "NOPE"}, headers=steward).status_code == 422
    # Messages name fields and rules, never a value from the file.
    raw = sample("instrument__mixed_errors.csv").decode()
    for value in ("2026-02-30", "510544.10001", "EURO", "INS-000001"):
        assert value in raw
        assert all(value not in i["message"] for i in everything["items"]), value
