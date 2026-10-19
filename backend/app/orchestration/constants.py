"""orchestration.constants

Stress scenarios (Phase 24, WS4 RO-02, RO-06; D-44, D-47): the closed template list, the
lifecycle (D-06), bounds and labels. PROVISIONAL and ILLUSTRATIVE (D-11, D-35).
"""

from enum import StrEnum


class ScenarioTemplate(StrEnum):
    """The closed template list (D-44 §2, design §8). A new template needs code, tests and an
    appended decision."""

    DELAYED_INFLOWS = "DELAYED_INFLOWS"
    DEPOSIT_WITHDRAWAL = "DEPOSIT_WITHDRAWAL"
    FUNDING_UNAVAILABLE = "FUNDING_UNAVAILABLE"


class ScenarioVersionStatus(StrEnum):
    """Data Model §9 `scenario.status` plus CONFIRMED_FOR_EXPLORATION (D-06, D-44 §5).
    RUNNING and TESTED are reserved: runs are synchronous."""

    DRAFT = "DRAFT"
    CONFIRMED_FOR_EXPLORATION = "CONFIRMED_FOR_EXPLORATION"
    IN_REVIEW = "IN_REVIEW"
    APPROVED_FOR_TEST = "APPROVED_FOR_TEST"
    RUNNING = "RUNNING"
    TESTED = "TESTED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


# D-44 §5: status -> statuses it may move to (each move once). The 0026 trigger enforces the
# same table.
SCENARIO_TRANSITIONS: dict[ScenarioVersionStatus, frozenset[ScenarioVersionStatus]] = {
    ScenarioVersionStatus.DRAFT: frozenset(
        {ScenarioVersionStatus.CONFIRMED_FOR_EXPLORATION, ScenarioVersionStatus.IN_REVIEW}
    ),
    ScenarioVersionStatus.CONFIRMED_FOR_EXPLORATION: frozenset(
        {ScenarioVersionStatus.IN_REVIEW, ScenarioVersionStatus.CLOSED}
    ),
    ScenarioVersionStatus.IN_REVIEW: frozenset(
        {
            ScenarioVersionStatus.APPROVED_FOR_TEST,
            ScenarioVersionStatus.REJECTED,
            ScenarioVersionStatus.CLOSED,
        }
    ),
    ScenarioVersionStatus.APPROVED_FOR_TEST: frozenset({ScenarioVersionStatus.CLOSED}),
    ScenarioVersionStatus.REJECTED: frozenset({ScenarioVersionStatus.CLOSED}),
    ScenarioVersionStatus.RUNNING: frozenset(),
    ScenarioVersionStatus.TESTED: frozenset(),
    ScenarioVersionStatus.CLOSED: frozenset(),
}

# Only these may run (D-44 §5); the run pins the approval state (D-45 §4).
RUNNABLE_STATUSES = frozenset(
    {ScenarioVersionStatus.CONFIRMED_FOR_EXPLORATION, ScenarioVersionStatus.APPROVED_FOR_TEST}
)


class ScenarioOrigin(StrEnum):
    MANUAL = "MANUAL"
    AI_PROPOSED = "AI_PROPOSED"


class ParameterSource(StrEnum):
    """Per AI-filled parameter (D-44 §7, D-48 §4); null for manual versions."""

    STATED = "STATED"
    PROPOSED_DEFAULT = "PROPOSED_DEFAULT"


class DelayCalendar(StrEnum):
    CALENDAR_DAYS = "CALENDAR_DAYS"  # the only value in v1 (D-44 §2)


class FlowOrigin(StrEnum):
    CONTRACTUAL = "CONTRACTUAL"
    BEHAVIOURAL = "BEHAVIOURAL"


class FlowDirection(StrEnum):
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"


class ParameterKind(StrEnum):
    """The kinds a template parameter or selector field takes (GET scenario-templates)."""

    DECIMAL = "DECIMAL"  # exact decimal text, padded to `scale`, never a JSON number
    INTEGER = "INTEGER"
    ENUM = "ENUM"
    INSTRUMENT_TYPE = "INSTRUMENT_TYPE"  # a key of the ACTIVE cashflow_direction rule
    INSTRUMENT_KEYS = "INSTRUMENT_KEYS"  # instrument business keys picked in the UI
    CURRENCY = "CURRENCY"  # ISO 4217
    TEXT = "TEXT"


class ApprovalLabel(StrEnum):
    """D-44 §8: what a version (and its runs) is labelled with."""

    EXPLORATION = "Exploration, not approved"
    APPROVED = "Approved scenario (illustrative)"


# D-44 §8, D-35 §3: every scenario response and page carries it.
SCENARIO_STATEMENT = (
    "Illustrative stress exploration, not a regulatory stress test. Not a regulatory "
    "calculation; no parity with any vendor system is claimed."
)

# D-44 §1-4 bounds.
MIN_SHOCKS = 1
MAX_SHOCKS = 5
MAX_INSTRUMENT_KEYS = 50
FRACTION_SCALE = 4
MAX_DELAY_DAYS = 3650
MAX_UNAVAILABLE_DAYS = 365
MAX_DECISION_COMMENT = 1000

# D-44 §4: decimal text, at most FRACTION_SCALE places (documentation pattern; the
# validator refuses exponents and JSON numbers with invalid_shock / inexact_number).
DECIMAL_TEXT_PATTERN = r"^[0-9]+(\.[0-9]{1,4})?$"

AUDIT_RESOURCE = "scenario_version"


class ScenarioAction(StrEnum):
    """Audit actions of the seven mutating routes (BE-24.3)."""

    CREATE = "scenario.create"
    VERSION = "scenario.version"
    EDIT = "scenario.edit"
    CONFIRM = "scenario.confirm"
    SUBMIT = "scenario.submit"
    APPROVE = "scenario.approve"
    REJECT = "scenario.reject"
    CLOSE = "scenario.close"


class ScenarioRefusal(StrEnum):
    """The refusal codes of the Scenario API (D-44 §4-6, §9). Every one is audited DENIED."""

    NOT_FOUND = "not_found"  # 404: absent and foreign alike (D-04)
    SCENARIOS_DISABLED = "scenarios_disabled"  # 409: SCENARIOS_ENABLED=false
    DUPLICATE_CODE = "duplicate_code"  # 409: the code is taken in this tenant
    NOT_AUTHOR = "not_author"  # 409: only the author edits, confirms, submits, closes
    SELF_APPROVAL = "self_approval"  # 409: the author never decides
    INVALID_STATUS = "invalid_status"  # 409: not a move the version's status allows
    INVALID_SHOCK = "invalid_shock"  # 422: value-free details [{shock_index, field, code}]
    AI_REVIEW_REQUIRED = "ai_review_required"  # 422: AI_PROPOSED without acknowledge_ai_origin
    UNACKNOWLEDGED_DEFAULTS = "unacknowledged_defaults"  # 422: a PROPOSED_DEFAULT not acked
    FORBIDDEN = "forbidden"  # 403: close of an approved version without scenario:approve


class ShockProblem(StrEnum):
    """The value-free codes of a 422 invalid_shock detail (D-44 §4). Each detail names the
    shock index (None for the list itself), a structural field path and one of these; never
    the value, and never an unknown key's name."""

    NOT_A_LIST = "not_a_list"
    TOO_FEW_SHOCKS = "too_few_shocks"
    TOO_MANY_SHOCKS = "too_many_shocks"
    NOT_AN_OBJECT = "not_an_object"
    UNKNOWN_FIELD = "unknown_field"
    REQUIRED = "required"
    UNKNOWN_TEMPLATE = "unknown_template"
    INEXACT_NUMBER = "inexact_number"  # a JSON fraction or an exponent
    NOT_DECIMAL = "not_decimal"  # not plain decimal text (e.g. a formula)
    TOO_MANY_PLACES = "too_many_places"  # more than FRACTION_SCALE places: never rounded
    NOT_INTEGER = "not_integer"
    NOT_TEXT = "not_text"
    OUT_OF_RANGE = "out_of_range"
    UNSUPPORTED_VALUE = "unsupported_value"  # outside the field's ENUM (e.g. BUSINESS_DAYS)
    UNKNOWN_INSTRUMENT_TYPE = "unknown_instrument_type"  # not a key of the ACTIVE rule
    DIRECTION_MISMATCH = "direction_mismatch"  # the type's direction is not the template's
    INVALID_WINDOW = "invalid_window"  # due_from_day > due_to_day
    TOO_MANY_ITEMS = "too_many_items"  # more than MAX_INSTRUMENT_KEYS
    DUPLICATE_ITEM = "duplicate_item"
    # An instrument key that is not an instrument of the tenant with a version of the
    # selector's instrument_type, or one the author is not cleared to see (the same code for
    # both, so existence is not revealed).
    UNKNOWN_INSTRUMENT = "unknown_instrument"


# The ACTIVE rule whose keys and directions the instrument-type fields are checked against.
DIRECTION_RULE = "cashflow_direction"
# Seed tag of migration 0026's permission grants (D-11, D-47).
SCENARIO_SEED_TAG = "0026_scenario_permissions"
MAX_CODE = 64
MAX_NAME = 200
MAX_DESCRIPTION = 2000
MAX_CHANGE_SUMMARY = 1000
MAX_TEXT_FIELD = 200  # an instrument key
MAX_SOURCE_SYSTEM_CODE = 100


# --- Phase 25: running scenarios and comparing them (D-45, D-46) -------------------------------


class ApprovalState(StrEnum):
    """D-45 §4: the approval state a stressed run pins (from its version's status at run
    time). EXPLORATION runs are never authoritative (D-47 §3)."""

    EXPLORATION = "EXPLORATION"  # CONFIRMED_FOR_EXPLORATION
    APPROVED = "APPROVED"  # APPROVED_FOR_TEST


APPROVAL_LABELS: dict[ApprovalState, ApprovalLabel] = {
    ApprovalState.EXPLORATION: ApprovalLabel.EXPLORATION,
    ApprovalState.APPROVED: ApprovalLabel.APPROVED,
}


class ScenarioRunAction(StrEnum):
    """Audit action of POST scenarios/{scenario_id}/run (BE-25.3); each member run is also
    audited `calculation_run.create`."""

    RUN = "scenario.run"


AUDIT_RUN_RESOURCE = "scenario_run"


class ScenarioRunRefusal(StrEnum):
    """D-45 §10: refusals of the run route. Every one is 409 (404 for not_found), audited
    DENIED, with nothing written."""

    NOT_FOUND = "not_found"  # 404: scenario, version or baseline run (absent and foreign alike)
    SCENARIO_NOT_RUNNABLE = "scenario_not_runnable"  # not CONFIRMED_FOR_EXPLORATION/APPROVED
    INVALID_BASELINE = "invalid_baseline"  # not a succeeded, non-stressed maturity_ladder run
    BASELINE_NOT_REPRODUCIBLE = "baseline_not_reproducible"  # its pins cannot be re-pinned
    NO_MATCHING_CASHFLOWS = "no_matching_cashflows"  # a shock matches no flow of the baseline
    SCENARIOS_DISABLED = "scenarios_disabled"  # SCENARIOS_ENABLED=false
    CALCULATIONS_DISABLED = "calculations_disabled"  # CALCULATIONS_ENABLED=false
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"  # the key was used for another request


class PartChange(StrEnum):
    """D-46 §4: what a shock did to one part of a cash flow (the drill-down)."""

    MOVED = "MOVED"  # the delayed or unavailable fraction, now due later
    STAYED = "STAYED"  # the remainder, due as before
    ADDED = "ADDED"  # the extra outflow of DEPOSIT_WITHDRAWAL
    UNCHANGED = "UNCHANGED"  # matched no shock (never listed by the drill-down)


# D-45 §2: overlay bounds.
MAX_PARTS_PER_FLOW = 32
# Drill-down page size (GET .../comparison/contributions).
MAX_CONTRIBUTIONS_PAGE = 500
# Largest changes listed per comparison (D-46 §2), ranked over clear deltas only.
MAX_LARGEST_CHANGES = 10
