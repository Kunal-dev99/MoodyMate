"""Stressed runs: scenario runs, their pins and split-aware outputs (BE-25.1, WS4 RO-02, RO-07,
FC-08; D-45 §4-9).

(a) `scenario_run` (D-45 §9, Data Model §6): one run of a scenario version against a baseline
    maturity_ladder run: the version, its shocks' sha256, the approval state pinned at run
    time (EXPLORATION or APPROVED), the baseline, the reporting date, the number of shocks
    and runs (k + 1 for k > 1) and value-free counts per shock. Append-only: UPDATE, DELETE
    and TRUNCATE are refused. Insert check: the version is a version of the scenario, both
    in its tenant, with the same shocks' sha256; the baseline is a run of its tenant.
(b) `calculation_scenario_pin` (D-45 §4): one per stressed run: the scenario run, version
    and shock subset it applied, `combined`, the approval state and the baseline whose pins
    it copied. Its own table because `calculation_input_ref.ref_type` is a closed CHECK.
(c) `scenario_contribution` (D-45 §5): one row per part of a cash flow in a stressed run,
    `UNIQUE (calculation_run_id, cashflow_event_id, part_path)`, with its shocks, days and
    bucket before and after, its classification and the `intermediate_value` lineage
    columns, checked on insert as 0018 checks them (the event's lineage in its tenant, a
    member import of the run's snapshot, a ladder row of the run, currency and bucket, an
    instrument version of the tenant), and only under a run that has a scenario pin.
(d) `scenario_shock_application` (D-45 §5): one row per (stressed run, cash flow, shock):
    parts matched and after, the amount moved or added, the delay or the day moved to.
    Insert check: the event is of the tenant and the shock is in the run's pinned subset.
(b)-(d) sit under 0018's run-output guard `calc_output_guard()`: inserted only while their
run is RUNNING, in its tenant, then append-only (UPDATE, DELETE and TRUNCATE raise).

D-01: additive only. `intermediate_value` and its constraints are untouched (stressed runs
write `scenario_contribution` instead), as are `calculation_input_ref.ref_type` and
`calculation_warning.code`. There is no seed.

The downgrade refuses while any of the four tables has a row (D-02: stressed runs and their
evidence are kept), then drops the triggers, functions, indexes and tables.

Revision ID: 0027
Revises: 0026 (pre-numbered for Phase 25; Phases 21-22 hold 0024-0025 and Phase 26 holds
0028, and the coordinator re-chains `down_revision` to the merged head at merge time,
CLAUDE.md step 6)
Create Date: 2026-10-19

D-01: additive only. D-02: the downgrade restores the prior schema.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DENY = "USING ERRCODE = 'insufficient_privilege'"

# Frozen copies of the code's enums and bounds (kept equal by the Phase 25 tests).
APPROVAL_STATES = ("EXPLORATION", "APPROVED")
PART_CHANGES = ("MOVED", "STAYED", "ADDED", "UNCHANGED")
TEMPLATES = ("DELAYED_INFLOWS", "DEPOSIT_WITHDRAWAL", "FUNDING_UNAVAILABLE")
DIRECTIONS = ("INFLOW", "OUTFLOW")
ORIGINS = ("CONTRACTUAL", "BEHAVIOURAL")
MIN_SHOCKS, MAX_SHOCKS, MAX_PARTS_PER_FLOW = 1, 5, 32
PART_PATH = "~ '^0(/[1-5][MSA])*$'"
SHA = "~ '^[0-9a-f]{64}$'"
CURRENCY = "~ '^[A-Z]{3}$'"

RUN, PIN, CONTRIBUTION, APPLICATION = (
    "scenario_run",
    "calculation_scenario_pin",
    "scenario_contribution",
    "scenario_shock_application",
)
TABLES = (RUN, PIN, CONTRIBUTION, APPLICATION)  # creation order
OUTPUT_TABLES = (PIN, CONTRIBUTION, APPLICATION)  # under 0018's calc_output_guard()

# --- trigger functions -----------------------------------------------------------------------

RUN_GUARD = f"""
CREATE FUNCTION scenario_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'scenario_run is append-only (D-45 §9): % rejected', TG_OP {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM scenario_version v JOIN scenario s ON s.id = v.scenario_id
            WHERE v.id = NEW.scenario_version_id AND v.scenario_id = NEW.scenario_id
            AND v.tenant_id = NEW.tenant_id AND s.tenant_id = NEW.tenant_id
            AND v.version = NEW.version AND v.shocks_sha256 = NEW.shocks_sha256) THEN
        RAISE EXCEPTION 'a scenario_run runs a version of its scenario, in its tenant, with'
            ' that version''s shocks (D-45 §9, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM calculation_run r WHERE r.id = NEW.baseline_run_id
            AND r.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'a scenario_run baseline is a run of its tenant (D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

PIN_CHECKS = f"""
CREATE FUNCTION calculation_scenario_pin_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM scenario_run s WHERE s.id = NEW.scenario_run_id
            AND s.tenant_id = NEW.tenant_id AND s.scenario_id = NEW.scenario_id
            AND s.scenario_version_id = NEW.scenario_version_id AND s.version = NEW.version
            AND s.shocks_sha256 = NEW.shocks_sha256 AND s.approval_state = NEW.approval_state
            AND s.baseline_run_id = NEW.baseline_run_id) THEN
        RAISE EXCEPTION 'calculation_scenario_pin must repeat its scenario_run, in its tenant'
            ' (D-45 §4, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM calculation_run r JOIN calculation_run b
            ON b.id = NEW.baseline_run_id AND b.tenant_id = r.tenant_id
            WHERE r.id = NEW.calculation_run_id AND r.tenant_id = NEW.tenant_id
            AND r.dataset_snapshot_id = b.dataset_snapshot_id
            AND r.module_version_id = b.module_version_id AND r.rule_set_id = b.rule_set_id
            AND r.assumption_set_id = b.assumption_set_id
            AND r.reporting_date = b.reporting_date) THEN
        RAISE EXCEPTION 'a stressed run copies its baseline''s pins exactly (D-45 §1)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

CONTRIBUTION_CHECKS = f"""
CREATE FUNCTION scenario_contribution_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM calculation_scenario_pin p
            WHERE p.calculation_run_id = NEW.calculation_run_id
            AND p.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'scenario_contribution rows belong to a stressed run (D-45 §5)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cashflow_event e WHERE e.id = NEW.cashflow_event_id
            AND e.tenant_id = NEW.tenant_id AND e.canonical_record_id = NEW.canonical_record_id
            AND e.import_job_id = NEW.import_job_id AND e.source_record_id = NEW.source_record_id
            AND e.source_ordinal = NEW.source_ordinal) THEN
        RAISE EXCEPTION 'scenario_contribution must repeat its cash-flow event''s lineage, in'
            ' its tenant (D-31 §6, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM calculation_run r JOIN dataset_snapshot_member m
            ON m.dataset_snapshot_id = r.dataset_snapshot_id
            WHERE r.id = NEW.calculation_run_id AND m.import_job_id = NEW.import_job_id) THEN
        RAISE EXCEPTION 'scenario_contribution must read a member import of its run''s'
            ' snapshot (D-30, D-31 §6)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM liquidity_gap_result g WHERE g.id = NEW.liquidity_gap_result_id
            AND g.calculation_run_id = NEW.calculation_run_id AND g.currency = NEW.unit
            AND g.bucket_code = NEW.bucket_code) THEN
        RAISE EXCEPTION 'scenario_contribution must add to a ladder row of its own run,'
            ' currency and bucket (D-31)' {DENY};
    END IF;
    IF NEW.instrument_version_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM instrument_version v
            WHERE v.id = NEW.instrument_version_id AND v.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'scenario_contribution must name an instrument version of its tenant'
            ' (D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

APPLICATION_CHECKS = f"""
CREATE FUNCTION scenario_shock_application_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM calculation_scenario_pin p
            WHERE p.calculation_run_id = NEW.calculation_run_id AND p.tenant_id = NEW.tenant_id
            AND p.shock_subset @> to_jsonb(NEW.shock_sequence)) THEN
        RAISE EXCEPTION 'scenario_shock_application names a shock its stressed run applied'
            ' (D-45 §5)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cashflow_event e WHERE e.id = NEW.cashflow_event_id
            AND e.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'scenario_shock_application must name a cash-flow event of its tenant'
            ' (D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (RUN_GUARD, PIN_CHECKS, CONTRIBUTION_CHECKS, APPLICATION_CHECKS)
_ROW_EVENTS = "BEFORE INSERT OR UPDATE OR DELETE"
# (trigger, timing and events, table, level, function); calc_output_guard() is 0018's.
TRIGGERS: tuple[tuple[str, str, str, str, str], ...] = (
    (f"{RUN}_guard", _ROW_EVENTS, RUN, "ROW", "scenario_run_guard()"),
    (f"{RUN}_no_truncate", "BEFORE TRUNCATE", RUN, "STATEMENT", "scenario_run_guard()"),
    *((f"{t}_guard", _ROW_EVENTS, t, "ROW", "calc_output_guard()") for t in OUTPUT_TABLES),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "calc_output_guard()")
        for t in OUTPUT_TABLES
    ),
    (f"{PIN}_checks", "BEFORE INSERT", PIN, "ROW", "calculation_scenario_pin_checks()"),
    (
        f"{CONTRIBUTION}_checks",
        "BEFORE INSERT",
        CONTRIBUTION,
        "ROW",
        "scenario_contribution_checks()",
    ),
    (
        f"{APPLICATION}_checks",
        "BEFORE INSERT",
        APPLICATION,
        "ROW",
        "scenario_shock_application_checks()",
    ),
)

# --- columns -------------------------------------------------------------------------------


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _common(table: str) -> list[Any]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _fk(table: str, column: str, target: str, nullable: bool = False) -> list[Any]:
    return [
        sa.Column(column, sa.UUID(), nullable=nullable),
        sa.ForeignKeyConstraint(
            [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}")
        ),
    ]


def _ck(table: str, name: str, expression: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=op.f(f"ck_{table}_{name}"))


def _uq(table: str, *columns: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(*columns, name=op.f(f"uq_{table}_{columns[0]}"))


def _classification(table: str) -> list[Any]:
    return [
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["classification"],
            ["data_classification.code"],
            name=op.f(f"fk_{table}_classification_data_classification"),
        ),
    ]


def _output(table: str) -> list[Any]:
    return [
        *_fk(table, "calculation_run_id", "calculation_run"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        _uq(table, "calculation_run_id", "sequence"),
        _ck(table, "sequence", "sequence >= 1"),
    ]


def _jsonb(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False)


def _create_tables() -> None:
    t = RUN
    op.create_table(
        t,
        *_fk(t, "scenario_id", "scenario"),
        *_fk(t, "scenario_version_id", "scenario_version"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("shocks_sha256", sa.String(length=64), nullable=False),
        sa.Column("approval_state", sa.String(length=20), nullable=False),
        *_fk(t, "baseline_run_id", "calculation_run"),
        sa.Column("reporting_date", sa.Date(), nullable=False),
        sa.Column("shock_count", sa.Integer(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        _jsonb("shock_counts"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        *_common(t),
        sa.UniqueConstraint(
            "tenant_id", "created_by", "idempotency_key", name=op.f("uq_scenario_run_idempotency")
        ),
        _ck(t, "approval_state", _in("approval_state", APPROVAL_STATES)),
        _ck(t, "shocks_sha256", f"shocks_sha256 {SHA}"),
        _ck(t, "request_sha256", f"request_sha256 {SHA}"),
        _ck(t, "shock_count", f"shock_count BETWEEN {MIN_SHOCKS} AND {MAX_SHOCKS}"),
        _ck(t, "run_count", "run_count = CASE WHEN shock_count = 1 THEN 1 ELSE shock_count + 1 END"),
        _ck(
            t,
            "shock_counts",
            "jsonb_typeof(shock_counts) = 'array' AND jsonb_array_length(shock_counts) ="
            " shock_count",
        ),
        _ck(t, "version", "version >= 1"),
    )
    t = PIN
    op.create_table(
        t,
        *_fk(t, "calculation_run_id", "calculation_run"),
        *_fk(t, "scenario_run_id", RUN),
        *_fk(t, "scenario_id", "scenario"),
        *_fk(t, "scenario_version_id", "scenario_version"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("shocks_sha256", sa.String(length=64), nullable=False),
        _jsonb("shock_subset"),
        sa.Column("combined", sa.Boolean(), nullable=False),
        sa.Column("approval_state", sa.String(length=20), nullable=False),
        *_fk(t, "baseline_run_id", "calculation_run"),
        *_common(t),
        sa.UniqueConstraint("calculation_run_id", name=op.f("uq_calculation_scenario_pin_run")),
        _ck(t, "shocks_sha256", f"shocks_sha256 {SHA}"),
        _ck(
            t,
            "shock_subset",
            "jsonb_typeof(shock_subset) = 'array' AND jsonb_array_length(shock_subset)"
            f" BETWEEN {MIN_SHOCKS} AND {MAX_SHOCKS}",
        ),
        _ck(t, "approval_state", _in("approval_state", APPROVAL_STATES)),
        _ck(t, "version", "version >= 1"),
        _ck(t, "baseline", "baseline_run_id <> calculation_run_id"),
    )
    t = CONTRIBUTION
    op.create_table(
        t,
        *_output(t),
        *_fk(t, "cashflow_event_id", "cashflow_event"),
        sa.Column("part_path", sa.String(length=40), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=False),
        _jsonb("shock_sequences"),
        sa.Column("change", sa.String(length=10), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(length=3), nullable=False),
        *_classification(t),
        *_fk(t, "liquidity_gap_result_id", "liquidity_gap_result"),
        sa.Column("bucket_code", sa.String(length=20), nullable=False),
        sa.Column("original_bucket_code", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("days_after_reporting_date", sa.Integer(), nullable=False),
        sa.Column("original_days", sa.Integer(), nullable=False),
        _jsonb("rule_version_ids"),
        *_fk(t, "instrument_version_id", "instrument_version", nullable=True),
        *_fk(t, "canonical_record_id", "canonical_record"),
        *_fk(t, "import_job_id", "import_job"),
        *_fk(t, "source_record_id", "source_record"),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        *_common(t),
        sa.UniqueConstraint(
            "calculation_run_id",
            "cashflow_event_id",
            "part_path",
            name=op.f("uq_scenario_contribution_part"),
        ),
        _ck(t, "part_path", f"part_path {PART_PATH}"),
        _ck(t, "change", _in("change", PART_CHANGES)),
        _ck(t, "unchanged", "(change = 'UNCHANGED') = (part_path = '0')"),
        _ck(t, "shock_sequences", "jsonb_typeof(shock_sequences) = 'array'"),
        _ck(t, "unit", f"unit {CURRENCY}"),
        _ck(t, "direction", _in("direction", DIRECTIONS)),
        _ck(t, "origin", _in("origin", ORIGINS)),
        _ck(t, "value", "value >= 0"),
        _ck(t, "rule_version_ids", "jsonb_typeof(rule_version_ids) = 'array'"),
        _ck(t, "source_ordinal", "source_ordinal >= 1"),
    )
    t = APPLICATION
    op.create_table(
        t,
        *_output(t),
        *_fk(t, "cashflow_event_id", "cashflow_event"),
        sa.Column("shock_sequence", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=30), nullable=False),
        sa.Column("unit", sa.String(length=3), nullable=False),
        sa.Column("parts_matched", sa.Integer(), nullable=False),
        sa.Column("parts_after", sa.Integer(), nullable=False),
        sa.Column("moved_amount", sa.Numeric(), nullable=False),
        sa.Column("added_amount", sa.Numeric(), nullable=False),
        sa.Column("delay_days", sa.Integer(), nullable=True),
        sa.Column("moved_to_day", sa.Integer(), nullable=True),
        *_classification(t),
        *_common(t),
        sa.UniqueConstraint(
            "calculation_run_id",
            "cashflow_event_id",
            "shock_sequence",
            name=op.f("uq_scenario_shock_application_flow"),
        ),
        _ck(t, "shock_sequence", f"shock_sequence BETWEEN 1 AND {MAX_SHOCKS}"),
        _ck(t, "template", _in("template", TEMPLATES)),
        _ck(t, "unit", f"unit {CURRENCY}"),
        _ck(
            t,
            "parts",
            "parts_matched >= 1 AND parts_after >= parts_matched"
            f" AND parts_after <= {MAX_PARTS_PER_FLOW}",
        ),
        _ck(t, "amounts", "moved_amount >= 0 AND added_amount >= 0"),
        _ck(
            t,
            "template_fields",
            "CASE template"
            " WHEN 'DEPOSIT_WITHDRAWAL' THEN moved_amount = 0 AND delay_days IS NULL"
            " AND moved_to_day IS NULL"
            " WHEN 'DELAYED_INFLOWS' THEN added_amount = 0 AND delay_days >= 1"
            " AND moved_to_day IS NULL"
            " ELSE added_amount = 0 AND delay_days IS NULL AND moved_to_day >= 2 END",
        ),
    )


def _indexes() -> list[tuple[str, str, list[str]]]:
    """(index, table, columns), as the models declare them."""
    found = [(f"ix_{t}_tenant_id", t, ["tenant_id"]) for t in TABLES]
    found += [
        (f"ix_{RUN}_scenario_id", RUN, ["scenario_id"]),
        (f"ix_{RUN}_baseline_run_id", RUN, ["baseline_run_id"]),
        (f"ix_{PIN}_scenario_run_id", PIN, ["scenario_run_id"]),
        (f"ix_{PIN}_baseline_run_id", PIN, ["baseline_run_id"]),
    ]
    found += [(f"ix_{t}_calculation_run_id", t, ["calculation_run_id"]) for t in OUTPUT_TABLES[1:]]
    found.append(
        (f"ix_{CONTRIBUTION}_liquidity_gap_result_id", CONTRIBUTION, ["liquidity_gap_result_id"])
    )
    return found


def upgrade() -> None:
    _create_tables()
    for name, table, columns in _indexes():
        op.create_index(op.f(name), table, columns, unique=False)
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, call in TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {call}")


def downgrade() -> None:
    # Stressed runs and their evidence are kept (D-02, D-45): refuse rather than drop them.
    checks = " OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in TABLES)
    op.execute(
        f"DO $$ BEGIN IF {checks} THEN RAISE EXCEPTION 'scenario runs, scenario pins,"
        " scenario contributions or shock applications exist; they are kept (D-02, D-45), so"
        " this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    for name, table, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
