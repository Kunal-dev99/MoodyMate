"""BE-9.3 (D-04, BE-3.5): GET /imports/{import_id}/profile.

* Top values (and the min/max, which are values too) are masked through `mask_mapping` by
  the column's classification: its mapped field's, otherwise the source profile's default.
  The seeded source's default is confidential: masked for risk_analyst, clear for
  data_steward.
* Each column carries `in_contract` and `suggested_target_field` (a normalised exact-name
  match against the pinned schema version), marked `suggestion: true`.
* Profiling and reading the profile change no schema and no contract: the registry and
  contract hashes are the same before and after.
* `import:read` is required; another tenant's import is the same 404 as a random id.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.governance.constants import ROLE_PERMISSIONS, Role
from app.tests.conftest import dev_token, insert_tenant, sql
from app.tests.test_import_tables import insert_import
from app.tests.test_imports import IMPORTS, seeded_source_id, upload
from app.tests.test_schema_change import token_for
from app.tests.test_sources import contract_body, source_body
from scripts.make_synthetic_samples import OUT

pytestmark = pytest.mark.db

MASKED = {"masked": True, "classification": "confidential"}
CLEARANCE = {  # role -> sees confidential values (0005 access policy seed)
    "data_steward": True,
    "approver": True,
    "platform_admin": True,
    "risk_analyst": False,
    "auditor": False,
}


def profile_url(import_id: str) -> str:
    return f"{IMPORTS}/{import_id}/profile"


def uploaded(api: TestClient, db: str, sample: str = "instrument.csv", source: str = "") -> str:
    r = upload(
        api,
        dev_token(api, "data_steward"),
        source or seeded_source_id(db),
        (OUT / sample).read_bytes(),
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def by_name(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in body["columns"]}


@pytest.mark.parametrize("role", list(CLEARANCE))
def test_confidential_top_values_are_masked_by_clearance(
    api: TestClient, db: str, role: str
) -> None:
    import_id = uploaded(api, db)
    r = api.get(profile_url(import_id), headers=dev_token(api, role))
    assert r.status_code == 200, r.text
    columns = by_name(r.json())
    assert len(columns) == 9
    for column in columns.values():
        # Not mapped (no mapping before Phase 10): the source profile's default class.
        assert column["classification"] == "confidential"
        assert column["top_values"], column["name"]
        for top in column["top_values"]:
            assert top["count"] >= 1  # counts are not values: always shown
            if CLEARANCE[role]:
                assert isinstance(top["value"], str)
            else:
                assert top["value"] == MASKED
        if not CLEARANCE[role]:
            assert column["min"] == MASKED and column["max"] == MASKED
    if CLEARANCE[role]:
        assert columns["notional"]["min"] == "27988.62"
        assert columns["currency"]["top_values"][0] == {
            "value": "CHF",
            "count": 9,
            "truncated": False,
        }
        assert "INS-000001" in r.text
    else:
        for secret in ("INS-000001", "27988.62", "CHF", "CP-3318", "2031-02-17"):
            assert secret not in r.text, secret


def test_masked_and_clear_profiles_differ_only_in_values(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)
    clear = api.get(profile_url(import_id), headers=dev_token(api, "data_steward")).json()
    masked = api.get(profile_url(import_id), headers=dev_token(api, "risk_analyst")).json()

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

    assert without_values(clear) == without_values(masked)


def test_the_column_class_is_the_source_default_when_not_mapped(api: TestClient, db: str) -> None:
    """An internal-by-default source: risk_analyst (cleared for internal) sees values."""
    steward = dev_token(api, "data_steward")
    body = source_body(
        db,
        code="internal_feed",
        profile={"format": "CSV", "owner": "Test data team", "default_classification": "internal"},
        contract=contract_body(db),
    )
    created = api.post("/api/v1/sources", json=body, headers=steward)
    assert created.status_code == 201, created.text
    import_id = uploaded(api, db, source=created.json()["id"])
    columns = by_name(
        api.get(profile_url(import_id), headers=dev_token(api, "risk_analyst")).json()
    )
    assert columns["currency"]["classification"] == "internal"
    assert columns["currency"]["top_values"][0]["value"] == "CHF"


def test_the_extra_column_fixture_reports_in_contract_false(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db, "instrument__unknown_extra_column.csv")
    r = api.get(profile_url(import_id), headers=dev_token(api, "data_steward"))
    assert r.status_code == 200, r.text
    columns = by_name(r.json())
    assert list(columns)[-1] == "legacy_flag"
    extra = columns["legacy_flag"]
    assert extra["in_contract"] is False
    assert extra["suggested_target_field"] is None
    assert extra["suggestion"] is True
    for name, column in columns.items():
        assert column["suggestion"] is True
        if name != "legacy_flag":
            assert column["in_contract"] is True, name
    # Normalised exact-name matches against the pinned instrument v1 (book_code has none:
    # v1 names it legacy_book_code).
    assert {n: c["suggested_target_field"] for n, c in columns.items()} == {
        "instrument_id": "instrument_id",
        "instrument_type": "instrument_type",
        "currency": "currency",
        "counterparty_id": "counterparty_id",
        "product_family": "product_family",
        "notional": "notional",
        "interest_rate": "interest_rate",
        "maturity_date": "maturity_date",
        "book_code": None,
        "legacy_flag": None,
    }
    assert columns["notional"]["suggested_type"] == "DECIMAL"
    assert columns["legacy_flag"]["suggested_type"] == "STRING"


def test_suggested_targets_are_normalised_exact_matches(api: TestClient, db: str) -> None:
    data = (
        b"# ILLUSTRATIVE / SYNTHETIC test\n"
        b"Instrument ID, NOTIONAL ,interest-rate,Maturity.Date,instrumentid\n"
        b"A,1.00,2.5,2031-01-01,A\n"
    )
    r = upload(api, dev_token(api, "data_steward"), seeded_source_id(db), data)
    assert r.status_code == 201, r.text
    body = api.get(profile_url(r.json()["id"]), headers=dev_token(api, "data_steward")).json()
    assert [
        (c["name"], c["in_contract"], c["suggested_target_field"]) for c in body["columns"]
    ] == [
        ("Instrument ID", False, "instrument_id"),
        (" NOTIONAL ", False, "notional"),
        ("interest-rate", False, "interest_rate"),
        ("Maturity.Date", False, "maturity_date"),
        ("instrumentid", False, None),  # not exact once normalised
    ]


def test_profile_response_shape(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)
    body = api.get(profile_url(import_id), headers=dev_token(api, "data_steward")).json()
    snapshot = sql(
        db,
        "SELECT id, content_sha256 FROM data_profile_snapshot WHERE import_job_id = $1",
        uuid.UUID(import_id),
    )[0]
    assert body["import_id"] == import_id
    assert body["snapshot_id"] == str(snapshot["id"])
    assert body["content_sha256"] == snapshot["content_sha256"]
    assert body["import_status"] == "MAPPING_REQUIRED"
    assert (body["row_count"], body["column_count"], body["profiler_version"]) == (25, 9, 1)
    assert (body["schema_code"], body["schema_version"], body["contract_version"]) == (
        "instrument",
        1,
        1,
    )
    assert body["summary"] == {
        "blank_rows": 0,
        "rows_with_missing_cells": 0,
        "rows_with_extra_cells": 0,
        "max_extra_cells": 0,
        "flag_counts": {},
    }
    notional = by_name(body)["notional"]
    assert set(notional) == {
        "name",
        "position",
        "classification",
        "value_count",
        "null_count",
        "null_rate",
        "distinct_count",
        "distinct_capped",
        "candidate_types",
        "suggested_type",
        "min",
        "max",
        "max_length",
        "max_precision",
        "max_scale",
        "date_formats",
        "currency_pattern_hits",
        "top_values",
        "top_values_exact",
        "in_contract",
        "suggested_target_field",
        "suggestion",
    }
    assert notional["null_rate"] == "0.0000" and isinstance(notional["null_rate"], str)


# --- profiling changes no schema and no contract ------------------------------------------------

REGISTRY = {
    "schema_definition": "SELECT * FROM schema_definition ORDER BY id",
    "schema_version": "SELECT * FROM schema_version ORDER BY id",
    "schema_field": "SELECT * FROM schema_field ORDER BY id",
    "schema_dependency": "SELECT * FROM schema_dependency ORDER BY id",
    "source_profile": "SELECT * FROM source_profile ORDER BY id",
    "source_contract": "SELECT * FROM source_contract ORDER BY id",
}


def registry(db: str) -> dict[str, Any]:
    return {name: sql(db, query) for name, query in REGISTRY.items()}


def test_profiling_leaves_registry_and_contract_hashes_unchanged(api: TestClient, db: str) -> None:
    before = registry(db)
    hashes = sql(db, "SELECT id, content_sha256 FROM schema_version ORDER BY id") + sql(
        db, "SELECT id, content_sha256 FROM source_contract ORDER BY id"
    )
    import_id = uploaded(api, db, "instrument__unknown_extra_column.csv")
    for role in CLEARANCE:
        assert api.get(profile_url(import_id), headers=dev_token(api, role)).status_code == 200
    assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 1
    after_hashes = sql(db, "SELECT id, content_sha256 FROM schema_version ORDER BY id") + sql(
        db, "SELECT id, content_sha256 FROM source_contract ORDER BY id"
    )
    assert after_hashes == hashes
    assert registry(db) == before  # every row, every column, updated_at included


# --- authorization and tenant scope -------------------------------------------------------------


def test_every_seeded_role_with_import_read_can_read_it(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)
    for role in Role:
        assert "import:read" in ROLE_PERMISSIONS[role]
        r = api.get(profile_url(import_id), headers=dev_token(api, role.value))
        assert r.status_code == 200, (role, r.text)


def test_without_import_read_is_403(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)
    nobody = token_for(db, "dev:no-roles", ())
    r = api.get(profile_url(import_id), headers=nobody)
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert r.json()["details"]["required_permission"] == "import:read"
    assert api.get(profile_url(import_id)).status_code == 401


def test_cross_tenant_profile_is_the_same_404_as_a_random_id(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    outsider = token_for(db, "dev:outsider", ("data_steward",), tenant_id=other)
    theirs = api.get(profile_url(import_id), headers=outsider)
    random = api.get(profile_url(str(uuid.uuid4())), headers=outsider)
    assert theirs.status_code == random.status_code == 404
    strip = ("correlation_id",)
    assert {k: v for k, v in theirs.json().items() if k not in strip} == {
        k: v for k, v in random.json().items() if k not in strip
    }
    assert theirs.json()["code"] == "not_found"


def test_an_import_without_a_profile_is_404_profile_not_found(api: TestClient, db: str) -> None:
    job = insert_import(db)["job"]  # inserted directly: never profiled
    r = api.get(profile_url(str(job["id"])), headers=dev_token(api, "auditor"))
    assert (r.status_code, r.json()["code"]) == (404, "profile_not_found")


def test_reading_a_profile_is_not_audited_and_changes_nothing(api: TestClient, db: str) -> None:
    import_id = uploaded(api, db)
    status = sql(db, "SELECT status, updated_at FROM import_job")
    cid = f"read-{uuid.uuid4().hex[:8]}"
    r = api.get(
        profile_url(import_id),
        headers={**dev_token(api, "auditor"), "X-Correlation-ID": cid},
    )
    assert r.status_code == 200
    assert sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid) == []
    assert sql(db, "SELECT status, updated_at FROM import_job") == status
