"""orchestration.schemas

The Scenario API contract (Phase 24, BE-24.2 - BE-24.5; D-44, D-47).

A scenario is data, never code (D-44 §1): a `scenario_version` holds 1 to 5 shocks, each a
template from a closed list with typed parameters and a selector. Fractions are exact decimal
text (scale 4), never JSON numbers.

Request bodies carry `shocks` as raw JSON so that `app/orchestration/scenario_templates.py`
(pure) answers every refusal as 422 `invalid_shock` with value-free details (shock index,
field, code), never the generic `validation_error`. The OpenAPI schema still advertises the
typed shock union (`ShockIn`), which is what the frontend generates its types from.

Every response carries `labels.statement` (D-44 §8).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from app.calculations.constants import CalculationRunStatus
from app.core.classification import MaskedValue
from app.core.pagination import HasMore, NextCursor
from app.core.text import SafeMultilineText, SafeText
from app.governance.constants import ProposalDecision
from app.orchestration.constants import (
    DECIMAL_TEXT_PATTERN,
    MAX_CHANGE_SUMMARY,
    MAX_DECISION_COMMENT,
    MAX_DELAY_DAYS,
    MAX_DESCRIPTION,
    MAX_INSTRUMENT_KEYS,
    MAX_NAME,
    MAX_PARTS_PER_FLOW,
    MAX_SHOCKS,
    MAX_SOURCE_SYSTEM_CODE,
    MAX_TEXT_FIELD,
    MAX_UNAVAILABLE_DAYS,
    MIN_SHOCKS,
    ApprovalLabel,
    ApprovalState,
    DelayCalendar,
    FlowDirection,
    FlowOrigin,
    ParameterKind,
    ParameterSource,
    PartChange,
    ScenarioOrigin,
    ScenarioTemplate,
    ScenarioVersionStatus,
)
from app.rules.constants import ValidationStatus

DecimalText = Annotated[
    str,
    Field(
        pattern=DECIMAL_TEXT_PATTERN,
        description="Exact decimal text, at most 4 places (padded to 4); never a JSON number "
        "or an exponent (422 invalid_shock, code inexact_number).",
    ),
]
InstrumentType = Annotated[
    str,
    Field(
        min_length=1,
        max_length=50,
        description="A key of the ACTIVE cashflow_direction rule with the direction the "
        "template needs (GET scenario-templates lists them).",
    ),
]
DayOffset = Annotated[int, Field(ge=0, le=MAX_DELAY_DAYS)]


class ScenarioLabels(BaseModel):
    statement: str = Field(
        description="D-44 §8: 'Illustrative stress exploration, not a regulatory stress test. "
        "Not a regulatory calculation; no parity with any vendor system is claimed.'"
    )


# --- Shocks (D-44 §2-4) -------------------------------------------------------------------


class ShockSelector(BaseModel):
    """The common optional selector fields (D-44 §3)."""

    model_config = ConfigDict(extra="forbid")

    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    origin: FlowOrigin | None = None
    due_from_day: DayOffset | None = Field(
        default=None, description="Inclusive, in days after the reporting date."
    )
    due_to_day: DayOffset | None = Field(
        default=None,
        description="Inclusive, in days after the reporting date; not before due_from_day.",
    )


class FundingSourceSelector(ShockSelector):
    """FUNDING_UNAVAILABLE's funding-source selector (D-44 §3). Counterparty selection is not
    possible in v1."""

    instrument_type: InstrumentType = Field(description="An INFLOW instrument type.")
    instrument_keys: (
        list[Annotated[str, Field(min_length=1, max_length=MAX_TEXT_FIELD)] | MaskedValue] | None
    ) = Field(
        default=None,
        max_length=MAX_INSTRUMENT_KEYS,
        description="At most 50 instrument business keys, picked by the analyst (never by the "
        "model, D-49 §3). Writes take strings only: each must be an instrument of the tenant "
        "with a version of instrument_type that the author is cleared to see, else 422 "
        "invalid_shock code unknown_instrument. Reads mask each key the caller is not cleared "
        "for (the instrument_id field's classification, D-33) as a MaskedValue.",
    )
    source_system_code: str | None = Field(
        default=None,
        max_length=MAX_SOURCE_SYSTEM_CODE,
        description="Matches all of a snapshot's cash flows or none in v1 (D-30 §1).",
    )


class DelayedInflowsParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cashflow_type: InstrumentType = Field(description="An INFLOW instrument type, e.g. LOAN.")
    affected_fraction: DecimalText = Field(description="0 < f <= 1, scale 4.")
    delay_days: int = Field(ge=1, le=MAX_DELAY_DAYS)
    delay_calendar: DelayCalendar = DelayCalendar.CALENDAR_DAYS


class DepositWithdrawalParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cashflow_type: InstrumentType = Field(description="An OUTFLOW instrument type, e.g. DEPOSIT.")
    increase_fraction: DecimalText = Field(
        description="0 < x <= 1, scale 4 (PROVISIONAL bound); '+15%' is '0.1500'."
    )


class FundingUnavailableParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unavailable_days: int = Field(ge=1, le=MAX_UNAVAILABLE_DAYS)
    affected_fraction: DecimalText = Field(description="0 < f <= 1, scale 4; 1 is fully off.")
    delay_calendar: DelayCalendar = DelayCalendar.CALENDAR_DAYS


class DelayedInflowsShock(BaseModel):
    """Delayed loan repayments: f x amount of each matching INFLOW moves delay_days later."""

    model_config = ConfigDict(extra="forbid")

    template: Literal[ScenarioTemplate.DELAYED_INFLOWS]
    parameters: DelayedInflowsParameters
    selector: ShockSelector = Field(default_factory=ShockSelector)


class DepositWithdrawalShock(BaseModel):
    """Increased deposit withdrawals: each matching OUTFLOW amount x (1 + x)."""

    model_config = ConfigDict(extra="forbid")

    template: Literal[ScenarioTemplate.DEPOSIT_WITHDRAWAL]
    parameters: DepositWithdrawalParameters
    selector: ShockSelector = Field(default_factory=ShockSelector)


class FundingUnavailableShock(BaseModel):
    """A funding source unavailable: f x amount of matching INFLOWs due in (0, days] moves to
    day days + 1 (timing only)."""

    model_config = ConfigDict(extra="forbid")

    template: Literal[ScenarioTemplate.FUNDING_UNAVAILABLE]
    parameters: FundingUnavailableParameters
    selector: FundingSourceSelector


Shock = Annotated[
    DelayedInflowsShock | DepositWithdrawalShock | FundingUnavailableShock,
    Field(discriminator="template"),
]

_SHOCK_REFS = [
    {"$ref": f"#/components/schemas/{m.__name__}"}
    for m in (DelayedInflowsShock, DepositWithdrawalShock, FundingUnavailableShock)
]
# Raw at runtime (the template validator answers 422 invalid_shock); typed in the contract.
ShocksIn = Annotated[
    list[dict[str, Any]],
    WithJsonSchema(
        {
            "type": "array",
            "minItems": MIN_SHOCKS,
            "maxItems": MAX_SHOCKS,
            "items": {
                "oneOf": _SHOCK_REFS,
                "discriminator": {
                    "propertyName": "template",
                    "mapping": {
                        t.value: r["$ref"]
                        for t, r in zip(ScenarioTemplate, _SHOCK_REFS, strict=True)
                    },
                },
            },
            "description": "1 to 5 shocks (D-44 §1). Any refusal is 422 invalid_shock with "
            'value-free details exactly {"problems": [{"shock_index": int | null, '
            '"field": str, "code": str}, ...]}: shock_index null for the list itself, '
            "field a structural path (e.g. parameters.affected_fraction), code a ShockProblem "
            "code; never a submitted value or an unknown key's name.",
        }
    ),
]


# --- GET /scenario-templates (BE-24.2) ------------------------------------------------------


class TemplateField(BaseModel):
    name: str
    kind: ParameterKind
    required: bool
    min: str | None = Field(description="Inclusive lower bound (exact text), if any.")
    min_exclusive: bool = Field(description="True: the bound itself is refused (0 < f).")
    max: str | None = Field(description="Inclusive upper bound (exact text), if any.")
    scale: int | None = Field(description="DECIMAL: the places text is padded to.")
    max_items: int | None = Field(description="INSTRUMENT_KEYS: at most this many.")
    values: list[str] = Field(description="ENUM / INSTRUMENT_TYPE: the allowed values.")
    unit: str | None = Field(description="e.g. 'fraction' or 'days'.")
    description: str


class TemplateOut(BaseModel):
    template: ScenarioTemplate
    name: str
    description: str
    direction: FlowDirection = Field(description="The direction the matched flows must have.")
    changes_amounts: bool = Field(description="Only DEPOSIT_WITHDRAWAL changes amounts.")
    parameters: list[TemplateField]
    selector: list[TemplateField]


class TemplatePage(BaseModel):
    items: list[TemplateOut] = Field(description="The three v1 templates, in template order.")
    max_shocks: int
    labels: ScenarioLabels


# --- Writes (BE-24.3) -----------------------------------------------------------------------


class ScenarioCreate(BaseModel):
    """A scenario and its first DRAFT version (origin MANUAL)."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{1,63}$",
        description="Unique per tenant (409 duplicate_code); assigned by the service if absent.",
    )
    name: SafeText = Field(max_length=MAX_NAME)
    description: SafeMultilineText | None = Field(default=None, max_length=MAX_DESCRIPTION)
    shocks: ShocksIn
    change_summary: SafeMultilineText | None = Field(default=None, max_length=MAX_CHANGE_SUMMARY)


class ScenarioVersionCreate(BaseModel):
    """A new DRAFT version; `parent_version` records an adaptation (D-44 §7)."""

    model_config = ConfigDict(extra="forbid")

    shocks: ShocksIn
    parent_version: int | None = Field(default=None, ge=1, le=1_000_000)
    change_summary: SafeMultilineText | None = Field(default=None, max_length=MAX_CHANGE_SUMMARY)


class ScenarioVersionUpdate(BaseModel):
    """Replace a DRAFT's shocks (author only)."""

    model_config = ConfigDict(extra="forbid")

    shocks: ShocksIn
    change_summary: SafeMultilineText | None = Field(default=None, max_length=MAX_CHANGE_SUMMARY)


class AiAcknowledgement(BaseModel):
    """D-44 §6: an AI_PROPOSED version needs these to confirm or submit (else 422
    ai_review_required / unacknowledged_defaults); ignored for MANUAL versions."""

    model_config = ConfigDict(extra="forbid")

    acknowledge_ai_origin: bool = False
    acknowledged_defaults: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list,
        max_length=100,
        description="The parameter paths of every PROPOSED_DEFAULT parameter.",
    )


class ScenarioVersionConfirm(AiAcknowledgement):
    """DRAFT -> CONFIRMED_FOR_EXPLORATION, by the author."""


class ScenarioVersionSubmit(AiAcknowledgement):
    """DRAFT or CONFIRMED_FOR_EXPLORATION -> IN_REVIEW, by the author."""

    comment: SafeMultilineText | None = Field(default=None, max_length=MAX_DECISION_COMMENT)


class ScenarioVersionDecision(BaseModel):
    """IN_REVIEW -> APPROVED_FOR_TEST or REJECTED, by an approver who is not the author."""

    model_config = ConfigDict(extra="forbid")

    decision: ProposalDecision
    comment: SafeMultilineText = Field(min_length=1, max_length=MAX_DECISION_COMMENT)


class ScenarioVersionClose(BaseModel):
    """Any status but DRAFT -> CLOSED: the author (scenario:propose) for a non-approved
    version, an approver (scenario:approve) for an approved one."""

    model_config = ConfigDict(extra="forbid")

    comment: SafeMultilineText | None = Field(default=None, max_length=MAX_DECISION_COMMENT)


# --- Reads (BE-24.4) ------------------------------------------------------------------------


class ScenarioChangeOut(BaseModel):
    """One `scenario_change` row: a shock materialised when the version left DRAFT."""

    shock_index: int
    template: ScenarioTemplate
    shock: Shock


class ScenarioVersionOut(BaseModel):
    scenario_version_id: uuid.UUID
    scenario_id: uuid.UUID
    version: int
    status: ScenarioVersionStatus
    origin: ScenarioOrigin
    parent_version: int | None
    parent_version_id: uuid.UUID | None
    parameter_sources: dict[str, ParameterSource] | None = Field(
        description="Per AI-filled parameter path; null for MANUAL versions."
    )
    shocks: list[Shock]
    shocks_sha256: str = Field(description="SHA-256 of the canonical JSON of `shocks`.")
    change_summary: str | None
    changes: list[ScenarioChangeOut] = Field(description="Empty while DRAFT.")
    authored_by: str
    author_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    submitted_by: str | None
    submitted_at: datetime | None
    submit_comment: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None
    closed_by: str | None
    closed_at: datetime | None
    close_comment: str | None
    ai_acknowledged: bool
    acknowledged_defaults: list[str]
    runnable: bool = Field(description="CONFIRMED_FOR_EXPLORATION or APPROVED_FOR_TEST.")
    approval_label: ApprovalLabel | None = Field(
        description="'Exploration, not approved' or 'Approved scenario (illustrative)'; "
        "None for versions that cannot run."
    )
    labels: ScenarioLabels


class ScenarioSummary(BaseModel):
    scenario_id: uuid.UUID
    code: str
    name: str
    description: str | None
    created_by: str
    created_at: datetime
    latest_version: int
    latest_status: ScenarioVersionStatus
    matching_versions: list[int] = Field(
        description="The versions that match the list filter (status, origin, mine)."
    )
    approved_version: int | None = Field(description="The newest APPROVED_FOR_TEST version.")


class ScenarioPage(BaseModel):
    items: list[ScenarioSummary] = Field(description="Newest first.")
    total: int
    limit: int
    offset: int
    labels: ScenarioLabels
    has_more: HasMore
    next_cursor: NextCursor


class ScenarioDetail(BaseModel):
    scenario_id: uuid.UUID
    code: str
    name: str
    description: str | None
    created_by: str
    created_at: datetime
    versions: list[ScenarioVersionOut] = Field(
        description="Every version, by version number (CLOSED and REJECTED included)."
    )
    scenarios_enabled: bool = Field(description="SCENARIOS_ENABLED: false means 409 on writes.")
    labels: ScenarioLabels


# --- Phase 25: run against a baseline, compare (BE-25.3, BE-25.4; D-45, D-46) ---------------

Value = str | MaskedValue


class ScenarioRunCreate(BaseModel):
    """POST scenarios/{scenario_id}/run (D-45 §10). With k > 1 shocks the engine creates k + 1
    stressed runs (each shock alone, then all combined), grouped by one `scenario_run`."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        ge=1,
        le=1_000_000,
        description="A CONFIRMED_FOR_EXPLORATION or APPROVED_FOR_TEST version.",
    )
    baseline_run_id: uuid.UUID = Field(
        description="A SUCCEEDED (or SUCCEEDED_WITH_WARNINGS), non-stressed maturity_ladder run "
        "of the caller's tenant. Its pins are reused exactly."
    )


class ShockMatchCount(BaseModel):
    """Value-free counts of what one shock matches on the baseline's inputs (D-45 §9)."""

    shock_index: int = Field(description="1-based, in declared order.")
    template: ScenarioTemplate
    matched_flows: int = Field(description="Distinct cash flows the shock changes.")
    parts: int = Field(description="Parts the shock produces (a split flow counts each part).")
    direction_mismatches: int = Field(
        description="Flows the selector names whose pinned direction is not the template's: "
        "matched nothing, counted (D-45 §2)."
    )
    skipped_past_due: int = Field(
        description="Matching flows due on or before the reporting date (days <= 0): never "
        "shocked."
    )
    skipped_incomplete: int = Field(description="Matching incomplete flows: never shocked.")


class ScenarioValidationOut(BaseModel):
    """GET .../versions/{version}/validation?baseline_run_id= : a dry run of the overlay on the
    baseline's pinned inputs. Nothing is written. Counts are public (D-46 §3)."""

    scenario_id: uuid.UUID
    scenario_version_id: uuid.UUID
    version: int
    status: ScenarioVersionStatus
    baseline_run_id: uuid.UUID
    shocks: list[ShockMatchCount] = Field(description="One per shock, in declared order.")
    runnable: bool = Field(
        description="The version is runnable, the baseline is valid and every shock matches at "
        "least one flow. The UI disables Run while false."
    )
    blocking_reasons: list[str] = Field(
        description="The run refusal codes a POST would answer now (e.g. scenario_not_runnable, "
        "invalid_baseline, no_matching_cashflows, scenarios_disabled, "
        "calculations_disabled); empty when runnable."
    )
    run_count: int = Field(description="Runs a POST would create: 1, or k + 1 for k > 1 shocks.")
    approval_state: ApprovalState | None = Field(
        description="What the runs would be labelled; None when the version cannot run."
    )
    approval_label: ApprovalLabel | None
    labels: ScenarioLabels


class ScenarioRunMember(BaseModel):
    calculation_run_id: uuid.UUID
    shock_subset: list[int] = Field(description="1-based shock indexes the run applied.")
    combined: bool = Field(description="True for the run with every shock (also when k = 1).")
    status: CalculationRunStatus
    manifest_sha256: str
    result_sha256: str | None


class ScenarioRunOut(BaseModel):
    """One `scenario_run` (D-45 §9, append-only): the baseline, the version, the approval state
    and the member runs, with value-free counts per shock."""

    scenario_run_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_code: str
    scenario_name: str
    scenario_version_id: uuid.UUID
    version: int
    shocks_sha256: str
    approval_state: ApprovalState
    approval_label: ApprovalLabel
    baseline_run_id: uuid.UUID
    reporting_date: date
    members: list[ScenarioRunMember] = Field(
        description="Single-shock runs by shock index, then the combined run."
    )
    shock_counts: list[ShockMatchCount]
    created_by: str
    created_at: datetime
    correlation_id: str | None
    labels: ScenarioLabels


class ScenarioRunPage(BaseModel):
    items: list[ScenarioRunOut] = Field(description="Newest first.")
    total: int
    limit: int
    offset: int
    labels: ScenarioLabels
    has_more: HasMore
    next_cursor: NextCursor


class ComparisonBucket(BaseModel):
    bucket_definition_id: uuid.UUID
    sequence: int
    code: str
    label: str
    lower_exclusive_days: int
    upper_inclusive_days: int | None = Field(description="None: the open last bucket.")


class ComparisonFigure(BaseModel):
    """Exact decimal text, or masked (D-46 §3). `delta` = stressed - baseline, exactly."""

    baseline: Value
    stressed: Value
    delta: Value


class ComparisonCell(BaseModel):
    """One bucket of one currency under one member run (a shock alone, or combined)."""

    calculation_run_id: uuid.UUID
    shock_subset: list[int]
    combined: bool
    inflows: ComparisonFigure
    outflows: ComparisonFigure
    net_gap: ComparisonFigure
    cumulative_gap: ComparisonFigure
    changed_flow_count: int = Field(
        description="Distinct non-zero cash flows the shocks changed in this bucket (parts of "
        "one flow count once). Public (D-46 §3)."
    )
    turns_negative_net: bool | None = Field(
        description="Baseline net >= 0 and stressed net < 0; null when it reads a masked figure."
    )
    turns_negative_cumulative: bool | None = Field(
        description="Baseline cumulative >= 0 and stressed < 0; null when masked."
    )


class ComparisonRow(BaseModel):
    bucket_code: str
    bucket_sequence: int
    bucket_label: str
    cells: list[ComparisonCell] = Field(
        description="Single-shock runs by shock index, then the combined run."
    )
    breaks_only_combined: bool | None = Field(
        description="The combined net gap is negative while the baseline and every single-shock "
        "net gap are not; null when it reads a masked figure, or when fewer single-shock runs "
        "were compared than the version has shocks (always null for one shock)."
    )


class FirstNegativeCumulative(BaseModel):
    """The first bucket whose cumulative gap is negative (illustrative, D-46 §2)."""

    shock_subset: list[int]
    combined: bool
    baseline_bucket_code: str | MaskedValue | None = Field(
        description="None: never negative; masked when the deciding figure is masked."
    )
    stressed_bucket_code: str | MaskedValue | None


class ComparisonCurrency(BaseModel):
    currency: str
    rows: list[ComparisonRow] = Field(description="Every bucket, in bucket order.")
    first_negative_cumulative: list[FirstNegativeCumulative] = Field(
        description="One per member run."
    )


class LargestChange(BaseModel):
    """Ranked by absolute net delta among clear deltas only; masked deltas are never ranked.
    Ranked per currency (no FX): the rank restarts at 1 in each currency, at most 10 each."""

    rank: int = Field(description="1-based, within its currency.")
    currency: str
    bucket_code: str
    bucket_sequence: int
    calculation_run_id: uuid.UUID
    shock_subset: list[int]
    combined: bool
    net_gap_delta: str = Field(description="Exact decimal text; always clear.")


class ComparisonSummary(BaseModel):
    """D-46 §2: a deterministic, templated (non-AI) summary citing only clear figures. The
    fallback for AI explanations (D-51)."""

    template_version: str
    sentences: list[str]


# Phase 30 (BE-30.3, D-56 §5): calculations' `ResultContext`, referenced by name so the module
# imports no other module's schemas (as ManifestShocks does). Built by the calculations service
# from the baseline run's pins and carried here as its JSON dump.
ResultContextRef = Annotated[
    dict[str, Any],
    WithJsonSchema(
        {
            "allOf": [{"$ref": "#/components/schemas/ResultContext"}],
            "description": "D-56 §5: units, currency, as-of date, methodology pins, validation "
            "status and the D-35 statement, from the baseline run's pins (every member run pins "
            "the same module, engine and rule versions).",
        }
    ),
]


class ScenarioComparisonOut(BaseModel):
    """GET scenarios/{scenario_id}/comparison?scenario_run_id= (D-46): computed on read from the
    immutable ladder rows of the baseline and of every member run. Per currency, no FX and no
    cross-currency total."""

    scenario_run_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_version_id: uuid.UUID
    version: int
    approval_state: ApprovalState
    approval_label: ApprovalLabel
    baseline_run_id: uuid.UUID
    reporting_date: date
    members: list[ScenarioRunMember]
    buckets: list[ComparisonBucket]
    currencies: list[ComparisonCurrency]
    largest_changes: list[LargestChange]
    summary: ComparisonSummary
    min_aggregate_count: int = Field(
        description="CALC_MIN_AGGREGATE_COUNT (D-33, PROVISIONAL): a delta changed by fewer "
        "distinct non-zero flows is masked for a role not cleared for the amounts."
    )
    illustrative: bool
    labels: ScenarioLabels
    validation_status: ValidationStatus = Field(
        description="D-56: the compared module version's status (MODULE_VALIDATION). Response "
        "wrapper only."
    )
    context: ResultContextRef


class ContributionSourceLineage(BaseModel):
    """cash-flow event -> canonical record -> source row -> import (D-31 §6)."""

    cashflow_event_id: uuid.UUID
    canonical_record_id: uuid.UUID
    import_id: uuid.UUID
    source_record_id: uuid.UUID
    source_ordinal: int


class ScenarioContributionOut(BaseModel):
    """One changed part (D-46 §4). Amounts are confidential (masked for risk_analyst); days,
    buckets and templates are internal."""

    scenario_contribution_id: uuid.UUID
    calculation_run_id: uuid.UUID
    shock_subset: list[int]
    cashflow_event_id: uuid.UUID
    part_path: str = Field(
        description=f"Deterministic part path (at most {MAX_PARTS_PER_FLOW} parts per flow)."
    )
    part_id: uuid.UUID = Field(description="uuid5 of the event id and the part path.")
    instrument_business_key: Value | None
    shock_sequences: list[int] = Field(description="The shocks that touched this part, in order.")
    templates: list[ScenarioTemplate]
    change: PartChange
    direction: FlowDirection
    origin: FlowOrigin
    currency: str
    days_before: int
    days_after: int
    bucket_code_before: str
    bucket_code_after: str
    amount: Value = Field(description="Exact decimal text in `currency`, or masked (D-33).")
    classification: str
    lineage: ContributionSourceLineage


class ScenarioContributionPage(BaseModel):
    scenario_run_id: uuid.UUID
    items: list[ScenarioContributionOut]
    total: int
    limit: int
    offset: int
    labels: ScenarioLabels
    has_more: HasMore
    next_cursor: NextCursor
