"""calculations.constants

Enums, transition table and constants of dataset snapshots and calculation runs (D-06, D-30,
D-31, D-33). Every enum column is CHECK-constrained in the database (migration 0018, which
holds frozen copies kept equal by test_calculations).
"""

from enum import StrEnum

from app.governance.constants import ClassificationCode


class CalculationRunStatus(StrEnum):
    """Data Model §9 `calculation_run.status` (D-06, D-31 §3). Runs are synchronous in the
    MVP, so QUEUED and CANCELLED are reserved for WS4."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_WARNINGS = "SUCCEEDED_WITH_WARNINGS"
    FAILED = "FAILED"  # a post-check failed (or the engine did): never a silent success
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"  # the input contract was not met


TERMINAL_STATUSES = frozenset(
    {
        CalculationRunStatus.SUCCEEDED,
        CalculationRunStatus.SUCCEEDED_WITH_WARNINGS,
        CalculationRunStatus.FAILED,
        CalculationRunStatus.CANCELLED,
        CalculationRunStatus.BLOCKED,
    }
)
# D-06 transition table (D-31 §5), mirrored by the DB trigger: a run is written RUNNING and
# moves once, to one terminal status; nothing moves after that.
RUN_TRANSITIONS: dict[CalculationRunStatus, frozenset[CalculationRunStatus]] = {
    CalculationRunStatus.QUEUED: frozenset(),
    CalculationRunStatus.RUNNING: TERMINAL_STATUSES,
    **{s: frozenset() for s in TERMINAL_STATUSES},
}


class WarningSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class CalculationWarningCode(StrEnum):
    """`calculation_warning.code`. Messages and details are value-free (codes, counts, ids).

    - Exclusions (WARNING): the input is in scope but not used, never silently.
    - BLOCKED (ERROR): the input contract or the pinned configuration was not met.
    - FAILED (ERROR): a post-check or the engine failed.
    """

    INSTRUMENT_NOT_IN_SNAPSHOT = "INSTRUMENT_NOT_IN_SNAPSHOT"
    UNMAPPED_INSTRUMENT_TYPE = "UNMAPPED_INSTRUMENT_TYPE"
    PAST_DUE_EXCLUDED = "PAST_DUE_EXCLUDED"
    PAST_DUE_IN_FIRST_BUCKET = "PAST_DUE_IN_FIRST_BUCKET"
    OUTSIDE_BUCKETS = "OUTSIDE_BUCKETS"
    MISSING_VALUE = "MISSING_VALUE"
    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    UNKNOWN_ORIGIN = "UNKNOWN_ORIGIN"
    INPUT_SCHEMA_MISSING = "INPUT_SCHEMA_MISSING"
    PARAMETERS_INVALID = "PARAMETERS_INVALID"
    BUCKET_DEFINITIONS_MISMATCH = "BUCKET_DEFINITIONS_MISMATCH"
    AMBIGUOUS_INSTRUMENT_KEY = "AMBIGUOUS_INSTRUMENT_KEY"
    POST_CHECK_FAILED = "POST_CHECK_FAILED"
    ENGINE_ERROR = "ENGINE_ERROR"


class PostCheck(StrEnum):
    """The engine's post-run checks (D-31 §3, Arch §6.2 step 5)."""

    COVERAGE = "COVERAGE"  # every in-scope input is used once or excluded with a warning
    BUCKET_DEFINED = "BUCKET_DEFINED"  # every contribution lands in a pinned bucket
    BUCKET_TOTALS = "BUCKET_TOTALS"  # every aggregate equals the sum of its contributions
    CUMULATIVE_GAP = "CUMULATIVE_GAP"  # the cumulative gap is the running sum of net gaps
    # lcr_hqla (Phase 17): every HQLA level total equals the sum of its included asset rows,
    # and every per-currency LCR figure is recomputed from the contributions.
    HQLA_TOTALS = "HQLA_TOTALS"
    LCR_FIGURES = "LCR_FIGURES"
    # A re-run only (D-31 §5, BE-16.3): its result_sha256 equals its original's. Otherwise the
    # re-run ends FAILED (POST_CHECK_FAILED, details check=RERUN_REPRODUCED), no result kept.
    RERUN_REPRODUCED = "RERUN_REPRODUCED"
    # Stressed runs only (Phase 25, D-45 §6; in code only, no CHECK widened): the overlay
    # recomputed from the unstressed inputs equals the stored parts and applications;
    SCENARIO_OVERLAY = "SCENARIO_OVERLAY"
    # per flow the parts sum to the original amount (x (1 + f) for DEPOSIT_WITHDRAWAL), and per
    # currency and direction the stressed total equals the baseline total plus declared deltas.
    SCENARIO_CONSERVATION = "SCENARIO_CONSERVATION"


class InputRefType(StrEnum):
    """`calculation_input_ref.ref_type`: one row per pin of a run (D-31 §4)."""

    DATASET_SNAPSHOT = "DATASET_SNAPSHOT"
    IMPORT_MANIFEST = "IMPORT_MANIFEST"
    DOMAIN_PROJECTION = "DOMAIN_PROJECTION"
    SCHEMA_VERSION = "SCHEMA_VERSION"
    MAPPING_VERSION = "MAPPING_VERSION"
    MODULE_VERSION = "MODULE_VERSION"
    ENGINE = "ENGINE"
    RULE_SET = "RULE_SET"
    RULE_VERSION = "RULE_VERSION"
    ASSUMPTION_SET = "ASSUMPTION_SET"


class IntermediateCode(StrEnum):
    """`intermediate_value.code`."""

    # One bucketed cash flow: its amount, in its currency, counted in one bucket's inflows or
    # outflows (D-31 §6: it references the cash-flow event, and so its lineage).
    CASHFLOW_CONTRIBUTION = "CASHFLOW_CONTRIBUTION"


class HqlaExclusionReason(StrEnum):
    """`hqla_asset_result.exclusion_reason` (BE-17.1): why an asset row adds nothing to HQLA.
    Inputs that cannot be read at all are excluded with a run warning instead."""

    NOT_HQLA_ELIGIBLE = "NOT_HQLA_ELIGIBLE"  # its instrument type has no level in the rule


class LcrWarningCode(StrEnum):
    """`lcr_result.warning_codes` (BE-17.2): value-free warnings about one currency's LCR.
    Kept on the LCR row (a closed list, CHECK-constrained by migration 0020) because
    `calculation_warning.code` is a closed CHECK of migration 0018, which D-01 does not let a
    forward migration widen. A run with any of them ends SUCCEEDED_WITH_WARNINGS."""

    # outflows - inflows after the cap is zero or negative: the ratio is NULL (not computable).
    LCR_DENOMINATOR_ZERO = "LCR_DENOMINATOR_ZERO"
    # Level 2A/2B assets are included without the L2 caps (a documented v1 limitation).
    LCR_LEVEL_CAPS_NOT_APPLIED = "LCR_LEVEL_CAPS_NOT_APPLIED"


class SnapshotRefusal(StrEnum):
    """409 refusals of POST /dataset-snapshots, each audited DENIED (D-30 §3)."""

    CALCULATIONS_DISABLED = "calculations_disabled"
    NOT_PROJECTED = "not_projected"
    DUPLICATE_SCHEMA = "duplicate_schema"
    RECONCILIATION_BROKEN = "reconciliation_broken"
    AMBIGUOUS_BUSINESS_KEY = "ambiguous_business_key"


class RunRefusal(StrEnum):
    """409 refusals of POST /calculation-runs, each audited DENIED (D-31 §3, §7)."""

    CALCULATIONS_DISABLED = "calculations_disabled"
    MODULE_NOT_AVAILABLE = "module_not_available"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    RERUN_NOT_REPRODUCIBLE = "rerun_not_reproducible"  # BE-16.3: engine or manifest changed
    # Phase 25 (D-44 §9, D-45 §8): a stressed run, or a re-run of one, while SCENARIOS_ENABLED
    # is off.
    SCENARIOS_DISABLED = "scenarios_disabled"


class RunUnavailable(StrEnum):
    """503 refusals of a run request (D-59 §4, BE-33.1): retryable, with Retry-After, audited
    DENIED under the route's action; no run is written."""

    WORKERS_BUSY = "workers_busy"  # the bounded worker queue is full (CALC_WORKER_QUEUE_MAX)


# D-59 §2 (BE-33.2): `Prefer: respond-async` (RFC 7240) answers 202 with the RUNNING run,
# `Location` (the run's URL) and `Preference-Applied: respond-async`.
PREFER_HEADER = "Prefer"
PREFERENCE_APPLIED_HEADER = "Preference-Applied"
RESPOND_ASYNC = "respond-async"

# Audit actions (D-10, D-24 §4): the create and each of its refusals share one action.
CREATE_SNAPSHOT = "dataset_snapshot.create"
CREATE_RUN = "calculation_run.create"
FINISH_RUN = "calculation_run.finish"

# The calculation engine's version, pinned by every run (D-31 §4). A change to the engine's
# pinning, post-checks or hashing is a new engine version.
ENGINE_VERSION = "1"
MANIFEST_VERSION = 1
# D-33 option A: aggregates (bucket and currency totals, gaps) are internal; per-input
# contributions carry the most sensitive classification among the fields they read.
AGGREGATE_CLASSIFICATION = ClassificationCode.INTERNAL.value

# BE-17.3: what the lcr_hqla read states it does not model (GET .../liquidity `limitations`).
LCR_LIMITATIONS = [
    "Level 2A/2B caps are not applied (LCR_LEVEL_CAPS_NOT_APPLIED when a Level 2 asset is "
    "included).",
    "The value of an asset is its position balance: the schema has no market value "
    "(PROVISIONAL).",
    "Per currency, with no FX conversion and no all-currency total.",
    "Inflows are cash flows due (0, horizon] calendar days after the reporting date; "
    "outflows are position balances x run-off. No other LCR categories are modelled.",
]


# --- Phase 30: the result context's units (BE-30.3, D-56 §5) ---------------------------------
# Labels of what a result read's figures are measured in, as the engine computes them: they
# describe the code as built (decimals.py, the module implementations) and change nothing.
CONTEXT_CURRENCY_BASIS = "PER_CURRENCY_NO_FX"
CONTEXT_CONSOLIDATION = "BLOCKED"  # D-61 §6: no FX consolidation (BE-32.4)
CONTEXT_AMOUNT_UNIT = "Exact decimal, in the ISO 4217 currency of its row (no FX conversion)"
CONTEXT_DAYS_UNIT = "Calendar days after the reporting date"
# Per module code: only lcr_hqla reports a ratio; every other module reads None.
CONTEXT_RATIO_UNITS = {"lcr_hqla": "Dimensionless fraction, 4 dp ROUND_HALF_EVEN"}


# --- BE-35.1: the scenario shock contract the engine applies (D-44, D-45, D-46) ---------------
# Moved here from app.orchestration.constants, which re-exports every name: the overlay
# (scenario_overlay.py) and the run views read them, so calculations never imports
# orchestration. Values unchanged, so every shock, manifest and result hash is unchanged.


class ScenarioTemplate(StrEnum):
    """The closed template list (D-44 §2, design §8). A new template needs code, tests and an
    appended decision."""

    DELAYED_INFLOWS = "DELAYED_INFLOWS"
    DEPOSIT_WITHDRAWAL = "DEPOSIT_WITHDRAWAL"
    FUNDING_UNAVAILABLE = "FUNDING_UNAVAILABLE"


# D-44 §1: a version carries MIN_SHOCKS..MAX_SHOCKS shocks (the 0026/0027 CHECKs hold copies).
MIN_SHOCKS = 1
MAX_SHOCKS = 5


class ApprovalLabel(StrEnum):
    """D-44 §8: what a version (and its runs) is labelled with."""

    EXPLORATION = "Exploration, not approved"
    APPROVED = "Approved scenario (illustrative)"


class ApprovalState(StrEnum):
    """D-45 §4: the approval state a stressed run pins (from its version's status at run
    time). EXPLORATION runs are never authoritative (D-47 §3)."""

    EXPLORATION = "EXPLORATION"  # CONFIRMED_FOR_EXPLORATION
    APPROVED = "APPROVED"  # APPROVED_FOR_TEST


APPROVAL_LABELS: dict[ApprovalState, ApprovalLabel] = {
    ApprovalState.EXPLORATION: ApprovalLabel.EXPLORATION,
    ApprovalState.APPROVED: ApprovalLabel.APPROVED,
}


class PartChange(StrEnum):
    """D-46 §4: what a shock did to one part of a cash flow (the drill-down)."""

    MOVED = "MOVED"  # the delayed or unavailable fraction, now due later
    STAYED = "STAYED"  # the remainder, due as before
    ADDED = "ADDED"  # the extra outflow of DEPOSIT_WITHDRAWAL
    UNCHANGED = "UNCHANGED"  # matched no shock (never listed by the drill-down)


# D-45 §2: overlay bounds.
MAX_PARTS_PER_FLOW = 32
