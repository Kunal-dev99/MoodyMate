"""Phase 12 (BE-12.1 – BE-12.4): reconciliation and drift detection, in three essential tests.

1. DB guards: runs and items are append-only, an item can neither sit non-MATCHED under a
   MATCHED run nor carry an unexplained gap under a run that is not BROKEN, drift events
   move only OPEN -> reviewed once, TRUNCATE is refused, the frozen enum copies match the
   code, and the downgrade refuses while runs or events exist (then 0015's still holds).
2. Reconciliation: the clean fixture is MATCHED, the quarantine fixture is REVIEW_REQUIRED
   with the exact Decimal variance as a string, a wrong expected_row_count is BROKEN; a
   table test proves the outcome is never MATCHED while an item is not (in code and in
   every stored run); figures are masked by classification and are never floats.
3. Drift and review: a first import has no events; the extra-column fixture after a clean
   baseline raises exactly one COLUMN_ADDED and no schema, contract or mapping changes; the
   setting off writes none; the detector's five types on synthetic profiles; the review is
   recorded with the reviewer and audit event, a second one is 409, risk_analyst is 403,
   ACTION_REQUIRED links to the proposal page and creates no proposal.
"""

import importlib.util
import inspect
import itertools
import json
import uuid
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
from app.main import create_app
from app.mapping import service as mapping_service
from app.mapping.constants import DRIFT_TRANSITIONS, DriftStatus, DriftType
from app.mapping.service import SUMMARY_KEYS, detect_drift
from app.reconciliation import service as reconciliation_service
from app.reconciliation.constants import (
    COUNT_CHECKS,
    ReconciliationCheck,
    ReconciliationItemStatus,
    ReconciliationOutcome,
)
from app.reconciliation.service import ItemSpec, outcome_of
from app.tests.conftest import (
    _PHASE12_CLEANUP,
    alembic_config,
    dev_token,
    insert_tenant,
    script,
    sql,
)
from app.tests.helpers import (
    IMPORTS,
    cid,
    events,
    import_sample,
    instrument_mapping,
    sample,
    schema_id,
    token_for,
    validate_import,
)

pytestmark = pytest.mark.db

IMMUTABLE = asyncpg.InsufficientPrivilegeError
CHECK = asyncpg.CheckViolationError
HEAD = str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"


def migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline_and_mapping(api: TestClient, db: str) -> tuple[dict[str, str], dict[str, Any]]:
    """The clean instrument file, imported, mapped (v1 ACTIVE) and validated COMPLETED: the
    source's drift baseline. Returns the steward's headers and that import."""
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    first = import_sample(api, steward, db, sample("instrument.csv"))
    instrument_mapping(api, db, steward, approver, first["id"])
    done = validate_import(api, steward, first["id"])
    assert done.status_code == 200 and done.json()["status"] == "COMPLETED", done.text
    return steward, done.json()


def reconciliation(api: TestClient, headers: dict[str, str], import_id: str) -> Any:
    r = api.get(f"{IMPORTS}/{import_id}/reconciliation", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def by_check(body: Any) -> dict[tuple[str, str | None], Any]:
    return {(i["check"], i["column"]): i for i in body["items"]}


def notional_total(data: bytes, rows: set[int] | None = None) -> Decimal:
    """The fixture's notional total (rows 1-based after the header), computed here."""
    lines = data.decode().splitlines()[2:]  # marker and header
    total = Decimal(0)
    for ordinal, line in enumerate(lines, start=1):
        value = line.split(",")[5]
        if value and (rows is None or ordinal in rows):
            total += Decimal(value)
    return total


# --- 1. DB guards, frozen copies and the downgrade refusal (BE-12.1) -----------------------------


def test_phase12_tables_are_guarded_in_the_database(api: TestClient, db: str) -> None:
    steward, clean = baseline_and_mapping(api, db)
    held = import_sample(api, steward, db, sample("instrument__mixed_errors.csv"))
    assert validate_import(api, steward, held["id"]).json()["status"] == "QUARANTINED"
    wider = import_sample(api, steward, db, sample("instrument__unknown_extra_column.csv"))

    # The migration's frozen copies equal the code (D-06, D-23).
    m16 = migration("0016_reconciliation_and_drift")
    assert m16.OUTCOMES == tuple(ReconciliationOutcome)
    assert m16.ITEM_STATUSES == tuple(ReconciliationItemStatus)
    assert m16.CHECKS == tuple(ReconciliationCheck)
    assert set(m16.COUNT_CHECKS) == set(COUNT_CHECKS)
    assert m16.DRIFT_TYPES == tuple(DriftType)
    assert {k.value: sorted(v) for k, v in DRIFT_TRANSITIONS.items()} == {
        k: sorted(v) for k, v in m16.DRIFT_TRANSITIONS.items()
    }

    run = {
        r["import_job_id"]: r
        for r in sql(db, "SELECT id, import_job_id, outcome FROM reconciliation_run")
    }
    matched, review = run[uuid.UUID(clean["id"])], run[uuid.UUID(held["id"])]
    assert (matched["outcome"], review["outcome"]) == ("MATCHED", "REVIEW_REQUIRED")
    [event] = sql(
        db, "SELECT * FROM data_drift_event WHERE import_job_id = $1", uuid.UUID(wider["id"])
    )
    assert (event["drift_type"], event["column_name"]) == ("COLUMN_ADDED", "legacy_flag")

    # Runs and items: UPDATE, DELETE and TRUNCATE are refused, with no bypass setting.
    item = sql(db, "SELECT id FROM reconciliation_item LIMIT 1")[0]["id"]
    for table, row_id in (("reconciliation_run", matched["id"]), ("reconciliation_item", item)):
        for statement in (
            f"UPDATE {table} SET created_by = 'x' WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(IMMUTABLE, match="append-only"):
                sql(db, statement, row_id)
        with pytest.raises(IMMUTABLE, match="append-only"):
            sql(db, f"TRUNCATE {table} CASCADE")
    with pytest.raises(IMMUTABLE, match="append-only"):  # an outcome never moves
        sql(db, "UPDATE reconciliation_run SET outcome = 'MATCHED' WHERE id = $1", review["id"])

    # Item inserts: never non-MATCHED under MATCHED, never an unexplained gap unless BROKEN;
    # the status follows the variance; the variance is source - target; enums are CHECKed.
    insert_item = (
        "INSERT INTO reconciliation_item (id, reconciliation_run_id, sequence, check_code,"
        " column_name, classification, source_value, target_value, held_value, variance,"
        " tolerance, status, details, created_by) VALUES (gen_random_uuid(), $1, $2, $3,"
        " 'notional', 'public', $4, $5, $6, $7, 0, $8, '{}', 't')"
    )
    D = Decimal
    for run_id, values, error, match in (
        (
            matched["id"],
            ("CONTROL_TOTAL", D(2), D(1), D(1), D(1), "VARIANCE"),
            IMMUTABLE,
            "MATCHED",
        ),
        (review["id"], ("CONTROL_TOTAL", D(2), D(1), None, D(1), "VARIANCE"), IMMUTABLE, "BROKEN"),
        (review["id"], ("CONTROL_TOTAL", D(2), D(1), D(1), D(1), "MATCHED"), CHECK, "status_of"),
        (review["id"], ("CONTROL_TOTAL", D(2), D(1), D(5), D(5), "VARIANCE"), CHECK, "_variance"),
        (review["id"], ("NOPE", D(1), D(1), None, D(0), "MATCHED"), CHECK, "check_code"),
    ):
        with pytest.raises(error, match=match):
            sql(db, insert_item, run_id, 99, *values)
    # A run reconciles a validated import of its own tenant only.
    with pytest.raises(IMMUTABLE, match="validated import"):
        sql(
            db,
            "INSERT INTO reconciliation_run (id, tenant_id, import_job_id, outcome,"
            " tolerance_policy, reconciler_version, created_by) SELECT gen_random_uuid(),"
            " tenant_id, id, 'MATCHED', '{}', 1, 't' FROM import_job WHERE id = $1",
            uuid.UUID(wider["id"]),
        )

    # Drift events: content frozen, never deleted or truncated, reviewed once from OPEN.
    for statement in (
        "UPDATE data_drift_event SET column_name = 'renamed' WHERE id = $1",
        "UPDATE data_drift_event SET observed_summary = '{}' WHERE id = $1",
        "UPDATE data_drift_event SET status = 'OPEN', review_comment = 'x' WHERE id = $1",
    ):
        with pytest.raises(IMMUTABLE):
            sql(db, statement, event["id"])
    with pytest.raises(IMMUTABLE, match="DELETE"):
        sql(db, "DELETE FROM data_drift_event WHERE id = $1", event["id"])
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE data_drift_event CASCADE")
    with pytest.raises(CHECK, match="ck_data_drift_event_review"):  # a review names its fields
        sql(db, "UPDATE data_drift_event SET status = 'ACKNOWLEDGED' WHERE id = $1", event["id"])
    review_sql = (
        "UPDATE data_drift_event SET status = $2, reviewed_by = 'user:x', review_comment = $3,"
        " reviewed_at = now() WHERE id = $1"
    )
    sql(db, review_sql, event["id"], "ACKNOWLEDGED", "ok")
    after = sql(db, "SELECT * FROM data_drift_event WHERE id = $1", event["id"])[0]
    review_fields = {"status", "reviewed_by", "review_comment", "reviewed_at", "updated_at"}
    assert after["status"] == "ACKNOWLEDGED"
    assert {k: v for k, v in after.items() if k not in review_fields} == {
        k: v for k, v in event.items() if k not in review_fields
    }
    for status, comment in (("ACTION_REQUIRED", "ok"), ("ACKNOWLEDGED", "changed")):
        with pytest.raises(IMMUTABLE, match="reviewed once"):
            sql(db, review_sql, event["id"], status, comment)
    insert_event = (
        "INSERT INTO data_drift_event (id, tenant_id, import_job_id, sequence,"
        " baseline_import_id, observed_snapshot_id, baseline_snapshot_id, drift_type,"
        " column_name, observed_summary, baseline_summary, thresholds, detector_version,"
        " status, reviewed_by, review_comment, reviewed_at, created_by) SELECT"
        " gen_random_uuid(), tenant_id, import_job_id, 9, $2, observed_snapshot_id, $3, $4,"
        " 'other', observed_summary, observed_summary, thresholds, 1, $5, $6::text, $6::text,"
        " CASE WHEN $6::text IS NULL THEN NULL ELSE now() END, 't'"
        " FROM data_drift_event WHERE id = $1"
    )
    snapshots = {
        r["import_job_id"]: r["id"] for r in sql(db, "SELECT * FROM data_profile_snapshot")
    }
    base, quarantined = uuid.UUID(clean["id"]), uuid.UUID(held["id"])
    for args, error, match in (
        ((base, snapshots[base], "COLUMN_ADDED", "ACKNOWLEDGED", "x"), IMMUTABLE, "created OPEN"),
        (  # the baseline must be COMPLETED or COMPLETED_WITH_WARNINGS
            (quarantined, snapshots[quarantined], "COLUMN_ADDED", "OPEN", None),
            IMMUTABLE,
            "completed import",
        ),
        ((base, snapshots[base], "NOPE", "OPEN", None), CHECK, "drift_type"),
    ):
        with pytest.raises(error, match=match):
            sql(db, insert_event, event["id"], *args)

    # The downgrade refuses while runs or events exist, in one transaction: nothing changes.
    tables = ("reconciliation_run", "reconciliation_item", "data_drift_event")
    before = {t: sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in tables}
    try:
        with pytest.raises(Exception, match="reconciliation runs or drift events exist"):
            command.downgrade(alembic_config(db), "0015")
        assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == HEAD
        assert {t: sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in tables} == before
        # Without them, 0015's own refusal still holds (validation results are kept).
        script(db, _PHASE12_CLEANUP)
        with pytest.raises(Exception, match="validation results exist"):
            command.downgrade(alembic_config(db), "0014")
        assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == HEAD
    finally:
        command.upgrade(alembic_config(db), "head")


# --- 2. Reconciliation outcomes, exact variances and the never-MATCHED table (BE-12.2) ----------


def test_reconciliation_outcomes_and_exact_variances(api: TestClient, db: str) -> None:
    steward, clean = baseline_and_mapping(api, db)

    # The clean fixture is MATCHED: every row accepted, totals equal, all figures text.
    body = reconciliation(api, steward, clean["id"])
    items = by_check(body)
    assert body["outcome"] == "MATCHED" and body["import_status"] == "COMPLETED"
    assert list(items) == [("ROW_COUNT", None), ("CONTROL_TOTAL", "notional")]
    total = str(notional_total(sample("instrument.csv")))
    assert {k: items[("ROW_COUNT", None)][k] for k in ("source", "target", "held", "variance")} == {
        "source": "25",
        "target": "25",
        "held": "0",
        "variance": "0",
    }
    notional = items[("CONTROL_TOTAL", "notional")]
    assert (notional["source"], notional["target"], notional["variance"]) == (total, total, "0.00")
    assert (notional["unit"], notional["classification"]) == ("currency", "confidential")
    assert body["tolerance_policy"]["tolerance"] == "0" and body["tolerance_policy"]["provisional"]

    # The quarantine fixture is REVIEW_REQUIRED: every gap is explained by held rows, and the
    # variance is the exact Decimal text of the held rows' notional (rows 3, 4, 5, 6; row 8's
    # is empty), never rounded.
    data = sample("instrument__mixed_errors.csv")
    mixed = import_sample(api, steward, db, data)
    c = cid()
    assert validate_import(api, steward, mixed["id"], **{"X-Correlation-ID": c}).status_code == 200
    body = reconciliation(api, steward, mixed["id"])
    items = by_check(body)
    assert (body["outcome"], body["import_status"]) == ("REVIEW_REQUIRED", "QUARANTINED")
    rows = items[("ROW_COUNT", None)]
    assert (rows["source"], rows["target"], rows["held"], rows["variance"]) == (
        "25",
        "20",
        "5",
        "5",
    )
    assert rows["status"] == "VARIANCE" and rows["tolerance"] == "0"
    notional = items[("CONTROL_TOTAL", "notional")]
    assert notional["variance"] == "4237607.14001" == str(notional_total(data, {3, 4, 5, 6}))
    assert notional["held"] == notional["variance"] and notional["status"] == "VARIANCE"
    assert notional["source"] == str(notional_total(data))
    assert notional["details"] == {
        "parseable_values": 24,
        "unparseable_values": 0,
        "empty_values": 1,
    }
    [audit] = [e for e in events(db, c) if e["action"] == "import.reconcile"]
    assert json.loads(audit["details"])["outcome"] == "REVIEW_REQUIRED"

    # A wrong expected_row_count is BROKEN; a declared total equal to the file's matches.
    declared = import_sample(
        api,
        steward,
        db,
        sample("instrument.csv"),
        expected_row_count="24",
        control_totals=json.dumps({"notional": total}),
    )
    assert validate_import(api, steward, declared["id"]).json()["status"] == "COMPLETED"
    body = reconciliation(api, steward, declared["id"])
    items = by_check(body)
    assert body["outcome"] == "BROKEN"
    expected = items[("EXPECTED_ROW_COUNT", None)]
    assert (expected["source"], expected["target"], expected["variance"]) == ("24", "25", "-1")
    assert (expected["held"], expected["status"]) == (None, "VARIANCE")
    assert items[("DECLARED_CONTROL_TOTAL", "notional")]["status"] == "MATCHED"

    # Figures are exact text, never JSON numbers; a role not cleared for the column's
    # classification sees counts but masked totals (BE-3.5).
    for role in ("risk_analyst", "auditor"):
        seen = by_check(reconciliation(api, dev_token(api, role), mixed["id"]))
        assert seen[("ROW_COUNT", None)]["variance"] == "5"
        for key in ("source", "target", "held", "variance"):
            assert seen[("CONTROL_TOTAL", "notional")][key] == {
                "masked": True,
                "classification": "confidential",
            }, (role, key)
    # No reconciliation before validation: 404 reconciliation_not_found.
    pending = import_sample(api, steward, db, sample("instrument.csv"))
    r = api.get(f"{IMPORTS}/{pending['id']}/reconciliation", headers=steward)
    assert (r.status_code, r.json()["code"]) == (404, "reconciliation_not_found")

    # Table test: across every combination of up to three items of each kind, the outcome is
    # MATCHED only when every item is MATCHED, and BROKEN exactly when a gap is unexplained.
    D = Decimal
    kinds = [
        ItemSpec(ReconciliationCheck.ROW_COUNT, D(5), D(5), D(0), D(0)),  # MATCHED
        ItemSpec(ReconciliationCheck.CONTROL_TOTAL, D("1.5"), D("1.4"), D(0), D("0.1")),  # within
        ItemSpec(ReconciliationCheck.ROW_COUNT, D(5), D(3), D(2), D(0)),  # VARIANCE, held
        ItemSpec(ReconciliationCheck.EXPECTED_ROW_COUNT, D(4), D(5), None, D(0)),  # unexplained
        ItemSpec(ReconciliationCheck.CONTROL_TOTAL, D("9.99"), D(0), D("9.98"), D("0.01")),
    ]
    assert [k.status for k in kinds] == [
        "MATCHED",
        "WITHIN_TOLERANCE",
        "VARIANCE",
        "VARIANCE",
        "VARIANCE",
    ]
    checked = 0
    for size in (1, 2, 3):
        for combo in itertools.product(kinds, repeat=size):
            outcome = outcome_of(combo)
            all_matched = all(i.status == "MATCHED" for i in combo)
            assert (outcome == "MATCHED") == all_matched, combo
            assert (outcome == "BROKEN") == any(i.broken for i in combo), combo
            checked += 1
    assert checked == 5 + 25 + 125
    # ... and in every run stored by this test.
    stored = sql(
        db,
        "SELECT r.outcome, array_agg(i.status) AS statuses FROM reconciliation_run r"
        " JOIN reconciliation_item i ON i.reconciliation_run_id = r.id GROUP BY r.id, r.outcome",
    )
    assert len(stored) == 3
    for run in stored:
        assert (run["outcome"] == "MATCHED") == all(s == "MATCHED" for s in run["statuses"])
    # No float anywhere in the reconciler.
    for part in reconciliation_service.RECONCILER_PARTS:
        assert "float" not in inspect.getsource(getattr(reconciliation_service, part)), part


# --- 3. Drift detection, the setting, and the review (BE-12.3, BE-12.4) ---------------------------


def governed(db: str) -> dict[str, Any]:
    """Every schema, contract and mapping version with its status and hash, and the
    proposals: what drift detection and review must never change."""
    return {
        t: [dict(r) for r in sql(db, f"SELECT id, status, content_sha256 FROM {t} ORDER BY id")]
        for t in ("schema_version", "source_contract", "mapping_version")
    } | {
        "schema_field": sql(db, "SELECT count(*) AS n FROM schema_field")[0]["n"],
        "schema_change_proposal": sql(db, "SELECT count(*) AS n FROM schema_change_proposal")[0][
            "n"
        ],
    }


def drift(api: TestClient, headers: dict[str, str], import_id: str) -> Any:
    r = api.get(f"{IMPORTS}/{import_id}/drift", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_drift_detection_setting_and_review(api: TestClient, db: str, artifact_dir: Path) -> None:
    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    c = cid()
    first = import_sample(api, steward, db, sample("instrument.csv"), cid=c)
    # A first import has no baseline: nothing is compared, written or audited as detection.
    assert drift(api, steward, first["id"]) == {
        "import_id": first["id"],
        "baseline_import_id": None,
        "items": [],
        "total": 0,
        "status_counts": {},
    }
    assert sorted(e["action"] for e in events(db, c)) == ["import.create", "import.profile"]
    instrument_mapping(api, db, steward, approver, first["id"])
    assert validate_import(api, steward, first["id"]).json()["status"] == "COMPLETED"

    # The extra-column fixture after the clean baseline: exactly one COLUMN_ADDED, and no
    # schema, contract or mapping changes (hashes, statuses and counts equal).
    state = governed(db)
    c = cid()
    wider = import_sample(api, steward, db, sample("instrument__unknown_extra_column.csv"), cid=c)
    [detected] = [e for e in events(db, c) if e["action"] == "import.drift_detect"]
    assert json.loads(detected["details"]) == {
        "baseline_import_id": first["id"],
        "event_count": 1,
        "drift_types": {"COLUMN_ADDED": 1},
        "detector_version": 1,
    }
    page = drift(api, steward, wider["id"])
    [event] = page["items"]
    assert (page["total"], page["baseline_import_id"]) == (1, first["id"])
    assert (event["drift_type"], event["column"], event["status"]) == (
        "COLUMN_ADDED",
        "legacy_flag",
        "OPEN",
    )
    assert event["baseline"] is None and event["observed"]["null_rate"] == "0.0000"
    assert event["observed"]["candidate_types"] == ["STRING"]  # Y / N
    assert event["classification"] == "confidential"  # unmapped: the source's default
    assert sorted(event["allowed_outcomes"]) == ["ACKNOWLEDGED", "ACTION_REQUIRED"]
    assert event["thresholds"] == {
        "null_rate_threshold": "0.10",
        "range_threshold": "0.25",
        "provisional": True,
    }
    stored = sql(db, "SELECT observed_summary FROM data_drift_event")[0]["observed_summary"]
    assert set(json.loads(stored)) == {*SUMMARY_KEYS, "suggested_type"}  # no top values
    assert governed(db) == state

    # With DRIFT_DETECTION_ENABLED off, no event (and no detection) is written.
    off = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, DRIFT_DETECTION_ENABLED=False
    )
    count = "SELECT count(*) AS n FROM data_drift_event"
    with TestClient(create_app(off)) as quiet:
        c = cid()
        again = import_sample(
            quiet, steward, db, sample("instrument__unknown_extra_column.csv"), cid=c
        )
        assert drift(quiet, steward, again["id"])["total"] == 0
    assert "import.drift_detect" not in [e["action"] for e in events(db, c)]
    assert sql(db, count)[0]["n"] == 1

    # The detector itself, on synthetic profiles: each of the five types, exactly.
    def col(name: str, types: list[str], rate: str, low: str | None, high: str | None) -> Any:
        return {
            "name": name,
            "candidate_types": types,
            "null_rate": rate,
            "min": low,
            "max": high,
            "value_count": 10,
        }

    num = ["DECIMAL", "STRING"]
    base = [col("a", num, "0.0000", "10", "20"), col("b", num, "0.0000", "1", "2")]
    for observed, expected in (
        ([col("a", num, "0.0000", "10", "20"), col("b", num, "0.0000", "1", "2")], []),
        ([col("a", ["STRING"], "0.0000", None, None), base[1]], [("TYPE_CHANGED", "a")]),
        ([col("a", num, "0.1001", "10", "20"), base[1]], [("NULL_RATE_SHIFT", "a")]),
        ([col("a", num, "0.1000", "7.5", "22.5"), base[1]], []),  # at the thresholds
        ([col("a", num, "0.0000", "7.4999", "20"), base[1]], [("RANGE_SHIFT", "a")]),
        ([base[0]], [("COLUMN_REMOVED", "b")]),
        ([*base, col("c", num, "0.0000", "1", "1")], [("COLUMN_ADDED", "c")]),
    ):
        found = detect_drift(observed, base, Decimal("0.10"), Decimal("0.25"))
        assert [(f.drift_type.value, f.column) for f in found] == expected, expected
    assert {t.value for t in DriftType} == {
        "COLUMN_ADDED",
        "COLUMN_REMOVED",
        "TYPE_CHANGED",
        "NULL_RATE_SHIFT",
        "RANGE_SHIFT",
    }
    for part in mapping_service.DRIFT_PARTS:
        assert "float" not in inspect.getsource(getattr(mapping_service, part)), part

    # The review (quality:review): risk_analyst and approver are 403; a comment is required.
    url = f"/api/v1/drift-events/{event['id']}/review"
    body = {"outcome": "ACTION_REQUIRED", "comment": "New column: propose it on the schema."}
    for role in ("risk_analyst", "approver"):
        r = api.post(url, json=body, headers=dev_token(api, role))
        assert (r.status_code, r.json()["details"]["required_permission"]) == (
            403,
            "quality:review",
        ), role
    for bad in (
        {"outcome": "ACTION_REQUIRED"},
        {**body, "comment": "  "},
        {**body, "outcome": "OPEN"},
    ):
        assert api.post(url, json=bad, headers=steward).status_code == 422, bad
    # A foreign tenant's steward gets the same 404 as a random id (D-04), audited DENIED.
    outsider = token_for(
        db, "dev:drift-outsider", ("data_steward",), tenant_id=insert_tenant(db, "drift-other")
    )
    c = cid()
    foreign = api.post(url, json=body, headers={**outsider, "X-Correlation-ID": c})
    random = api.post(f"/api/v1/drift-events/{uuid.uuid4()}/review", json=body, headers=steward)
    assert foreign.status_code == random.status_code == 404
    assert foreign.json()["message"] == random.json()["message"]
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
        ("drift_event.review", "DENIED")
    ]

    # ACTION_REQUIRED: recorded with the reviewer and the audit event; it returns a link to
    # the Phase 5 proposal page (target schema prefilled) and creates nothing else.
    me = api.get("/api/v1/me", headers=steward).json()["user_id"]
    c = cid()
    r = api.post(url, json=body, headers={**steward, "X-Correlation-ID": c})
    assert r.status_code == 200, r.text
    reviewed = r.json()
    assert (reviewed["status"], reviewed["reviewed_by"], reviewed["review_comment"]) == (
        "ACTION_REQUIRED",
        f"user:{me}",
        body["comment"],
    )
    assert reviewed["reviewed_at"] and reviewed["allowed_outcomes"] == []
    instrument = schema_id(db, "instrument")
    assert reviewed["proposal_link"] == {
        "kind": "SCHEMA_CHANGE_PROPOSAL",
        "href": f"/schemas/proposals/new?schema={instrument}",
        "schema_id": str(instrument),
    }
    [audit] = events(db, c)
    assert (audit["action"], audit["outcome"], audit["resource_id"]) == (
        "drift_event.review",
        "SUCCESS",
        event["id"],
    )
    assert {k: v for k, v in json.loads(audit["details"]).items() if k != "comment"} == {
        "import_id": wider["id"],
        "drift_type": "COLUMN_ADDED",
        "from": "OPEN",
        "to": "ACTION_REQUIRED",
    }
    assert governed(db) == state  # no proposal, no schema, contract or mapping change
    assert drift(api, steward, wider["id"])["items"][0] == reviewed
    # Values (min, max) are masked for a role not cleared for the column's classification.
    seen = drift(api, dev_token(api, "risk_analyst"), wider["id"])["items"][0]["observed"]
    assert seen["min"] == seen["max"] == {"masked": True, "classification": "confidential"}

    # A second review is 409 already_reviewed, audited DENIED, and changes nothing.
    c = cid()
    again = api.post(
        url,
        json={"outcome": "ACKNOWLEDGED", "comment": "Second look."},
        headers={**steward, "X-Correlation-ID": c},
    )
    assert (again.status_code, again.json()["code"]) == (409, "already_reviewed")
    [denied] = events(db, c)
    assert (denied["outcome"], json.loads(denied["details"])["reason"]) == (
        "DENIED",
        "already_reviewed",
    )
    assert drift(api, steward, wider["id"])["items"][0] == reviewed
    assert sql(db, count)[0]["n"] == 1
    assert DriftStatus.OPEN not in DRIFT_TRANSITIONS[DriftStatus.ACTION_REQUIRED]
