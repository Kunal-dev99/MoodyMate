"""Phase 4 verification follow-ups: defects found by the independent review of BE-4.1…4.3."""

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest
from alembic import command

from app.governance.constants import DEFAULT_TENANT_ID
from app.governance.service import diff_fields
from app.tests.conftest import alembic_config, insert_tenant, sql
from app.tests.test_schema_registry import schema_id, spec, version_id

pytestmark = pytest.mark.db

IMMUTABLE = (asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError)


def _draft_version(db: str) -> Any:
    """A user-created DRAFT version (v3) of instrument with no fields yet."""
    return sql(
        db,
        "INSERT INTO schema_version (id, schema_id, version, status, content_sha256, created_by)"
        " VALUES (gen_random_uuid(), $1, 3, 'DRAFT', repeat('a', 64), 'test') RETURNING id",
        schema_id(db, "instrument"),
    )[0]["id"]


# --- 1. TRUNCATE ------------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["schema_field", "schema_version", "schema_definition"])
def test_registry_tables_cannot_be_truncated(db: str, table: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="immutable"):
        sql(db, f"TRUNCATE {table} CASCADE")  # noqa: S608
    assert sql(db, "SELECT count(*) AS n FROM schema_version")[0]["n"] == 4


# --- 2. The seed-removal gate is not a session setting anyone can turn on ----------------------


def test_session_setting_does_not_unlock_seeded_versions(db: str) -> None:
    active = version_id(db, "instrument", 1)

    async def attempt() -> None:
        conn = await asyncpg.connect(db.replace("+asyncpg", ""))
        try:
            await conn.execute("SET moodymate.schema_seed_removal = 'on'")
            await conn.execute("DELETE FROM schema_field WHERE schema_version_id = $1", active)
        finally:
            await conn.close()

    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="immutable"):
        asyncio.run(attempt())
    assert (
        sql(db, "SELECT count(*) AS n FROM schema_field WHERE schema_version_id = $1", active)[0][
            "n"
        ]
        == 9
    )


# --- 3. A field cannot slip into a version while it is being activated -------------------------


def test_field_insert_blocks_concurrent_activation(db: str) -> None:
    draft = _draft_version(db)

    async def race() -> None:
        a = await asyncpg.connect(db.replace("+asyncpg", ""))
        b = await asyncpg.connect(db.replace("+asyncpg", ""))
        try:
            tx = a.transaction()
            await tx.start()
            await a.execute(
                "INSERT INTO schema_field (id, schema_version_id, position, name, data_type,"
                " required, classification, extension_eligible, created_by) VALUES"
                " (gen_random_uuid(), $1, 1, 'late', 'STRING', false, 'public', false, 'test')",
                draft,
            )
            await b.execute("SET lock_timeout = '500ms'")
            # The insert holds a share lock on the version: activation must wait for it.
            with pytest.raises(asyncpg.LockNotAvailableError):
                await b.execute("UPDATE schema_version SET status = 'ACTIVE' WHERE id = $1", draft)
            await tx.rollback()
        finally:
            await a.close()
            await b.close()

    asyncio.run(race())
    assert sql(db, "SELECT status FROM schema_version WHERE id = $1", draft)[0]["status"] == "DRAFT"


# --- 5. Enumerated columns are constrained -----------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE schema_version SET status = 'active' WHERE id = $1", "status"),
        ("UPDATE schema_version SET compatibility = 'MAYBE' WHERE id = $1", "compatibility"),
    ],
)
def test_version_enums_are_checked(db: str, statement: str, constraint: str) -> None:
    draft = _draft_version(db)
    with pytest.raises(asyncpg.CheckViolationError, match=constraint):
        sql(db, statement, draft)


@pytest.mark.parametrize(
    ("data_type", "classification", "error"),
    [
        ("decimal", "public", asyncpg.CheckViolationError),
        ("STRING", "top-secret", asyncpg.ForeignKeyViolationError),
    ],
)
def test_field_type_and_classification_are_checked(
    db: str, data_type: str, classification: str, error: type[Exception]
) -> None:
    draft = _draft_version(db)
    with pytest.raises(error):
        sql(
            db,
            "INSERT INTO schema_field (id, schema_version_id, position, name, data_type, required,"
            " classification, extension_eligible, created_by) VALUES (gen_random_uuid(), $1, 1,"
            " 'x', $2, false, $3, false, 'test')",
            draft,
            data_type,
            classification,
        )


def test_dependent_type_is_checked(db: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match="dependent_type"):
        sql(
            db,
            "INSERT INTO schema_dependency (id, schema_id, dependent_type, dependent_ref,"
            " provisional, created_by) VALUES (gen_random_uuid(), $1, 'WIDGET', 'w', true, 'test')",
            schema_id(db, "instrument"),
        )


# --- 6. Governance metadata and terminal statuses ----------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE schema_version SET effective_from = '2099-01-01' WHERE id = $1",
        "UPDATE schema_version SET compatibility = 'BREAKING' WHERE id = $1",
        "UPDATE schema_version SET seeded_by = NULL WHERE id = $1",
    ],
)
def test_active_version_metadata_is_frozen(db: str, statement: str) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="immutable"):
        sql(db, statement, version_id(db, "instrument", 1))


@pytest.mark.parametrize("terminal", ["REJECTED", "RETIRED"])
def test_terminal_statuses_are_final(db: str, terminal: str) -> None:
    draft = _draft_version(db)
    sql(db, "UPDATE schema_version SET status = $2 WHERE id = $1", draft, terminal)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="final"):
        sql(db, "UPDATE schema_version SET status = 'ACTIVE' WHERE id = $1", draft)


# --- 9. A dependency never links two tenants ---------------------------------------------------


def test_dependency_cannot_point_at_another_tenant(db: str) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    foreign = sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'foreign', 'Foreign', 'x', false, 'test') RETURNING id",
        other,
    )[0]["id"]
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="tenant"):
        sql(
            db,
            "INSERT INTO schema_dependency (id, schema_id, dependent_type, dependent_ref,"
            " dependent_schema_id, provisional, created_by) VALUES (gen_random_uuid(), $1,"
            " 'SCHEMA', 'foreign', $2, true, 'test')",
            schema_id(db, "instrument"),
            foreign,
        )
    assert DEFAULT_TENANT_ID != other


# --- 0006 downgrade refusal ---------------------------------------------------------------------


def test_registry_downgrade_refuses_while_user_schemas_exist(db: str) -> None:
    sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'user_made', 'User made', 'x', false, 'test')",
        DEFAULT_TENANT_ID,
    )
    try:
        with pytest.raises(Exception, match="schema_definition has rows"):
            command.downgrade(alembic_config(db), "0005")
    finally:
        command.upgrade(alembic_config(db), "head")
    assert sql(db, "SELECT count(*) AS n FROM schema_version")[0]["n"] == 4


# --- 8. Diff edge cases ---------------------------------------------------------------------------


DEC = {"data_type": "DECIMAL"}


def changes(old: list[Any], new: list[Any]) -> set[tuple[str, str | None, str]]:
    return {(c.change, c.attribute, c.compatibility) for c in diff_fields(old, new).changes}


def test_decimal_bounds_are_part_of_the_contract() -> None:
    # Phase 5 review: a DECIMAL without precision/scale is not "wider" (fixed precision).
    assert changes([spec(**DEC, precision=10, scale=2)], [spec(**DEC)]) == {
        ("MODIFIED", "precision_scale", "BREAKING")
    }
    assert changes([spec(**DEC)], [spec(**DEC, precision=10, scale=2)]) == {
        ("MODIFIED", "precision_scale", "BREAKING")
    }


def test_precision_changes_are_reported_on_any_type() -> None:
    assert changes([spec(precision=10)], [spec(precision=5)]) == {
        ("MODIFIED", "precision_scale", "BREAKING")
    }


def test_type_change_does_not_hide_a_size_change() -> None:
    old = [spec(data_type="INTEGER", precision=10, scale=0)]
    new = [spec(**DEC, precision=8, scale=2)]
    assert changes(old, new) == {
        ("MODIFIED", "data_type", "BREAKING"),
        ("MODIFIED", "precision_scale", "BREAKING"),
    }


def test_reordering_is_neither_a_change_nor_a_new_hash() -> None:
    from app.governance.service import contract_sha256

    a, b = spec("a"), spec("b")
    assert diff_fields([a, b], [b, a]).overall is None
    assert contract_sha256([a, b]) == contract_sha256([b, a])
