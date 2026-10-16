"""BE-8.1: import tables. `import_artifact`, `import_batch` and `source_record` are
append-only (UPDATE, DELETE and TRUNCATE raise); an `import_job` is never deleted, its
content is frozen and its status moves only along IMPORT_TRANSITIONS (BE-8.5, the database
side); every enum column is CHECK-constrained; the downgrade refuses while imports exist.
Rows are inserted directly here; the API path is covered in test_imports.py."""

import json
import uuid
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.governance.constants import DEFAULT_TENANT_ID
from app.ingestion.constants import IMPORT_TRANSITIONS, ImportStatus
from app.tests.conftest import alembic_config, insert_tenant, sql
from app.tests.test_sources import _migration

pytestmark = pytest.mark.db

IMMUTABLE = asyncpg.InsufficientPrivilegeError
STATUSES = [s.value for s in ImportStatus]
SHA = "a" * 64


def head() -> str:
    return str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())


def seeded_source(db: str) -> dict[str, Any]:
    row = sql(
        db,
        "SELECT s.id AS source_id, c.id AS contract_id, c.schema_id FROM source_system s"
        " JOIN source_profile p ON p.source_system_id = s.id"
        " JOIN source_contract c ON c.source_profile_id = p.id WHERE s.code = 'core_banking'",
    )[0]
    version = sql(
        db,
        "SELECT id FROM schema_version WHERE schema_id = $1 AND status = 'ACTIVE'",
        row["schema_id"],
    )[0]["id"]
    return {**dict(row), "schema_version_id": version}


def insert_import(db: str, status: str = "RECEIVED", **overrides: Any) -> dict[str, Any]:
    """A job with one artifact, one batch and one record, inserted directly."""
    job = insert_job(db, status, **overrides)
    artifact = insert_artifact(db, job["id"])
    batch = sql(
        db,
        "INSERT INTO import_batch (id, import_job_id, batch_number, columns, row_count,"
        " created_by) VALUES (gen_random_uuid(), $1, 1, '[\"a\"]', 1, 'test') RETURNING *",
        job["id"],
    )[0]
    record = sql(
        db,
        "INSERT INTO source_record (id, batch_id, ordinal, locator, raw_values, extra_values,"
        " flags, row_sha256, created_by) VALUES (gen_random_uuid(), $1, 1, '{\"line\": 2}',"
        " '{\"a\": \"1\"}', '[]', '[]', $2, 'test') RETURNING *",
        batch["id"],
        SHA,
    )[0]
    return {"job": job, "artifact": artifact, "batch": batch, "record": record}


def insert_artifact(
    db: str, job_id: Any, ref: str = f"sha256/aa/{SHA}", retention: str = "RETAINED"
) -> Any:
    return sql(
        db,
        "INSERT INTO import_artifact (id, import_job_id, sha256, size_bytes, detected_mime,"
        " original_filename, storage_ref, classification, retention_state, created_by)"
        " VALUES (gen_random_uuid(), $1, $2, 10, 'text/csv', 'x.csv', $3, 'confidential',"
        " $4, 'test') RETURNING *",
        job_id,
        SHA,
        ref,
        retention,
    )[0]


def insert_job(db: str, status: str = "RECEIVED", **overrides: Any) -> Any:
    src = seeded_source(db)
    values = {
        "tenant_id": DEFAULT_TENANT_ID,
        "source_system_id": src["source_id"],
        "source_contract_id": src["contract_id"],
        "schema_version_id": src["schema_version_id"],
        "status": status,
        "format": "CSV",
        "data_class": "synthetic",
        "synthetic_attested": True,
        "idempotency_key": f"key-{uuid.uuid4().hex}",
        **overrides,
    }
    return sql(
        db,
        "INSERT INTO import_job (id, tenant_id, source_system_id, source_contract_id,"
        " schema_version_id, status, format, data_class, synthetic_attested,"
        " synthetic_marker, idempotency_key, request_sha256, row_count, created_by)"
        " VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, true, $9, $10, 1, 'test')"
        " RETURNING *",
        values["tenant_id"],
        values["source_system_id"],
        values["source_contract_id"],
        values["schema_version_id"],
        values["status"],
        values["format"],
        values["data_class"],
        values["synthetic_attested"],
        values["idempotency_key"],
        SHA,
    )[0]


# --- append-only rows ---------------------------------------------------------------------

APPEND_ONLY = {
    "source_record": [
        'UPDATE source_record SET raw_values = \'{"a": "2"}\' WHERE id = $1',
        "UPDATE source_record SET row_sha256 = repeat('b', 64) WHERE id = $1",
        "UPDATE source_record SET updated_at = now() WHERE id = $1",
        "DELETE FROM source_record WHERE id = $1",
    ],
    "import_artifact": [
        "UPDATE import_artifact SET sha256 = repeat('b', 64) WHERE id = $1",
        "UPDATE import_artifact SET retention_state = 'RETAINED' WHERE id = $1",
        "UPDATE import_artifact SET storage_ref = 'elsewhere' WHERE id = $1",
        "DELETE FROM import_artifact WHERE id = $1",
    ],
    "import_batch": [
        "UPDATE import_batch SET row_count = 2 WHERE id = $1",
        "DELETE FROM import_batch WHERE id = $1",
    ],
}
KEY = {"source_record": "record", "import_artifact": "artifact", "import_batch": "batch"}


@pytest.mark.parametrize(
    ("table", "statement"),
    [(t, s) for t, statements in APPEND_ONLY.items() for s in statements],
)
def test_append_only_rows_reject_update_and_delete(db: str, table: str, statement: str) -> None:
    row = insert_import(db)[KEY[table]]
    with pytest.raises(IMMUTABLE, match="append-only"):
        sql(db, statement, row["id"])
    assert sql(db, f"SELECT * FROM {table} WHERE id = $1", row["id"])[0] == row  # noqa: S608


@pytest.mark.parametrize("table", [*APPEND_ONLY, "import_job"])
def test_import_tables_reject_truncate(db: str, table: str) -> None:
    insert_import(db)
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, f"TRUNCATE {table} CASCADE")
    assert sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"] == 1  # noqa: S608


# --- import_job: never deleted, content frozen, status along the table -----------------------

JOB_CONTENT = [
    "UPDATE import_job SET row_count = 2 WHERE id = $1",
    "UPDATE import_job SET data_class = 'client' WHERE id = $1",
    "UPDATE import_job SET synthetic_attested = false WHERE id = $1",
    "UPDATE import_job SET synthetic_marker = false WHERE id = $1",
    "UPDATE import_job SET idempotency_key = 'other' WHERE id = $1",
    "UPDATE import_job SET request_sha256 = repeat('b', 64) WHERE id = $1",
    "UPDATE import_job SET format = 'JSON' WHERE id = $1",
    "UPDATE import_job SET expected_row_count = 5 WHERE id = $1",
    'UPDATE import_job SET control_totals = \'{"notional": "1"}\' WHERE id = $1',
    "UPDATE import_job SET correlation_id = 'rewritten' WHERE id = $1",
    "UPDATE import_job SET created_by = 'someone-else' WHERE id = $1",
    "UPDATE import_job SET created_at = now() - interval '1 day' WHERE id = $1",
    "UPDATE import_job SET schema_version_id = (SELECT id FROM schema_version"
    " WHERE status = 'DRAFT' LIMIT 1) WHERE id = $1",
    "UPDATE import_job SET retry_of_import_id = id WHERE id = $1",
    "UPDATE import_job SET id = gen_random_uuid() WHERE id = $1",
    "DELETE FROM import_job WHERE id = $1",
]


@pytest.mark.parametrize("statement", JOB_CONTENT)
def test_import_job_content_is_frozen_and_never_deleted(db: str, statement: str) -> None:
    job = insert_import(db)["job"]
    with pytest.raises(IMMUTABLE, match="immutable"):
        sql(db, statement, job["id"])
    assert sql(db, "SELECT * FROM import_job WHERE id = $1", job["id"])[0] == job


def test_import_job_cannot_move_tenant(db: str) -> None:
    job = insert_import(db)["job"]
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    with pytest.raises(IMMUTABLE):
        sql(db, "UPDATE import_job SET tenant_id = $2 WHERE id = $1", job["id"], other)


TERMINAL = {s.value for s, targets in IMPORT_TRANSITIONS.items() if not targets}


@pytest.mark.parametrize("target", STATUSES)
@pytest.mark.parametrize("start", STATUSES)
def test_import_status_moves_only_along_the_transition_table(
    db: str, start: str, target: str
) -> None:
    job = insert_import(db, status=start)["job"]
    allowed = ImportStatus(target) in IMPORT_TRANSITIONS[ImportStatus(start)]
    statement = "UPDATE import_job SET status = $2 WHERE id = $1"
    if allowed or (start == target and start not in TERMINAL):
        sql(db, statement, job["id"], target)
        assert sql(db, "SELECT status FROM import_job WHERE id = $1", job["id"])[0]["status"] == (
            target
        )
    else:
        with pytest.raises(IMMUTABLE, match="immutable"):
            sql(db, statement, job["id"], target)
        assert sql(db, "SELECT * FROM import_job WHERE id = $1", job["id"])[0] == job


def test_trigger_transition_table_matches_the_service() -> None:
    frozen = _migration("0012_import_tables").IMPORT_TRANSITIONS
    assert frozen == {str(k): sorted(str(v) for v in vs) for k, vs in IMPORT_TRANSITIONS.items()}


# --- CHECKs and same-tenant references -------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": "UPLOADED"}, "ck_import_job_status"),
        ({"status": "received"}, "ck_import_job_status"),
        ({"data_class": "real"}, "ck_import_job_data_class"),
        ({"format": "XLS"}, "ck_import_job_format"),
        ({"synthetic_attested": False}, "ck_import_job_synthetic_attested"),
    ],
)
def test_import_job_enums_are_checked(db: str, overrides: dict[str, Any], constraint: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match=constraint):
        insert_import(db, **overrides)


def test_client_imports_need_no_attestation(db: str) -> None:
    insert_import(db, data_class="client", synthetic_attested=False)


def test_artifact_retention_state_is_checked(db: str) -> None:
    job = insert_job(db)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_import_artifact_retention_state"):
        insert_artifact(db, job["id"], retention="DELETED")


def test_artifact_ref_must_match_its_hash(db: str) -> None:
    job = insert_job(db)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_import_artifact_storage_ref"):
        insert_artifact(db, job["id"], ref=f"sha256/bb/{SHA}")


def test_one_artifact_per_import(db: str) -> None:
    job = insert_import(db)["job"]
    with pytest.raises(asyncpg.UniqueViolationError):
        insert_artifact(db, job["id"])


def test_import_must_reference_its_own_tenants_source(db: str) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    with pytest.raises(IMMUTABLE, match="own tenant"):
        insert_import(db, tenant_id=other)


def test_import_contract_must_belong_to_its_source(db: str) -> None:
    src = seeded_source(db)
    sql(
        db,
        "INSERT INTO source_system (id, tenant_id, code, name, kind, created_by)"
        " VALUES (gen_random_uuid(), $1, 'second_source', 'Second', 'FILE_DROP', 'test')",
        DEFAULT_TENANT_ID,
    )
    second = sql(db, "SELECT id FROM source_system WHERE code = 'second_source'")[0]["id"]
    with pytest.raises(IMMUTABLE, match="contract of its own source"):
        insert_import(db, source_system_id=second, source_contract_id=src["contract_id"])


def test_records_hold_strings_in_jsonb(db: str) -> None:
    record = insert_import(db)["record"]
    assert json.loads(record["raw_values"]) == {"a": "1"}
    assert json.loads(record["locator"]) == {"line": 2}


# --- migration round trip and downgrade refusal -------------------------------------------------


def test_import_migration_round_trips_without_imports(db: str) -> None:
    cfg = alembic_config(db)
    command.downgrade(cfg, "0011")
    assert sql(db, "SELECT to_regclass('source_record') AS t")[0]["t"] is None
    assert sql(db, "SELECT to_regclass('import_job') AS t")[0]["t"] is None
    command.upgrade(cfg, "head")
    assert sql(db, "SELECT to_regclass('source_record') AS t")[0]["t"] is not None


def test_downgrade_refuses_while_an_import_exists(db: str) -> None:
    inserted = insert_import(db)
    tables = ("import_job", "import_artifact", "import_batch", "source_record")
    before = {t: sql(db, f"SELECT * FROM {t} ORDER BY id") for t in tables}  # noqa: S608
    try:
        with pytest.raises(Exception, match="import_job has rows"):
            command.downgrade(alembic_config(db), "0011")
        version = sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"]
        assert version == head()
        assert {t: sql(db, f"SELECT * FROM {t} ORDER BY id") for t in tables} == before  # noqa: S608
    finally:
        command.upgrade(alembic_config(db), "head")
    assert sql(db, "SELECT id FROM import_job")[0]["id"] == inserted["job"]["id"]
