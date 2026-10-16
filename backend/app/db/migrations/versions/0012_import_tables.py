"""Import tables (BE-8.1, DI-02): import_job, import_artifact, import_batch, source_record.

* `import_artifact`, `import_batch` and `source_record` are append-only: UPDATE, DELETE and
  TRUNCATE raise (D-20 §3: corrections are new imports, never edits).
* `import_job` is never deleted or truncated. Its content (CONTENT_COLUMNS) is frozen from
  creation; only `status` and `updated_at` change, `status` only along the transition table
  below (a frozen copy of app.ingestion.constants.IMPORT_TRANSITIONS, kept equal by a test),
  and the outcome statuses are final.
* An import references its own tenant's source, a contract of that source, a schema version
  of its own tenant, and retries only an import of its own tenant (D-04).
* Every enum column has a CHECK (D-06, D-23); a synthetic import is always attested (D-16 §5).

No session setting unlocks a trigger; disabling one needs the table owner. The downgrade
refuses while any import exists (imports are kept, D-20 §4).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-16 13:38:01.089510

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTENT_COLUMNS = (
    "id",
    "tenant_id",
    "source_system_id",
    "source_contract_id",
    "schema_version_id",
    "format",
    "data_class",
    "synthetic_attested",
    "synthetic_marker",
    "idempotency_key",
    "request_sha256",
    "correlation_id",
    "retry_of_import_id",
    "expected_row_count",
    "control_totals",
    "row_count",
    "created_by",
    "created_at",
)
# D-06/D-21 transition table: a frozen copy of IMPORT_TRANSITIONS, kept equal by
# test_import_tables. A status may also be "set" to itself, which is not a move.
IMPORT_TRANSITIONS: dict[str, list[str]] = {
    "CREATED": ["CANCELLED", "FAILED", "RECEIVED"],
    "RECEIVED": ["CANCELLED", "FAILED", "PROFILING", "VALIDATING"],
    "PROFILING": ["FAILED", "MAPPING_REQUIRED", "RECEIVED"],
    "MAPPING_REQUIRED": ["CANCELLED", "FAILED", "VALIDATING"],
    "VALIDATING": ["COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "QUARANTINED"],
    "COMPLETED": [],
    "COMPLETED_WITH_WARNINGS": [],
    "QUARANTINED": [],
    "FAILED": [],
    "CANCELLED": [],
}
FINAL = ", ".join(f"'{s}'" for s, targets in IMPORT_TRANSITIONS.items() if not targets)
ALLOWED_MOVES = ", ".join(
    f"('{a}', '{b}')" for a, targets in IMPORT_TRANSITIONS.items() for b in targets
)
NEW_CONTENT = ", ".join(f"NEW.{c}" for c in CONTENT_COLUMNS)
OLD_CONTENT = ", ".join(f"OLD.{c}" for c in CONTENT_COLUMNS)

JOB_GUARD = f"""
CREATE FUNCTION import_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'import_job is immutable (BE-8.1): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'import_job % is immutable (BE-8.1): DELETE rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status IN ({FINAL}) THEN
        RAISE EXCEPTION 'import_job % is %, which is final and immutable (D-21)',
            OLD.id, OLD.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF ({NEW_CONTENT}) IS DISTINCT FROM ({OLD_CONTENT}) THEN
        RAISE EXCEPTION 'import_job % is immutable (BE-8.1): content change rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
        AND (OLD.status, NEW.status) NOT IN ({ALLOWED_MOVES})
    THEN
        RAISE EXCEPTION 'import_job % is immutable (D-06): % -> % is not an allowed transition',
            OLD.id, OLD.status, NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

APPEND_ONLY = """
CREATE FUNCTION import_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (BE-8.1, D-20): % rejected',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

# D-04: every reference of an import stays inside its tenant and its source.
SAME_TENANT = """
CREATE FUNCTION import_job_same_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT tenant_id FROM source_system WHERE id = NEW.source_system_id)
        IS DISTINCT FROM NEW.tenant_id
    OR (
        SELECT d.tenant_id FROM schema_version v
            JOIN schema_definition d ON d.id = v.schema_id
            WHERE v.id = NEW.schema_version_id
    ) IS DISTINCT FROM NEW.tenant_id
    OR (
        NEW.retry_of_import_id IS NOT NULL
        AND (SELECT tenant_id FROM import_job WHERE id = NEW.retry_of_import_id)
            IS DISTINCT FROM NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'import_job must reference its own tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (
        SELECT p.source_system_id FROM source_contract c
            JOIN source_profile p ON p.id = c.source_profile_id
            WHERE c.id = NEW.source_contract_id
    ) IS DISTINCT FROM NEW.source_system_id THEN
        RAISE EXCEPTION 'import_job must pin a contract of its own source (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

APPEND_ONLY_TABLES = ("import_artifact", "import_batch", "source_record")


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('import_job',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('source_system_id', sa.UUID(), nullable=False),
    sa.Column('source_contract_id', sa.UUID(), nullable=False),
    sa.Column('schema_version_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('format', sa.String(length=10), nullable=False),
    sa.Column('data_class', sa.String(length=20), nullable=False),
    sa.Column('synthetic_attested', sa.Boolean(), nullable=False),
    sa.Column('synthetic_marker', sa.Boolean(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('request_sha256', sa.String(length=64), nullable=False),
    sa.Column('correlation_id', sa.String(length=128), nullable=True),
    sa.Column('retry_of_import_id', sa.UUID(), nullable=True),
    sa.Column('expected_row_count', sa.Integer(), nullable=True),
    sa.Column('control_totals', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("data_class <> 'synthetic' OR synthetic_attested", name=op.f('ck_import_job_synthetic_attested')),
    sa.CheckConstraint("data_class IN ('synthetic', 'client')", name=op.f('ck_import_job_data_class')),
    sa.CheckConstraint("format IN ('CSV', 'XLSX', 'JSON')", name=op.f('ck_import_job_format')),
    sa.CheckConstraint("status IN ('CREATED', 'RECEIVED', 'PROFILING', 'MAPPING_REQUIRED', 'VALIDATING', 'COMPLETED', 'COMPLETED_WITH_WARNINGS', 'QUARANTINED', 'FAILED', 'CANCELLED')", name=op.f('ck_import_job_status')),
    sa.CheckConstraint('expected_row_count IS NULL OR expected_row_count >= 0', name=op.f('ck_import_job_expected_row_count')),
    sa.CheckConstraint('row_count >= 0', name=op.f('ck_import_job_row_count')),
    sa.ForeignKeyConstraint(['retry_of_import_id'], ['import_job.id'], name=op.f('fk_import_job_retry_of_import_id_import_job')),
    sa.ForeignKeyConstraint(['schema_version_id'], ['schema_version.id'], name=op.f('fk_import_job_schema_version_id_schema_version')),
    sa.ForeignKeyConstraint(['source_contract_id'], ['source_contract.id'], name=op.f('fk_import_job_source_contract_id_source_contract')),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_system.id'], name=op.f('fk_import_job_source_system_id_source_system')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_import_job_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_job')),
    sa.UniqueConstraint('tenant_id', 'created_by', 'idempotency_key', name=op.f('uq_import_job_tenant_id'))
    )
    op.create_index(op.f('ix_import_job_source_system_id'), 'import_job', ['source_system_id'], unique=False)
    op.create_index('ix_import_job_tenant_created', 'import_job', ['tenant_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_import_job_tenant_id'), 'import_job', ['tenant_id'], unique=False)
    op.create_table('import_artifact',
    sa.Column('import_job_id', sa.UUID(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('detected_mime', sa.String(length=100), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=True),
    sa.Column('storage_ref', sa.String(length=200), nullable=False),
    sa.Column('classification', sa.String(length=50), nullable=False),
    sa.Column('retention_state', sa.String(length=20), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("retention_state IN ('RETAINED')", name=op.f('ck_import_artifact_retention_state')),
    sa.CheckConstraint("storage_ref = 'sha256/' || substr(sha256, 1, 2) || '/' || sha256", name=op.f('ck_import_artifact_storage_ref')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_import_artifact_size_bytes')),
    sa.ForeignKeyConstraint(['classification'], ['data_classification.code'], name=op.f('fk_import_artifact_classification_data_classification')),
    sa.ForeignKeyConstraint(['import_job_id'], ['import_job.id'], name=op.f('fk_import_artifact_import_job_id_import_job')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_artifact'))
    )
    op.create_index(op.f('ix_import_artifact_import_job_id'), 'import_artifact', ['import_job_id'], unique=True)
    op.create_table('import_batch',
    sa.Column('import_job_id', sa.UUID(), nullable=False),
    sa.Column('batch_number', sa.Integer(), nullable=False),
    sa.Column('sheet_name', sa.String(length=31), nullable=True),
    sa.Column('columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint('batch_number >= 1', name=op.f('ck_import_batch_batch_number')),
    sa.CheckConstraint('row_count >= 0', name=op.f('ck_import_batch_row_count')),
    sa.ForeignKeyConstraint(['import_job_id'], ['import_job.id'], name=op.f('fk_import_batch_import_job_id_import_job')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_import_batch')),
    sa.UniqueConstraint('import_job_id', 'batch_number', name=op.f('uq_import_batch_import_job_id'))
    )
    op.create_table('source_record',
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('locator', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('raw_values', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('extra_values', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('flags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('row_sha256', sa.String(length=64), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint('ordinal >= 1', name=op.f('ck_source_record_ordinal')),
    sa.ForeignKeyConstraint(['batch_id'], ['import_batch.id'], name=op.f('fk_source_record_batch_id_import_batch')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_record')),
    sa.UniqueConstraint('batch_id', 'ordinal', name=op.f('uq_source_record_batch_id'))
    )
    # ### end Alembic commands ###
    # One statement per op.execute: asyncpg cannot run several in one call.
    op.execute(JOB_GUARD)
    op.execute(APPEND_ONLY)
    op.execute(SAME_TENANT)
    op.execute(
        "CREATE TRIGGER import_job_immutable BEFORE UPDATE OR DELETE ON import_job "
        "FOR EACH ROW EXECUTE FUNCTION import_job_guard()"
    )
    op.execute(
        "CREATE TRIGGER import_job_no_truncate BEFORE TRUNCATE ON import_job "
        "FOR EACH STATEMENT EXECUTE FUNCTION import_job_guard()"
    )
    op.execute(
        "CREATE TRIGGER import_job_same_tenant BEFORE INSERT OR UPDATE ON import_job "
        "FOR EACH ROW EXECUTE FUNCTION import_job_same_tenant()"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION import_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION import_append_only()"
        )


def downgrade() -> None:
    # Imports are user data and are kept (D-20 §4): refuse rather than drop them.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM import_job) THEN RAISE EXCEPTION "
        "'import_job has rows; imports are kept (D-20), so this downgrade is refused'; "
        "END IF; END $$"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP TRIGGER IF EXISTS import_job_same_tenant ON import_job")
    op.execute("DROP TRIGGER IF EXISTS import_job_no_truncate ON import_job")
    op.execute("DROP TRIGGER IF EXISTS import_job_immutable ON import_job")
    for fn in ("import_job_same_tenant", "import_append_only", "import_job_guard"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('source_record')
    op.drop_table('import_batch')
    op.drop_index(op.f('ix_import_artifact_import_job_id'), table_name='import_artifact')
    op.drop_table('import_artifact')
    op.drop_index(op.f('ix_import_job_tenant_id'), table_name='import_job')
    op.drop_index('ix_import_job_tenant_created', table_name='import_job')
    op.drop_index(op.f('ix_import_job_source_system_id'), table_name='import_job')
    op.drop_table('import_job')
    # ### end Alembic commands ###
