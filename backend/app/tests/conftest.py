import asyncio
import os
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

# Tests build their own Settings: a developer's backend/.env (copied from .env.example,
# which sets DATA_CLASS, AUTH_MODE, ...) must not leak into them.
Settings.model_config["env_file"] = None

# Never the live dev DB (D-03): tests use their own database.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_test",
)


def _db_reachable(url: str) -> bool:
    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(url.replace("+asyncpg", ""), timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(probe())


# BE-6.3 / BE-13.2: rows of the route x role matrices, printed at the end of the test report.
WS1_MATRIX: list[dict[str, Any]] = []
WS2_MATRIX: list[dict[str, Any]] = []

# BE-18.3: the WS3 matrix is the WS1 matrix's rows for WS3 routes, picked by path, so WS3
# routes added later join it with no list to keep in step.
WS3_PREFIXES = (
    "/instruments",
    "/dataset-snapshots",
    "/calculation-",
    "/rules",
    "/assumption-sets",
    "/counterparties",  # Phase 19: the credit reads are WS3 domain routes
    "/credit-",
    "/obligor-ratings",  # Phase 20: the rating reads and the approval POST
    "/legal-entities",  # Phase 31: the legal entity reads and the identity links (D-58)
    "/identity-links",
)


def is_ws3_route(route: str) -> bool:
    """A "METHOD /api/v1/..." matrix route that belongs to WS3."""
    path = route.split(" ", 1)[1].removeprefix("/api/v1")
    projection = path.startswith("/imports/") and path.endswith("/projection")
    return path.startswith(WS3_PREFIXES) or projection


def _print_matrix(tr: Any, title: str, matrix: list[dict[str, Any]]) -> None:
    roles = [k for k in matrix[0] if k not in ("route", "permission")]
    width = max(len(r["route"]) for r in matrix)
    tr.section(title)
    tr.write_line(f"{'route':<{width}}  {'permission':<15} " + " ".join(f"{r:>14}" for r in roles))
    for row in matrix:
        cells = " ".join(f"{row[r]:>14}" for r in roles)
        tr.write_line(f"{row['route']:<{width}}  {row['permission']:<15} {cells}")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if WS1_MATRIX:
        title = "WS1 authorization matrix (route x role, HTTP status; 403 = denied)"
        _print_matrix(terminalreporter, title, WS1_MATRIX)
    if WS2_MATRIX:
        title = "WS2 authorization matrix (route x role, HTTP status; 403 = denied)"
        _print_matrix(terminalreporter, title, WS2_MATRIX)
    ws3 = [row for row in WS1_MATRIX if is_ws3_route(row["route"])]
    if ws3:
        title = "WS3 authorization matrix (route x role, HTTP status; 403 = denied)"
        _print_matrix(terminalreporter, title, ws3)


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _db_reachable(TEST_DATABASE_URL):
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(f"REQUIRE_DB=1 but test database is unreachable: {TEST_DATABASE_URL}")
        pytest.skip("test database unreachable (set REQUIRE_DB=1 to make this a failure)")
    return TEST_DATABASE_URL


# --- Phase 2: migrated database, cleanup and helpers -----------------------------------

from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.governance.constants import DEFAULT_TENANT_ID  # noqa: E402

# Registry rows and proposals tests add. Some versions are no longer DRAFT, so the
# immutability triggers are lifted for this one transaction, as the seed downgrade does
# (needs the table owner).
_REGISTRY_CLEANUP = """
BEGIN;
ALTER TABLE schema_field DISABLE TRIGGER schema_field_immutable;
ALTER TABLE schema_version DISABLE TRIGGER schema_version_immutable;
ALTER TABLE schema_change_proposal DISABLE TRIGGER schema_change_proposal_immutable;
DELETE FROM schema_change_proposal;
-- Approvals in tests move seeded versions on: put them back as seeded.
UPDATE schema_version SET status = CASE WHEN version = 2 THEN 'DRAFT' ELSE 'ACTIVE' END
    WHERE seeded_by IS NOT NULL;
DELETE FROM schema_dependency WHERE seeded_by IS NULL;
DELETE FROM schema_field WHERE seeded_by IS NULL;
DELETE FROM schema_version WHERE seeded_by IS NULL;
DELETE FROM schema_definition WHERE seeded_by IS NULL;
ALTER TABLE schema_field ENABLE TRIGGER schema_field_immutable;
ALTER TABLE schema_version ENABLE TRIGGER schema_version_immutable;
ALTER TABLE schema_change_proposal ENABLE TRIGGER schema_change_proposal_immutable;
COMMIT;
"""

# Sources tests register (Feature 02). Their rows are never deleted by the app, so the
# no-delete/immutability triggers are lifted for this one transaction, as the seed
# downgrade does. Runs before the registry cleanup: contracts reference schemas.
_INGESTION_CLEANUP = """
BEGIN;
ALTER TABLE source_contract DISABLE TRIGGER source_contract_immutable;
ALTER TABLE source_profile DISABLE TRIGGER source_profile_no_delete;
ALTER TABLE source_system DISABLE TRIGGER source_system_no_delete;
DELETE FROM source_contract WHERE seeded_by IS NULL;
-- Tests supersede the seeded contract: put it back as seeded.
UPDATE source_contract SET status = 'PROVISIONAL' WHERE seeded_by IS NOT NULL;
DELETE FROM source_profile WHERE seeded_by IS NULL;
DELETE FROM source_system WHERE seeded_by IS NULL;
ALTER TABLE source_contract ENABLE TRIGGER source_contract_immutable;
ALTER TABLE source_profile ENABLE TRIGGER source_profile_no_delete;
ALTER TABLE source_system ENABLE TRIGGER source_system_no_delete;
COMMIT;
"""

# Imports tests create (Phase 8). Append-only and never deleted by the app, so the triggers
# are lifted for this one transaction, as above. Runs before the source cleanup: imports
# reference sources and contracts.
_IMPORT_CLEANUP = """
BEGIN;
ALTER TABLE source_record DISABLE TRIGGER source_record_append_only;
ALTER TABLE import_batch DISABLE TRIGGER import_batch_append_only;
ALTER TABLE import_artifact DISABLE TRIGGER import_artifact_append_only;
ALTER TABLE import_job DISABLE TRIGGER import_job_immutable;
DELETE FROM source_record;
DELETE FROM import_batch;
DELETE FROM import_artifact;
DELETE FROM import_job;
ALTER TABLE source_record ENABLE TRIGGER source_record_append_only;
ALTER TABLE import_batch ENABLE TRIGGER import_batch_append_only;
ALTER TABLE import_artifact ENABLE TRIGGER import_artifact_append_only;
ALTER TABLE import_job ENABLE TRIGGER import_job_immutable;
COMMIT;
"""

# Profiles of those imports (Phase 9), append-only likewise. Its own statement, before the
# import cleanup, so a schema without this table (0012) still has its imports cleaned.
_PROFILE_CLEANUP = """
BEGIN;
ALTER TABLE data_profile_snapshot DISABLE TRIGGER data_profile_snapshot_append_only;
DELETE FROM data_profile_snapshot;
ALTER TABLE data_profile_snapshot ENABLE TRIGGER data_profile_snapshot_append_only;
COMMIT;
"""

# Mappings (Phase 10) reference imports, contracts and schemas: removed first.
_MAPPING_CLEANUP = """
BEGIN;
ALTER TABLE import_job DISABLE TRIGGER import_job_immutable;
ALTER TABLE import_job DISABLE TRIGGER import_job_mapping_pin;
ALTER TABLE mapping_version DISABLE TRIGGER mapping_version_immutable;
ALTER TABLE mapping_version DISABLE TRIGGER mapping_version_checks;
ALTER TABLE mapping_rule DISABLE TRIGGER mapping_rule_immutable;
ALTER TABLE mapping_validation DISABLE TRIGGER mapping_validation_append_only;
ALTER TABLE field_mapping DISABLE TRIGGER field_mapping_immutable;
UPDATE import_job SET mapping_version_id = NULL WHERE mapping_version_id IS NOT NULL;
UPDATE mapping_version SET last_validation_id = NULL WHERE last_validation_id IS NOT NULL;
DELETE FROM mapping_validation;
DELETE FROM mapping_rule;
DELETE FROM mapping_version;
DELETE FROM field_mapping;
ALTER TABLE import_job ENABLE TRIGGER import_job_immutable;
ALTER TABLE import_job ENABLE TRIGGER import_job_mapping_pin;
ALTER TABLE mapping_version ENABLE TRIGGER mapping_version_immutable;
ALTER TABLE mapping_version ENABLE TRIGGER mapping_version_checks;
ALTER TABLE mapping_rule ENABLE TRIGGER mapping_rule_immutable;
ALTER TABLE mapping_validation ENABLE TRIGGER mapping_validation_append_only;
ALTER TABLE field_mapping ENABLE TRIGGER field_mapping_immutable;
COMMIT;
"""

# Validation results (Phase 11) reference imports, records and mapping versions: removed
# before the mappings. Append-only, so the triggers are lifted for this one transaction.
_VALIDATION_CLEANUP = """
BEGIN;
ALTER TABLE record_lineage DISABLE TRIGGER record_lineage_append_only;
ALTER TABLE canonical_record DISABLE TRIGGER canonical_record_append_only;
ALTER TABLE validation_issue DISABLE TRIGGER validation_issue_append_only;
ALTER TABLE import_manifest DISABLE TRIGGER import_manifest_append_only;
ALTER TABLE validation_profile DISABLE TRIGGER validation_profile_append_only;
ALTER TABLE quarantine_item DISABLE TRIGGER quarantine_item_guard;
DELETE FROM record_lineage;
DELETE FROM canonical_record;
DELETE FROM validation_issue;
DELETE FROM import_manifest;
DELETE FROM quarantine_item;
DELETE FROM validation_profile WHERE seeded_by IS NULL;
ALTER TABLE record_lineage ENABLE TRIGGER record_lineage_append_only;
ALTER TABLE canonical_record ENABLE TRIGGER canonical_record_append_only;
ALTER TABLE validation_issue ENABLE TRIGGER validation_issue_append_only;
ALTER TABLE import_manifest ENABLE TRIGGER import_manifest_append_only;
ALTER TABLE validation_profile ENABLE TRIGGER validation_profile_append_only;
ALTER TABLE quarantine_item ENABLE TRIGGER quarantine_item_guard;
COMMIT;
"""

# Reconciliation runs and drift events (Phase 12) reference imports, profiles and validated
# results: removed first. Append-only (events: review only), so the triggers are lifted for
# this one transaction.
_PHASE12_CLEANUP = """
BEGIN;
ALTER TABLE reconciliation_item DISABLE TRIGGER reconciliation_item_append_only;
ALTER TABLE reconciliation_run DISABLE TRIGGER reconciliation_run_append_only;
ALTER TABLE data_drift_event DISABLE TRIGGER data_drift_event_guard;
DELETE FROM reconciliation_item;
DELETE FROM reconciliation_run;
DELETE FROM data_drift_event;
ALTER TABLE reconciliation_item ENABLE TRIGGER reconciliation_item_append_only;
ALTER TABLE reconciliation_run ENABLE TRIGGER reconciliation_run_append_only;
ALTER TABLE data_drift_event ENABLE TRIGGER data_drift_event_guard;
COMMIT;
"""

# WS3 domain rows (Phase 14) reference imports, canonical records, sources and schema
# versions: removed first. Append-only (TRUNCATE included), so the triggers are lifted for
# this one transaction; the seeded instrument types stay.
_DOMAIN_TABLES = (
    "instrument_attribute_value",
    "instrument_attribute_definition",
    "cashflow_event",
    "position_version",
    "instrument_version",
    "position",
    "instrument",
    "instrument_type",
    "domain_projection",
)
_DOMAIN_CLEANUP = "\n".join(
    [
        "BEGIN;",
        *(f"ALTER TABLE {t} DISABLE TRIGGER {t}_append_only;" for t in _DOMAIN_TABLES),
        *(
            f"DELETE FROM {t}" + (" WHERE seeded_by IS NULL;" if t == "instrument_type" else ";")
            for t in _DOMAIN_TABLES
        ),
        *(f"ALTER TABLE {t} ENABLE TRIGGER {t}_append_only;" for t in _DOMAIN_TABLES),
        "COMMIT;",
    ]
)

# Feature 04 credit rows (Phase 19, 0022) reference domain projections, imports and canonical
# records: removed before the domain rows. Append-only (TRUNCATE included), so every user
# trigger is lifted for this one transaction; the tagged credit_vocabulary seed stays.
_CREDIT_TABLES = (
    "credit_exposure_version",
    "counterparty_version",
    "credit_exposure",
    "counterparty",
    "credit_vocabulary",
)
_CREDIT_CLEANUP = "\n".join(
    [
        "BEGIN;",
        *(f"ALTER TABLE {t} DISABLE TRIGGER USER;" for t in _CREDIT_TABLES),
        *(
            f"DELETE FROM {t}" + (" WHERE seeded_by IS NULL;" if t == "credit_vocabulary" else ";")
            for t in _CREDIT_TABLES
        ),
        *(f"ALTER TABLE {t} ENABLE TRIGGER USER;" for t in _CREDIT_TABLES),
        "COMMIT;",
    ]
)

# WS3 snapshots, runs and the rule registry (Phase 15) reference domain rows, imports and
# reconciliation runs: removed first. Append-only or lifecycle-guarded (TRUNCATE included),
# so every user trigger of these tables is lifted for this one transaction. The tagged seed
# stays, put back to its seeded status. Phase 17's LCR outputs (0020) go first: an asset
# row references its LCR row, and both reference their run.
_CALC_TABLES = (
    "hqla_asset_result",
    "lcr_result",
    "calculation_warning",
    "intermediate_value",
    "liquidity_gap_result",
    "calculation_input_ref",
    "calculation_run",
    "dataset_snapshot_member",
    "dataset_snapshot",
    "rule_set_member",
    "rule_set",
)
_RULE_REGISTRY_TABLES = (
    "maturity_bucket_definition",
    "rule_version",
    "rule_definition",
    "assumption_set",
    "module_version",
    "calculation_module",
)
_CALC_CLEANUP = "\n".join(
    [
        "BEGIN;",
        *(f"ALTER TABLE {t} DISABLE TRIGGER USER;" for t in _CALC_TABLES + _RULE_REGISTRY_TABLES),
        *(f"DELETE FROM {t};" for t in _CALC_TABLES),
        *(f"DELETE FROM {t} WHERE seeded_by IS NULL;" for t in _RULE_REGISTRY_TABLES),
        "UPDATE rule_version SET status = 'ACTIVE' WHERE seeded_by IS NOT NULL"
        " AND status <> 'ACTIVE';",
        "UPDATE assumption_set SET status = 'ACTIVE' WHERE seeded_by IS NOT NULL"
        " AND status <> 'ACTIVE';",
        *(f"ALTER TABLE {t} ENABLE TRIGGER USER;" for t in _CALC_TABLES + _RULE_REGISTRY_TABLES),
        "COMMIT;",
    ]
)

# Phase 20 obligor ratings (0023) reference counterparties and their proposer's user
# account: removed before both. Lifecycle-guarded (DELETE and TRUNCATE refused), so the
# table's user triggers are lifted for this one transaction.
_RATING_CLEANUP = """
BEGIN;
ALTER TABLE obligor_rating DISABLE TRIGGER USER;
DELETE FROM obligor_rating;
ALTER TABLE obligor_rating ENABLE TRIGGER USER;
COMMIT;
"""

# Phase 24 scenarios (0026) reference their author's user account: removed before it.
# Lifecycle-guarded (DELETE and TRUNCATE refused), so the user triggers are lifted for this
# one transaction.
_SCENARIO_TABLES = ("scenario_change", "scenario_version", "scenario")
# Phase 25 (0027): the scenario runs and their run outputs reference calculation runs and
# scenario versions, so they go first; append-only, so their user triggers are lifted.
_SCENARIO_RUN_TABLES = (
    "scenario_contribution",
    "scenario_shock_application",
    "calculation_scenario_pin",
    "scenario_run",
)
_SCENARIO_RUN_CLEANUP = "\n".join(
    [
        "BEGIN;",
        *(f"ALTER TABLE {t} DISABLE TRIGGER USER;" for t in _SCENARIO_RUN_TABLES),
        *(f"DELETE FROM {t};" for t in _SCENARIO_RUN_TABLES),
        *(f"ALTER TABLE {t} ENABLE TRIGGER USER;" for t in _SCENARIO_RUN_TABLES),
        "COMMIT;",
    ]
)
_SCENARIO_CLEANUP = "\n".join(
    [
        "BEGIN;",
        *(f"ALTER TABLE {t} DISABLE TRIGGER USER;" for t in _SCENARIO_TABLES),
        *(f"DELETE FROM {t};" for t in _SCENARIO_TABLES),
        *(f"ALTER TABLE {t} ENABLE TRIGGER USER;" for t in _SCENARIO_TABLES),
        "COMMIT;",
    ]
)

# Phase 31 (0030, D-58): identity links reference domain and credit rows by id, legal
# entities their source system. Append-only (TRUNCATE included), so their user triggers are
# lifted for this one transaction; removed before the domain and credit rows.
_IDENTITY_TABLES = ("domain_identity_link", "legal_entity")
_IDENTITY_CLEANUP = "\n".join(
    [
        "BEGIN;",
        *(f"ALTER TABLE {t} DISABLE TRIGGER USER;" for t in _IDENTITY_TABLES),
        *(f"DELETE FROM {t};" for t in _IDENTITY_TABLES),
        *(f"ALTER TABLE {t} ENABLE TRIGGER USER;" for t in _IDENTITY_TABLES),
        "COMMIT;",
    ]
)


def clear_identity_links(url: str) -> None:
    """Phase 31: remove every legal entity and identity link (so 0030's downgrade, which
    refuses while they exist, can pass in a test that downgrades past it)."""
    try:
        script(url, _IDENTITY_CLEANUP)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedObjectError):
        pass  # below 0030: nothing to clear


# Deletes everything that is not seed data (D-11), children first.
_CLEANUP = [
    _IDENTITY_CLEANUP,
    _SCENARIO_RUN_CLEANUP,
    _SCENARIO_CLEANUP,
    _RATING_CLEANUP,
    "DELETE FROM user_role",
    "DELETE FROM user_account",
    "DELETE FROM role_permission WHERE seeded_by IS NULL",
    "DELETE FROM role WHERE seeded_by IS NULL",
    "DELETE FROM permission WHERE seeded_by IS NULL",
    _CALC_CLEANUP,
    _CREDIT_CLEANUP,
    _DOMAIN_CLEANUP,
    _PHASE12_CLEANUP,
    _VALIDATION_CLEANUP,
    _MAPPING_CLEANUP,
    _PROFILE_CLEANUP,
    _IMPORT_CLEANUP,
    _INGESTION_CLEANUP,
    _REGISTRY_CLEANUP,
    "DELETE FROM tenant WHERE seeded_by IS NULL",
]


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["skip_logging"] = True
    # Tests reset the schema freely; the audit-trail downgrade guard is tested on its own.
    cfg.attributes["allow_audit_loss"] = True
    return cfg


def sql(url: str, statement: str, *args: Any) -> list[Any]:
    """Run one statement directly with asyncpg (outside the app) and return rows."""

    async def run() -> list[Any]:
        conn = await asyncpg.connect(url.replace("+asyncpg", ""))
        try:
            return list(await conn.fetch(statement, *args))
        finally:
            await conn.close()

    return asyncio.run(run())


def script(url: str, statements: str) -> None:
    """Run a multi-statement script directly with asyncpg."""

    async def run() -> None:
        conn = await asyncpg.connect(url.replace("+asyncpg", ""))
        try:
            await conn.execute(statements)
        finally:
            await conn.close()

    asyncio.run(run())


def clean(url: str) -> None:
    for statement in _CLEANUP:
        if statement in (
            _IDENTITY_CLEANUP,
            _SCENARIO_RUN_CLEANUP,
            _SCENARIO_CLEANUP,
            _RATING_CLEANUP,
            _CALC_CLEANUP,
            _CREDIT_CLEANUP,
            _DOMAIN_CLEANUP,
            _INGESTION_CLEANUP,
            _IMPORT_CLEANUP,
            _PROFILE_CLEANUP,
            _MAPPING_CLEANUP,
            _VALIDATION_CLEANUP,
            _PHASE12_CLEANUP,
        ):
            try:
                script(url, statement)
            except (asyncpg.UndefinedTableError, asyncpg.UndefinedObjectError):
                pass  # an older schema (session start): nothing of that phase to clean
        elif statement is _REGISTRY_CLEANUP:
            script(url, statement)
        else:
            sql(url, statement)


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    cfg = alembic_config(database_url)
    try:
        clean(database_url)  # previous run's leftovers would block the seed downgrade
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedObjectError):
        pass  # an older schema: the downgrade below starts from what is there
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
def db(migrated_db: str) -> Iterator[str]:
    clean(migrated_db)
    yield migrated_db
    clean(migrated_db)


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    """This test's own artifact store root (D-15), outside the repository."""
    return tmp_path / "artifacts"


@pytest.fixture
def api(db: str, artifact_dir: Path) -> Iterator[TestClient]:
    settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir)
    with TestClient(create_app(settings)) as c:
        yield c


def dev_token(client: TestClient, role: str) -> dict[str, str]:
    r = client.post("/api/v1/dev/sessions", json={"role": role})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def insert_user(
    url: str,
    issuer: str,
    subject: str,
    roles: tuple[str, ...] = (),
    tenant_id: Any = DEFAULT_TENANT_ID,
    status: str = "ACTIVE",
) -> Any:
    row = sql(
        url,
        "INSERT INTO user_account (id, tenant_id, idp_issuer, idp_subject, email, display_name,"
        " status, created_by) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, 'test')"
        " RETURNING id",
        tenant_id,
        issuer,
        subject,
        f"{subject}@example.test",
        f"User {subject}",
        status,
    )
    user_id = row[0]["id"]
    for code in roles:
        sql(
            url,
            "INSERT INTO user_role (user_id, role_id, created_by)"
            " SELECT $1, id, 'test' FROM role WHERE code = $2",
            user_id,
            code,
        )
    return user_id


def insert_tenant(url: str, slug: str) -> Any:
    return sql(
        url,
        "INSERT INTO tenant (id, name, slug, status, created_by)"
        " VALUES (gen_random_uuid(), $1, $1, 'ACTIVE', 'test') RETURNING id",
        slug,
    )[0]["id"]
