"""Reconciliation and drift detection (BE-12.1, DI-05, DI-06, D-23): reconciliation_run,
reconciliation_item and data_drift_event.

* `reconciliation_run` and `reconciliation_item` are append-only: UPDATE, DELETE and
  TRUNCATE raise. A run is written once, when its import is validated (one per import).
* An item's status is a function of its variance and tolerance (CHECK), and its variance is
  `source - target` (CHECK). Row-count checks have zero tolerance (CHECK).
* An item cannot sit under a MATCHED run unless it is MATCHED, and an item with a gap that
  its held rows do not explain (|variance - held| > tolerance) needs a BROKEN run (insert
  trigger; the run is append-only, so its outcome cannot move afterwards).
* `data_drift_event` is never deleted or truncated. Its content is frozen; only `status`
  moves, once, OPEN -> ACKNOWLEDGED | ACTION_REQUIRED (a frozen copy of
  app.mapping.constants.DRIFT_TRANSITIONS, kept equal by a test), written together with the
  review fields (reviewer, comment, time; CHECK), and nothing changes afterwards. An event
  is inserted OPEN, within its tenant, against a baseline import of the same source that is
  COMPLETED or COMPLETED_WITH_WARNINGS, naming both imports' profiles.
* Every enum column has a CHECK (D-06, D-23).

No session setting unlocks any trigger; disabling one needs the table owner. The downgrade
refuses while any run or drift event exists (D-20 §4: kept until the client sets retention).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-17

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the code's enums (test_reconciliation keeps them equal).
OUTCOMES = ("MATCHED", "REVIEW_REQUIRED", "BROKEN")
ITEM_STATUSES = ("MATCHED", "WITHIN_TOLERANCE", "VARIANCE")
CHECKS = ("ROW_COUNT", "EXPECTED_ROW_COUNT", "CONTROL_TOTAL", "DECLARED_CONTROL_TOTAL")
COUNT_CHECKS = ("ROW_COUNT", "EXPECTED_ROW_COUNT")
DRIFT_TYPES = (
    "COLUMN_ADDED",
    "COLUMN_REMOVED",
    "TYPE_CHANGED",
    "NULL_RATE_SHIFT",
    "RANGE_SHIFT",
)
DRIFT_TRANSITIONS: dict[str, list[str]] = {
    "OPEN": ["ACKNOWLEDGED", "ACTION_REQUIRED"],
    "ACKNOWLEDGED": [],
    "ACTION_REQUIRED": [],
}
DRIFT_STATUSES = tuple(DRIFT_TRANSITIONS)
VALIDATED = ("COMPLETED", "COMPLETED_WITH_WARNINGS", "QUARANTINED", "FAILED")
BASELINE = ("COMPLETED", "COMPLETED_WITH_WARNINGS")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


ALLOWED_MOVES = ", ".join(
    f"('{a}', '{b}')" for a, targets in DRIFT_TRANSITIONS.items() for b in targets
)
EVENT_CONTENT = (
    "id",
    "tenant_id",
    "import_job_id",
    "sequence",
    "baseline_import_id",
    "observed_snapshot_id",
    "baseline_snapshot_id",
    "drift_type",
    "column_name",
    "observed_summary",
    "baseline_summary",
    "thresholds",
    "detector_version",
    "created_by",
    "created_at",
)
REVIEW_FIELDS = ("status", "reviewed_by", "review_comment", "reviewed_at")
APPEND_ONLY_TABLES = ("reconciliation_run", "reconciliation_item")


def _row(prefix: str, columns: Sequence[str]) -> str:
    return "(" + ", ".join(f"{prefix}.{c}" for c in columns) + ")"


APPEND_ONLY = """
CREATE FUNCTION reconciliation_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (BE-12.1): % rejected',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

RUN_CHECKS = f"""
CREATE FUNCTION reconciliation_run_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM import_job j
        WHERE j.id = NEW.import_job_id AND j.tenant_id = NEW.tenant_id
            AND {_in("j.status", VALIDATED)}
    ) THEN
        RAISE EXCEPTION 'reconciliation_run must reconcile a validated import of its own'
            ' tenant (D-04)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

ITEM_CHECKS = """
CREATE FUNCTION reconciliation_item_checks() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    run_outcome text;
BEGIN
    SELECT outcome INTO run_outcome FROM reconciliation_run WHERE id = NEW.reconciliation_run_id;
    IF NEW.status <> 'MATCHED' AND run_outcome = 'MATCHED' THEN
        RAISE EXCEPTION 'a % reconciliation item cannot belong to a MATCHED run (BE-12.2)',
            NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF abs(NEW.variance - coalesce(NEW.held_value, 0)) > NEW.tolerance
        AND run_outcome <> 'BROKEN'
    THEN
        RAISE EXCEPTION 'a reconciliation item with an unexplained gap needs a BROKEN run'
            ' (BE-12.2)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

EVENT_GUARD = f"""
CREATE FUNCTION data_drift_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'data_drift_event is kept (BE-12.1): TRUNCATE rejected'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'data_drift_event % is kept (BE-12.1): DELETE rejected',
            OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF {_row("NEW", EVENT_CONTENT)} IS DISTINCT FROM {_row("OLD", EVENT_CONTENT)} THEN
        RAISE EXCEPTION 'data_drift_event % allows its review only (BE-12.1): content change'
            ' rejected', OLD.id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF {_row("NEW", REVIEW_FIELDS)} IS DISTINCT FROM {_row("OLD", REVIEW_FIELDS)}
        AND (OLD.status, NEW.status) NOT IN ({ALLOWED_MOVES})
    THEN
        RAISE EXCEPTION 'data_drift_event % is reviewed once, from OPEN (BE-12.4): % -> %'
            ' rejected', OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

EVENT_CHECKS = f"""
CREATE FUNCTION data_drift_event_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'OPEN' THEN
        RAISE EXCEPTION 'data_drift_event must be created OPEN (BE-12.1)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM import_job j
            JOIN import_job b ON b.source_system_id = j.source_system_id
                AND b.tenant_id = j.tenant_id
            JOIN data_profile_snapshot o ON o.import_job_id = j.id
            JOIN data_profile_snapshot s ON s.import_job_id = b.id
        WHERE j.id = NEW.import_job_id AND j.tenant_id = NEW.tenant_id
            AND b.id = NEW.baseline_import_id AND {_in("b.status", BASELINE)}
            AND o.id = NEW.observed_snapshot_id AND s.id = NEW.baseline_snapshot_id
    ) THEN
        RAISE EXCEPTION 'data_drift_event must compare profiles of its import and a completed'
            ' import of the same source and tenant (BE-12.3, D-04)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (APPEND_ONLY, RUN_CHECKS, ITEM_CHECKS, EVENT_GUARD, EVENT_CHECKS)
# (trigger, timing and events, table, level, function)
TRIGGERS = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", "reconciliation_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "reconciliation_append_only")
        for t in APPEND_ONLY_TABLES
    ),
    (
        "reconciliation_run_checks",
        "BEFORE INSERT",
        "reconciliation_run",
        "ROW",
        "reconciliation_run_checks",
    ),
    (
        "reconciliation_item_checks",
        "BEFORE INSERT",
        "reconciliation_item",
        "ROW",
        "reconciliation_item_checks",
    ),
    (
        "data_drift_event_guard",
        "BEFORE UPDATE OR DELETE",
        "data_drift_event",
        "ROW",
        "data_drift_event_guard",
    ),
    (
        "data_drift_event_no_truncate",
        "BEFORE TRUNCATE",
        "data_drift_event",
        "STATEMENT",
        "data_drift_event_guard",
    ),
    (
        "data_drift_event_checks",
        "BEFORE INSERT",
        "data_drift_event",
        "ROW",
        "data_drift_event_checks",
    ),
)
# What the downgrade refuses to lose (D-20 §4).
RESULT_TABLES = ("reconciliation_run", "reconciliation_item", "data_drift_event")


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


def upgrade() -> None:
    op.create_table(
        "reconciliation_run",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("tolerance_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reconciler_version", sa.Integer(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(_in("outcome", OUTCOMES), name=op.f("ck_reconciliation_run_outcome")),
        sa.CheckConstraint(
            "jsonb_typeof(tolerance_policy) = 'object'",
            name=op.f("ck_reconciliation_run_tolerance_policy"),
        ),
        sa.CheckConstraint(
            "reconciler_version >= 1", name=op.f("ck_reconciliation_run_reconciler_version")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_reconciliation_run_tenant_id_tenant")
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_job.id"],
            name=op.f("fk_reconciliation_run_import_job_id_import_job"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_run")),
    )
    op.create_index(
        op.f("ix_reconciliation_run_tenant_id"), "reconciliation_run", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_reconciliation_run_import_job_id"),
        "reconciliation_run",
        ["import_job_id"],
        unique=True,
    )
    op.create_table(
        "reconciliation_item",
        sa.Column("reconciliation_run_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("check_code", sa.String(length=30), nullable=False),
        sa.Column("column_name", sa.String(length=200), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.Column("source_value", sa.Numeric(), nullable=False),
        sa.Column("target_value", sa.Numeric(), nullable=False),
        sa.Column("held_value", sa.Numeric(), nullable=True),
        sa.Column("variance", sa.Numeric(), nullable=False),
        sa.Column("tolerance", sa.Numeric(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(
            _in("check_code", CHECKS), name=op.f("ck_reconciliation_item_check_code")
        ),
        sa.CheckConstraint(
            _in("status", ITEM_STATUSES), name=op.f("ck_reconciliation_item_status")
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_reconciliation_item_sequence")),
        sa.CheckConstraint(
            "variance = source_value - target_value", name=op.f("ck_reconciliation_item_variance")
        ),
        sa.CheckConstraint("tolerance >= 0", name=op.f("ck_reconciliation_item_tolerance")),
        sa.CheckConstraint(
            "status = CASE WHEN variance = 0 THEN 'MATCHED'"
            " WHEN abs(variance) <= tolerance THEN 'WITHIN_TOLERANCE' ELSE 'VARIANCE' END",
            name=op.f("ck_reconciliation_item_status_of_variance"),
        ),
        sa.CheckConstraint(
            f"NOT ({_in('check_code', COUNT_CHECKS)}) OR (tolerance = 0 AND column_name IS NULL)",
            name=op.f("ck_reconciliation_item_count_checks"),
        ),
        sa.CheckConstraint(
            f"{_in('check_code', COUNT_CHECKS)} OR column_name IS NOT NULL",
            name=op.f("ck_reconciliation_item_column"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name=op.f("ck_reconciliation_item_details")
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_run_id"],
            ["reconciliation_run.id"],
            name=op.f("fk_reconciliation_item_reconciliation_run_id_reconciliation_run"),
        ),
        sa.ForeignKeyConstraint(
            ["classification"],
            ["data_classification.code"],
            name=op.f("fk_reconciliation_item_classification_data_classification"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_item")),
        sa.UniqueConstraint(
            "reconciliation_run_id",
            "sequence",
            name=op.f("uq_reconciliation_item_reconciliation_run_id"),
        ),
    )
    op.create_table(
        "data_drift_event",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("baseline_import_id", sa.UUID(), nullable=False),
        sa.Column("observed_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("baseline_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("drift_type", sa.String(length=20), nullable=False),
        sa.Column("column_name", sa.String(length=200), nullable=False),
        sa.Column("observed_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("baseline_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detector_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("review_comment", sa.String(length=1000), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            _in("drift_type", DRIFT_TYPES), name=op.f("ck_data_drift_event_drift_type")
        ),
        sa.CheckConstraint(_in("status", DRIFT_STATUSES), name=op.f("ck_data_drift_event_status")),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_data_drift_event_sequence")),
        sa.CheckConstraint(
            "detector_version >= 1", name=op.f("ck_data_drift_event_detector_version")
        ),
        sa.CheckConstraint(
            "baseline_import_id <> import_job_id", name=op.f("ck_data_drift_event_baseline")
        ),
        sa.CheckConstraint(
            "(status = 'OPEN') = (reviewed_by IS NULL)"
            " AND (reviewed_by IS NULL) = (reviewed_at IS NULL)"
            " AND (reviewed_by IS NULL) = (review_comment IS NULL)",
            name=op.f("ck_data_drift_event_review"),
        ),
        sa.CheckConstraint(
            "(observed_summary IS NULL) = (drift_type = 'COLUMN_REMOVED')"
            " AND (baseline_summary IS NULL) = (drift_type = 'COLUMN_ADDED')",
            name=op.f("ck_data_drift_event_summaries"),
        ),
        sa.CheckConstraint(
            "coalesce(jsonb_typeof(observed_summary), 'object') = 'object'"
            " AND coalesce(jsonb_typeof(baseline_summary), 'object') = 'object'"
            " AND jsonb_typeof(thresholds) = 'object'",
            name=op.f("ck_data_drift_event_json"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_data_drift_event_tenant_id_tenant")
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_job.id"],
            name=op.f("fk_data_drift_event_import_job_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["baseline_import_id"],
            ["import_job.id"],
            name=op.f("fk_data_drift_event_baseline_import_id_import_job"),
        ),
        sa.ForeignKeyConstraint(
            ["observed_snapshot_id"],
            ["data_profile_snapshot.id"],
            name=op.f("fk_data_drift_event_observed_snapshot_id_data_profile_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["baseline_snapshot_id"],
            ["data_profile_snapshot.id"],
            name=op.f("fk_data_drift_event_baseline_snapshot_id_data_profile_snapshot"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_drift_event")),
        sa.UniqueConstraint(
            "import_job_id", "sequence", name=op.f("uq_data_drift_event_import_job_id")
        ),
        sa.UniqueConstraint(
            "import_job_id",
            "drift_type",
            "column_name",
            name=op.f("uq_data_drift_event_import_job_id_drift_type"),
        ),
    )
    op.create_index(
        op.f("ix_data_drift_event_tenant_id"), "data_drift_event", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_data_drift_event_import_job_id"),
        "data_drift_event",
        ["import_job_id"],
        unique=False,
    )
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )


def downgrade() -> None:
    # Runs and drift events are kept (D-20 §4): refuse rather than drop them.
    checks = " OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in RESULT_TABLES)
    op.execute(
        "DO $$ BEGIN IF " + checks + " THEN RAISE EXCEPTION 'reconciliation runs or drift"
        " events exist; they are kept (D-20), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    op.drop_index(op.f("ix_data_drift_event_import_job_id"), table_name="data_drift_event")
    op.drop_index(op.f("ix_data_drift_event_tenant_id"), table_name="data_drift_event")
    op.drop_table("data_drift_event")
    op.drop_table("reconciliation_item")
    op.drop_index(op.f("ix_reconciliation_run_import_job_id"), table_name="reconciliation_run")
    op.drop_index(op.f("ix_reconciliation_run_tenant_id"), table_name="reconciliation_run")
    op.drop_table("reconciliation_run")
