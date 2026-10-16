"""BE-9.2 (DI-06, D-21, D-25): the deterministic profiler.

* Pure-function tests: candidate types, exact Decimal null rates, numeric and date ranges,
  precision/scale, date formats, currency patterns, capped distinct counts and top values,
  and the D-25 row semantics (missing cells, extra cells, blank rows, JSON nulls). No float
  is used anywhere: the profiler's source holds none and its output holds none.
* The golden file: the stored profile of `samples/synthetic/instrument.csv` equals
  `golden/instrument_profile.json` exactly, and an independent count over the CSV agrees.
* Profiling the same artifact twice gives the same `content_sha256`.
* The status path: RECEIVED -> PROFILING -> MAPPING_REQUIRED (no mapping can exist before
  Phase 10), or RECEIVED when a usable ACTIVE mapping exists; audited `import.profile`.
"""

import ast
import csv
import inspect
import io
import json
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.ingestion import service as ingestion_service
from app.ingestion.adapters import AdapterOptions, Limits, parse_upload
from app.ingestion.constants import SourceFormat
from app.ingestion.service import PROFILER_VERSION, ProfileInput, Profiler, profile_rows
from app.main import create_app
from app.tests.conftest import dev_token, sql
from app.tests.test_imports import SAMPLES, cid, events, seeded_source_id, source_for, upload
from scripts.make_synthetic_samples import OUT

GOLDEN = Path(__file__).parent / "golden" / "instrument_profile.json"


def prof(columns: list[str], *rows: dict[str, str], **kw: Any) -> Any:
    return profile_rows(columns, [(r, [], []) for r in rows], **kw)


def col(result: Any, name: str) -> dict[str, Any]:
    return next(c for c in result.columns if c["name"] == name)


def floats_in(value: Any) -> list[Any]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [f for v in value.values() for f in floats_in(v)]
    if isinstance(value, list):
        return [f for v in value for f in floats_in(v)]
    return []


# --- pure function ------------------------------------------------------------------------------


def test_candidate_types_most_specific_first() -> None:
    r = prof(
        ["i", "d", "b", "dt", "ts", "s", "empty"],
        {"i": "12", "d": "1.50", "b": "TRUE", "dt": "2031-02-17", "ts": "2031-02-17T10:00:00Z"},
        {"i": "-3", "d": "7", "b": "false", "dt": "2030-12-31", "ts": "2031-02-17 10:00"},
        {"s": "x", "i": "+0", "d": "-0.001", "b": "true", "dt": "2029-01-01"},
    )
    assert col(r, "i")["candidate_types"] == ["INTEGER", "DECIMAL", "STRING"]
    assert col(r, "d")["candidate_types"] == ["DECIMAL", "STRING"]
    assert col(r, "b")["candidate_types"] == ["BOOLEAN", "STRING"]
    assert col(r, "dt")["candidate_types"] == ["DATE", "STRING"]
    assert col(r, "ts")["candidate_types"] == ["TIMESTAMP", "STRING"]
    assert col(r, "s")["candidate_types"] == ["STRING"]
    assert col(r, "empty")["candidate_types"] == []  # nothing observed, nothing suggested


@pytest.mark.parametrize(
    "value", ["1e5", "1.", ".5", "1,000", " 1", "1 ", "NaN", "Infinity", "١٢", "0x10", "--1"]
)
def test_only_plain_decimal_text_is_numeric(value: str) -> None:
    assert col(prof(["a"], {"a": value}), "a")["candidate_types"] == ["STRING"]


def test_invalid_calendar_dates_are_not_dates() -> None:
    r = prof(["a"], {"a": "2031-02-30"}, {"a": "2031-02-28"})
    assert col(r, "a")["candidate_types"] == ["STRING"]
    assert col(r, "a")["date_formats"] == {"YYYY-MM-DD": 1}


def test_null_rate_is_exact_decimal_text() -> None:
    r = prof(["a", "b"], {"a": "x", "b": ""}, {"a": "", "b": "  "}, {"a": "y"})
    a, b = col(r, "a"), col(r, "b")
    assert (a["null_count"], a["value_count"], a["null_rate"]) == (1, 2, "0.3333")
    # b: "" and whitespace are empty; a missing key is empty too.
    assert (b["null_count"], b["value_count"], b["null_rate"]) == (3, 0, "1.0000")
    assert prof(["a"]).columns[0]["null_rate"] is None  # no rows: no rate
    two_thirds = prof(["a"], {"a": ""}, {"a": ""}, {"a": "1"})
    assert col(two_thirds, "a")["null_rate"] == str(
        (Decimal(2) / Decimal(3)).quantize(Decimal("0.0001"))
    )


def test_numeric_range_compares_values_not_text() -> None:
    r = prof(["n"], {"n": "9.5"}, {"n": "10.25"}, {"n": "-0012.500"}, {"n": "10.250"})
    n = col(r, "n")
    assert (n["min"], n["max"]) == ("-0012.500", "10.25")  # first text of an equal value
    # NUMERIC(p, s) that holds every value: 2 integer digits + 3 decimals.
    assert (n["max_precision"], n["max_scale"]) == (5, 3)


def test_precision_and_scale_follow_numeric_semantics() -> None:
    r = prof(["n"], {"n": "0.0012"}, {"n": "100"})
    assert (col(r, "n")["max_precision"], col(r, "n")["max_scale"]) == (7, 4)


def test_ranges_are_only_reported_for_a_uniform_type() -> None:
    r = prof(["n", "d"], {"n": "1", "d": "2031-01-01"}, {"n": "x", "d": "soon"})
    for c in ("n", "d"):
        assert (col(r, c)["min"], col(r, c)["max"]) == (None, None)
        assert (col(r, c)["max_precision"], col(r, c)["max_scale"]) == (None, None)


def test_date_formats_and_date_range() -> None:
    r = prof(
        ["a", "b", "c"],
        {"a": "2031-02-17", "b": "05/06/2030", "c": "17.02.2031"},
        {"a": "2029-12-01", "b": "13/06/2030", "c": "2031-02-17"},
    )
    a, b, c = col(r, "a"), col(r, "b"), col(r, "c")
    assert a["date_formats"] == {"YYYY-MM-DD": 2}
    assert (a["min"], a["max"]) == ("2029-12-01", "2031-02-17")
    # Ambiguous day/month: both formats seen once; only DD/MM/YYYY holds every value.
    assert b["date_formats"] == {"DD/MM/YYYY": 2, "MM/DD/YYYY": 1}
    assert b["candidate_types"] == ["DATE", "STRING"]
    assert (b["min"], b["max"]) == ("2030-06-05", "2030-06-13")
    # Two formats, neither for every value: seen, but not a DATE column.
    assert c["date_formats"] == {"DD.MM.YYYY": 1, "YYYY-MM-DD": 1}
    assert c["candidate_types"] == ["STRING"]


def test_timestamp_range_needs_one_kind_of_offset() -> None:
    aware = prof(["t"], {"t": "2031-02-17T10:00:00+01:00"}, {"t": "2031-02-17T09:30:00Z"})
    assert (col(aware, "t")["min"], col(aware, "t")["max"]) == (
        "2031-02-17T09:00:00+00:00",
        "2031-02-17T09:30:00+00:00",
    )
    mixed = prof(["t"], {"t": "2031-02-17T10:00:00"}, {"t": "2031-02-17T09:30:00Z"})
    assert col(mixed, "t")["candidate_types"] == ["TIMESTAMP", "STRING"]
    assert (col(mixed, "t")["min"], col(mixed, "t")["max"]) == (None, None)


def test_currency_pattern_hits() -> None:
    r = prof(
        ["c"],
        {"c": "EUR"},
        {"c": "usd"},
        {"c": "EUR 1,234.50"},
        {"c": "-12.5 CHF"},
        {"c": "€100"},
        {"c": "EURO"},
        {"c": "INS-000001"},  # an identifier, not an amount
        {"c": "12.50€"},
    )
    assert col(r, "c")["currency_pattern_hits"] == {"ISO_4217_CODE": 1, "AMOUNT_WITH_CURRENCY": 4}


def test_lengths_count_characters() -> None:
    r = prof(["a"], {"a": "é" * 3}, {"a": "ab"}, {"a": ""})
    assert col(r, "a")["max_length"] == 3
    assert prof(["a"], {"a": ""}).columns[0]["max_length"] is None


def test_top_values_order_by_count_then_value() -> None:
    rows = [{"a": v} for v in ["b", "a", "c", "b", "a", "b", ""]]
    r = prof(["a"], *rows)
    assert col(r, "a")["top_values"] == [
        {"value": "b", "count": 3, "truncated": False},
        {"value": "a", "count": 2, "truncated": False},
        {"value": "c", "count": 1, "truncated": False},
    ]
    assert (col(r, "a")["distinct_count"], col(r, "a")["distinct_capped"]) == (3, False)
    assert col(r, "a")["top_values_exact"] is True


def test_top_values_are_capped_and_long_values_truncated() -> None:
    rows = [{"a": f"v{i:03d}" + "x" * 200} for i in range(30)]
    a = col(prof(["a"], *rows), "a")
    assert len(a["top_values"]) == ingestion_service.PROFILE_TOP_VALUES
    first = a["top_values"][0]
    assert first["truncated"] is True
    assert len(first["value"]) == ingestion_service.PROFILE_VALUE_MAX_CHARS
    assert first["value"].startswith("v000")


def test_distinct_count_is_capped_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingestion_service, "PROFILE_DISTINCT_CAP", 3)
    rows = [{"a": v} for v in ["p", "q", "r", "s", "t", "p", "s"]]
    a = col(prof(["a"], *rows), "a")
    assert (a["distinct_count"], a["distinct_capped"], a["top_values_exact"]) == (3, True, False)
    # Values first seen after the cap are not tracked; tracked ones keep counting.
    assert [t["value"] for t in a["top_values"]] == ["p", "q", "r"]
    assert a["top_values"][0]["count"] == 2


def test_row_semantics_of_d25() -> None:
    """Missing cells and JSON nulls are empty; extra cells and blank rows are counted, never
    dropped or invented."""
    rows: list[ProfileInput] = [
        ({"a": "1", "b": "2"}, [], []),
        ({"a": "1"}, [], [{"code": "MISSING_CELLS", "column": 2}]),
        ({"a": "1", "b": "2"}, ["x", "y"], [{"code": "EXTRA_CELLS", "column": 3}]),
        ({}, [], [{"code": "BLANK_ROW", "column": None}]),
        ({"a": "3"}, [], [{"code": "NULL_VALUE", "column": 2}]),
        ({"a": "", "b": ""}, [], []),  # XLSX pads empty cells with ""
    ]
    r = profile_rows(["a", "b"], rows)
    assert r.row_count == 6
    assert r.summary == {
        "blank_rows": 1,
        "rows_with_missing_cells": 1,
        "rows_with_extra_cells": 1,
        "max_extra_cells": 2,
        "flag_counts": {"BLANK_ROW": 1, "EXTRA_CELLS": 1, "MISSING_CELLS": 1, "NULL_VALUE": 1},
    }
    assert (col(r, "a")["null_count"], col(r, "b")["null_count"]) == (2, 4)
    assert col(r, "b")["null_rate"] == "0.6667"


def test_columns_keep_file_order_and_positions() -> None:
    r = prof(["z", "a", "m"], {"z": "1", "a": "2", "m": "3"})
    assert [(c["name"], c["position"]) for c in r.columns] == [("z", 1), ("a", 2), ("m", 3)]


def test_same_rows_give_the_same_hash_and_any_change_another() -> None:
    rows = [{"a": "1", "b": "x"}, {"a": "2", "b": ""}]
    one, two = prof(["a", "b"], *rows), prof(["a", "b"], *rows)
    assert one.content_sha256 == two.content_sha256 and one.columns == two.columns
    assert len(one.content_sha256) == 64
    changed = prof(["a", "b"], rows[0], {"a": "3", "b": ""})
    assert changed.content_sha256 != one.content_sha256


def test_the_profiler_uses_no_float() -> None:
    """Decimal arithmetic only: no float() call, float literal or true division of ints in
    the profiler's source, and no float in its output."""
    source = inspect.getsource(Profiler) + "\n".join(
        inspect.getsource(getattr(ingestion_service, name))
        for name in ingestion_service.PROFILER_PARTS
    )
    tree = ast.parse(inspect.cleandoc("\n" + source) if source.startswith(" ") else source)
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Name) and node.id == "float"), ast.unparse(node)
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float))
    r = prof(["n", "p"], {"n": "1.5", "p": "0.1"}, {"n": "", "p": "0.2"}, {"n": "3"})
    assert floats_in({"columns": r.columns, "summary": r.summary}) == []
    assert col(r, "n")["null_rate"] == "0.3333"


# --- golden file and determinism through the import path -----------------------------------------


def snapshot_of(db: str, import_id: str) -> dict[str, Any]:
    [row] = sql(
        db,
        "SELECT profiler_version, row_count, column_count, summary::text AS summary,"
        " columns::text AS columns, content_sha256 FROM data_profile_snapshot"
        " WHERE import_job_id = $1",
        uuid.UUID(import_id),
    )
    return {
        "profiler_version": row["profiler_version"],
        "row_count": row["row_count"],
        "column_count": row["column_count"],
        "summary": json.loads(row["summary"]),
        "columns": json.loads(row["columns"]),
        "content_sha256": row["content_sha256"],
    }


@pytest.mark.db
def test_golden_profile_of_the_synthetic_csv(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    data = (OUT / "instrument.csv").read_bytes()
    r = upload(api, steward, seeded_source_id(db), data, filename="instrument.csv")
    assert r.status_code == 201, r.text
    stored = snapshot_of(db, r.json()["id"])
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert stored == golden
    assert golden["profiler_version"] == PROFILER_VERSION

    # The pure function over the same parsed rows gives the same profile.
    parsed = parse_upload(data, SourceFormat.CSV, AdapterOptions(), Limits())
    direct = profile_rows(
        parsed.columns,
        [
            (row.values, row.extras, [{"code": f.code.value} for f in row.flags])
            for row in parsed.rows
        ],
    )
    assert direct.columns == golden["columns"] and direct.content_sha256 == golden["content_sha256"]

    # An independent count over the CSV agrees with the golden file.
    lines = data.decode().splitlines()[1:]  # the marker line
    table = list(csv.DictReader(io.StringIO("\n".join(lines))))
    assert golden["row_count"] == len(table) == 25
    for column in golden["columns"]:
        values = [row[column["name"]] for row in table]
        counts = Counter(values)
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        assert column["top_values"] == [
            {"value": v, "count": n, "truncated": False} for v, n in top
        ], column["name"]
        assert column["distinct_count"] == len(counts)
        assert column["null_count"] == 0 and column["null_rate"] == "0.0000"
        assert column["max_length"] == max(len(v) for v in values)
    notional = next(c for c in golden["columns"] if c["name"] == "notional")
    amounts = [Decimal(row["notional"]) for row in table]
    assert (Decimal(notional["min"]), Decimal(notional["max"])) == (min(amounts), max(amounts))
    assert notional["candidate_types"] == ["DECIMAL", "STRING"]
    assert (notional["max_precision"], notional["max_scale"]) == (9, 2)
    maturity = next(c for c in golden["columns"] if c["name"] == "maturity_date")
    assert maturity["candidate_types"] == ["DATE", "STRING"]
    assert maturity["date_formats"] == {"YYYY-MM-DD": 25}
    dates = sorted(row["maturity_date"] for row in table)
    assert (maturity["min"], maturity["max"]) == (dates[0], dates[-1])
    currency = next(c for c in golden["columns"] if c["name"] == "currency")
    assert currency["currency_pattern_hits"] == {"ISO_4217_CODE": 25, "AMOUNT_WITH_CURRENCY": 0}


@pytest.mark.db
def test_profiling_the_same_artifact_twice_gives_the_same_hash(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    data = (OUT / "instrument.csv").read_bytes()
    first = upload(api, steward, seeded_source_id(db), data).json()
    second = upload(api, steward, seeded_source_id(db), data).json()  # another key: a new import
    assert first["id"] != second["id"]
    assert first["artifact"]["sha256"] == second["artifact"]["sha256"]
    a, b = snapshot_of(db, first["id"]), snapshot_of(db, second["id"])
    assert (
        a["content_sha256"]
        == b["content_sha256"]
        == json.loads(GOLDEN.read_text())["content_sha256"]
    )
    assert a == b


@pytest.mark.db
@pytest.mark.parametrize("fmt", ["XLSX", "JSON"])
def test_every_format_is_profiled(api: TestClient, db: str, fmt: str) -> None:
    steward = dev_token(api, "data_steward")
    source = source_for(api, db, steward, fmt)
    r = upload(api, steward, source, (OUT / SAMPLES[fmt]).read_bytes())
    assert r.status_code == 201, r.text
    snap = snapshot_of(db, r.json()["id"])
    assert (
        snap["row_count"] == 25 and snap["column_count"] == r.json()["batches"][0]["column_count"]
    )
    assert all(c["null_rate"] is not None for c in snap["columns"])


# --- the status path (D-21) and its audit --------------------------------------------------------


@pytest.mark.db
def test_profiling_ends_mapping_required_and_is_audited(api: TestClient, db: str) -> None:
    """RECEIVED -> PROFILING -> MAPPING_REQUIRED. The trigger refuses RECEIVED ->
    MAPPING_REQUIRED, so reaching it proves the import passed through PROFILING."""
    steward = dev_token(api, "data_steward")
    correlation = cid()
    r = upload(
        api, steward, seeded_source_id(db), (OUT / "instrument.csv").read_bytes(), cid=correlation
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "MAPPING_REQUIRED"
    assert api.get(f"/api/v1/imports/{body['id']}", headers=steward).json()["status"] == (
        "MAPPING_REQUIRED"
    )
    by_action = {e["action"]: e for e in events(db, correlation)}
    assert set(by_action) == {"import.create", "import.profile"}
    create = json.loads(by_action["import.create"]["details"])
    assert create["status"] == "RECEIVED"  # what the upload itself set
    profile = by_action["import.profile"]
    assert (profile["outcome"], profile["resource_id"]) == ("SUCCESS", body["id"])
    details = json.loads(profile["details"])
    snap = snapshot_of(db, body["id"])
    assert details == {
        "status": "MAPPING_REQUIRED",
        "snapshot_id": details["snapshot_id"],
        "content_sha256": snap["content_sha256"],
        "profiler_version": PROFILER_VERSION,
        "row_count": 25,
        "column_count": 9,
        "usable_mapping": False,
    }
    # Only metadata: no column names, no values.
    assert "notional" not in profile["details"] and "INS-" not in profile["details"]
    assert set(json.loads(profile["details_classification"]).values()) == {"public"}


@pytest.mark.db
def test_a_usable_active_mapping_returns_the_import_to_received(
    api: TestClient, db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase 10 seam: when the contract has a usable ACTIVE mapping, profiling ends
    RECEIVED (ready to validate)."""

    async def usable(self: Any, job: Any) -> bool:
        return True

    monkeypatch.setattr(ingestion_service.ImportService, "has_usable_mapping", usable)
    r = upload(api, dev_token(api, "data_steward"), seeded_source_id(db), b"instrument_id\nA\n")
    assert r.status_code == 201 and r.json()["status"] == "RECEIVED"
    assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 1


@pytest.mark.db
def test_a_replay_does_not_profile_again(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    first = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n", key="same-key")
    again = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n", key="same-key")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 1
    profiles = sql(
        db,
        "SELECT count(*) AS n FROM audit_event WHERE action = 'import.profile'"
        " AND resource_id = $1",
        first.json()["id"],
    )
    assert profiles[0]["n"] == 1


@pytest.mark.db
def test_a_failed_profile_leaves_the_import_received_without_a_profile(
    db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Profiling is its own transaction after the upload's (D-21: a step a runner can call
    again). If it fails, the upload stands as RECEIVED and nothing of the profile is kept."""

    def boom(self: Any, values: Any, extras: Any, flags: Any) -> None:
        raise RuntimeError("profiler failure")

    monkeypatch.setattr(Profiler, "add", boom)
    settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=tmp_path / "a")
    with TestClient(create_app(settings), raise_server_exceptions=False) as api:
        steward = dev_token(api, "data_steward")
        r = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n")
        assert r.status_code == 500 and r.json()["code"] == "internal_error"
        [job] = sql(db, "SELECT id, status FROM import_job")
        assert job["status"] == "RECEIVED"
        assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 0
        got = api.get(f"/api/v1/imports/{job['id']}/profile", headers=steward)
        assert (got.status_code, got.json()["code"]) == (404, "profile_not_found")


def test_profiling_can_be_switched_off(db: str, tmp_path: Path) -> None:
    """D-02 (review P9-1): IMPORT_PROFILING_ENABLED=false gives the Phase 8 behaviour back:
    the upload ends RECEIVED, with no profile and no import.profile audit event."""
    settings = Settings(
        ENV="test",
        DATABASE_URL=db,
        ARTIFACT_STORE_DIR=tmp_path / "a",
        IMPORT_PROFILING_ENABLED=False,
    )
    with TestClient(create_app(settings)) as api:
        steward = dev_token(api, "data_steward")
        r = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n")
        assert r.status_code == 201 and r.json()["status"] == "RECEIVED"
        assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 0
        profiled = sql(
            db,
            "SELECT count(*) AS n FROM audit_event"
            f" WHERE action = 'import.profile' AND resource_id = '{r.json()['id']}'",
        )
        assert profiled[0]["n"] == 0
        got = api.get(f"/api/v1/imports/{r.json()['id']}/profile", headers=steward)
        assert (got.status_code, got.json()["code"]) == (404, "profile_not_found")


def test_a_replay_profiles_an_import_whose_profiling_failed(
    db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P9-2: a failed profiling run is retried by replaying the same upload."""
    original = Profiler.add

    def boom(self: Any, values: Any, extras: Any, flags: Any) -> None:
        raise RuntimeError("profiler failure")

    settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=tmp_path / "a")
    with TestClient(create_app(settings), raise_server_exceptions=False) as api:
        steward = dev_token(api, "data_steward")
        monkeypatch.setattr(Profiler, "add", boom)
        first = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n", key="retry-1")
        assert first.status_code == 500
        monkeypatch.setattr(Profiler, "add", original)
        again = upload(api, steward, seeded_source_id(db), b"instrument_id\nA\n", key="retry-1")
        assert again.status_code == 200 and again.json()["status"] == "MAPPING_REQUIRED"
        assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 1
        assert sql(db, "SELECT count(*) AS n FROM import_job")[0]["n"] == 1
