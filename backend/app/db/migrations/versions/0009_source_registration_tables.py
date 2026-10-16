"""Source registration tables (BE-7.1, DI-01): source_system, source_profile, source_contract.

Nothing is deleted (D-02): triggers reject DELETE and TRUNCATE on all three tables. A
source_contract's content (D-24 §3: id, profile, version, target schema, expected /
business-key / control-total columns, hash, lineage, summary, seed tag, created_by,
created_at) is frozen from creation, in every status; only `status` and `updated_at` change,
and `status` only along the transition table below (a frozen copy of
app.ingestion.constants.CONTRACT_TRANSITIONS, kept equal by a test). SUPERSEDED is final.
A source never moves tenant and a profile never moves source (D-04); a source has at most
one profile. A contract targets a schema of its own source's tenant and supersedes only a
contract of its own profile. Every enum column has a CHECK (D-06, D-23). A seed migration's
downgrade (D-11) disables the row triggers for its own transaction, which needs the table
owner: no session setting unlocks them.

Amended in place before release (D-24 Consequences): never merged or deployed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16 11:39:07.052543

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTENT_COLUMNS = (
    "source_profile_id",
    "version",
    "schema_id",
    "expected_columns",
    "business_key_columns",
    "control_total_columns",
    "content_sha256",
    "supersedes_id",
    "change_summary",
    "seeded_by",
    "id",
    "created_by",
    "created_at",
)
# D-06 transition table: a frozen copy of CONTRACT_TRANSITIONS, kept equal by
# test_source_contract_guards. A status may also be "set" to itself, which is not a move.
CONTRACT_TRANSITIONS: dict[str, list[str]] = {
    "PROVISIONAL": ["APPROVED", "IN_REVIEW", "SUPERSEDED"],
    "IN_REVIEW": ["APPROVED", "SUPERSEDED"],
    "APPROVED": ["SUPERSEDED"],
    "SUPERSEDED": [],
}
ALLOWED_MOVES = ", ".join(
    f"('{a}', '{b}')" for a, targets in CONTRACT_TRANSITIONS.items() for b in targets
)
NEW_CONTENT = ", ".join(f"NEW.{c}" for c in CONTENT_COLUMNS)
OLD_CONTENT = ", ".join(f"OLD.{c}" for c in CONTENT_COLUMNS)

CONTRACT_GUARD = f"""
CREATE FUNCTION source_contract_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'source_contract is immutable (BE-7.1): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'source_contract % is immutable (BE-7.1): DELETE rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'SUPERSEDED' THEN
        RAISE EXCEPTION 'source_contract % is SUPERSEDED, which is final and immutable (BE-7.1)',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF ({NEW_CONTENT}) IS DISTINCT FROM ({OLD_CONTENT}) THEN
        RAISE EXCEPTION 'source_contract % is immutable (D-24): content change rejected; add a new version',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
        AND (OLD.status, NEW.status) NOT IN ({ALLOWED_MOVES})
    THEN
        RAISE EXCEPTION 'source_contract % is immutable (D-06): % -> % is not an allowed transition',
            OLD.id, OLD.status, NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# Systems and profiles are hidden by status in later phases, never deleted (D-02).
NO_DELETE = """
CREATE FUNCTION source_registration_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows are never deleted (BE-7.1, D-02): % rejected',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

# D-04: a source never moves tenant and a profile never moves source. Two functions: a
# PL/pgSQL expression naming a column the row lacks fails even when not reached.
NO_TENANT_MOVE = """
CREATE FUNCTION source_system_no_move() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'source_system % cannot move tenant (D-04)',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""
NO_SOURCE_MOVE = """
CREATE FUNCTION source_profile_no_move() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.source_system_id IS DISTINCT FROM OLD.source_system_id THEN
        RAISE EXCEPTION 'source_profile % cannot move to another source (D-04)',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# D-04: a contract targets a schema of its own source's tenant, and supersedes only a
# contract of the same profile.
SAME_TENANT = """
CREATE FUNCTION source_contract_same_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (
        SELECT s.tenant_id FROM source_profile p
            JOIN source_system s ON s.id = p.source_system_id
            WHERE p.id = NEW.source_profile_id
    ) IS DISTINCT FROM (
        SELECT tenant_id FROM schema_definition WHERE id = NEW.schema_id
    ) THEN
        RAISE EXCEPTION 'source_contract must target a schema of its own tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.supersedes_id IS NOT NULL AND (
        SELECT source_profile_id FROM source_contract WHERE id = NEW.supersedes_id
    ) IS DISTINCT FROM NEW.source_profile_id THEN
        RAISE EXCEPTION 'source_contract may only supersede a contract of its own profile'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

NO_DELETE_TABLES = ("source_system", "source_profile")


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('source_system',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("kind IN ('FILE_DROP', 'WAREHOUSE', 'APPLICATION', 'FEED')", name=op.f('ck_source_system_kind')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_source_system_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_system')),
    sa.UniqueConstraint('tenant_id', 'code', name=op.f('uq_source_system_tenant_id'))
    )
    op.create_index(op.f('ix_source_system_tenant_id'), 'source_system', ['tenant_id'], unique=False)
    op.create_table('source_profile',
    sa.Column('source_system_id', sa.UUID(), nullable=False),
    sa.Column('format', sa.String(length=10), nullable=False),
    sa.Column('encoding', sa.String(length=20), nullable=False),
    sa.Column('delimiter', sa.String(length=1), nullable=True),
    sa.Column('sheet_name', sa.String(length=31), nullable=True),
    sa.Column('cadence', sa.String(length=100), nullable=True),
    sa.Column('owner', sa.String(length=200), nullable=False),
    sa.Column('default_classification', sa.String(length=50), nullable=False),
    sa.Column('provisional', sa.Boolean(), nullable=False),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("(format = 'CSV') = (delimiter IS NOT NULL)", name=op.f('ck_source_profile_delimiter_format')),
    sa.CheckConstraint("delimiter IS NULL OR delimiter IN (',', ';', '|', chr(9))", name=op.f('ck_source_profile_delimiter')),
    sa.CheckConstraint("encoding IN ('UTF-8')", name=op.f('ck_source_profile_encoding')),
    sa.CheckConstraint("format = 'XLSX' OR sheet_name IS NULL", name=op.f('ck_source_profile_sheet_name_format')),
    sa.CheckConstraint("format IN ('CSV', 'XLSX', 'JSON')", name=op.f('ck_source_profile_format')),
    sa.ForeignKeyConstraint(['default_classification'], ['data_classification.code'], name=op.f('fk_source_profile_default_classification_data_classification')),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_system.id'], name=op.f('fk_source_profile_source_system_id_source_system')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_profile'))
    )
    op.create_index(op.f('ix_source_profile_source_system_id'), 'source_profile', ['source_system_id'], unique=True)
    op.create_table('source_contract',
    sa.Column('source_profile_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('schema_id', sa.UUID(), nullable=False),
    sa.Column('expected_columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('business_key_columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('control_total_columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('supersedes_id', sa.UUID(), nullable=True),
    sa.Column('change_summary', sa.String(length=1000), nullable=True),
    sa.Column('seeded_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=False),
    sa.CheckConstraint("status IN ('PROVISIONAL', 'IN_REVIEW', 'APPROVED', 'SUPERSEDED')", name=op.f('ck_source_contract_status')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_source_contract_version')),
    sa.ForeignKeyConstraint(['schema_id'], ['schema_definition.id'], name=op.f('fk_source_contract_schema_id_schema_definition')),
    sa.ForeignKeyConstraint(['source_profile_id'], ['source_profile.id'], name=op.f('fk_source_contract_source_profile_id_source_profile')),
    sa.ForeignKeyConstraint(['supersedes_id'], ['source_contract.id'], name=op.f('fk_source_contract_supersedes_id_source_contract')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_contract')),
    sa.UniqueConstraint('source_profile_id', 'version', name=op.f('uq_source_contract_source_profile_id'))
    )
    op.create_index(op.f('ix_source_contract_schema_id'), 'source_contract', ['schema_id'], unique=False)
    op.create_index(op.f('ix_source_contract_source_profile_id'), 'source_contract', ['source_profile_id'], unique=False)
    op.create_index('uq_source_contract_current', 'source_contract', ['source_profile_id'], unique=True, postgresql_where=sa.text("status <> 'SUPERSEDED'"))
    # ### end Alembic commands ###
    # One statement per op.execute: asyncpg cannot run several in one call.
    op.execute(CONTRACT_GUARD)
    op.execute(NO_DELETE)
    op.execute(SAME_TENANT)
    op.execute(NO_TENANT_MOVE)
    op.execute(NO_SOURCE_MOVE)
    op.execute(
        "CREATE TRIGGER source_system_no_move BEFORE UPDATE ON source_system "
        "FOR EACH ROW EXECUTE FUNCTION source_system_no_move()"
    )
    op.execute(
        "CREATE TRIGGER source_profile_no_move BEFORE UPDATE ON source_profile "
        "FOR EACH ROW EXECUTE FUNCTION source_profile_no_move()"
    )
    op.execute(
        "CREATE TRIGGER source_contract_immutable BEFORE UPDATE OR DELETE ON source_contract "
        "FOR EACH ROW EXECUTE FUNCTION source_contract_guard()"
    )
    op.execute(
        "CREATE TRIGGER source_contract_no_truncate BEFORE TRUNCATE ON source_contract "
        "FOR EACH STATEMENT EXECUTE FUNCTION source_contract_guard()"
    )
    op.execute(
        "CREATE TRIGGER source_contract_same_tenant BEFORE INSERT OR UPDATE ON source_contract "
        "FOR EACH ROW EXECUTE FUNCTION source_contract_same_tenant()"
    )
    for table in NO_DELETE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION source_registration_no_delete()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION source_registration_no_delete()"
        )


def downgrade() -> None:
    # Registered sources are user data: refuse rather than drop them. The seeded source is
    # removed by 0011's downgrade, which runs first.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM source_system) THEN RAISE EXCEPTION "
        "'source_system has rows; downgrade 0011 first, and archive any sources created "
        "since'; END IF; END $$"
    )
    for table in NO_DELETE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table}")
    op.execute("DROP TRIGGER IF EXISTS source_profile_no_move ON source_profile")
    op.execute("DROP TRIGGER IF EXISTS source_system_no_move ON source_system")
    op.execute("DROP TRIGGER IF EXISTS source_contract_same_tenant ON source_contract")
    op.execute("DROP TRIGGER IF EXISTS source_contract_no_truncate ON source_contract")
    op.execute("DROP TRIGGER IF EXISTS source_contract_immutable ON source_contract")
    for fn in (
        "source_profile_no_move",
        "source_system_no_move",
        "source_contract_same_tenant",
        "source_registration_no_delete",
        "source_contract_guard",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('uq_source_contract_current', table_name='source_contract', postgresql_where=sa.text("status <> 'SUPERSEDED'"))
    op.drop_index(op.f('ix_source_contract_source_profile_id'), table_name='source_contract')
    op.drop_index(op.f('ix_source_contract_schema_id'), table_name='source_contract')
    op.drop_table('source_contract')
    op.drop_index(op.f('ix_source_profile_source_system_id'), table_name='source_profile')
    op.drop_table('source_profile')
    op.drop_index(op.f('ix_source_system_tenant_id'), table_name='source_system')
    op.drop_table('source_system')
    # ### end Alembic commands ###
