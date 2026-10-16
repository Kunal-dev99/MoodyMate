"""Profiling (Phase 9, BE-9.2/9.3, D-21, D-25): the golden profile of the synthetic
instrument CSV, masking of profile values by role, in_contract on an extra column, and the
IMPORT_PROFILING_ENABLED switch. (The snapshot's append-only trigger is proven in
test_imports.py with the other import tables.)"""

import csv
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
from app.ingestion.service import PROFILER_VERSION
from app.main import create_app
from app.tests.conftest import dev_token, sql
from app.tests.helpers import IMPORTS, cid, events, seeded_source_id, upload
from scripts.make_synthetic_samples import OUT

pytestmark = pytest.mark.db

GOLDEN = Path(__file__).parent / "golden" / "instrument_profile.json"
MASKED = {"masked": True, "classification": "confidential"}


def uploaded(api: TestClient, db: str, sample: str = "instrument.csv", **kw: Any) -> Any:
    data = (OUT / sample).read_bytes()
    r = upload(api, dev_token(api, "data_steward"), seeded_source_id(db), data, **kw)
    assert r.status_code == 201, r.text
    return r.json()


def test_golden_profile_of_the_synthetic_instrument_csv(api: TestClient, db: str) -> None:
    correlation = cid()
    body = uploaded(api, db, cid=correlation)
    assert body["status"] == "MAPPING_REQUIRED"
    [row] = sql(
        db,
        "SELECT profiler_version, row_count, column_count, summary::text AS summary,"
        " columns::text AS columns, content_sha256 FROM data_profile_snapshot"
        " WHERE import_job_id = $1",
        uuid.UUID(body["id"]),
    )
    stored = {
        **dict(row),
        "summary": json.loads(row["summary"]),
        "columns": json.loads(row["columns"]),
    }
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert stored == golden
    assert golden["profiler_version"] == PROFILER_VERSION

    # An independent count over the CSV agrees with the golden file.
    data = (OUT / "instrument.csv").read_bytes()
    table = list(csv.DictReader(io.StringIO("\n".join(data.decode().splitlines()[1:]))))
    assert golden["row_count"] == len(table) == 25
    for column in golden["columns"]:
        values = [r[column["name"]] for r in table]
        top = sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        assert column["top_values"] == [
            {"value": v, "count": n, "truncated": False} for v, n in top
        ], column["name"]
        assert column["distinct_count"] == len(set(values))
        assert column["null_rate"] == "0.0000"
    notional = next(c for c in golden["columns"] if c["name"] == "notional")
    amounts = [Decimal(r["notional"]) for r in table]
    assert (Decimal(notional["min"]), Decimal(notional["max"])) == (min(amounts), max(amounts))
    assert notional["candidate_types"] == ["DECIMAL", "STRING"]

    # Audited as import.profile, with metadata only: no column names, no values.
    profile = {e["action"]: e for e in events(db, correlation)}["import.profile"]
    assert profile["outcome"] == "SUCCESS"
    assert json.loads(profile["details"])["content_sha256"] == golden["content_sha256"]
    assert "notional" not in profile["details"] and "INS-" not in profile["details"]


def test_profile_values_are_masked_by_role(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)["id"]
    clearance = {  # role -> sees confidential values (0005 access policy seed)
        "data_steward": True,
        "approver": True,
        "platform_admin": True,
        "risk_analyst": False,
        "auditor": False,
    }
    bodies = {}
    for role, cleared in clearance.items():
        r = api.get(f"{IMPORTS}/{import_id}/profile", headers=dev_token(api, role))
        assert r.status_code == 200, (role, r.text)
        bodies[role] = r.json()
        for column in r.json()["columns"]:
            assert column["classification"] == "confidential"
            assert column["top_values"], column["name"]
            for top in column["top_values"]:
                assert top["count"] >= 1  # counts are not values: always shown
                assert (top["value"] == MASKED) is not cleared, (role, column["name"])
            if not cleared:
                assert column["min"] == MASKED and column["max"] == MASKED
        for secret in ("INS-000001", "27988.62", "CHF"):
            assert (secret in r.text) is cleared, (role, secret)

    def without_values(body: dict[str, Any]) -> dict[str, Any]:
        columns = [
            {
                **c,
                "min": None,
                "max": None,
                "top_values": [{**t, "value": None} for t in c["top_values"]],
            }
            for c in body["columns"]
        ]
        return {**body, "columns": columns}

    # Masked and clear profiles differ only in the values.
    assert without_values(bodies["data_steward"]) == without_values(bodies["risk_analyst"])


def test_the_extra_column_fixture_reports_in_contract_false(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db, "instrument__unknown_extra_column.csv")["id"]
    r = api.get(f"{IMPORTS}/{import_id}/profile", headers=dev_token(api, "data_steward"))
    assert r.status_code == 200, r.text
    columns = {c["name"]: c for c in r.json()["columns"]}
    assert list(columns)[-1] == "legacy_flag"
    assert columns["legacy_flag"]["in_contract"] is False
    assert columns["legacy_flag"]["suggested_target_field"] is None
    for name, column in columns.items():
        assert column["suggestion"] is True
        if name != "legacy_flag":
            assert column["in_contract"] is True, name
    assert columns["notional"]["suggested_target_field"] == "notional"


def test_profiling_switched_off_leaves_the_import_received(db: str, tmp_path: Path) -> None:
    settings = Settings(
        ENV="test",
        DATABASE_URL=db,
        ARTIFACT_STORE_DIR=tmp_path / "a",
        IMPORT_PROFILING_ENABLED=False,
    )
    with TestClient(create_app(settings)) as api:
        correlation = cid()
        body = uploaded(api, db, cid=correlation)
        assert body["status"] == "RECEIVED"
        assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 0
        assert [e["action"] for e in events(db, correlation)] == ["import.create"]
        got = api.get(f"{IMPORTS}/{body['id']}/profile", headers=dev_token(api, "data_steward"))
        assert (got.status_code, got.json()["code"]) == (404, "profile_not_found")
