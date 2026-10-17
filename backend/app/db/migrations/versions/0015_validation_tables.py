"""Validation, quarantine and canonical records (BE-11.1, DI-04, D-20, D-22, D-23):
validation_profile, validation_issue, quarantine_item, canonical_record, record_lineage,
import_manifest, and the PROVISIONAL default validation profile (D-11).

* `validation_profile`, `validation_issue`, `canonical_record`, `record_lineage` and
  `import_manifest` are append-only: UPDATE, DELETE and TRUNCATE raise (D-20 §3: a bad
  result is superseded by a new import, never edited).
* `quarantine_item` is never deleted or truncated. Its reference, reasons and owner are
  frozen; only `status` moves, along the transition table below (a frozen copy of
  app.ingestion.constants.QUARANTINE_TRANSITIONS, kept equal by a test), and
  `resolved_by_import_id` is written once, with RESOLVED, naming a retry of the item's own
  import. An item is inserted OPEN or REJECTED.
* Every row stays inside its import: an issue's or item's source record belongs to the
  import (and batch); a canonical record carries exactly the schema and mapping versions
  its import pinned (D-18 §5); lineage repeats its canonical record's source and mapping; a
  manifest's counts add up to the import's rows (CHECK) and it names the pinned versions;
  a profile used by an import is of the import's tenant (D-04).
* Every enum column has a CHECK (D-06, D-23).

The seeded default profile is tagged `seeded_by = '0015_validation_profile_seed'`.

No session setting unlocks any trigger; disabling one needs the table owner. The downgrade
refuses while any validation result or untagged profile exists (D-20 §4: kept until the
client sets retention).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-17

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0015_validation_profile_seed"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("9a4e3c71-2b5d-4f08-8e6a-1d7c0b3f5a92")  # namespace for seed IDs
PROFILE_ID = uuid.uuid5(NS, f"validation_profile:{TENANT}:default:1")

# Frozen copy of app.ingestion.constants.DEFAULT_VALIDATION_RULES (test_validation keeps
# them equal): issue code -> (severity, disposition).
_W = ("WARNING", "ACCEPTED_WITH_WARNING")
_E = ("ERROR", "QUARANTINED")
_R = ("BLOCKER", "REJECTED")
_F = ("BLOCKER", "FILE_BLOCKED")
DEFAULT_RULES: dict[str, tuple[str, str]] = {
    "REQUIRED_FIELD_UNMAPPED": _F,
    "REQUIRED_COLUMN_MISSING": _F,
    "BUSINESS_KEY_COLUMN_MISSING": _F,
    "MAPPING_RULE_INVALID": _F,
    "SOURCE_COLUMN_MISSING": _W,
    "UNMAPPED_COLUMN": _W,
    "BLANK_ROW": _R,
    "EXTRA_CELLS": _R,
    "MISSING_CELLS": _W,
    "CELL_ERROR": _E,
    "FORMULA_WITHOUT_CACHED_VALUE": _E,
    "FORMULA_CACHED_VALUE": ("INFO", "ACCEPTED_WITH_WARNING"),
    "PRECISION_BEYOND_15_DIGITS": _E,
    "REQUIRED_VALUE_MISSING": _E,
    "INVALID_TYPE": _E,
    "INVALID_DATE": _E,
    "SCALE_EXCEEDED": _E,
    "PRECISION_EXCEEDED": _E,
    "INVALID_CURRENCY_CODE": _E,
    "UNMAPPED_VALUE": _E,
    "MISSING_BUSINESS_KEY": _E,
    "DUPLICATE_BUSINESS_KEY": _E,
}


def profile_rules(rules: dict[str, tuple[str, str]]) -> dict[str, dict[str, str]]:
    return {code: {"severity": s, "disposition": d} for code, (s, d) in sorted(rules.items())}


def profile_sha256(rules: dict[str, dict[str, str]]) -> str:
    """Frozen copy of app.ingestion.service.profile_rules_sha256 (a test keeps them equal)."""
    text = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


# D-20 §2: a frozen copy of QUARANTINE_TRANSITIONS. A status may also be "set" to itself.
QUARANTINE_TRANSITIONS: dict[str, list[str]] = {
    "OPEN": ["UNDER_REVIEW"],
    "UNDER_REVIEW": ["OPEN", "REJECTED", "RETRY_READY"],
    "RETRY_READY": ["CORRECTED"],
    "CORRECTED": ["RESOLVED", "RETRY_READY"],
    "REJECTED": [],
    "RESOLVED": [],
}
ALLOWED_MOVES = ", ".join(
    f"('{a}', '{b}')" for a, targets in QUARANTINE_TRANSITIONS.items() for b in targets
)
ITEM_CONTENT = (
    "id",
    "import_job_id",
    "batch_id",
    "source_record_id",
    "ordinal",
    "reason_codes",
    "business_key_sha256",
    "owner",
    "created_by",
    "created_at",
)
APPEND_ONLY_TABLES = (
    "validation_profile",
    "validation_issue",
    "canonical_record",
    "record_lineage",
    "import_manifest",
)


def _row(prefix: str, columns: Sequence[str]) -> str:
    return "(" + ", ".join(f"{prefix}.{c}" for c in columns) + ")"


APPEND_ONLY = """
CREATE FUNCTION validation_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (BE-11.1, D-20): % rejected',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

ITEM_GUARD = f"""
CREATE FUNCTION quarantine_item_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'quarantine_item is kept (D-20): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'quarantine_item % is kept (D-20): DELETE rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF {_row("NEW", ITEM_CONTENT)} IS DISTINCT FROM {_row("OLD", ITEM_CONTENT)} THEN
        RAISE EXCEPTION 'quarantine_item % allows status changes only (D-20): content change'
            ' rejected', OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
        AND (OLD.status, NEW.status) NOT IN ({ALLOWED_MOVES})
    THEN
        RAISE EXCEPTION 'quarantine_item % (D-20): % -> % is not an allowed transition',
            OLD.id, OLD.status, NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.resolved_by_import_id IS DISTINCT FROM OLD.resolved_by_import_id THEN
        IF OLD.resolved_by_import_id IS NOT NULL OR NEW.status <> 'RESOLVED'
            OR NOT EXISTS (
                SELECT 1 FROM import_job r JOIN import_job o ON o.id = OLD.import_job_id
                WHERE r.id = NEW.resolved_by_import_id
                    AND r.retry_of_import_id = o.id AND r.tenant_id = o.tenant_id
            )
        THEN
            RAISE EXCEPTION 'quarantine_item % is resolved once, by a retry of its own import'
                ' (D-20)', OLD.id USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""

ITEM_CHECKS = """
CREATE FUNCTION quarantine_item_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status NOT IN ('OPEN', 'REJECTED') OR NEW.resolved_by_import_id IS NOT NULL THEN
        RAISE EXCEPTION 'quarantine_item must be created OPEN or REJECTED (D-20)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM source_record r JOIN import_batch b ON b.id = r.batch_id
        WHERE r.id = NEW.source_record_id AND b.id = NEW.batch_id
            AND b.import_job_id = NEW.import_job_id AND r.ordinal = NEW.ordinal
    ) THEN
        RAISE EXCEPTION 'quarantine_item must hold a row of its own import and batch (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

CANONICAL_CHECKS = """
CREATE FUNCTION canonical_record_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM import_job j
            JOIN import_batch b ON b.import_job_id = j.id
            JOIN source_record r ON r.batch_id = b.id
        WHERE j.id = NEW.import_job_id AND r.id = NEW.source_record_id
            AND r.ordinal = NEW.ordinal
            AND j.mapping_version_id = NEW.mapping_version_id
            AND j.schema_version_id = NEW.schema_version_id
    ) THEN
        RAISE EXCEPTION 'canonical_record must be a row of its import, under the schema and'
            ' mapping versions the import pinned (D-18, D-22)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

LINEAGE_CHECKS = """
CREATE FUNCTION record_lineage_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM canonical_record c
        WHERE c.id = NEW.canonical_record_id AND c.source_record_id = NEW.source_record_id
            AND c.mapping_version_id = NEW.mapping_version_id
    ) THEN
        RAISE EXCEPTION 'record_lineage must repeat its canonical record''s source and mapping'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

ISSUE_CHECKS = """
CREATE FUNCTION validation_issue_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT tenant_id FROM validation_profile WHERE id = NEW.validation_profile_id)
        IS DISTINCT FROM (SELECT tenant_id FROM import_job WHERE id = NEW.import_job_id)
    OR (
        NEW.source_record_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM source_record r JOIN import_batch b ON b.id = r.batch_id
            WHERE r.id = NEW.source_record_id AND b.import_job_id = NEW.import_job_id
                AND r.ordinal = NEW.ordinal
        )
    ) THEN
        RAISE EXCEPTION 'validation_issue must stay within its import and tenant (D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

MANIFEST_CHECKS = """
CREATE FUNCTION import_manifest_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM import_job j JOIN validation_profile p ON p.tenant_id = j.tenant_id
        WHERE j.id = NEW.import_job_id AND p.id = NEW.validation_profile_id
            AND j.mapping_version_id = NEW.mapping_version_id
            AND j.schema_version_id = NEW.schema_version_id
            AND j.row_count = NEW.row_count
    ) THEN
        RAISE EXCEPTION 'import_manifest must name the versions its import pinned and count'
            ' its rows (D-22)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (
    APPEND_ONLY,
    ITEM_GUARD,
    ITEM_CHECKS,
    CANONICAL_CHECKS,
    LINEAGE_CHECKS,
    ISSUE_CHECKS,
    MANIFEST_CHECKS,
)
# (trigger, timing and events, table, level, function)
TRIGGERS = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", "validation_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "validation_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    (
        "quarantine_item_guard",
        "BEFORE UPDATE OR DELETE",
        "quarantine_item",
        "ROW",
        "quarantine_item_guard",
    ),
    (
        "quarantine_item_no_truncate",
        "BEFORE TRUNCATE",
        "quarantine_item",
        "STATEMENT",
        "quarantine_item_guard",
    ),
    ("quarantine_item_checks", "BEFORE INSERT", "quarantine_item", "ROW", "quarantine_item_checks"),
    (
        "canonical_record_checks",
        "BEFORE INSERT",
        "canonical_record",
        "ROW",
        "canonical_record_checks",
    ),
    ("record_lineage_checks", "BEFORE INSERT", "record_lineage", "ROW", "record_lineage_checks"),
    (
        "validation_issue_checks",
        "BEFORE INSERT",
        "validation_issue",
        "ROW",
        "validation_issue_checks",
    ),
    ("import_manifest_checks", "BEFORE INSERT", "import_manifest", "ROW", "import_manifest_checks"),
)
# What the downgrade refuses to lose (D-20 §4).
RESULT_TABLES = (
    "import_manifest",
    "canonical_record",
    "record_lineage",
    "quarantine_item",
    "validation_issue",
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "validation_profile",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("seeded_by", sa.String(length=100), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_validation_profile_content_sha256")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rules) = 'object'", name=op.f("ck_validation_profile_rules")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_validation_profile_version")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_validation_profile_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_profile")),
        sa.UniqueConstraint(
            "tenant_id", "code", "version", name=op.f("uq_validation_profile_tenant_id")
        ),
    )
    op.create_index(
        op.f("ix_validation_profile_tenant_id"), "validation_profile", ["tenant_id"], unique=False
    )
    op.create_table(
        "import_manifest",
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("validation_profile_id", sa.UUID(), nullable=False),
        sa.Column("mapping_version_id", sa.UUID(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(manifest) = 'object'", name=op.f("ck_import_manifest_manifest")
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_import_manifest_manifest_sha256")
        ),
        sa.CheckConstraint(
            "accepted_count >= 0 AND quarantined_count >= 0 AND rejected_count >= 0 AND warning_count >= 0",
            name=op.f("ck_import_manifest_counts"),
        ),
        sa.CheckConstraint(
            "row_count = accepted_count + quarantined_count + rejected_count",
            name=op.f("ck_import_manifest_conservation"),
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_job.id"],
            name=op.f("fk_import_manifest_import_job_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["mapping_version_id"],
            ["mapping_version.id"],
            name=op.f("fk_import_manifest_mapping_version_id_mapping_version"),
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_version.id"],
            name=op.f("fk_import_manifest_schema_version_id_schema_version"),
        ),
        sa.ForeignKeyConstraint(
            ["validation_profile_id"],
            ["validation_profile.id"],
            name=op.f("fk_import_manifest_validation_profile_id_validation_profile"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_manifest")),
    )
    op.create_index(
        op.f("ix_import_manifest_import_job_id"), "import_manifest", ["import_job_id"], unique=True
    )
    op.create_table(
        "canonical_record",
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("mapping_version_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_canonical_record_payload")
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_canonical_record_payload_sha256")
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_job.id"],
            name=op.f("fk_canonical_record_import_job_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["mapping_version_id"],
            ["mapping_version.id"],
            name=op.f("fk_canonical_record_mapping_version_id_mapping_version"),
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_version.id"],
            name=op.f("fk_canonical_record_schema_version_id_schema_version"),
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_canonical_record_source_record_id_source_record"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_record")),
        sa.UniqueConstraint("source_record_id", name=op.f("uq_canonical_record_source_record_id")),
    )
    op.create_index(
        op.f("ix_canonical_record_import_job_id"),
        "canonical_record",
        ["import_job_id"],
        unique=False,
    )
    op.create_table(
        "quarantine_item",
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("business_key_sha256", sa.String(length=64), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resolved_by_import_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "(resolved_by_import_id IS NULL) = (status <> 'RESOLVED')",
            name=op.f("ck_quarantine_item_resolution"),
        ),
        sa.CheckConstraint(
            "business_key_sha256 IS NULL OR business_key_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_quarantine_item_business_key_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes) = 'array'", name=op.f("ck_quarantine_item_reason_codes")
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'UNDER_REVIEW', 'REJECTED', 'CORRECTED', 'RETRY_READY', 'RESOLVED')",
            name=op.f("ck_quarantine_item_status"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_batch.id"], name=op.f("fk_quarantine_item_batch_id_import_batch")
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_job.id"],
            name=op.f("fk_quarantine_item_import_job_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_import_id"],
            ["import_job.id"],
            name=op.f("fk_quarantine_item_resolved_by_import_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_quarantine_item_source_record_id_source_record"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quarantine_item")),
        sa.UniqueConstraint("source_record_id", name=op.f("uq_quarantine_item_source_record_id")),
    )
    op.create_index(
        "ix_quarantine_item_batch_status", "quarantine_item", ["batch_id", "status"], unique=False
    )
    op.create_index(
        op.f("ix_quarantine_item_import_job_id"), "quarantine_item", ["import_job_id"], unique=False
    )
    op.create_table(
        "validation_issue",
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("source_column", sa.String(length=200), nullable=True),
        sa.Column("rule_position", sa.Integer(), nullable=True),
        sa.Column("validation_profile_id", sa.UUID(), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('ACCEPTED_WITH_WARNING', 'QUARANTINED', 'REJECTED', 'FILE_BLOCKED')",
            name=op.f("ck_validation_issue_disposition"),
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'ERROR', 'BLOCKER')",
            name=op.f("ck_validation_issue_severity"),
        ),
        sa.CheckConstraint(
            "source_record_id IS NOT NULL OR disposition IN ('FILE_BLOCKED', 'ACCEPTED_WITH_WARNING')",
            name=op.f("ck_validation_issue_file_level"),
        ),
        sa.CheckConstraint(
            "(source_record_id IS NULL) = (ordinal IS NULL)",
            name=op.f("ck_validation_issue_row_ref"),
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_validation_issue_sequence")),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_job.id"],
            name=op.f("fk_validation_issue_import_job_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_validation_issue_source_record_id_source_record"),
        ),
        sa.ForeignKeyConstraint(
            ["validation_profile_id"],
            ["validation_profile.id"],
            name=op.f("fk_validation_issue_validation_profile_id_validation_profile"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_issue")),
        sa.UniqueConstraint(
            "import_job_id", "sequence", name=op.f("uq_validation_issue_import_job_id")
        ),
    )
    op.create_index(
        "ix_validation_issue_import_code",
        "validation_issue",
        ["import_job_id", "code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_issue_import_job_id"),
        "validation_issue",
        ["import_job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_validation_issue_source_record_id"),
        "validation_issue",
        ["source_record_id"],
        unique=False,
    )
    op.create_table(
        "record_lineage",
        sa.Column("canonical_record_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.UUID(), nullable=False),
        sa.Column("mapping_version_id", sa.UUID(), nullable=False),
        sa.Column("transform_trace", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(transform_trace) = 'array'",
            name=op.f("ck_record_lineage_transform_trace"),
        ),
        sa.ForeignKeyConstraint(
            ["canonical_record_id"],
            ["canonical_record.id"],
            name=op.f("fk_record_lineage_canonical_record_id_canonical_record"),
        ),
        sa.ForeignKeyConstraint(
            ["mapping_version_id"],
            ["mapping_version.id"],
            name=op.f("fk_record_lineage_mapping_version_id_mapping_version"),
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["source_record.id"],
            name=op.f("fk_record_lineage_source_record_id_source_record"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_record_lineage")),
        sa.UniqueConstraint(
            "canonical_record_id", name=op.f("uq_record_lineage_canonical_record_id")
        ),
    )
    op.create_index(
        op.f("ix_record_lineage_source_record_id"),
        "record_lineage",
        ["source_record_id"],
        unique=False,
    )
    # ### end Alembic commands ###
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )
    # The PROVISIONAL default profile (D-11), inserted after the triggers: it is append-only.
    rules = profile_rules(DEFAULT_RULES)
    op.execute(
        sa.text(
            "INSERT INTO validation_profile (id, tenant_id, code, version, description, rules,"
            " content_sha256, provisional, created_by, seeded_by) VALUES (:id,"
            " CAST(:tenant AS uuid), 'default', 1, :description, CAST(:rules AS jsonb), :sha,"
            " true, :actor, :tag)"
        ).bindparams(
            id=PROFILE_ID,
            tenant=TENANT,
            description="PROVISIONAL default checks (D-11): severities and dispositions per"
            " issue code, until the client sets its own.",
            rules=json.dumps(rules),
            sha=profile_sha256(rules),
            actor=ACTOR,
            tag=TAG,
        )
    )


def downgrade() -> None:
    # Validation results are kept (D-20 §4) and profiles other than the seed are user data:
    # refuse rather than drop them. The seed itself goes with its table (D-11).
    checks = " OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in RESULT_TABLES)
    op.execute(
        "DO $$ BEGIN IF " + checks + " OR EXISTS (SELECT 1 FROM validation_profile WHERE"
        " seeded_by IS DISTINCT FROM '" + TAG + "') THEN RAISE EXCEPTION 'validation results"
        " exist; they are kept (D-20), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_record_lineage_source_record_id"), table_name="record_lineage")
    op.drop_table("record_lineage")
    op.drop_index(op.f("ix_validation_issue_source_record_id"), table_name="validation_issue")
    op.drop_index(op.f("ix_validation_issue_import_job_id"), table_name="validation_issue")
    op.drop_index("ix_validation_issue_import_code", table_name="validation_issue")
    op.drop_table("validation_issue")
    op.drop_index(op.f("ix_quarantine_item_import_job_id"), table_name="quarantine_item")
    op.drop_index("ix_quarantine_item_batch_status", table_name="quarantine_item")
    op.drop_table("quarantine_item")
    op.drop_index(op.f("ix_canonical_record_import_job_id"), table_name="canonical_record")
    op.drop_table("canonical_record")
    op.drop_index(op.f("ix_import_manifest_import_job_id"), table_name="import_manifest")
    op.drop_table("import_manifest")
    op.drop_index(op.f("ix_validation_profile_tenant_id"), table_name="validation_profile")
    op.drop_table("validation_profile")
    # ### end Alembic commands ###
