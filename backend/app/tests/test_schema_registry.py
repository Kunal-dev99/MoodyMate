"""Phase 4: schema registry tables (BE-4.1), seed (BE-4.2) and read API with diff (BE-4.3)."""

import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.governance.constants import SCHEMA_SEED_TAG, Compatibility
from app.governance.service import FieldSpec, contract_sha256, diff_fields
from app.tests.conftest import dev_token, insert_tenant, sql

SEEDED = {"instrument", "position", "cashflow_event"}


def version_id(url: str, code: str, version: int) -> Any:
    return sql(
        url,
        "SELECT v.id FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id"
        " WHERE d.code = $1 AND v.version = $2",
        code,
        version,
    )[0]["id"]


def schema_id(url: str, code: str) -> Any:
    return sql(url, "SELECT id FROM schema_definition WHERE code = $1", code)[0]["id"]


# --- BE-4.1 immutability ----------------------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE schema_field SET data_type = 'INTEGER' WHERE schema_version_id = $1",
        "UPDATE schema_field SET description = 'edited' WHERE schema_version_id = $1",
        "DELETE FROM schema_field WHERE schema_version_id = $1",
        "INSERT INTO schema_field (id, schema_version_id, position, name, data_type, required,"
        " classification, extension_eligible, created_by) VALUES (gen_random_uuid(), $1, 99,"
        " 'smuggled', 'STRING', false, 'public', false, 'test')",
        "UPDATE schema_version SET content_sha256 = repeat('0', 64) WHERE id = $1",
        "UPDATE schema_version SET version = 99 WHERE id = $1",
        "UPDATE schema_version SET status = 'DRAFT' WHERE id = $1",
        "DELETE FROM schema_version WHERE id = $1",
    ],
)
def test_active_version_contract_cannot_change(db: str, statement: str) -> None:
    active = version_id(db, "instrument", 1)
    before = sql(
        db,
        "SELECT count(*) AS n, max(data_type) AS t FROM schema_field WHERE schema_version_id = $1",
        active,
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="immutable"):
        sql(db, statement, active)
    assert (
        sql(db, "SELECT status FROM schema_version WHERE id = $1", active)[0]["status"] == "ACTIVE"
    )
    after = sql(
        db,
        "SELECT count(*) AS n, max(data_type) AS t FROM schema_field WHERE schema_version_id = $1",
        active,
    )
    assert before == after


@pytest.mark.db
def test_status_can_still_move_forward(db: str) -> None:
    active = version_id(db, "instrument", 1)
    try:
        sql(db, "UPDATE schema_version SET status = 'DEPRECATED' WHERE id = $1", active)
        assert (
            sql(db, "SELECT status FROM schema_version WHERE id = $1", active)[0]["status"]
            == "DEPRECATED"
        )
    finally:
        sql(db, "UPDATE schema_version SET status = 'ACTIVE' WHERE id = $1", active)


@pytest.mark.db
def test_draft_version_fields_are_editable(db: str) -> None:
    draft = version_id(db, "instrument", 2)
    original = sql(
        db,
        "SELECT description FROM schema_field"
        " WHERE schema_version_id = $1 AND name = 'rating_category'",
        draft,
    )[0]["description"]
    try:
        sql(
            db,
            "UPDATE schema_field SET description = 'edited'"
            " WHERE schema_version_id = $1 AND name = 'rating_category'",
            draft,
        )
    finally:
        sql(
            db,
            "UPDATE schema_field SET description = $2"
            " WHERE schema_version_id = $1 AND name = 'rating_category'",
            draft,
            original,
        )


@pytest.mark.db
def test_seeded_content_hashes_match_the_service(db: str) -> None:
    rows = sql(db, "SELECT id, content_sha256 FROM schema_version")
    assert len(rows) == 4
    for row in rows:
        fields = sql(
            db,
            "SELECT * FROM schema_field WHERE schema_version_id = $1 ORDER BY position",
            row["id"],
        )
        assert (
            contract_sha256([FieldSpec.from_row(dict(f)) for f in fields]) == row["content_sha256"]
        )


# --- BE-4.2 seed ---------------------------------------------------------------------------


@pytest.mark.db
def test_seed_is_tagged_and_provisional(db: str) -> None:
    for table in ("schema_definition", "schema_version", "schema_field", "schema_dependency"):
        rows = sql(db, f"SELECT DISTINCT seeded_by FROM {table}")  # noqa: S608
        assert [r["seeded_by"] for r in rows] == [SCHEMA_SEED_TAG], table


@pytest.mark.db
def test_list_schemas(api: TestClient) -> None:
    r = api.get("/api/v1/schemas", headers=dev_token(api, "risk_analyst"))
    assert r.status_code == 200, r.text
    body = r.json()
    items = {s["code"]: s for s in body["items"]}
    assert set(items) == SEEDED and body["total"] == 3
    for s in items.values():
        assert s["provisional"] is True
        assert s["status"] == "ACTIVE"
        assert s["effective_version"]["version"] == 1
        assert "ILLUSTRATIVE" in s["description"]
        assert s["owner"]
    assert items["instrument"]["latest_version"] == {
        **items["instrument"]["latest_version"],
        "version": 2,
        "status": "DRAFT",
    }
    deps = {(d["dependent_type"], d["dependent_ref"]) for d in items["instrument"]["dependents"]}
    assert ("SCHEMA", "position") in deps and ("SCHEMA", "cashflow_event") in deps


@pytest.mark.db
def test_schemas_require_authentication(api: TestClient) -> None:
    assert api.get("/api/v1/schemas").status_code == 401


# --- BE-4.3 versions and diff -----------------------------------------------------------------


@pytest.mark.db
def test_versions_include_fields(api: TestClient, db: str) -> None:
    sid = schema_id(db, "instrument")
    r = api.get(f"/api/v1/schemas/{sid}/versions", headers=dev_token(api, "auditor"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"]["code"] == "instrument"
    assert [v["version"] for v in body["items"]] == [1, 2]
    v1 = body["items"][0]
    assert v1["status"] == "ACTIVE" and len(v1["content_sha256"]) == 64
    rate = next(f for f in v1["fields"] if f["name"] == "interest_rate")
    assert (rate["data_type"], rate["precision"], rate["scale"], rate["unit"]) == (
        "DECIMAL",
        9,
        4,
        "percent",
    )


@pytest.mark.db
def test_unknown_and_foreign_schemas_are_not_found(api: TestClient, db: str) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    foreign = sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'secret', 'Secret', 'x', false, 'test') RETURNING id",
        other,
    )[0]["id"]
    headers = dev_token(api, "risk_analyst")
    for sid in (uuid.uuid4(), foreign):
        assert api.get(f"/api/v1/schemas/{sid}/versions", headers=headers).status_code == 404
        r = api.get(f"/api/v1/schemas/{sid}/diff", params={"from": 1, "to": 2}, headers=headers)
        assert r.status_code == 404
    assert "secret" not in api.get("/api/v1/schemas", headers=headers).text


@pytest.mark.db
def test_seeded_diff_fixture(api: TestClient, db: str) -> None:
    sid = schema_id(db, "instrument")
    r = api.get(
        f"/api/v1/schemas/{sid}/diff",
        params={"from": 1, "to": 2},
        headers=dev_token(api, "risk_analyst"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall"] == "BREAKING"
    got = {(c["field"], c["change"], c["attribute"], c["compatibility"]) for c in body["changes"]}
    assert got == {
        ("rating_category", "ADDED", None, "ADDITIVE"),
        ("interest_rate", "MODIFIED", "precision_scale", "COMPATIBLE"),
        ("legacy_book_code", "REMOVED", None, "BREAKING"),
        ("product_family", "MODIFIED", "required", "BREAKING"),
        ("maturity_date", "MODIFIED", "data_type", "BREAKING"),
    }
    rate = next(c for c in body["changes"] if c["field"] == "interest_rate")
    assert (rate["from_value"], rate["to_value"]) == (
        {"precision": 9, "scale": 4},
        {"precision": 11, "scale": 6},
    )


@pytest.mark.db
def test_diff_rejects_unknown_versions(api: TestClient, db: str) -> None:
    sid = schema_id(db, "position")
    r = api.get(
        f"/api/v1/schemas/{sid}/diff",
        params={"from": 1, "to": 7},
        headers=dev_token(api, "auditor"),
    )
    assert r.status_code == 404
    assert r.json()["details"] == {"version": 7}


# --- BE-4.3 classification rules (unit) -------------------------------------------------------


def spec(name: str = "x", **kw: Any) -> FieldSpec:
    base: dict[str, Any] = {
        "name": name,
        "data_type": "STRING",
        "required": False,
        "unit": None,
        "precision": None,
        "scale": None,
        "classification": "internal",
        "extension_eligible": False,
    }
    return FieldSpec(**{**base, **kw})


def classify(old: list[FieldSpec], new: list[FieldSpec]) -> set[tuple[str, str | None, str]]:
    return {(c.change, c.attribute, c.compatibility) for c in diff_fields(old, new).changes}


DEC = {"data_type": "DECIMAL"}


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ([], [spec()], ("ADDED", None, "ADDITIVE")),
        ([], [spec(required=True)], ("ADDED", None, "BREAKING")),
        ([spec()], [], ("REMOVED", None, "BREAKING")),
        ([spec()], [spec(required=True)], ("MODIFIED", "required", "BREAKING")),
        ([spec(required=True)], [spec()], ("MODIFIED", "required", "COMPATIBLE")),
        ([spec()], [spec(data_type="DATE")], ("MODIFIED", "data_type", "BREAKING")),
        ([spec(unit="EUR")], [spec(unit="USD")], ("MODIFIED", "unit", "BREAKING")),
        (
            [spec(**DEC, precision=9, scale=4)],
            [spec(**DEC, precision=11, scale=6)],
            ("MODIFIED", "precision_scale", "COMPATIBLE"),
        ),
        (
            [spec(**DEC, precision=9, scale=4)],
            [spec(**DEC, precision=12, scale=4)],
            ("MODIFIED", "precision_scale", "COMPATIBLE"),
        ),
        (
            [spec(**DEC, precision=9, scale=4)],
            [spec(**DEC, precision=9, scale=2)],
            ("MODIFIED", "precision_scale", "BREAKING"),
        ),
        (
            [spec(**DEC, precision=9, scale=4)],
            [spec(**DEC, precision=9, scale=6)],  # fewer integer digits
            ("MODIFIED", "precision_scale", "BREAKING"),
        ),
        (
            [spec()],
            [spec(classification="restricted")],
            ("MODIFIED", "classification", "COMPATIBLE"),
        ),
        (
            [spec()],
            [spec(extension_eligible=True)],
            ("MODIFIED", "extension_eligible", "COMPATIBLE"),
        ),
    ],
)
def test_change_classification(
    old: list[FieldSpec], new: list[FieldSpec], expected: tuple[str, str | None, str]
) -> None:
    assert classify(old, new) == {expected}


def test_identical_contracts_have_no_changes() -> None:
    result = diff_fields([spec()], [spec()])
    assert result.changes == [] and result.overall is None


def test_overall_is_the_most_severe_change() -> None:
    result = diff_fields([spec("a")], [spec("a", required=True), spec("b")])
    assert result.overall == Compatibility.BREAKING
