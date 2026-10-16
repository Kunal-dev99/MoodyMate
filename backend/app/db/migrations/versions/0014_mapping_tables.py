"""Versioned mapping tables (BE-10.1, DI-03, D-18): field_mapping, mapping_version,
mapping_rule, mapping_validation, and the nullable import_job.mapping_version_id (D-01).

* `field_mapping` is never deleted and its content never changes (only `updated_at`). It
  stays in its tenant and names a contract of its own source and that contract's target
  schema (D-04).
* `mapping_version` is never deleted. Its identity (mapping, number, pinned schema version,
  author) never changes; once it leaves DRAFT its content (hash, summary, last validation,
  submission) is frozen too; its decision is written once; and its status moves only along
  the transition table below (a frozen copy of
  app.mapping.constants.MAPPING_VERSION_TRANSITIONS). A version is inserted as DRAFT with
  no decision, pins a version of its mapping's schema, and at most one per mapping is
  ACTIVE (partial unique index).
* `mapping_rule` rows may be inserted, changed or deleted only while their version is DRAFT
  (the version row is share-locked while checked, so a concurrent submit waits).
* `mapping_validation` is append-only and stays inside the tenant of its import.
* `import_job.mapping_version_id` is written once (NULL -> an ACTIVE version of the
  mapping of the import's own contract, pinned to the import's schema version), then frozen
  (D-18 §5).
* TRUNCATE is refused on all four new tables. Every enum column has a CHECK (D-06, D-23).

No session setting unlocks any trigger; disabling one needs the table owner. The downgrade
refuses while any mapping exists (D-02: a bad mapping is undone by a new version).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-16 17:38:06.908677

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# D-06 transition table: a frozen copy of MAPPING_VERSION_TRANSITIONS, kept equal by
# test_mappings. A status may also be "set" to itself, which is not a move.
MAPPING_VERSION_TRANSITIONS: dict[str, list[str]] = {
    "DRAFT": ["IN_REVIEW", "REJECTED"],
    "IN_REVIEW": ["ACTIVE", "REJECTED"],
    "APPROVED": [],
    "ACTIVE": ["DEPRECATED"],
    "DEPRECATED": ["RETIRED"],
    "RETIRED": [],
    "REJECTED": [],
}
ALLOWED_MOVES = ", ".join(
    f"('{a}', '{b}')" for a, targets in MAPPING_VERSION_TRANSITIONS.items() for b in targets
)


def _row(prefix: str, columns: Sequence[str]) -> str:
    return "(" + ", ".join(f"{prefix}.{c}" for c in columns) + ")"


MAPPING_COLUMNS = (
    "id", "tenant_id", "code", "name", "description", "source_system_id",
    "source_contract_id", "schema_id", "created_by", "created_at",
)
VERSION_IDENTITY = (
    "id", "field_mapping_id", "version", "schema_version_id", "author_user_id",
    "created_by", "created_at",
)
VERSION_CONTENT = ("content_sha256", "change_summary", "last_validation_id", "submitted_at")
VERSION_DECISION = ("decided_by", "decided_by_user_id", "decided_at", "decision_comment")

MAPPING_GUARD = f"""
CREATE FUNCTION field_mapping_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'field_mapping is immutable (BE-10.1): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'field_mapping % is immutable (BE-10.1): DELETE rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF {_row("NEW", MAPPING_COLUMNS)} IS DISTINCT FROM {_row("OLD", MAPPING_COLUMNS)} THEN
        RAISE EXCEPTION 'field_mapping % is immutable (BE-10.1): content change rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# D-04: a mapping names its own tenant's source, a contract of that source, and the
# contract's target schema.
MAPPING_SAME_TENANT = """
CREATE FUNCTION field_mapping_same_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT tenant_id FROM source_system WHERE id = NEW.source_system_id)
        IS DISTINCT FROM NEW.tenant_id
    OR (SELECT tenant_id FROM schema_definition WHERE id = NEW.schema_id)
        IS DISTINCT FROM NEW.tenant_id
    OR (
        SELECT p.source_system_id FROM source_contract c
            JOIN source_profile p ON p.id = c.source_profile_id
            WHERE c.id = NEW.source_contract_id
    ) IS DISTINCT FROM NEW.source_system_id
    OR (SELECT schema_id FROM source_contract WHERE id = NEW.source_contract_id)
        IS DISTINCT FROM NEW.schema_id
    THEN
        RAISE EXCEPTION 'field_mapping must map a contract of its own tenant to its target'
            ' schema (D-04)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

VERSION_GUARD = f"""
CREATE FUNCTION mapping_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'mapping_version is immutable (BE-10.1): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'mapping_version % is immutable (BE-10.1): DELETE rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF {_row("NEW", VERSION_IDENTITY)} IS DISTINCT FROM {_row("OLD", VERSION_IDENTITY)} THEN
        RAISE EXCEPTION 'mapping_version % is immutable (BE-10.1): identity change rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'DRAFT'
        AND {_row("NEW", VERSION_CONTENT)} IS DISTINCT FROM {_row("OLD", VERSION_CONTENT)}
    THEN
        RAISE EXCEPTION 'mapping_version % is %, so it is immutable (D-18): content change'
            ' rejected', OLD.id, OLD.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.decided_at IS NOT NULL
        AND {_row("NEW", VERSION_DECISION)} IS DISTINCT FROM {_row("OLD", VERSION_DECISION)}
    THEN
        RAISE EXCEPTION 'mapping_version % is immutable (D-18): its decision is final',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
        AND (OLD.status, NEW.status) NOT IN ({ALLOWED_MOVES})
    THEN
        RAISE EXCEPTION 'mapping_version % is immutable (D-06): % -> % is not an allowed'
            ' transition', OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# A version starts as an undecided DRAFT (no maker-checker bypass by INSERT), pins a version
# of its mapping's schema, and its last validation is one of its own.
VERSION_CHECKS = """
CREATE FUNCTION mapping_version_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND (NEW.status <> 'DRAFT' OR NEW.decided_at IS NOT NULL
        OR NEW.submitted_at IS NOT NULL)
    THEN
        RAISE EXCEPTION 'mapping_version must be created as an undecided DRAFT (D-18)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (SELECT schema_id FROM schema_version WHERE id = NEW.schema_version_id)
        IS DISTINCT FROM (SELECT schema_id FROM field_mapping WHERE id = NEW.field_mapping_id)
    THEN
        RAISE EXCEPTION 'mapping_version must pin a version of its mapping''s schema (D-18)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.last_validation_id IS NOT NULL AND (
        SELECT mapping_version_id FROM mapping_validation WHERE id = NEW.last_validation_id
    ) IS DISTINCT FROM NEW.id THEN
        RAISE EXCEPTION 'mapping_version may only reference its own validation'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

RULE_GUARD = """
CREATE FUNCTION mapping_rule_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_status text;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'mapping_rule is immutable (BE-10.1): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO version_status FROM mapping_version
            WHERE id = OLD.mapping_version_id FOR SHARE;
        IF version_status IS DISTINCT FROM 'DRAFT' THEN
            RAISE EXCEPTION 'mapping_rule % belongs to a % version, so it is immutable'
                ' (D-18): % rejected', OLD.id, version_status, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO version_status FROM mapping_version
            WHERE id = NEW.mapping_version_id FOR SHARE;
        IF version_status IS DISTINCT FROM 'DRAFT' THEN
            RAISE EXCEPTION 'mapping_rule: version % is %, so it is immutable (D-18): %'
                ' rejected', NEW.mapping_version_id, version_status, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$
"""

APPEND_ONLY = """
CREATE FUNCTION mapping_validation_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (BE-10.4): % rejected',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

VALIDATION_SAME_TENANT = """
CREATE FUNCTION mapping_validation_same_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT tenant_id FROM import_job WHERE id = NEW.import_job_id) IS DISTINCT FROM (
        SELECT m.tenant_id FROM mapping_version v
            JOIN field_mapping m ON m.id = v.field_mapping_id
            WHERE v.id = NEW.mapping_version_id
    ) THEN
        RAISE EXCEPTION 'mapping_validation must stay within one tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# D-18 §5: an import pins its mapping version once, and only a usable one.
IMPORT_PIN = """
CREATE FUNCTION import_job_mapping_pin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.mapping_version_id IS NOT NULL
        AND NEW.mapping_version_id IS DISTINCT FROM OLD.mapping_version_id
    THEN
        RAISE EXCEPTION 'import_job % pins its mapping version permanently (D-18)',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.mapping_version_id IS NOT NULL
        AND (TG_OP = 'INSERT' OR OLD.mapping_version_id IS NULL)
        AND (
            SELECT ROW(m.tenant_id, m.source_contract_id, v.schema_version_id, v.status)
                FROM mapping_version v JOIN field_mapping m ON m.id = v.field_mapping_id
                WHERE v.id = NEW.mapping_version_id
        ) IS DISTINCT FROM
            ROW(NEW.tenant_id, NEW.source_contract_id, NEW.schema_version_id, 'ACTIVE'::varchar)
    THEN
        RAISE EXCEPTION 'import_job must pin an ACTIVE mapping version of its own contract and'
            ' schema version (D-18)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (
    MAPPING_GUARD,
    MAPPING_SAME_TENANT,
    VERSION_GUARD,
    VERSION_CHECKS,
    RULE_GUARD,
    APPEND_ONLY,
    VALIDATION_SAME_TENANT,
    IMPORT_PIN,
)
# (trigger, timing and events, table, level, function)
TRIGGERS = (
    ("field_mapping_immutable", "BEFORE UPDATE OR DELETE", "field_mapping", "ROW",
     "field_mapping_guard"),
    ("field_mapping_no_truncate", "BEFORE TRUNCATE", "field_mapping", "STATEMENT",
     "field_mapping_guard"),
    ("field_mapping_same_tenant", "BEFORE INSERT", "field_mapping", "ROW",
     "field_mapping_same_tenant"),
    ("mapping_version_immutable", "BEFORE UPDATE OR DELETE", "mapping_version", "ROW",
     "mapping_version_guard"),
    ("mapping_version_no_truncate", "BEFORE TRUNCATE", "mapping_version", "STATEMENT",
     "mapping_version_guard"),
    ("mapping_version_checks", "BEFORE INSERT OR UPDATE", "mapping_version", "ROW",
     "mapping_version_checks"),
    ("mapping_rule_immutable", "BEFORE INSERT OR UPDATE OR DELETE", "mapping_rule", "ROW",
     "mapping_rule_guard"),
    ("mapping_rule_no_truncate", "BEFORE TRUNCATE", "mapping_rule", "STATEMENT",
     "mapping_rule_guard"),
    ("mapping_validation_append_only", "BEFORE UPDATE OR DELETE", "mapping_validation", "ROW",
     "mapping_validation_append_only"),
    ("mapping_validation_no_truncate", "BEFORE TRUNCATE", "mapping_validation", "STATEMENT",
     "mapping_validation_append_only"),
    ("mapping_validation_same_tenant", "BEFORE INSERT", "mapping_validation", "ROW",
     "mapping_validation_same_tenant"),
    ("import_job_mapping_pin", "BEFORE INSERT OR UPDATE", "import_job", "ROW",
     "import_job_mapping_pin"),
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('field_mapping',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('source_system_id', sa.UUID(), nullable=False),
    sa.Column('source_contract_id', sa.UUID(), nullable=False),
    sa.Column('schema_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['schema_id'], ['schema_definition.id'], name=op.f('fk_field_mapping_schema_id_schema_definition')),
    sa.ForeignKeyConstraint(['source_contract_id'], ['source_contract.id'], name=op.f('fk_field_mapping_source_contract_id_source_contract')),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_system.id'], name=op.f('fk_field_mapping_source_system_id_source_system')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_field_mapping_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_field_mapping')),
    sa.UniqueConstraint('source_contract_id', name=op.f('uq_field_mapping_source_contract_id')),
    sa.UniqueConstraint('tenant_id', 'code', name=op.f('uq_field_mapping_tenant_id'))
    )
    op.create_index(op.f('ix_field_mapping_schema_id'), 'field_mapping', ['schema_id'], unique=False)
    op.create_index(op.f('ix_field_mapping_source_system_id'), 'field_mapping', ['source_system_id'], unique=False)
    op.create_index(op.f('ix_field_mapping_tenant_id'), 'field_mapping', ['tenant_id'], unique=False)
    op.create_table('mapping_version',
    sa.Column('field_mapping_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('schema_version_id', sa.UUID(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=False),
    sa.Column('change_summary', sa.String(length=1000), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.String(length=255), nullable=True),
    sa.Column('decided_by_user_id', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_comment', sa.String(length=2000), nullable=True),
    sa.Column('last_validation_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_mapping_version_content_sha256')),
    sa.CheckConstraint("status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'ACTIVE', 'DEPRECATED', 'RETIRED', 'REJECTED')", name=op.f('ck_mapping_version_status')),
    sa.CheckConstraint('(decided_at IS NULL) = (decided_by IS NULL) AND (decided_at IS NULL) = (decided_by_user_id IS NULL)', name=op.f('ck_mapping_version_decision')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_mapping_version_version')),
    sa.ForeignKeyConstraint(['field_mapping_id'], ['field_mapping.id'], name=op.f('fk_mapping_version_field_mapping_id_field_mapping')),
    sa.ForeignKeyConstraint(['schema_version_id'], ['schema_version.id'], name=op.f('fk_mapping_version_schema_version_id_schema_version')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mapping_version')),
    sa.UniqueConstraint('field_mapping_id', 'version', name=op.f('uq_mapping_version_field_mapping_id'))
    )
    op.create_index(op.f('ix_mapping_version_field_mapping_id'), 'mapping_version', ['field_mapping_id'], unique=False)
    op.create_index('uq_mapping_version_active', 'mapping_version', ['field_mapping_id'], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"))
    op.create_table('mapping_rule',
    sa.Column('mapping_version_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('source_column', sa.String(length=200), nullable=True),
    sa.Column('target_field', sa.String(length=100), nullable=True),
    sa.Column('transform_code', sa.String(length=30), nullable=False),
    sa.Column('transform_params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('null_policy', sa.String(length=20), nullable=False),
    sa.Column('default_value', sa.String(length=200), nullable=True),
    sa.Column('column_policy', sa.String(length=10), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("(column_policy = 'MAP') = (target_field IS NOT NULL)", name=op.f('ck_mapping_rule_policy_target')),
    sa.CheckConstraint("(null_policy = 'USE_DEFAULT') = (default_value IS NOT NULL)", name=op.f('ck_mapping_rule_default_value')),
    sa.CheckConstraint("(source_column IS NULL) = (transform_code = 'CONSTANT')", name=op.f('ck_mapping_rule_source_column')),
    sa.CheckConstraint("column_policy = 'MAP' OR (source_column IS NOT NULL AND transform_code = 'COPY')", name=op.f('ck_mapping_rule_preserve')),
    sa.CheckConstraint("column_policy IN ('MAP', 'PRESERVE')", name=op.f('ck_mapping_rule_column_policy')),
    sa.CheckConstraint("jsonb_typeof(transform_params) = 'object'", name=op.f('ck_mapping_rule_transform_params')),
    sa.CheckConstraint("null_policy IN ('ALLOW_NULL', 'REJECT_NULL', 'USE_DEFAULT')", name=op.f('ck_mapping_rule_null_policy')),
    sa.CheckConstraint("transform_code IN ('COPY', 'TRIM', 'UPPER', 'LOWER', 'PARSE_DECIMAL', 'SCALE', 'PARSE_DATE', 'PARSE_BOOLEAN', 'MAP_VALUES', 'DEFAULT', 'CONSTANT')", name=op.f('ck_mapping_rule_transform_code')),
    sa.CheckConstraint('position >= 1', name=op.f('ck_mapping_rule_position')),
    sa.ForeignKeyConstraint(['mapping_version_id'], ['mapping_version.id'], name=op.f('fk_mapping_rule_mapping_version_id_mapping_version')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mapping_rule')),
    sa.UniqueConstraint('mapping_version_id', 'position', name=op.f('uq_mapping_rule_mapping_version_id'))
    )
    op.create_index(op.f('ix_mapping_rule_mapping_version_id'), 'mapping_rule', ['mapping_version_id'], unique=False)
    op.create_index('uq_mapping_rule_target', 'mapping_rule', ['mapping_version_id', 'target_field'], unique=True, postgresql_where=sa.text('target_field IS NOT NULL'))
    op.create_table('mapping_validation',
    sa.Column('mapping_version_id', sa.UUID(), nullable=False),
    sa.Column('import_job_id', sa.UUID(), nullable=False),
    sa.Column('mapping_sha256', sa.String(length=64), nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('blocker_count', sa.Integer(), nullable=False),
    sa.Column('error_count', sa.Integer(), nullable=False),
    sa.Column('warning_count', sa.Integer(), nullable=False),
    sa.Column('issues', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rule_results', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("(outcome = 'FAILED') = (blocker_count + error_count > 0)", name=op.f('ck_mapping_validation_outcome_counts')),
    sa.CheckConstraint("jsonb_typeof(issues) = 'array'", name=op.f('ck_mapping_validation_issues')),
    sa.CheckConstraint("jsonb_typeof(rule_results) = 'array'", name=op.f('ck_mapping_validation_rule_results')),
    sa.CheckConstraint("mapping_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_mapping_validation_mapping_sha256')),
    sa.CheckConstraint("outcome IN ('OK', 'WARNINGS', 'FAILED')", name=op.f('ck_mapping_validation_outcome')),
    sa.CheckConstraint('row_count >= 0 AND blocker_count >= 0 AND error_count >= 0 AND warning_count >= 0', name=op.f('ck_mapping_validation_counts')),
    sa.ForeignKeyConstraint(['import_job_id'], ['import_job.id'], name=op.f('fk_mapping_validation_import_job_id_import_job')),
    sa.ForeignKeyConstraint(['mapping_version_id'], ['mapping_version.id'], name=op.f('fk_mapping_validation_mapping_version_id_mapping_version')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mapping_validation'))
    )
    op.create_index(op.f('ix_mapping_validation_import_job_id'), 'mapping_validation', ['import_job_id'], unique=False)
    op.create_index(op.f('ix_mapping_validation_mapping_version_id'), 'mapping_validation', ['mapping_version_id'], unique=False)
    op.create_foreign_key(op.f('fk_mapping_version_last_validation_id_mapping_validation'), 'mapping_version', 'mapping_validation', ['last_validation_id'], ['id'])
    op.add_column('import_job', sa.Column('mapping_version_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_import_job_mapping_version_id_mapping_version'), 'import_job', 'mapping_version', ['mapping_version_id'], ['id'])
    # ### end Alembic commands ###
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )


def downgrade() -> None:
    # Mappings are kept (D-02: a bad mapping is undone by a new version): refuse rather than
    # drop them. With no mapping there is no version, rule, validation or pinned import.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM field_mapping) THEN RAISE EXCEPTION "
        "'field_mapping has rows; mappings are kept (D-02), so this downgrade is refused'; "
        "END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(op.f('fk_import_job_mapping_version_id_mapping_version'), 'import_job', type_='foreignkey')
    op.drop_column('import_job', 'mapping_version_id')
    op.drop_constraint(op.f('fk_mapping_version_last_validation_id_mapping_validation'), 'mapping_version', type_='foreignkey')
    op.drop_index(op.f('ix_mapping_validation_mapping_version_id'), table_name='mapping_validation')
    op.drop_index(op.f('ix_mapping_validation_import_job_id'), table_name='mapping_validation')
    op.drop_table('mapping_validation')
    op.drop_index('uq_mapping_rule_target', table_name='mapping_rule', postgresql_where=sa.text('target_field IS NOT NULL'))
    op.drop_index(op.f('ix_mapping_rule_mapping_version_id'), table_name='mapping_rule')
    op.drop_table('mapping_rule')
    op.drop_index('uq_mapping_version_active', table_name='mapping_version', postgresql_where=sa.text("status = 'ACTIVE'"))
    op.drop_index(op.f('ix_mapping_version_field_mapping_id'), table_name='mapping_version')
    op.drop_table('mapping_version')
    op.drop_index(op.f('ix_field_mapping_tenant_id'), table_name='field_mapping')
    op.drop_index(op.f('ix_field_mapping_source_system_id'), table_name='field_mapping')
    op.drop_index(op.f('ix_field_mapping_schema_id'), table_name='field_mapping')
    op.drop_table('field_mapping')
    # ### end Alembic commands ###
