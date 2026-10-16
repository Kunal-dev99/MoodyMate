"""Schema registry tables (BE-4.1, DG-01).

A schema_version is immutable once it leaves DRAFT: triggers reject any change to its
contract (version, schema, content hash, compatibility, fields), its effective date once
ACTIVE, and its deletion. Only the status may move on: never back to DRAFT, and never out
of REJECTED or RETIRED. TRUNCATE is rejected on every registry table. A seed migration's
downgrade (D-11) disables the row triggers for its own transaction, which needs the
table owner: a session setting is not enough.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16 05:58:54.083425

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FROZEN_ONCE_ACTIVE = "('ACTIVE', 'DEPRECATED', 'RETIRED', 'REJECTED')"

VERSION_GUARD = f"""
CREATE FUNCTION schema_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'DRAFT' THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'schema_version % is % and immutable (BE-4.1): DELETE rejected',
            OLD.id, OLD.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF (NEW.schema_id, NEW.version, NEW.content_sha256, NEW.compatibility, NEW.seeded_by)
            IS DISTINCT FROM
            (OLD.schema_id, OLD.version, OLD.content_sha256, OLD.compatibility, OLD.seeded_by)
        OR NEW.status = 'DRAFT'
        OR (OLD.status IN {FROZEN_ONCE_ACTIVE}
            AND NEW.effective_from IS DISTINCT FROM OLD.effective_from)
    THEN
        RAISE EXCEPTION 'schema_version % is % and immutable (BE-4.1): contract change rejected',
            OLD.id, OLD.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status IN ('REJECTED', 'RETIRED') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'schema_version % is %, which is final (D-06)', OLD.id, OLD.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# FOR SHARE: a field write locks its version row, so a concurrent status change waits
# for it (and then sees the field) instead of activating a version mid-edit.
FIELD_GUARD = """
CREATE FUNCTION schema_field_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    version_status text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO version_status FROM schema_version
            WHERE id = OLD.schema_version_id FOR SHARE;
        IF version_status <> 'DRAFT' THEN
            RAISE EXCEPTION 'fields of a % schema_version are immutable (BE-4.1): % rejected',
                version_status, TG_OP USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO version_status FROM schema_version
            WHERE id = NEW.schema_version_id FOR SHARE;
        IF version_status <> 'DRAFT' THEN
            RAISE EXCEPTION 'fields of a % schema_version are immutable (BE-4.1): % rejected',
                version_status, TG_OP USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$
"""

TRUNCATE_GUARD = """
CREATE FUNCTION schema_registry_no_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is part of the immutable schema registry (BE-4.1): TRUNCATE rejected',
        TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

# D-04: a dependency never links schemas of two tenants.
DEPENDENCY_GUARD = """
CREATE FUNCTION schema_dependency_same_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.dependent_schema_id IS NOT NULL AND (
        SELECT tenant_id FROM schema_definition WHERE id = NEW.dependent_schema_id
    ) IS DISTINCT FROM (
        SELECT tenant_id FROM schema_definition WHERE id = NEW.schema_id
    ) THEN
        RAISE EXCEPTION 'schema_dependency must stay within one tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

CHECKS = [
    (
        "schema_version",
        "status",
        "status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'ACTIVE', 'DEPRECATED', 'RETIRED',"
        " 'REJECTED')",
    ),
    (
        "schema_version",
        "compatibility",
        "compatibility IS NULL OR compatibility IN ('ADDITIVE', 'COMPATIBLE', 'BREAKING')",
    ),
    (
        "schema_field",
        "data_type",
        "data_type IN ('STRING', 'INTEGER', 'DECIMAL', 'BOOLEAN', 'DATE', 'TIMESTAMP')",
    ),
    (
        "schema_dependency",
        "dependent_type",
        "dependent_type IN ('SCHEMA', 'MAPPING', 'RULE', 'MODULE', 'REPORT', 'INTEGRATION')",
    ),
]

TRUNCATE_TABLES = ("schema_field", "schema_version", "schema_definition", "schema_dependency")


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('schema_definition',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('owner', sa.String(length=200), nullable=False),
    sa.Column('provisional', sa.Boolean(), nullable=False),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_schema_definition_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_definition')),
    sa.UniqueConstraint('tenant_id', 'code', name=op.f('uq_schema_definition_tenant_id'))
    )
    op.create_index(op.f('ix_schema_definition_tenant_id'), 'schema_definition', ['tenant_id'], unique=False)
    op.create_table('schema_dependency',
    sa.Column('schema_id', sa.UUID(), nullable=False),
    sa.Column('dependent_type', sa.String(length=20), nullable=False),
    sa.Column('dependent_ref', sa.String(length=200), nullable=False),
    sa.Column('dependent_schema_id', sa.UUID(), nullable=True),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('provisional', sa.Boolean(), nullable=False),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['dependent_schema_id'], ['schema_definition.id'], name=op.f('fk_schema_dependency_dependent_schema_id_schema_definition')),
    sa.ForeignKeyConstraint(['schema_id'], ['schema_definition.id'], name=op.f('fk_schema_dependency_schema_id_schema_definition')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_dependency')),
    sa.UniqueConstraint('schema_id', 'dependent_type', 'dependent_ref', name=op.f('uq_schema_dependency_schema_id'))
    )
    op.create_index(op.f('ix_schema_dependency_schema_id'), 'schema_dependency', ['schema_id'], unique=False)
    op.create_table('schema_version',
    sa.Column('schema_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('compatibility', sa.String(length=20), nullable=True),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('change_summary', sa.String(length=1000), nullable=True),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['schema_id'], ['schema_definition.id'], name=op.f('fk_schema_version_schema_id_schema_definition')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_version')),
    sa.UniqueConstraint('schema_id', 'version', name=op.f('uq_schema_version_schema_id'))
    )
    op.create_index(op.f('ix_schema_version_schema_id'), 'schema_version', ['schema_id'], unique=False)
    op.create_table('schema_field',
    sa.Column('schema_version_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('data_type', sa.String(length=20), nullable=False),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('unit', sa.String(length=50), nullable=True),
    sa.Column('precision', sa.Integer(), nullable=True),
    sa.Column('scale', sa.Integer(), nullable=True),
    sa.Column('classification', sa.String(length=50), nullable=False),
    sa.Column('extension_eligible', sa.Boolean(), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['schema_version_id'], ['schema_version.id'], name=op.f('fk_schema_field_schema_version_id_schema_version')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_field')),
    sa.UniqueConstraint('schema_version_id', 'name', name=op.f('uq_schema_field_schema_version_id'))
    )
    op.create_index(op.f('ix_schema_field_schema_version_id'), 'schema_field', ['schema_version_id'], unique=False)
    # ### end Alembic commands ###
    # Enumerated columns (D-06) and the classification code (BE-3.4), so a bad value can
    # never be frozen into an immutable version.
    for table, name, condition in CHECKS:
        op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)
    op.create_foreign_key(
        op.f("fk_schema_field_classification_data_classification"),
        "schema_field",
        "data_classification",
        ["classification"],
        ["code"],
    )
    op.execute(VERSION_GUARD)
    op.execute(FIELD_GUARD)
    op.execute(TRUNCATE_GUARD)
    op.execute(DEPENDENCY_GUARD)
    op.execute(
        "CREATE TRIGGER schema_version_immutable BEFORE UPDATE OR DELETE ON schema_version "
        "FOR EACH ROW EXECUTE FUNCTION schema_version_guard()"
    )
    op.execute(
        "CREATE TRIGGER schema_field_immutable BEFORE INSERT OR UPDATE OR DELETE ON schema_field "
        "FOR EACH ROW EXECUTE FUNCTION schema_field_guard()"
    )
    op.execute(
        "CREATE TRIGGER schema_dependency_same_tenant BEFORE INSERT OR UPDATE "
        "ON schema_dependency FOR EACH ROW EXECUTE FUNCTION schema_dependency_same_tenant()"
    )
    for table in TRUNCATE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION schema_registry_no_truncate()"
        )


def downgrade() -> None:
    # Registry content created after the seed is user data: refuse rather than drop it.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM schema_definition) THEN RAISE EXCEPTION "
        "'schema_definition has rows; downgrade 0007 first, and archive any schemas "
        "created since'; END IF; END $$"
    )
    for table in TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
    op.execute("DROP TRIGGER IF EXISTS schema_dependency_same_tenant ON schema_dependency")
    op.execute("DROP TRIGGER IF EXISTS schema_field_immutable ON schema_field")
    op.execute("DROP TRIGGER IF EXISTS schema_version_immutable ON schema_version")
    for fn in (
        "schema_dependency_same_tenant",
        "schema_registry_no_truncate",
        "schema_field_guard",
        "schema_version_guard",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_schema_field_schema_version_id'), table_name='schema_field')
    op.drop_table('schema_field')
    op.drop_index(op.f('ix_schema_version_schema_id'), table_name='schema_version')
    op.drop_table('schema_version')
    op.drop_index(op.f('ix_schema_dependency_schema_id'), table_name='schema_dependency')
    op.drop_table('schema_dependency')
    op.drop_index(op.f('ix_schema_definition_tenant_id'), table_name='schema_definition')
    op.drop_table('schema_definition')
    # ### end Alembic commands ###
