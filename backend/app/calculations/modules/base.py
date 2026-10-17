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

from app.calculations.constants import CalculationWarningCode, WarningSeverity
from app.core.decimals import DeclaredRounding
from app.domain.constants import CashflowOrigin
from app.rules.constants import CashflowDirection

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
class ModuleInputs:
    """Everything a module may read, frozen. The engine sorts every tuple (source row
    order, then id), so a module sees the same inputs in the same order on every run."""

    reporting_date: date
    cash_flows: tuple[CashFlowInput, ...] = ()
    instruments: tuple[InstrumentInput, ...] = ()


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
class ModuleResult:
    """A module's outputs. `blocked`: the inputs or pinned parameters do not meet the
    module's contract, so it produced no results (the run is BLOCKED)."""

    liquidity_gap: tuple[GapRow, ...] = ()
    contributions: tuple[Contribution, ...] = ()
    warnings: tuple[ModuleWarning, ...] = ()
    blocked: bool = False
    # Warning codes that exclude their entity from the outputs (the post-checks need them).
    exclusion_codes: frozenset[CalculationWarningCode] = frozenset()


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
