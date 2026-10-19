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
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from app.core.classification import MaskedValue
from app.core.text import SafeMultilineText, SafeText
from app.governance.constants import ProposalDecision
from app.orchestration.constants import (
    DECIMAL_TEXT_PATTERN,
    MAX_DECISION_COMMENT,
    MAX_DELAY_DAYS,
    MAX_INSTRUMENT_KEYS,
    MAX_SHOCKS,
    MAX_UNAVAILABLE_DAYS,
    MIN_SHOCKS,
    ApprovalLabel,
    DelayCalendar,
    FlowDirection,
    FlowOrigin,
    ParameterKind,
    ParameterSource,
    ScenarioOrigin,
    ScenarioTemplate,
    ScenarioVersionStatus,
)

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
        list[Annotated[str, Field(min_length=1, max_length=200)] | MaskedValue] | None
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
        max_length=100,
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
    name: SafeText = Field(max_length=200)
    description: SafeMultilineText | None = Field(default=None, max_length=2000)
    shocks: ShocksIn
    change_summary: SafeMultilineText | None = Field(default=None, max_length=1000)


class ScenarioVersionCreate(BaseModel):
    """A new DRAFT version; `parent_version` records an adaptation (D-44 §7)."""

    model_config = ConfigDict(extra="forbid")

    shocks: ShocksIn
    parent_version: int | None = Field(default=None, ge=1, le=1_000_000)
    change_summary: SafeMultilineText | None = Field(default=None, max_length=1000)


class ScenarioVersionUpdate(BaseModel):
    """Replace a DRAFT's shocks (author only)."""

    model_config = ConfigDict(extra="forbid")

    shocks: ShocksIn
    change_summary: SafeMultilineText | None = Field(default=None, max_length=1000)


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
