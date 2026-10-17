"""calculations.modules.base

The module interface (D-31 §1). A module:

- declares its `ModuleSpec`: code, version, the engine version it was built for, its input
  contract (the schemas and fields it reads, the rules and assumption keys) and its output
  contract (result tables, intermediate codes, warning codes). The spec is seeded as a
  `module_version` row (migration 0018 holds a frozen copy, kept equal by a test);
- runs `run(inputs, parameters) -> ModuleResult` as a pure function over frozen, typed
  inputs and pinned parameters. It does no I/O and reads no clock, randomness or
  environment. All arithmetic is Decimal, under the context the module declares, which
  traps any rounding unless the module declares an explicit quantize (D-31 §2).

The engine (app.calculations.service) resolves the pins, feeds the inputs in a stable order,
runs the module under its context, runs the post-checks and stores the outputs.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Context, Decimal
from typing import Any

from app.calculations.constants import (
    CalculationWarningCode,
    HqlaExclusionReason,
    LcrWarningCode,
    WarningSeverity,
)
from app.core.decimals import DeclaredRounding, exact_context, quantize
from app.domain.constants import CashflowOrigin
from app.rules.constants import CashflowDirection, HqlaLevel

# --- inputs -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CashFlowInput:
    """One projected cash-flow event of the snapshot (typed columns; None when missing)."""

    cashflow_event_id: uuid.UUID
    instrument_key: str | None
    due_date: date | None
    amount: Decimal | None
    currency: str | None
    origin: str | None


@dataclass(frozen=True)
class InstrumentInput:
    """One projected instrument version of the snapshot, by business key."""

    instrument_version_id: uuid.UUID
    instrument_id: uuid.UUID
    business_key: str
    instrument_type: str | None


@dataclass(frozen=True)
class PositionInput:
    """One projected position version of the snapshot (typed columns; None when missing)."""

    position_version_id: uuid.UUID
    position_id: uuid.UUID
    business_key: str
    instrument_key: str | None
    balance: Decimal | None
    currency: str | None


@dataclass(frozen=True)
class ModuleInputs:
    """Everything a module may read, frozen. The engine sorts every tuple (source row
    order, then id), so a module sees the same inputs in the same order on every run."""

    reporting_date: date
    cash_flows: tuple[CashFlowInput, ...] = ()
    instruments: tuple[InstrumentInput, ...] = ()
    positions: tuple[PositionInput, ...] = ()


@dataclass(frozen=True)
class PinnedRule:
    """A pinned rule version: its id and immutable parameters."""

    rule_version_id: uuid.UUID
    parameters: Mapping[str, Any]


# rule definition code -> its pinned version
PinnedParameters = Mapping[str, PinnedRule]

# --- outputs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleWarning:
    """A value-free warning: a code, the entity it is about, and codes or counts only."""

    code: CalculationWarningCode
    severity: WarningSeverity
    message: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    details: Mapping[str, str | int] = field(default_factory=dict)


@dataclass(frozen=True)
class Bucket:
    """A maturity bucket: calendar days after the reporting date in (lower, upper]; the
    last bucket is open (upper None)."""

    sequence: int
    code: str
    label: str
    lower_exclusive: int
    upper_inclusive: int | None

    def holds(self, days: int) -> bool:
        return days > self.lower_exclusive and (
            self.upper_inclusive is None or days <= self.upper_inclusive
        )


@dataclass(frozen=True)
class Contribution:
    """One bucketed cash flow: its amount counted in one bucket's inflows or outflows."""

    cashflow_event_id: uuid.UUID
    instrument_version_id: uuid.UUID
    currency: str
    bucket: Bucket
    direction: CashflowDirection
    origin: CashflowOrigin
    amount: Decimal
    days_after_reporting_date: int
    rule_version_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class GapRow:
    """One currency and bucket of a maturity ladder."""

    currency: str
    bucket: Bucket
    inflows_contractual: Decimal
    inflows_behavioural: Decimal
    outflows_contractual: Decimal
    outflows_behavioural: Decimal
    inflows: Decimal
    outflows: Decimal
    net_gap: Decimal
    cumulative_gap: Decimal
    contribution_count: int


@dataclass(frozen=True)
class HqlaAsset:
    """lcr_hqla: one asset position, included at its level (value = balance x (1 - haircut))
    or excluded with its reason (value 0)."""

    position_version_id: uuid.UUID
    instrument_version_id: uuid.UUID
    currency: str
    instrument_type: str
    balance: Decimal
    level: HqlaLevel | None
    haircut: Decimal | None
    hqla_value: Decimal
    exclusion_reason: HqlaExclusionReason | None
    rule_version_ids: tuple[uuid.UUID, ...]

    @property
    def included(self) -> bool:
        return self.level is not None


@dataclass(frozen=True)
class OutflowContribution:
    """lcr_hqla: one position whose instrument type has a run-off rate."""

    position_version_id: uuid.UUID
    currency: str
    balance: Decimal
    run_off: Decimal
    outflow: Decimal  # balance x run_off


@dataclass(frozen=True)
class InflowContribution:
    """lcr_hqla: one cash flow due within the horizon whose instrument type has an inflow
    rate."""

    cashflow_event_id: uuid.UUID
    currency: str
    amount: Decimal
    rate: Decimal
    inflow: Decimal  # amount x rate


@dataclass(frozen=True)
class LcrRow:
    """lcr_hqla: one currency's LCR (D-35 §2)."""

    currency: str
    hqla_level1: Decimal
    hqla_level2a: Decimal
    hqla_level2b: Decimal
    hqla_total: Decimal
    level1_count: int
    level2a_count: int
    level2b_count: int
    excluded_asset_count: int
    outflow_balance: Decimal
    outflows: Decimal
    outflow_count: int
    inflows_gross: Decimal
    inflows_before_cap: Decimal
    inflow_cap_rate: Decimal
    inflow_cap_amount: Decimal
    inflows_after_cap: Decimal
    inflow_cap_applied: bool
    inflow_count: int
    net_outflows: Decimal
    ratio: Decimal | None  # None: the denominator is not positive (LCR_DENOMINATOR_ZERO)
    warnings: tuple[LcrWarningCode, ...]
    rule_version_ids: tuple[uuid.UUID, ...]
    # D-33: per level, outflows and inflows, the contributions with a non-zero value (HQLA
    # value, balance, amount): the minimum aggregation counts only these.
    level1_nonzero_count: int
    level2a_nonzero_count: int
    level2b_nonzero_count: int
    outflow_nonzero_count: int
    inflow_nonzero_count: int


@dataclass(frozen=True)
class ModuleResult:
    """A module's outputs. `blocked`: the inputs or pinned parameters do not meet the
    module's contract, so it produced no results (the run is BLOCKED)."""

    liquidity_gap: tuple[GapRow, ...] = ()
    contributions: tuple[Contribution, ...] = ()
    warnings: tuple[ModuleWarning, ...] = ()
    blocked: bool = False
    # Warning codes that exclude their entity from the outputs (the post-checks need them).
    exclusion_codes: frozenset[CalculationWarningCode] = frozenset()
    # lcr_hqla (Phase 17): per-currency rows, asset rows, outflow and inflow contributions,
    # and the cash flows that are not inflows by rule (out of scope, no warning).
    lcr: tuple[LcrRow, ...] = ()
    hqla_assets: tuple[HqlaAsset, ...] = ()
    outflows: tuple[OutflowContribution, ...] = ()
    inflows: tuple[InflowContribution, ...] = ()
    out_of_scope: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class ModuleSpec:
    """What a module declares (D-31 §1). `input_contract` and `output_contract` are stored
    as is on the seeded `module_version` row."""

    code: str
    version: int
    name: str
    description: str
    engine_version: str
    input_contract: Mapping[str, Any]
    output_contract: Mapping[str, Any]

    @property
    def rules(self) -> tuple[str, ...]:
        return tuple(self.input_contract["rules"])

    @property
    def required_schemas(self) -> tuple[str, ...]:
        return tuple(s["schema_code"] for s in self.input_contract["schemas"] if s["required"])

    def fields_read(self) -> dict[str, tuple[str, ...]]:
        """schema code -> the fields the module reads (for the D-33 classification)."""
        return {s["schema_code"]: tuple(s["fields"]) for s in self.input_contract["schemas"]}


@dataclass(frozen=True)
class RegisteredModule:
    """A registered module (see app.calculations.modules.REGISTRY): its spec, the Decimal
    context it runs under, its declared rounding (None: exact, nothing is rounded) and its
    pure function."""

    spec: ModuleSpec
    context: Callable[[], Context]
    rounding: DeclaredRounding | None
    function: Callable[[ModuleInputs, PinnedParameters], ModuleResult]

    def run(self, inputs: ModuleInputs, parameters: PinnedParameters) -> ModuleResult:
        return self.function(inputs, parameters)


def divide(numerator: Decimal, denominator: Decimal, declared: DeclaredRounding) -> Decimal:
    """numerator / denominator rounded ONCE as the module declares (D-31 §2).

    Exact rational arithmetic on the operands' integer ratios: the quotient is computed to
    one digit past the declared exponent plus a sticky digit for any remainder, which keeps
    its position against every rounding boundary, then `quantize` applies the declared mode.
    No intermediate rounding (so no double rounding). A zero denominator raises."""
    if denominator == 0:
        raise ZeroDivisionError("the denominator is zero")
    exponent = declared.exponent.as_tuple().exponent
    if not isinstance(exponent, int) or exponent > 0:
        raise ValueError("the declared exponent is a finite decimal of at most 1")
    places = -exponent
    n_top, n_bottom = numerator.as_integer_ratio()
    d_top, d_bottom = denominator.as_integer_ratio()
    top, bottom = n_top * d_bottom, n_bottom * d_top
    negative = (top < 0) != (bottom < 0) and top != 0
    whole, rest = divmod(abs(top) * 10 ** (places + 1), abs(bottom))
    digits = whole * 10 + (1 if rest else 0)
    value = Decimal(-digits if negative else digits).scaleb(-(places + 2), context=exact_context())
    return quantize(value, declared)
