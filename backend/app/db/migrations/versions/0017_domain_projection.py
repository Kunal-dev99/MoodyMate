"""WS3 domain projection and the instrument catalogue (BE-14.2, FC-01, D-29), with the tagged
WS3 permission and role-grant seed (D-11, D-34) and the PROVISIONAL instrument types.

Tables: domain_projection, instrument_type, instrument, instrument_version,
instrument_attribute_definition, instrument_attribute_value, position, position_version and
cashflow_event.

* Every table is append-only: UPDATE, DELETE and TRUNCATE raise. No session setting
  unlocks a trigger; disabling one needs the table owner.
* Insert triggers (D-04): a projection belongs to a validated import (COMPLETED,
  COMPLETED_WITH_WARNINGS or QUARANTINED) of its own tenant, with that import's manifest,
  pinned schema version and that version's schema code. A version or event row belongs to a
  projection of its own schema code, is in its projection's tenant and import, and repeats
  its canonical record's import, source record, ordinal, schema version and payload hash.
  Identities, instrument types, attribute values and versions stay within one tenant.
* Every enum column has a CHECK (D-06); amounts are NUMERIC and dates DATE.
* Seeds, every row tagged `seeded_by = '0017_domain'` (D-11): the seven WS3 permissions and
  the Feature 03 role grants (a frozen copy of app.governance.constants.WS3_ROLE_PERMISSIONS,
  kept equal by test_domain), and the instrument types LOAN, DEPOSIT and BOND for the
  default tenant (PROVISIONAL / ILLUSTRATIVE).

The downgrade refuses while any projection or other non-seed domain row exists (D-29: domain
rows are kept), then removes only the tagged seed rows and the tables.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-17

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0017_domain"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs

# Frozen copies of the code's enums and seeds (test_domain keeps them equal).
SCHEMA_CODES = ("instrument", "position", "cashflow_event")
CASHFLOW_TYPES = ("PRINCIPAL", "INTEREST", "FEE")
ORIGINS = ("CONTRACTUAL", "BEHAVIOURAL")
FIELD_TYPES = ("STRING", "INTEGER", "DECIMAL", "BOOLEAN", "DATE", "TIMESTAMP")
PROJECTABLE = ("COMPLETED", "COMPLETED_WITH_WARNINGS", "QUARANTINED")
INSTRUMENT_TYPES = {
    "LOAN": "Loan (illustrative)",
    "DEPOSIT": "Deposit (illustrative)",
    "BOND": "Bond (illustrative)",
}
PERMISSIONS = {
    "domain:read": "Browse projected instruments, positions and cash flows.",
    "domain:project": "Project validated imports into the financial domain.",
    "rule:read": "Browse calculation modules, rules, rule versions and assumption sets.",
    "rule:propose": "Draft and submit rule versions.",
    "rule:approve": "Approve or reject rule versions (maker-checker).",
    "calc:read": "Browse dataset snapshots, calculation runs and their results.",
    "calc:run": "Create dataset snapshots and start calculation runs.",
}
_READ = ["domain:read", "rule:read", "calc:read"]
MATRIX = {
    "platform_admin": _READ,
    "data_steward": [*_READ, "domain:project"],
    "risk_analyst": [*_READ, "rule:propose", "calc:run"],
    "approver": [*_READ, "rule:approve"],
    "auditor": _READ,
}
ILLUSTRATIVE = (
    "PROVISIONAL / ILLUSTRATIVE seed vocabulary (D-11, D-29 §5); not client-approved."
)

APPEND_ONLY_TABLES = (
    "domain_projection",
    "instrument_type",
    "instrument",
    "position",
    "instrument_version",
    "position_version",
    "cashflow_event",
    "instrument_attribute_definition",
    "instrument_attribute_value",
)
# Tables whose rows the downgrade refuses to lose (instrument_type: its untagged rows).
KEPT = (
    "domain_projection",
    "instrument",
    "position",
    "instrument_version",
    "position_version",
    "cashflow_event",
    "instrument_attribute_definition",
    "instrument_attribute_value",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{code}")


APPEND_ONLY = """
CREATE FUNCTION domain_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (BE-14.2): % rejected',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

PROJECTION_CHECKS = f"""
CREATE FUNCTION domain_projection_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM import_job j JOIN import_manifest m ON m.import_job_id = j.id
        WHERE j.id = NEW.import_job_id AND j.tenant_id = NEW.tenant_id
            AND m.id = NEW.import_manifest_id AND {_in("j.status", PROJECTABLE)}
            AND j.schema_version_id = NEW.schema_version_id
    ) THEN
        RAISE EXCEPTION 'domain_projection must project a validated import of its own tenant,'
            ' with its manifest and schema version (D-29, D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id
        WHERE v.id = NEW.schema_version_id AND d.code = NEW.schema_code
    ) THEN
        RAISE EXCEPTION 'domain_projection schema_code % is not the import''s pinned schema'
            ' (D-29)', NEW.schema_code USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""


def _row_checks(table: str, code: str, identity: str | None, source_check: bool) -> str:
    """Insert checks of a version or event row: it belongs to a projection of its schema
    code, its projection, import and tenant agree, and it repeats its canonical record's
    references."""
    extra = ""
    if identity is not None:
        extra = f"""
    IF NOT EXISTS (SELECT 1 FROM {identity} i WHERE i.id = NEW.{identity}_id
            AND i.tenant_id = NEW.tenant_id
            AND i.source_system_id = (SELECT source_system_id FROM import_job
                WHERE id = NEW.import_job_id)) THEN
        RAISE EXCEPTION '{table} must version a {identity} of its tenant and source (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;"""
    if table == "instrument_version":
        extra += """
    IF NEW.instrument_type_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM instrument_type t
            WHERE t.id = NEW.instrument_type_id AND t.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'instrument_version must name an instrument type of its tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;"""
    if source_check:
        extra = """
    IF NOT EXISTS (SELECT 1 FROM import_job j WHERE j.id = NEW.import_job_id
            AND j.source_system_id = NEW.source_system_id) THEN
        RAISE EXCEPTION 'cashflow_event must name its import''s source (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;"""
    return f"""
CREATE FUNCTION {table}_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM domain_projection p WHERE p.id = NEW.domain_projection_id
            AND p.schema_code = '{code}') THEN
        RAISE EXCEPTION '{table} must belong to a {code} projection (D-29)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM domain_projection p
            JOIN import_job j ON j.id = p.import_job_id
            JOIN canonical_record c ON c.import_job_id = p.import_job_id
        WHERE p.id = NEW.domain_projection_id AND p.tenant_id = NEW.tenant_id
            AND j.tenant_id = NEW.tenant_id AND p.import_job_id = NEW.import_job_id
            AND c.id = NEW.canonical_record_id
            AND c.source_record_id = NEW.source_record_id AND c.ordinal = NEW.source_ordinal
            AND c.schema_version_id = NEW.schema_version_id
            AND c.payload_sha256 = NEW.payload_sha256
    ) THEN
        RAISE EXCEPTION '{table} must project a canonical record of its projection''s import'
            ' and tenant (D-29, D-04)' USING ERRCODE = 'insufficient_privilege';
    END IF;{extra}
    RETURN NEW;
END;
$$
"""


IDENTITY_CHECKS = """
CREATE FUNCTION domain_identity_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM source_system s WHERE s.id = NEW.source_system_id
            AND s.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION '% must name a source of its own tenant (D-04)', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

ATTRIBUTE_CHECKS = """
CREATE FUNCTION instrument_attribute_value_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM instrument_version v, instrument_attribute_definition d
        WHERE v.id = NEW.instrument_version_id AND v.tenant_id = NEW.tenant_id
            AND d.id = NEW.attribute_definition_id AND d.tenant_id = NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'instrument_attribute_value must stay within one tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (
    APPEND_ONLY,
    PROJECTION_CHECKS,
    _row_checks("instrument_version", "instrument", "instrument", False),
    _row_checks("position_version", "position", "position", False),
    _row_checks("cashflow_event", "cashflow_event", None, True),
    IDENTITY_CHECKS,
    ATTRIBUTE_CHECKS,
)
# (trigger, timing and events, table, level, function)
TRIGGERS = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", "domain_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "domain_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    (
        "domain_projection_checks",
        "BEFORE INSERT",
        "domain_projection",
        "ROW",
        "domain_projection_checks",
    ),
    *(
        (f"{t}_checks", "BEFORE INSERT", t, "ROW", f"{t}_checks")
        for t in ("instrument_version", "position_version", "cashflow_event")
    ),
    *(
        (f"{t}_checks", "BEFORE INSERT", t, "ROW", "domain_identity_checks")
        for t in ("instrument", "position")
    ),
    (
        "instrument_attribute_value_checks",
        "BEFORE INSERT",
        "instrument_attribute_value",
        "ROW",
        "instrument_attribute_value_checks",
    ),
)


def _audit_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
    ]


def _tenant(table: str) -> list[Any]:
    return [
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}")
    )


def _projected(table: str) -> list[Any]:
    """What every version and event row names (D-29 §6)."""
    return [
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("domain_projection_id", sa.UUID(), nullable=False),
        sa.Column("canonical_record_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        _fk(table, "import_job_id", "import_job"),
        _fk(table, "domain_projection_id", "domain_projection"),
        _fk(table, "canonical_record_id", "canonical_record"),
        _fk(table, "source_record_id", "source_record"),
        _fk(table, "schema_version_id", "schema_version"),
        sa.UniqueConstraint("canonical_record_id", name=op.f(f"uq_{table}_canonical_record_id")),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f(f"ck_{table}_payload_sha256")
        ),
        sa.CheckConstraint("source_ordinal >= 1", name=op.f(f"ck_{table}_source_ordinal")),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'", name=op.f(f"ck_{table}_currency")
        ),
    ]


def _indexes() -> list[tuple[str, str, list[str], bool]]:
    """(index, table, columns, unique), as the models declare them."""
    found = [
        (f"ix_{t}_tenant_id", t, ["tenant_id"], False) for t in APPEND_ONLY_TABLES
    ]
    found += [
        ("ix_domain_projection_import_job_id", "domain_projection", ["import_job_id"], True),
        ("ix_instrument_version_import_job_id", "instrument_version", ["import_job_id"], False),
        ("ix_instrument_version_instrument_id", "instrument_version", ["instrument_id"], False),
        ("ix_position_version_import_job_id", "position_version", ["import_job_id"], False),
        ("ix_position_version_position_id", "position_version", ["position_id"], False),
        ("ix_cashflow_event_import_job_id", "cashflow_event", ["import_job_id"], False),
        ("ix_cashflow_event_instrument_key", "cashflow_event", ["instrument_key"], False),
        (
            "ix_instrument_attribute_value_instrument_version_id",
            "instrument_attribute_value",
            ["instrument_version_id"],
            False,
        ),
    ]
    return found


permission = sa.table(
    "permission",
    sa.column("id", sa.UUID()),
    sa.column("code", sa.String()),
    sa.column("description", sa.String()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)
role_permission = sa.table(
    "role_permission",
    sa.column("role_id", sa.UUID()),
    sa.column("permission_id", sa.UUID()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)
instrument_type = sa.table(
    "instrument_type",
    sa.column("id", sa.UUID()),
    sa.column("tenant_id", sa.UUID()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("description", sa.String()),
    sa.column("provisional", sa.Boolean()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)


def upgrade() -> None:
    op.create_table(
        "domain_projection",
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("import_manifest_id", sa.UUID(), nullable=False),
        sa.Column("schema_code", sa.String(length=30), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_audit_columns(),
        *_tenant("domain_projection"),
        _fk("domain_projection", "import_job_id", "import_job"),
        _fk("domain_projection", "import_manifest_id", "import_manifest"),
        _fk("domain_projection", "schema_version_id", "schema_version"),
        sa.UniqueConstraint(
            "import_manifest_id", name=op.f("uq_domain_projection_import_manifest_id")
        ),
        sa.CheckConstraint(
            _in("schema_code", SCHEMA_CODES), name=op.f("ck_domain_projection_schema_code")
        ),
        sa.CheckConstraint(
            "projector_version >= 1", name=op.f("ck_domain_projection_projector_version")
        ),
        sa.CheckConstraint("record_count >= 0", name=op.f("ck_domain_projection_record_count")),
        sa.CheckConstraint(
            "jsonb_typeof(summary) = 'object'", name=op.f("ck_domain_projection_summary")
        ),
    )
    op.create_table(
        "instrument_type",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("seeded_by", sa.String(length=100), nullable=True),
        *_audit_columns(),
        *_tenant("instrument_type"),
        sa.UniqueConstraint("tenant_id", "code", name=op.f("uq_instrument_type_tenant_id")),
        sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name=op.f("ck_instrument_type_code")),
    )
    for table in ("instrument", "position"):
        op.create_table(
            table,
            sa.Column("source_system_id", sa.UUID(), nullable=False),
            sa.Column("business_key", sa.String(), nullable=False),
            *_audit_columns(),
            *_tenant(table),
            _fk(table, "source_system_id", "source_system"),
            sa.UniqueConstraint(
                "tenant_id",
                "source_system_id",
                "business_key",
                name=op.f(f"uq_{table}_tenant_id"),
            ),
        )
    op.create_table(
        "instrument_version",
        sa.Column("instrument_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("instrument_type_code", sa.String(), nullable=True),
        sa.Column("instrument_type_id", sa.UUID(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("notional", sa.Numeric(), nullable=True),
        sa.Column("interest_rate", sa.Numeric(), nullable=True),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        *_audit_columns(),
        *_tenant("instrument_version"),
        *_projected("instrument_version"),
        _fk("instrument_version", "instrument_id", "instrument"),
        _fk("instrument_version", "instrument_type_id", "instrument_type"),
        sa.UniqueConstraint(
            "instrument_id", "version", name=op.f("uq_instrument_version_instrument_id")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_instrument_version_version")),
    )
    op.create_table(
        "position_version",
        sa.Column("position_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("instrument_key", sa.String(), nullable=True),
        sa.Column("legal_entity_ref", sa.String(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("balance", sa.Numeric(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("valuation_date", sa.Date(), nullable=True),
        *_audit_columns(),
        *_tenant("position_version"),
        *_projected("position_version"),
        _fk("position_version", "position_id", "position"),
        sa.UniqueConstraint("position_id", "version", name=op.f("uq_position_version_position_id")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_position_version_version")),
    )
    op.create_table(
        "cashflow_event",
        sa.Column("source_system_id", sa.UUID(), nullable=False),
        sa.Column("business_key", sa.String(), nullable=False),
        sa.Column("instrument_key", sa.String(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("cashflow_type", sa.String(length=20), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=True),
        *_audit_columns(),
        *_tenant("cashflow_event"),
        *_projected("cashflow_event"),
        _fk("cashflow_event", "source_system_id", "source_system"),
        sa.CheckConstraint(
            f"cashflow_type IS NULL OR {_in('cashflow_type', CASHFLOW_TYPES)}",
            name=op.f("ck_cashflow_event_cashflow_type"),
        ),
        sa.CheckConstraint(
            f"origin IS NULL OR {_in('origin', ORIGINS)}", name=op.f("ck_cashflow_event_origin")
        ),
    )
    op.create_table(
        "instrument_attribute_definition",
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        *_audit_columns(),
        *_tenant("instrument_attribute_definition"),
        sa.UniqueConstraint(
            "tenant_id", "code", name=op.f("uq_instrument_attribute_definition_tenant_id")
        ),
        sa.CheckConstraint(
            _in("data_type", FIELD_TYPES), name=op.f("ck_instrument_attribute_definition_data_type")
        ),
    )
    op.create_table(
        "instrument_attribute_value",
        sa.Column("instrument_version_id", sa.UUID(), nullable=False),
        sa.Column("attribute_definition_id", sa.UUID(), nullable=False),
        sa.Column("value_text", sa.String(), nullable=False),
        *_audit_columns(),
        *_tenant("instrument_attribute_value"),
        _fk("instrument_attribute_value", "instrument_version_id", "instrument_version"),
        sa.ForeignKeyConstraint(
            ["attribute_definition_id"],
            ["instrument_attribute_definition.id"],
            name=op.f(
                "fk_instrument_attribute_value_attribute_definition_id_instrument_attribute_definition"
            ),
        ),
        sa.UniqueConstraint(
            "instrument_version_id",
            "attribute_definition_id",
            name=op.f("uq_instrument_attribute_value_instrument_version_id"),
        ),
    )
    for name, table, columns, unique in _indexes():
        op.create_index(op.f(name), table, columns, unique=unique)
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )

    # Seeds (D-11): the WS3 permissions and role grants, and the instrument types.
    op.bulk_insert(
        permission,
        [
            {"id": _id("permission", c), "code": c, "description": d, "created_by": ACTOR,
             "seeded_by": TAG}
            for c, d in PERMISSIONS.items()
        ],
    )
    op.bulk_insert(
        role_permission,
        [
            {"role_id": _id("role", r), "permission_id": _id("permission", p),
             "created_by": ACTOR, "seeded_by": TAG}
            for r, perms in MATRIX.items()
            for p in perms
        ],
    )
    op.bulk_insert(
        instrument_type,
        [
            {"id": uuid.uuid5(NS, f"instrument_type:{TENANT}:{code}"), "tenant_id": uuid.UUID(TENANT),
             "code": code, "name": name, "description": ILLUSTRATIVE, "provisional": True,
             "created_by": ACTOR, "seeded_by": TAG}
            for code, name in INSTRUMENT_TYPES.items()
        ],
    )


def downgrade() -> None:
    # Domain rows are kept (D-29): refuse rather than drop them. Seeded types do not count.
    checks = " OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in KEPT)
    op.execute(
        "DO $$ BEGIN IF " + checks + " OR EXISTS (SELECT 1 FROM instrument_type WHERE seeded_by"
        " IS DISTINCT FROM '" + TAG + "') THEN RAISE EXCEPTION 'domain projections or domain"
        " rows exist; they are kept (D-29), so this downgrade is refused'; END IF; END $$"
    )
    # Only the seed rows this migration inserted (D-11). A grant of a WS3 permission made by
    # hand (untagged) blocks the permission's removal, by design.
    for table in ("role_permission", "permission"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    for name, table, _, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=table)
    for table in (
        "instrument_attribute_value",
        "instrument_attribute_definition",
        "cashflow_event",
        "position_version",
        "instrument_version",
        "position",
        "instrument",
        "instrument_type",
        "domain_projection",
    ):
        op.drop_table(table)
