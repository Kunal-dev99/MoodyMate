"""WS3 rule, module and assumption registry, dataset snapshots and calculation runs (BE-15.1,
BE-15.2; FC-04, FC-07; D-30, D-31, D-32, D-35), with the tagged ILLUSTRATIVE seed (D-11).

Registry tables: calculation_module, module_version, rule_definition, rule_version, rule_set,
rule_set_member, maturity_bucket_definition and assumption_set.
Calculation tables: dataset_snapshot, dataset_snapshot_member, calculation_run,
calculation_input_ref, liquidity_gap_result, intermediate_value and calculation_warning.

* Append-only (UPDATE, DELETE and TRUNCATE raise): calculation_module, module_version,
  rule_definition, rule_set, rule_set_member, maturity_bucket_definition, dataset_snapshot
  and dataset_snapshot_member.
* Lifecycle (rule_version, assumption_set): inserted DRAFT; edited only while DRAFT; outside
  DRAFT only the status moves along the D-06 table (DRAFT -> IN_REVIEW -> ACTIVE | REJECTED,
  ACTIVE -> SUPERSEDED | RETIRED), and the approval columns change only on the move out of
  IN_REVIEW. Never deleted or truncated. At most one ACTIVE version per rule definition (and
  per assumption set code): partial unique indexes.
* calculation_run: inserted RUNNING; moves once to a terminal status, changing only its
  status, result hash, end time, outcome and warning count; then immutable. Never deleted or
  truncated. Its output tables (calculation_input_ref, liquidity_gap_result,
  intermediate_value, calculation_warning) take inserts only while the run is RUNNING, in
  its tenant (the run row is read FOR SHARE, so it cannot finish under an open insert), and
  are append-only.
* Insert checks (D-04): every reference stays in the row's tenant; bucket definitions are
  added only to a DRAFT DAY_BUCKETS rule version (materialised before it freezes); a ladder
  row names a bucket of its run's pinned bucket rule version; a snapshot member repeats
  its import's manifest (hash, counts, schema and mapping versions), projection (schema code,
  projector version), source and reconciliation (outcome, never BROKEN); a snapshot or rule
  set takes no member beyond its count; an intermediate value repeats its cash-flow event's
  lineage, comes from a member import of its run's snapshot and adds to a ladder row of its
  own run, currency and bucket.
* Seed, tagged `seeded_by = '0018_calculations'` (D-11), PROVISIONAL / ILLUSTRATIVE (D-35),
  for the default tenant: module `maturity_ladder` v1 AVAILABLE with its declared contracts;
  the rules `maturity_buckets`, `cashflow_direction` and `past_due_treatment`, each with an
  ACTIVE v1 (inserted DRAFT and moved through IN_REVIEW, approved_by the tag); the eight
  materialised bucket definitions; the ACTIVE `baseline (illustrative)` assumption set v1,
  empty. Frozen copies of the code's enums and seeds, kept equal by test_calculations.

The downgrade refuses while any snapshot, rule set or run exists, or any registry row that
is not the tagged seed (D-02: calculation evidence is kept), then drops the triggers,
functions and tables (the seed goes with its tables).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-17

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0018_calculations"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs

# --- frozen copies of the code's enums (test_calculations keeps them equal) ------------------
IMPLEMENTATION_STATUSES = ("AVAILABLE", "PLANNED")
RULE_STATUSES = (
    "DRAFT", "IN_REVIEW", "APPROVED", "ACTIVE", "SUPERSEDED", "RETIRED", "REJECTED",
)  # fmt: skip
RULE_TRANSITIONS = {
    "DRAFT": ("IN_REVIEW",),
    "IN_REVIEW": ("ACTIVE", "REJECTED"),
    "APPROVED": (),
    "ACTIVE": ("SUPERSEDED", "RETIRED"),
    "SUPERSEDED": (),
    "RETIRED": (),
    "REJECTED": (),
}
RULE_CATEGORIES = ("BUCKETING", "CLASSIFICATION", "TREATMENT", "FACTOR")
PARAMETER_KINDS = ("DECIMAL", "INTEGER", "ENUM", "DAY_BUCKETS", "ENUM_MAP", "DECIMAL_MAP")
RUN_STATUSES = (
    "QUEUED", "RUNNING", "SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "FAILED", "CANCELLED", "BLOCKED",
)  # fmt: skip
TERMINAL = ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS", "FAILED", "CANCELLED", "BLOCKED")
WARNING_CODES = (
    "INSTRUMENT_NOT_IN_SNAPSHOT", "UNMAPPED_INSTRUMENT_TYPE", "PAST_DUE_EXCLUDED",
    "PAST_DUE_IN_FIRST_BUCKET", "OUTSIDE_BUCKETS", "MISSING_VALUE", "NEGATIVE_AMOUNT",
    "UNKNOWN_ORIGIN", "INPUT_SCHEMA_MISSING", "PARAMETERS_INVALID", "BUCKET_DEFINITIONS_MISMATCH",
    "AMBIGUOUS_INSTRUMENT_KEY", "POST_CHECK_FAILED", "ENGINE_ERROR",
)  # fmt: skip
SEVERITIES = ("INFO", "WARNING", "ERROR")
INPUT_REF_TYPES = (
    "DATASET_SNAPSHOT", "IMPORT_MANIFEST", "DOMAIN_PROJECTION", "SCHEMA_VERSION",
    "MAPPING_VERSION", "MODULE_VERSION", "ENGINE", "RULE_SET", "RULE_VERSION", "ASSUMPTION_SET",
)  # fmt: skip
INTERMEDIATE_CODES = ("CASHFLOW_CONTRIBUTION",)
DIRECTIONS = ("INFLOW", "OUTFLOW")
ORIGINS = ("CONTRACTUAL", "BEHAVIOURAL")
SCHEMA_CODES = ("instrument", "position", "cashflow_event")
SNAPSHOT_OUTCOMES = ("MATCHED", "REVIEW_REQUIRED")  # D-30 §3: never BROKEN

# --- frozen copies of the seed (D-35) --------------------------------------------------------
STATEMENT = (
    "Illustrative prototype. Not a regulatory calculation; no parity with any vendor system "
    "is claimed."
)
MODULE = {
    "code": "maturity_ladder",
    "name": "Maturity ladder (illustrative)",
    "description": "Per-currency liquidity gaps by time bucket. " + STATEMENT,
}
MODULE_VERSION = {
    "version": 1,
    "implementation_status": "AVAILABLE",
    "engine_version": "1",
    "description": (
        "Per-currency maturity ladder with no FX: cash flows bucketed by calendar days after "
        "the reporting date, direction by instrument type, past-due cash flows per the rule. "
        "Illustrative prototype; not a regulatory calculation (D-35)."
    ),
    "input_contract": {
        "schemas": [
            {
                "schema_code": "cashflow_event",
                "required": True,
                "fields": ["cashflow_id", "instrument_id", "due_date", "amount", "currency", "origin"],
            },
            {
                "schema_code": "instrument",
                "required": True,
                "fields": ["instrument_id", "instrument_type"],
            },
        ],
        "rules": ["maturity_buckets", "cashflow_direction", "past_due_treatment"],
        "assumption_keys": [],
        "parameters": ["reporting_date"],
    },
    "output_contract": {
        "results": ["liquidity_gap_result"],
        "intermediates": ["CASHFLOW_CONTRIBUTION"],
        "warning_codes": [
            "AMBIGUOUS_INSTRUMENT_KEY", "INSTRUMENT_NOT_IN_SNAPSHOT", "MISSING_VALUE",
            "NEGATIVE_AMOUNT", "OUTSIDE_BUCKETS", "PARAMETERS_INVALID", "PAST_DUE_EXCLUDED",
            "PAST_DUE_IN_FIRST_BUCKET", "UNKNOWN_ORIGIN", "UNMAPPED_INSTRUMENT_TYPE",
        ],  # fmt: skip
    },
}


def _bucket(code: str, label: str, lower: int, upper: int | None) -> dict[str, Any]:
    return {"code": code, "label": label, "lower_exclusive": lower, "upper_inclusive": upper}


BUCKETS = [
    _bucket("B01", "(0,1]", 0, 1),
    _bucket("B02", "(1,7]", 1, 7),
    _bucket("B03", "(7,30]", 7, 30),
    _bucket("B04", "(30,90]", 30, 90),
    _bucket("B05", "(90,180]", 90, 180),
    _bucket("B06", "(180,365]", 180, 365),
    _bucket("B07", "(365,730]", 365, 730),
    _bucket("B08", "(730,inf)", 730, None),
]
# code -> (name, category, parameter_schema, parameters, description)
RULES: dict[str, tuple[str, str, dict[str, Any], dict[str, Any], str]] = {
    "maturity_buckets": (
        "Maturity buckets (illustrative)",
        "BUCKETING",
        {"kind": "DAY_BUCKETS", "unit": "calendar days after reporting date"},
        {"buckets": BUCKETS},
        "Illustrative day intervals (0,1], (1,7], (7,30], (30,90], (90,180], (180,365], "
        "(365,730] and (730,inf), in calendar days after the reporting date (D-35).",
    ),
    "cashflow_direction": (
        "Cash-flow direction by instrument type (illustrative)",
        "CLASSIFICATION",
        {"kind": "ENUM_MAP", "key_vocabulary": "instrument_type", "values": ["INFLOW", "OUTFLOW"]},
        {"map": {"BOND": "INFLOW", "DEPOSIT": "OUTFLOW", "LOAN": "INFLOW"}},
        "LOAN and BOND are inflows and DEPOSIT is an outflow: an illustrative asset/liability "
        "assumption, because cashflow_event v1 has no direction (D-35).",
    ),
    "past_due_treatment": (
        "Past-due cash-flow treatment (illustrative)",
        "TREATMENT",
        {"kind": "ENUM", "values": ["EXCLUDE_WITH_WARNING", "INCLUDE_IN_FIRST_BUCKET"]},
        {"value": "EXCLUDE_WITH_WARNING"},
        "A cash flow due before the reporting date is excluded from the ladder with a "
        "warning (D-35).",
    ),
}
RULE_OWNER = "Methodology owner (risk_analyst), provisional"
ASSUMPTION_SET = {
    "code": "baseline",
    "name": "baseline (illustrative)",
    "owner": RULE_OWNER,
    "provenance": "Seeded by migration 0018 (D-32 §6, D-35): empty in v1, no module reads an "
    "assumption yet. " + STATEMENT,
    "assumptions": {},
}
SEED_SUMMARY = "ILLUSTRATIVE seed (D-35); PROVISIONAL until the client's financial owner approves."

APPEND_ONLY_TABLES = (
    "calculation_module",
    "module_version",
    "rule_definition",
    "rule_set",
    "rule_set_member",
    "maturity_bucket_definition",
    "dataset_snapshot",
    "dataset_snapshot_member",
)
OUTPUT_TABLES = (
    "calculation_input_ref",
    "liquidity_gap_result",
    "intermediate_value",
    "calculation_warning",
)
SEEDED_TABLES = (
    "calculation_module",
    "module_version",
    "rule_definition",
    "rule_version",
    "maturity_bucket_definition",
    "assumption_set",
)
# Rows the downgrade refuses to lose: every row of these, and the untagged rows of the seeded.
KEPT = (
    "dataset_snapshot",
    "dataset_snapshot_member",
    "rule_set",
    "rule_set_member",
    "calculation_run",
    *OUTPUT_TABLES,
)
TABLES = (  # creation order
    "calculation_module",
    "module_version",
    "rule_definition",
    "rule_version",
    "rule_set",
    "rule_set_member",
    "maturity_bucket_definition",
    "assumption_set",
    "dataset_snapshot",
    "dataset_snapshot_member",
    "calculation_run",
    "calculation_input_ref",
    "liquidity_gap_result",
    "intermediate_value",
    "calculation_warning",
)


def canonical_sha256(value: object) -> str:
    """Frozen copy of app.rules.service.canonical_sha256 (a test keeps them equal)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{TENANT}:{code}")


# --- trigger functions ---------------------------------------------------------------------

DENY = "USING ERRCODE = 'insufficient_privilege'"

APPEND_ONLY = f"""
CREATE FUNCTION calc_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (BE-15.1, BE-15.2): % rejected',
        TG_TABLE_NAME, TG_OP {DENY};
END;
$$
"""

_MOVES = ", ".join(f"'{a}>{b}'" for a, bs in RULE_TRANSITIONS.items() for b in bs)
LIFECYCLE = f"""
CREATE FUNCTION calc_lifecycle_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutable text[] := ARRAY['status', 'approved_by', 'approved_at', 'decision_comment',
        'updated_at'];
    fixed text[];
    k text;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION '% rows are never deleted (D-32 §2): % rejected',
            TG_TABLE_NAME, TG_OP {DENY};
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'a new % is DRAFT (D-32 §2)', TG_TABLE_NAME {DENY};
        END IF;
        RETURN NEW;
    END IF;
    fixed := ARRAY['id', 'tenant_id', 'version', 'created_at', 'created_by', 'seeded_by',
        'authored_by', TG_ARGV[0]];
    FOREACH k IN ARRAY fixed LOOP
        IF (to_jsonb(NEW) -> k) IS DISTINCT FROM (to_jsonb(OLD) -> k) THEN
            RAISE EXCEPTION '% % never changes (D-32 §2)', TG_TABLE_NAME, k {DENY};
        END IF;
    END LOOP;
    IF OLD.status <> 'DRAFT' AND (to_jsonb(NEW) - mutable) IS DISTINCT FROM
            (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION '% is immutable once it leaves DRAFT (D-32 §2)', TG_TABLE_NAME {DENY};
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
            AND (OLD.status || '>' || NEW.status) NOT IN ({_MOVES}) THEN
        RAISE EXCEPTION '% status move % -> % is not allowed (D-06, D-32 §2)',
            TG_TABLE_NAME, OLD.status, NEW.status {DENY};
    END IF;
    IF OLD.status <> 'IN_REVIEW' AND (NEW.approved_by, NEW.approved_at, NEW.decision_comment)
            IS DISTINCT FROM (OLD.approved_by, OLD.approved_at, OLD.decision_comment) THEN
        RAISE EXCEPTION '% approval is recorded only on the move out of IN_REVIEW (D-32 §3)',
            TG_TABLE_NAME {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

RUN_GUARD = f"""
CREATE FUNCTION calculation_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutable text[] := ARRAY['status', 'result_sha256', 'ended_at', 'outcome', 'warning_count',
        'updated_at'];
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'calculation runs are never deleted (D-31 §5): % rejected', TG_OP {DENY};
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'RUNNING' THEN
            RAISE EXCEPTION 'a calculation_run is written RUNNING (D-31 §3)' {DENY};
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status <> 'RUNNING' THEN
        RAISE EXCEPTION 'a finished calculation_run is immutable (D-31 §5): % rejected',
            TG_OP {DENY};
    END IF;
    IF NOT ({_in("NEW.status", TERMINAL)}) THEN
        RAISE EXCEPTION 'a RUNNING calculation_run moves only to a terminal status (D-31 §5)'
            {DENY};
    END IF;
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION 'only the status, result hash, end time and outcome of a calculation_run'
            ' change, once (D-31 §5)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

OUTPUT_GUARD = f"""
CREATE FUNCTION calc_output_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '% is append-only (D-31 §5): % rejected', TG_TABLE_NAME, TG_OP {DENY};
    END IF;
    -- FOR SHARE: a run that is finishing (its row updated, not yet committed) is waited
    -- for and then seen as finished; a run cannot finish while an insert under it is open.
    PERFORM 1 FROM calculation_run r WHERE r.id = NEW.calculation_run_id
        AND r.tenant_id = NEW.tenant_id AND r.status = 'RUNNING' FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '% rows are written only while their run is RUNNING, in its tenant'
            ' (D-31 §5)', TG_TABLE_NAME {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

# Arguments: pairs of (column, referenced table); each referenced row is in NEW's tenant.
SAME_TENANT = f"""
CREATE FUNCTION calc_same_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    i integer := 0;
    ref text;
    ok boolean;
BEGIN
    WHILE i < TG_NARGS LOOP
        ref := to_jsonb(NEW) ->> TG_ARGV[i];
        IF ref IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE id = $1 AND tenant_id = $2)',
                TG_ARGV[i + 1]) INTO ok USING ref::uuid, NEW.tenant_id;
            IF NOT ok THEN
                RAISE EXCEPTION '% must name a % of its own tenant (D-04)',
                    TG_TABLE_NAME, TG_ARGV[i + 1] {DENY};
            END IF;
        END IF;
        i := i + 2;
    END LOOP;
    RETURN NEW;
END;
$$
"""

MEMBER_CHECKS = f"""
CREATE FUNCTION dataset_snapshot_member_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM import_job j
            JOIN import_manifest m ON m.import_job_id = j.id
            JOIN domain_projection p ON p.import_job_id = j.id
            JOIN reconciliation_run r ON r.import_job_id = j.id
        WHERE j.id = NEW.import_job_id AND j.tenant_id = NEW.tenant_id
            AND j.source_system_id = NEW.source_system_id
            AND m.id = NEW.import_manifest_id AND m.manifest_sha256 = NEW.manifest_sha256
            AND m.schema_version_id = NEW.schema_version_id
            AND m.mapping_version_id = NEW.mapping_version_id
            AND m.accepted_count = NEW.accepted_count
            AND m.quarantined_count = NEW.quarantined_count
            AND m.rejected_count = NEW.rejected_count
            AND p.id = NEW.domain_projection_id AND p.tenant_id = NEW.tenant_id
            AND p.schema_code = NEW.schema_code AND p.projector_version = NEW.projector_version
            AND r.id = NEW.reconciliation_run_id AND r.tenant_id = NEW.tenant_id
            AND r.outcome = NEW.reconciliation_outcome
    ) THEN
        RAISE EXCEPTION 'dataset_snapshot_member must repeat its import''s manifest, projection'
            ' and reconciliation, in its tenant (D-30, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM dataset_snapshot s WHERE s.id = NEW.dataset_snapshot_id
            AND s.tenant_id = NEW.tenant_id AND s.member_count > (SELECT count(*)
            FROM dataset_snapshot_member x WHERE x.dataset_snapshot_id = NEW.dataset_snapshot_id))
    THEN
        RAISE EXCEPTION 'dataset_snapshot_member: the snapshot is complete or in another tenant'
            ' (D-30)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

RULE_SET_MEMBER_CHECKS = f"""
CREATE FUNCTION rule_set_member_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM rule_version v WHERE v.id = NEW.rule_version_id
            AND v.rule_definition_id = NEW.rule_definition_id AND v.tenant_id = NEW.tenant_id)
    THEN
        RAISE EXCEPTION 'rule_set_member must name a rule version of its definition and tenant'
            ' (D-32, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM rule_set s WHERE s.id = NEW.rule_set_id
            AND s.tenant_id = NEW.tenant_id AND s.member_count > (SELECT count(*)
            FROM rule_set_member x WHERE x.rule_set_id = NEW.rule_set_id)) THEN
        RAISE EXCEPTION 'rule_set_member: the rule set is complete or in another tenant'
            ' (D-32 §4)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

BUCKET_CHECKS = f"""
CREATE FUNCTION maturity_bucket_definition_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Materialised while the version is DRAFT (its parameters freeze when it leaves DRAFT):
    -- an ACTIVE version's buckets can never change under the runs that pin it.
    IF NOT EXISTS (SELECT 1 FROM rule_version v JOIN rule_definition d
            ON d.id = v.rule_definition_id
            WHERE v.id = NEW.rule_version_id AND v.tenant_id = NEW.tenant_id
            AND v.status = 'DRAFT' AND d.parameter_schema ->> 'kind' = 'DAY_BUCKETS') THEN
        RAISE EXCEPTION 'maturity_bucket_definition rows are added only to a DRAFT DAY_BUCKETS'
            ' rule version of their tenant (D-32 §5)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

GAP_CHECKS = f"""
CREATE FUNCTION liquidity_gap_result_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM calculation_run r
            JOIN rule_set_member m ON m.rule_set_id = r.rule_set_id
            JOIN maturity_bucket_definition b ON b.rule_version_id = m.rule_version_id
            WHERE r.id = NEW.calculation_run_id AND b.id = NEW.bucket_definition_id
            AND b.tenant_id = NEW.tenant_id AND b.code = NEW.bucket_code
            AND b.sequence = NEW.bucket_sequence) THEN
        RAISE EXCEPTION 'liquidity_gap_result must name a bucket of its run''s pinned bucket'
            ' rule version, in its tenant (D-32 §5, D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

INTERMEDIATE_CHECKS = f"""
CREATE FUNCTION intermediate_value_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cashflow_event e WHERE e.id = NEW.cashflow_event_id
            AND e.tenant_id = NEW.tenant_id AND e.canonical_record_id = NEW.canonical_record_id
            AND e.import_job_id = NEW.import_job_id AND e.source_record_id = NEW.source_record_id
            AND e.source_ordinal = NEW.source_ordinal) THEN
        RAISE EXCEPTION 'intermediate_value must repeat its cash-flow event''s lineage, in its'
            ' tenant (D-31 §6, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM calculation_run r JOIN dataset_snapshot_member m
            ON m.dataset_snapshot_id = r.dataset_snapshot_id
            WHERE r.id = NEW.calculation_run_id AND m.import_job_id = NEW.import_job_id) THEN
        RAISE EXCEPTION 'intermediate_value must read a member import of its run''s snapshot'
            ' (D-30, D-31 §6)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM liquidity_gap_result g WHERE g.id = NEW.liquidity_gap_result_id
            AND g.calculation_run_id = NEW.calculation_run_id AND g.currency = NEW.unit
            AND g.bucket_code = NEW.bucket_code) THEN
        RAISE EXCEPTION 'intermediate_value must add to a ladder row of its own run, currency'
            ' and bucket (D-31)' {DENY};
    END IF;
    IF NEW.instrument_version_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM instrument_version v
            WHERE v.id = NEW.instrument_version_id AND v.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'intermediate_value must name an instrument version of its tenant'
            ' (D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (
    APPEND_ONLY,
    LIFECYCLE,
    RUN_GUARD,
    OUTPUT_GUARD,
    SAME_TENANT,
    MEMBER_CHECKS,
    RULE_SET_MEMBER_CHECKS,
    BUCKET_CHECKS,
    GAP_CHECKS,
    INTERMEDIATE_CHECKS,
)
_ROW_EVENTS = "BEFORE INSERT OR UPDATE OR DELETE"
# (trigger, timing and events, table, level, function and arguments)
TRIGGERS: tuple[tuple[str, str, str, str, str], ...] = (
    *(
        (f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", "calc_append_only()")
        for t in APPEND_ONLY_TABLES
    ),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "calc_append_only()")
        for t in APPEND_ONLY_TABLES
    ),
    *(
        (f"{t}_lifecycle", _ROW_EVENTS, t, "ROW", f"calc_lifecycle_guard('{arg}')")
        for t, arg in (("rule_version", "rule_definition_id"), ("assumption_set", "code"))
    ),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", f"calc_lifecycle_guard('{arg}')")
        for t, arg in (("rule_version", "rule_definition_id"), ("assumption_set", "code"))
    ),
    ("calculation_run_guard", _ROW_EVENTS, "calculation_run", "ROW", "calculation_run_guard()"),
    (
        "calculation_run_no_truncate",
        "BEFORE TRUNCATE",
        "calculation_run",
        "STATEMENT",
        "calculation_run_guard()",
    ),
    *((f"{t}_guard", _ROW_EVENTS, t, "ROW", "calc_output_guard()") for t in OUTPUT_TABLES),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "calc_output_guard()")
        for t in OUTPUT_TABLES
    ),
    *(
        (f"{t}_same_tenant", "BEFORE INSERT", t, "ROW", f"calc_same_tenant({args})")
        for t, args in (
            ("module_version", "'module_id', 'calculation_module'"),
            ("rule_definition", "'target_module_id', 'calculation_module'"),
            ("rule_version", "'rule_definition_id', 'rule_definition'"),
            (
                "calculation_run",
                "'dataset_snapshot_id', 'dataset_snapshot', 'module_version_id', "
                "'module_version', 'rule_set_id', 'rule_set', 'assumption_set_id', "
                "'assumption_set', 'rerun_of_run_id', 'calculation_run'",
            ),
        )
    ),
    (
        "dataset_snapshot_member_checks",
        "BEFORE INSERT",
        "dataset_snapshot_member",
        "ROW",
        "dataset_snapshot_member_checks()",
    ),
    (
        "rule_set_member_checks",
        "BEFORE INSERT",
        "rule_set_member",
        "ROW",
        "rule_set_member_checks()",
    ),
    (
        "maturity_bucket_definition_checks",
        "BEFORE INSERT",
        "maturity_bucket_definition",
        "ROW",
        "maturity_bucket_definition_checks()",
    ),
    (
        "liquidity_gap_result_checks",
        "BEFORE INSERT",
        "liquidity_gap_result",
        "ROW",
        "liquidity_gap_result_checks()",
    ),
    (
        "intermediate_value_checks",
        "BEFORE INSERT",
        "intermediate_value",
        "ROW",
        "intermediate_value_checks()",
    ),
)

# --- columns -------------------------------------------------------------------------------


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


def _labelled() -> list[Any]:
    return [
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("illustrative", sa.Boolean(), nullable=False),
        sa.Column("seeded_by", sa.String(length=100), nullable=True),
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


def _versioned(table: str) -> list[Any]:
    return [
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("authored_by", sa.String(length=255), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(length=1000), nullable=True),
        sa.Column("change_summary", sa.String(length=1000), nullable=True),
        _ck(table, "version", "version >= 1"),
        _ck(table, "status", _in("status", RULE_STATUSES)),
        _ck(
            table,
            "effective",
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
        ),
        _ck(
            table,
            "approved",
            "status <> 'ACTIVE' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
        ),
    ]


def _output(table: str) -> list[Any]:
    return [
        *_fk(table, "calculation_run_id", "calculation_run"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        _uq(table, "calculation_run_id", "sequence"),
        _ck(table, "sequence", "sequence >= 1"),
    ]


def _numeric(*names: str) -> list[sa.Column]:  # type: ignore[type-arg]
    return [sa.Column(n, sa.Numeric(), nullable=False) for n in names]


SHA = "~ '^[0-9a-f]{64}$'"
CODE = "~ '^[a-z][a-z0-9_]*$'"
CURRENCY = "~ '^[A-Z]{3}$'"


def _create_tables() -> None:
    t = "calculation_module"
    op.create_table(
        t,
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        *_labelled(),
        *_common(t),
        _uq(t, "tenant_id", "code"),
        _ck(t, "code", f"code {CODE}"),
    )
    t = "module_version"
    op.create_table(
        t,
        *_fk(t, "module_id", "calculation_module"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("implementation_status", sa.String(length=20), nullable=False),
        sa.Column("input_contract", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_contract", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("engine_version", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        *_labelled(),
        *_common(t),
        _uq(t, "module_id", "version"),
        _ck(t, "version", "version >= 1"),
        _ck(t, "implementation_status", _in("implementation_status", IMPLEMENTATION_STATUSES)),
        _ck(t, "input_contract", "jsonb_typeof(input_contract) = 'object'"),
        _ck(t, "output_contract", "jsonb_typeof(output_contract) = 'object'"),
    )
    t = "rule_definition"
    op.create_table(
        t,
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        *_fk(t, "target_module_id", "calculation_module"),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("parameter_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        *_labelled(),
        *_common(t),
        _uq(t, "tenant_id", "code"),
        _ck(t, "code", f"code {CODE}"),
        _ck(t, "category", _in("category", RULE_CATEGORIES)),
        _ck(
            t,
            "parameter_schema",
            "jsonb_typeof(parameter_schema) = 'object' AND "
            + _in("(parameter_schema ->> 'kind')", PARAMETER_KINDS),
        ),
    )
    t = "rule_version"
    op.create_table(
        t,
        *_fk(t, "rule_definition_id", "rule_definition"),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameters_sha256", sa.String(length=64), nullable=False),
        *_versioned(t),
        *_labelled(),
        *_common(t),
        _uq(t, "rule_definition_id", "version"),
        _ck(t, "parameters", "jsonb_typeof(parameters) = 'object'"),
        _ck(t, "parameters_sha256", f"parameters_sha256 {SHA}"),
    )
    t = "rule_set"
    op.create_table(
        t,
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        *_common(t),
        _uq(t, "tenant_id", "content_sha256"),
        _ck(t, "content_sha256", f"content_sha256 {SHA}"),
        _ck(t, "member_count", "member_count >= 1"),
    )
    t = "rule_set_member"
    op.create_table(
        t,
        *_fk(t, "rule_set_id", "rule_set"),
        *_fk(t, "rule_version_id", "rule_version"),
        *_fk(t, "rule_definition_id", "rule_definition"),
        sa.Column("precedence", sa.Integer(), nullable=False),
        *_common(t),
        _uq(t, "rule_set_id", "rule_version_id"),
        _uq(t, "rule_definition_id", "rule_set_id"),
        _ck(t, "precedence", "precedence >= 1"),
    )
    t = "maturity_bucket_definition"
    op.create_table(
        t,
        *_fk(t, "rule_version_id", "rule_version"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("lower_exclusive_days", sa.Integer(), nullable=False),
        sa.Column("upper_inclusive_days", sa.Integer(), nullable=True),
        *_labelled(),
        *_common(t),
        _uq(t, "rule_version_id", "sequence"),
        _uq(t, "code", "rule_version_id"),
        _ck(t, "sequence", "sequence >= 1"),
        _ck(
            t,
            "bounds",
            "upper_inclusive_days IS NULL OR upper_inclusive_days > lower_exclusive_days",
        ),
    )
    t = "assumption_set"
    op.create_table(
        t,
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("provenance", sa.String(length=1000), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        *_versioned(t),
        *_labelled(),
        *_common(t),
        _uq(t, "tenant_id", "code", "version"),
        _ck(t, "code", f"code {CODE}"),
        _ck(t, "assumptions", "jsonb_typeof(assumptions) = 'object'"),
        _ck(t, "content_sha256", f"content_sha256 {SHA}"),
    )
    t = "dataset_snapshot"
    op.create_table(
        t,
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        *_common(t),
        _uq(t, "tenant_id", "content_sha256"),
        _ck(t, "content_sha256", f"content_sha256 {SHA}"),
        _ck(t, "member_count", "member_count >= 1"),
    )
    t = "dataset_snapshot_member"
    op.create_table(
        t,
        *_fk(t, "dataset_snapshot_id", "dataset_snapshot"),
        *_fk(t, "import_job_id", "import_job"),
        *_fk(t, "import_manifest_id", "import_manifest"),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        *_fk(t, "domain_projection_id", "domain_projection"),
        sa.Column("projector_version", sa.Integer(), nullable=False),
        sa.Column("schema_code", sa.String(length=30), nullable=False),
        *_fk(t, "schema_version_id", "schema_version"),
        *_fk(t, "mapping_version_id", "mapping_version"),
        *_fk(t, "source_system_id", "source_system"),
        *_fk(t, "reconciliation_run_id", "reconciliation_run"),
        sa.Column("reconciliation_outcome", sa.String(length=20), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        *_common(t),
        _uq(t, "dataset_snapshot_id", "schema_code"),
        _uq(t, "import_job_id", "dataset_snapshot_id"),
        _ck(t, "schema_code", _in("schema_code", SCHEMA_CODES)),
        _ck(t, "manifest_sha256", f"manifest_sha256 {SHA}"),
        _ck(t, "reconciliation_outcome", _in("reconciliation_outcome", SNAPSHOT_OUTCOMES)),
        _ck(
            t,
            "counts",
            "accepted_count >= 0 AND quarantined_count >= 0 AND rejected_count >= 0",
        ),
    )
    t = "calculation_run"
    op.create_table(
        t,
        *_fk(t, "dataset_snapshot_id", "dataset_snapshot"),
        *_fk(t, "module_version_id", "module_version"),
        *_fk(t, "rule_set_id", "rule_set"),
        *_fk(t, "assumption_set_id", "assumption_set"),
        sa.Column("reporting_date", sa.Date(), nullable=False),
        sa.Column("engine_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        *_fk(t, "rerun_of_run_id", "calculation_run", nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        *_common(t),
        _uq(t, "tenant_id", "created_by", "idempotency_key"),
        _ck(t, "status", _in("status", RUN_STATUSES)),
        _ck(t, "manifest_sha256", f"manifest_sha256 {SHA}"),
        _ck(t, "request_sha256", f"request_sha256 {SHA}"),
        _ck(t, "result_sha256", f"result_sha256 IS NULL OR result_sha256 {SHA}"),
        _ck(t, "manifest", "jsonb_typeof(manifest) = 'object'"),
        _ck(
            t,
            "finished",
            f"({_in('status', TERMINAL)}) = (result_sha256 IS NOT NULL"
            " AND ended_at IS NOT NULL AND outcome IS NOT NULL)",
        ),
        _ck(t, "times", "ended_at IS NULL OR ended_at >= started_at"),
        _ck(t, "warning_count", "warning_count >= 0"),
    )
    t = "calculation_input_ref"
    op.create_table(
        t,
        *_output(t),
        sa.Column("ref_type", sa.String(length=30), nullable=False),
        sa.Column("ref_id", sa.UUID(), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        *_common(t),
        _ck(t, "ref_type", _in("ref_type", INPUT_REF_TYPES)),
        _ck(t, "sha256", f"sha256 IS NULL OR sha256 {SHA}"),
    )
    t = "liquidity_gap_result"
    op.create_table(
        t,
        *_output(t),
        sa.Column("currency", sa.String(length=3), nullable=False),
        *_fk(t, "bucket_definition_id", "maturity_bucket_definition"),
        sa.Column("bucket_sequence", sa.Integer(), nullable=False),
        sa.Column("bucket_code", sa.String(length=20), nullable=False),
        *_numeric(
            "inflows",
            "outflows",
            "net_gap",
            "cumulative_gap",
            "inflows_contractual",
            "inflows_behavioural",
            "outflows_contractual",
            "outflows_behavioural",
        ),
        sa.Column("contribution_count", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["classification"],
            ["data_classification.code"],
            name=op.f("fk_liquidity_gap_result_classification_data_classification"),
        ),
        *_common(t),
        _uq(t, "currency", "calculation_run_id", "bucket_definition_id"),
        _ck(t, "currency", f"currency {CURRENCY}"),
        _ck(t, "inflows", "inflows = inflows_contractual + inflows_behavioural"),
        _ck(t, "outflows", "outflows = outflows_contractual + outflows_behavioural"),
        _ck(t, "net_gap", "net_gap = inflows - outflows"),
        _ck(
            t,
            "non_negative",
            "inflows_contractual >= 0 AND inflows_behavioural >= 0"
            " AND outflows_contractual >= 0 AND outflows_behavioural >= 0",
        ),
        _ck(t, "contribution_count", "contribution_count >= 0"),
    )
    t = "intermediate_value"
    op.create_table(
        t,
        *_output(t),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.String(length=3), nullable=False),
        sa.Column("classification", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["classification"],
            ["data_classification.code"],
            name=op.f("fk_intermediate_value_classification_data_classification"),
        ),
        *_fk(t, "liquidity_gap_result_id", "liquidity_gap_result"),
        sa.Column("bucket_code", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("days_after_reporting_date", sa.Integer(), nullable=False),
        sa.Column("rule_version_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_fk(t, "cashflow_event_id", "cashflow_event"),
        *_fk(t, "instrument_version_id", "instrument_version", nullable=True),
        *_fk(t, "canonical_record_id", "canonical_record"),
        *_fk(t, "import_job_id", "import_job"),
        *_fk(t, "source_record_id", "source_record"),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        *_common(t),
        _uq(t, "cashflow_event_id", "calculation_run_id"),
        _ck(t, "code", _in("code", INTERMEDIATE_CODES)),
        _ck(t, "unit", f"unit {CURRENCY}"),
        _ck(t, "direction", _in("direction", DIRECTIONS)),
        _ck(t, "origin", _in("origin", ORIGINS)),
        _ck(t, "value", "value >= 0"),
        _ck(t, "rule_version_ids", "jsonb_typeof(rule_version_ids) = 'array'"),
        _ck(t, "source_ordinal", "source_ordinal >= 1"),
    )
    t = "calculation_warning"
    op.create_table(
        t,
        *_output(t),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_common(t),
        _ck(t, "code", _in("code", WARNING_CODES)),
        _ck(t, "severity", _in("severity", SEVERITIES)),
        _ck(t, "details", "jsonb_typeof(details) = 'object'"),
    )


def _indexes() -> list[tuple[str, str, list[str], bool, str | None]]:
    """(index, table, columns, unique, partial WHERE), as the models declare them."""
    found: list[tuple[str, str, list[str], bool, str | None]] = [
        (f"ix_{t}_tenant_id", t, ["tenant_id"], False, None) for t in TABLES
    ]
    found += [
        ("ix_module_version_module_id", "module_version", ["module_id"], False, None),
        (
            "ix_rule_version_rule_definition_id",
            "rule_version",
            ["rule_definition_id"],
            False,
            None,
        ),
        ("uq_rule_version_one_active", "rule_version", ["rule_definition_id"], True,
         "status = 'ACTIVE'"),
        ("ix_rule_set_member_rule_set_id", "rule_set_member", ["rule_set_id"], False, None),
        (
            "ix_maturity_bucket_definition_rule_version_id",
            "maturity_bucket_definition",
            ["rule_version_id"],
            False,
            None,
        ),
        ("uq_assumption_set_one_active", "assumption_set", ["tenant_id", "code"], True,
         "status = 'ACTIVE'"),
        (
            "ix_dataset_snapshot_member_dataset_snapshot_id",
            "dataset_snapshot_member",
            ["dataset_snapshot_id"],
            False,
            None,
        ),
        (
            "ix_calculation_run_dataset_snapshot_id",
            "calculation_run",
            ["dataset_snapshot_id"],
            False,
            None,
        ),
        (
            "ix_calculation_run_module_version_id",
            "calculation_run",
            ["module_version_id"],
            False,
            None,
        ),
        (
            "ix_calculation_run_tenant_started",
            "calculation_run",
            ["tenant_id", "started_at"],
            False,
            None,
        ),
        *(
            (f"ix_{t}_calculation_run_id", t, ["calculation_run_id"], False, None)
            for t in OUTPUT_TABLES
        ),
        (
            "ix_intermediate_value_liquidity_gap_result_id",
            "intermediate_value",
            ["liquidity_gap_result_id"],
            False,
            None,
        ),
    ]  # fmt: skip
    return found


# --- seed (D-11, D-35) -----------------------------------------------------------------------


def _seed() -> None:
    bind = op.get_bind()
    tenant = uuid.UUID(TENANT)
    common = {"tenant_id": tenant, "created_by": ACTOR, "seeded_by": TAG}
    labels = {"provisional": True, "illustrative": True}
    module_id = _id("calculation_module", MODULE["code"])
    version_id = _id("module_version", f"{MODULE['code']}:1")
    rows: list[tuple[str, dict[str, Any]]] = [
        ("calculation_module", {"id": module_id, **MODULE, **labels, **common}),
        (
            "module_version",
            {
                "id": version_id,
                "module_id": module_id,
                **{k: v for k, v in MODULE_VERSION.items() if not k.endswith("_contract")},
                "input_contract": json.dumps(MODULE_VERSION["input_contract"]),
                "output_contract": json.dumps(MODULE_VERSION["output_contract"]),
                **labels,
                **common,
            },
        ),
    ]
    for code, (name, category, schema, _, description) in RULES.items():
        rows.append(
            (
                "rule_definition",
                {
                    "id": _id("rule_definition", code),
                    "code": code,
                    "name": name,
                    "category": category,
                    "target_module_id": module_id,
                    "owner": RULE_OWNER,
                    "parameter_schema": json.dumps(schema),
                    "description": description + " " + STATEMENT,
                    **labels,
                    **common,
                },
            )
        )
    for code, (_, _, _, parameters, _) in RULES.items():
        rows.append(
            (
                "rule_version",
                {
                    "id": _id("rule_version", f"{code}:1"),
                    "rule_definition_id": _id("rule_definition", code),
                    "version": 1,
                    "status": "DRAFT",
                    "parameters": json.dumps(parameters),
                    "parameters_sha256": canonical_sha256(parameters),
                    "authored_by": TAG,
                    "change_summary": SEED_SUMMARY,
                    **labels,
                    **common,
                },
            )
        )
    for sequence, b in enumerate(BUCKETS, start=1):
        rows.append(
            (
                "maturity_bucket_definition",
                {
                    "id": _id("maturity_bucket_definition", f"maturity_buckets:1:{b['code']}"),
                    "rule_version_id": _id("rule_version", "maturity_buckets:1"),
                    "sequence": sequence,
                    "code": b["code"],
                    "label": b["label"],
                    "lower_exclusive_days": b["lower_exclusive"],
                    "upper_inclusive_days": b["upper_inclusive"],
                    **labels,
                    **common,
                },
            )
        )
    rows.append(
        (
            "assumption_set",
            {
                "id": _id("assumption_set", "baseline:1"),
                **{k: v for k, v in ASSUMPTION_SET.items() if k != "assumptions"},
                "assumptions": json.dumps(ASSUMPTION_SET["assumptions"]),
                "content_sha256": canonical_sha256(ASSUMPTION_SET["assumptions"]),
                "version": 1,
                "status": "DRAFT",
                "authored_by": TAG,
                "change_summary": SEED_SUMMARY,
                **labels,
                **common,
            },
        )
    )
    jsonb = {"input_contract", "output_contract", "parameter_schema", "parameters", "assumptions"}
    for table, row in rows:
        columns = ", ".join(row)
        values = ", ".join(
            f"CAST(:{k} AS jsonb)" if k in jsonb else f":{k}" for k in row
        )
        bind.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), row)  # noqa: S608
    # Seeded versions move as the application's would (D-32 §7): submitted, then approved by
    # the seed tag. Inserted DRAFT because the lifecycle trigger accepts nothing else.
    for table in ("rule_version", "assumption_set"):
        bind.execute(
            sa.text(f"UPDATE {table} SET status = 'IN_REVIEW' WHERE seeded_by = :tag"),  # noqa: S608
            {"tag": TAG},
        )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET status = 'ACTIVE', approved_by = :tag, approved_at = now(),"  # noqa: S608
                " decision_comment = :comment WHERE seeded_by = :tag"
            ),
            {"tag": TAG, "comment": "Seeded ACTIVE (D-32 §7). " + STATEMENT},
        )


def upgrade() -> None:
    _create_tables()
    for name, table, columns, unique, where in _indexes():
        op.create_index(
            op.f(name),
            table,
            columns,
            unique=unique,
            postgresql_where=sa.text(where) if where else None,
        )
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, call in TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {call}")
    _seed()


def downgrade() -> None:
    # Calculation evidence is kept (D-02, D-31 §5): refuse rather than drop it. The tagged
    # seed does not count.
    checks = [f"EXISTS (SELECT 1 FROM {t})" for t in KEPT]
    checks += [
        f"EXISTS (SELECT 1 FROM {t} WHERE seeded_by IS DISTINCT FROM '{TAG}')"
        for t in SEEDED_TABLES
    ]
    op.execute(
        "DO $$ BEGIN IF " + " OR ".join(checks) + " THEN RAISE EXCEPTION 'dataset snapshots,"
        " calculation runs or non-seed registry rows exist; they are kept (D-02, D-31), so this"
        " downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    # CASCADE: a trigger an earlier, unreleased cut of 0018 created under another name goes
    # with its function (only triggers of the tables dropped below depend on these).
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}() CASCADE")
    for name, table, _, _, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
