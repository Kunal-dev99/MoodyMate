"""BE-9.1: `data_profile_snapshot`, one per import. Append-only: UPDATE, DELETE and TRUNCATE
raise, with no session-settable bypass; the counts and the hash are CHECK-constrained; the
downgrade refuses while a snapshot exists. Rows are inserted directly here; the profiler and
the API are covered in test_profiler.py and test_import_profile.py."""

import json
from typing import Any

import asyncpg
import pytest
from alembic import command

from app.tests.conftest import alembic_config, script, sql
from app.tests.test_import_tables import head, insert_import

pytestmark = pytest.mark.db

IMMUTABLE = asyncpg.InsufficientPrivilegeError
SHA = "c" * 64
COLUMNS = [{"name": "a", "position": 1}]


def insert_snapshot(db: str, job_id: Any, **overrides: Any) -> Any:
    values = {
        "profiler_version": 1,
        "row_count": 1,
        "column_count": 1,
        "summary": json.dumps({"blank_rows": 0}),
        "columns": json.dumps(COLUMNS),
        "content_sha256": SHA,
        **overrides,
    }
    return sql(
        db,
        "INSERT INTO data_profile_snapshot (id, import_job_id, profiler_version, row_count,"
        " column_count, summary, columns, content_sha256, created_by) VALUES"
        " (gen_random_uuid(), $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, 'test') RETURNING *",
        job_id,
        values["profiler_version"],
        values["row_count"],
        values["column_count"],
        values["summary"],
        values["columns"],
        values["content_sha256"],
    )[0]


@pytest.fixture
def snapshot(db: str) -> Any:
    job = insert_import(db)["job"]
    return insert_snapshot(db, job["id"])


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE data_profile_snapshot SET columns = '[]' WHERE id = $1",
        "UPDATE data_profile_snapshot SET content_sha256 = repeat('d', 64) WHERE id = $1",
        "UPDATE data_profile_snapshot SET row_count = 2 WHERE id = $1",
        "UPDATE data_profile_snapshot SET updated_at = now() WHERE id = $1",
        "DELETE FROM data_profile_snapshot WHERE id = $1",
    ],
)
def test_snapshot_rejects_update_and_delete(db: str, snapshot: Any, statement: str) -> None:
    with pytest.raises(IMMUTABLE, match="append-only"):
        sql(db, statement, snapshot["id"])
    assert sql(db, "SELECT * FROM data_profile_snapshot WHERE id = $1", snapshot["id"])[0] == (
        snapshot
    )


def test_snapshot_rejects_truncate(db: str, snapshot: Any) -> None:
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE data_profile_snapshot")
    assert sql(db, "SELECT count(*) AS n FROM data_profile_snapshot")[0]["n"] == 1


def test_no_session_setting_unlocks_the_trigger(db: str, snapshot: Any) -> None:
    """No bypass: the trigger function reads no setting, and setting plausible ones in the
    same transaction leaves it in force (disabling it needs the table owner)."""
    body = sql(db, "SELECT pg_get_functiondef('data_profile_append_only'::regproc) AS src")[0][
        "src"
    ]
    assert "current_setting" not in body and "TG_OP" in body
    attempt = (
        "BEGIN; "
        "SELECT set_config('app.allow_profile_update', 'on', true); "
        "SELECT set_config('app.bypass_triggers', 'on', true); "
        "UPDATE data_profile_snapshot SET row_count = 2; "
        "COMMIT;"
    )
    with pytest.raises(IMMUTABLE, match="append-only"):
        script(db, attempt)
    assert sql(db, "SELECT row_count FROM data_profile_snapshot")[0]["row_count"] == 1


def test_one_snapshot_per_import(db: str, snapshot: Any) -> None:
    with pytest.raises(asyncpg.UniqueViolationError):
        insert_snapshot(db, snapshot["import_job_id"])


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"row_count": -1}, "ck_data_profile_snapshot_row_count"),
        ({"column_count": -1}, "ck_data_profile_snapshot_column_count"),
        ({"profiler_version": 0}, "ck_data_profile_snapshot_profiler_version"),
        ({"content_sha256": "C" * 64}, "ck_data_profile_snapshot_content_sha256"),
        ({"content_sha256": "c" * 63 + "g"}, "ck_data_profile_snapshot_content_sha256"),
        ({"columns": json.dumps({"a": 1})}, "ck_data_profile_snapshot_columns"),
        ({"columns": json.dumps(COLUMNS * 2)}, "ck_data_profile_snapshot_column_count"),
        ({"summary": json.dumps([])}, "ck_data_profile_snapshot_summary"),
    ],
)
def test_snapshot_columns_are_checked(db: str, overrides: dict[str, Any], constraint: str) -> None:
    job = insert_import(db)["job"]
    with pytest.raises(asyncpg.CheckViolationError, match=constraint):
        insert_snapshot(db, job["id"], **overrides)


def test_snapshot_needs_an_existing_import(db: str) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        insert_snapshot(db, "00000000-0000-0000-0000-000000000000")


# --- migration round trip and downgrade refusal -------------------------------------------------


def test_profile_migration_round_trips_without_snapshots(db: str) -> None:
    insert_import(db)  # imports alone do not block this downgrade (0012's own guard is later)
    cfg = alembic_config(db)
    command.downgrade(cfg, "0012")
    assert sql(db, "SELECT to_regclass('data_profile_snapshot') AS t")[0]["t"] is None
    assert sql(db, "SELECT count(*) AS n FROM import_job")[0]["n"] == 1
    command.upgrade(cfg, "head")
    assert sql(db, "SELECT to_regclass('data_profile_snapshot') AS t")[0]["t"] is not None


def test_downgrade_refuses_while_a_snapshot_exists(db: str, snapshot: Any) -> None:
    before = sql(db, "SELECT * FROM data_profile_snapshot ORDER BY id")
    try:
        with pytest.raises(Exception, match="data_profile_snapshot has rows"):
            command.downgrade(alembic_config(db), "0012")
        version = sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"]
        assert version == head()
        assert sql(db, "SELECT * FROM data_profile_snapshot ORDER BY id") == before
    finally:
        command.upgrade(alembic_config(db), "head")
