"""calculations.modules.lcr_hqla

LCR/HQLA v1 (BE-17.2, FC-03, D-35 §2): an ILLUSTRATIVE prototype. Not a regulatory
calculation; no parity with any vendor system is claimed.

Per currency, with no FX. Every parameter is a pinned rule version:
- `hqla_level` (ENUM_MAP instrument type -> L1 / L2A / L2B; a type that is not a key is not
  HQLA) and `hqla_haircut` (DECIMAL_MAP, every levelled type needs one);
- `lcr_outflow_run_off` (DECIMAL_MAP instrument type -> run-off rate);
- `lcr_inflow_rate` (DECIMAL_MAP), `lcr_inflow_horizon_days` (INTEGER) and `lcr_inflow_cap`
  (DECIMAL, a share of outflows).
Decimal parameters are JSON strings of exact decimal text, parsed with Decimal(str); a JSON
number or any other form is refused (PARAMETERS_INVALID, the run is BLOCKED). A type may not
be both HQLA and a run-off liability.

Each position (the value is its `balance`, PROVISIONAL: the schema has no market value) is,
in this order:
1. excluded with MISSING_VALUE when its balance or currency is missing;
2. excluded with NEGATIVE_AMOUNT when its balance is below zero;
3. excluded with INSTRUMENT_NOT_IN_SNAPSHOT when its instrument key names no instrument
   version of the snapshot (D-30 §4);
4. excluded with UNMAPPED_INSTRUMENT_TYPE when its instrument has no (known) type;
5. an outflow when its type has a run-off rate: balance x run-off;
6. otherwise an asset row: included at its level with value balance x (1 - haircut), or
   excluded with NOT_HQLA_ELIGIBLE (value 0).

Each cash flow is, in this order: excluded with INSTRUMENT_NOT_IN_SNAPSHOT or
UNMAPPED_INSTRUMENT_TYPE as above; out of scope (no warning) when its type has no inflow
rate; excluded with MISSING_VALUE (due date, amount or currency) or NEGATIVE_AMOUNT; out of
scope when not due (0, horizon] calendar days after the reporting date; otherwise an inflow:
amount x rate.

Per currency (every currency with an asset row, an outflow or an inflow):
- HQLA = sum of the included asset values, by level and in total;
- outflows = sum of balance x run-off;
- inflows after the cap = min(sum of amount x rate, cap x outflows);
- ratio = HQLA / (outflows - inflows after the cap), quantized ONCE to 4 dp ROUND_HALF_EVEN
  (the module's declared rounding, PROVISIONAL). A zero or negative denominator gives a None
  ratio and LCR_DENOMINATOR_ZERO.
Level caps (L2A/L2B) are NOT applied: a documented limitation, flagged per currency with
LCR_LEVEL_CAPS_NOT_APPLIED whenever a Level 2 asset is included.

Pure: no I/O, no clock, no randomness, no environment, Decimal only (exact products and
sums under the declared context; the ratio is the only rounding).
"""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.calculations.constants import (
    CalculationWarningCode,
    HqlaExclusionReason,
    LcrWarningCode,
    WarningSeverity,
)
from app.calculations.modules.base import (
    CashFlowInput,
    HqlaAsset,
    InflowContribution,
    InstrumentInput,
    LcrRow,
    ModuleInputs,
    ModuleResult,
    ModuleSpec,
    ModuleWarning,
    OutflowContribution,
    PinnedParameters,
    PositionInput,
    RegisteredModule,
    divide,
)
from app.core.decimals import DeclaredRounding, exact_context
from app.domain.constants import DomainSchemaCode
from app.rules.constants import (
    HQLA_HAIRCUT,
    HQLA_LEVEL,
    LCR_HQLA,
    LCR_INFLOW_CAP,
    LCR_INFLOW_HORIZON_DAYS,
    LCR_INFLOW_RATE,
    LCR_OUTFLOW_RUN_OFF,
    HqlaLevel,
)

W = CalculationWarningCode
POSITION = "position_version"
CASHFLOW = "cashflow_event"
ZERO = Decimal(0)
ONE = Decimal(1)
# D-35 §2: the ratio, 4 dp ROUND_HALF_EVEN (PROVISIONAL).
RATIO_ROUNDING = DeclaredRounding(Decimal("0.0001"), ROUND_HALF_EVEN)
DECIMAL_TEXT = re.compile(r"^\d+(\.\d+)?$")

EXCLUSIONS = frozenset(
    {
        W.MISSING_VALUE,
        W.NEGATIVE_AMOUNT,
        W.INSTRUMENT_NOT_IN_SNAPSHOT,
        W.UNMAPPED_INSTRUMENT_TYPE,
    }
)

SPEC = ModuleSpec(
    code=LCR_HQLA,
    version=1,
    name="LCR/HQLA (illustrative prototype)",
    description=(
        "Per-currency liquidity coverage ratio with no FX: HQLA by level from position "
        "balances less haircuts, outflows from run-off rates, inflows within the horizon "
        "capped as a share of outflows; ratio to 4 dp. No L2A/L2B caps. Illustrative "
        "prototype; not a regulatory calculation (D-35)."
    ),
    engine_version="1",
    input_contract={
        "schemas": [
            {
                "schema_code": DomainSchemaCode.POSITION.value,
                "required": True,
                "fields": ["position_id", "instrument_id", "balance", "currency"],
            },
            {
                "schema_code": DomainSchemaCode.INSTRUMENT.value,
                "required": True,
                "fields": ["instrument_id", "instrument_type"],
            },
            {
                "schema_code": DomainSchemaCode.CASHFLOW_EVENT.value,
                "required": True,
                "fields": ["cashflow_id", "instrument_id", "due_date", "amount", "currency"],
            },
        ],
        "rules": [
            HQLA_LEVEL,
            HQLA_HAIRCUT,
            LCR_OUTFLOW_RUN_OFF,
            LCR_INFLOW_RATE,
            LCR_INFLOW_HORIZON_DAYS,
            LCR_INFLOW_CAP,
        ],
        "assumption_keys": [],
        "parameters": ["reporting_date"],
    },
    output_contract={
        "results": ["lcr_result", "hqla_asset_result"],
        "intermediates": [],
        "warning_codes": sorted(
            [
                *(c.value for c in EXCLUSIONS),
                W.PARAMETERS_INVALID.value,
                W.AMBIGUOUS_INSTRUMENT_KEY.value,
                *(c.value for c in LcrWarningCode),
            ]
        ),
    },
)


class ParameterError(ValueError):
    """Pinned parameters that do not fit the module (value-free message)."""


@dataclass(frozen=True)
class Parameters:
    levels: Mapping[str, HqlaLevel]
    haircuts: Mapping[str, Decimal]
    run_off: Mapping[str, Decimal]
    inflow_rates: Mapping[str, Decimal]
    horizon_days: int
    inflow_cap: Decimal
    rule_ids: tuple[uuid.UUID, ...]


def fraction(value: Any) -> Decimal:
    """A DECIMAL parameter: a JSON string of plain decimal text in [0, 1], parsed exactly
    (never a JSON number, never exponent form, NaN or infinity)."""
    if not isinstance(value, str) or not DECIMAL_TEXT.match(value):
        raise ParameterError("a decimal parameter is a string of plain decimal text")
    parsed = Decimal(value)
    if parsed > ONE:
        raise ParameterError("a rate, haircut or cap is between 0 and 1")
    return parsed


def fraction_map(parameters: Mapping[str, Any]) -> dict[str, Decimal]:
    """DECIMAL_MAP instrument type -> a fraction in [0, 1] (may be empty)."""
    raw = parameters.get("map")
    if not isinstance(raw, dict):
        raise ParameterError("a decimal map is an object")
    found: dict[str, Decimal] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ParameterError("a decimal map is keyed by instrument type")
        found[key] = fraction(value)
    return found


def level_map(parameters: Mapping[str, Any]) -> dict[str, HqlaLevel]:
    """ENUM_MAP instrument type -> L1 / L2A / L2B (may be empty: nothing is HQLA)."""
    raw = parameters.get("map")
    if not isinstance(raw, dict):
        raise ParameterError("the level map is an object")
    allowed = {level.value: level for level in HqlaLevel}
    found: dict[str, HqlaLevel] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise ParameterError("each instrument type maps to an HQLA level")
        if value not in allowed:
            raise ParameterError("each instrument type maps to an HQLA level")
        found[key] = allowed[value]
    return found


def horizon(parameters: Mapping[str, Any]) -> int:
    """INTEGER: whole calendar days, at least 1."""
    value = parameters.get("value")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ParameterError("the horizon is a whole number of days, at least 1")
    return int(value)


def parse(parameters: PinnedParameters) -> Parameters:
    """Every pinned rule the module reads, typed and checked together."""
    try:
        pins = {code: parameters[code] for code in SPEC.rules}
    except KeyError as exc:
        raise ParameterError("a rule the module reads is not pinned") from exc
    cap = pins[LCR_INFLOW_CAP].parameters.get("value")
    found = Parameters(
        levels=level_map(pins[HQLA_LEVEL].parameters),
        haircuts=fraction_map(pins[HQLA_HAIRCUT].parameters),
        run_off=fraction_map(pins[LCR_OUTFLOW_RUN_OFF].parameters),
        inflow_rates=fraction_map(pins[LCR_INFLOW_RATE].parameters),
        horizon_days=horizon(pins[LCR_INFLOW_HORIZON_DAYS].parameters),
        inflow_cap=fraction(cap),
        rule_ids=tuple(sorted(pin.rule_version_id for pin in pins.values())),
    )
    if not set(found.levels) <= set(found.haircuts):
        raise ParameterError("every HQLA instrument type has a haircut")
    if set(found.levels) & set(found.run_off):
        raise ParameterError("an instrument type is HQLA or a run-off liability, not both")
    return found


def _excluded(
    code: CalculationWarningCode, entity_type: str, entity_id: uuid.UUID, message: str, **kw: str
) -> ModuleWarning:
    return ModuleWarning(code, WarningSeverity.WARNING, message, entity_type, entity_id, kw)


def _blocked(code: CalculationWarningCode, message: str, **details: str) -> ModuleResult:
    warning = ModuleWarning(code, WarningSeverity.ERROR, message, details=details)
    return ModuleResult(warnings=(warning,), blocked=True, exclusion_codes=EXCLUSIONS)


def _instruments(rows: Sequence[InstrumentInput]) -> tuple[dict[str, InstrumentInput], bool]:
    by_key: dict[str, InstrumentInput] = {}
    ambiguous = False
    for row in rows:
        ambiguous = ambiguous or row.business_key in by_key
        by_key[row.business_key] = row
    return by_key, ambiguous


def place_position(
    position: PositionInput, instruments: Mapping[str, InstrumentInput], p: Parameters
) -> HqlaAsset | OutflowContribution | ModuleWarning:
    """One position: an asset row, an outflow, or its exclusion warning."""
    pid = position.position_version_id
    missing = [
        name
        for name, value in (("balance", position.balance), ("currency", position.currency))
        if value is None
    ]
    if missing or position.balance is None or position.currency is None:
        return _excluded(
            W.MISSING_VALUE,
            POSITION,
            pid,
            "A field the LCR needs is missing; excluded.",
            fields=",".join(missing),
        )
    if position.balance < ZERO:
        return _excluded(
            W.NEGATIVE_AMOUNT, POSITION, pid, "A negative balance is ambiguous; excluded."
        )
    instrument = instruments.get(position.instrument_key or "")
    if instrument is None:
        return _excluded(
            W.INSTRUMENT_NOT_IN_SNAPSHOT,
            POSITION,
            pid,
            "The position's instrument is not in the snapshot; excluded.",
        )
    kind = instrument.instrument_type
    if kind is None:
        return _excluded(
            W.UNMAPPED_INSTRUMENT_TYPE,
            POSITION,
            pid,
            "The position's instrument has no known type; excluded.",
            instrument_version_id=str(instrument.instrument_version_id),
        )
    if kind in p.run_off:
        rate = p.run_off[kind]
        return OutflowContribution(
            pid, position.currency, position.balance, rate, position.balance * rate
        )
    level = p.levels.get(kind)
    if level is None:
        return HqlaAsset(
            pid,
            instrument.instrument_version_id,
            position.currency,
            kind,
            position.balance,
            None,
            None,
            ZERO,
            HqlaExclusionReason.NOT_HQLA_ELIGIBLE,
            p.rule_ids,
        )
    haircut = p.haircuts[kind]
    return HqlaAsset(
        pid,
        instrument.instrument_version_id,
        position.currency,
        kind,
        position.balance,
        level,
        haircut,
        position.balance * (ONE - haircut),
        None,
        p.rule_ids,
    )


def place_flow(
    flow: CashFlowInput,
    reporting_date: date,
    instruments: Mapping[str, InstrumentInput],
    p: Parameters,
) -> InflowContribution | ModuleWarning | None:
    """One cash flow: an inflow, its exclusion warning, or None (out of scope by rule)."""
    fid = flow.cashflow_event_id
    instrument = instruments.get(flow.instrument_key or "")
    if instrument is None:
        return _excluded(
            W.INSTRUMENT_NOT_IN_SNAPSHOT,
            CASHFLOW,
            fid,
            "The cash flow's instrument is not in the snapshot; excluded.",
        )
    kind = instrument.instrument_type
    if kind is None:
        return _excluded(
            W.UNMAPPED_INSTRUMENT_TYPE,
            CASHFLOW,
            fid,
            "The cash flow's instrument has no known type; excluded.",
            instrument_version_id=str(instrument.instrument_version_id),
        )
    rate = p.inflow_rates.get(kind)
    if rate is None:
        return None
    missing = [
        name
        for name, value in (
            ("due_date", flow.due_date),
            ("amount", flow.amount),
            ("currency", flow.currency),
        )
        if value is None
    ]
    if missing or flow.due_date is None or flow.amount is None or flow.currency is None:
        return _excluded(
            W.MISSING_VALUE,
            CASHFLOW,
            fid,
            "A field the LCR needs is missing; excluded.",
            fields=",".join(missing),
        )
    if flow.amount < ZERO:
        return _excluded(
            W.NEGATIVE_AMOUNT, CASHFLOW, fid, "A negative amount is ambiguous; excluded."
        )
    days = (flow.due_date - reporting_date).days
    if days <= 0 or days > p.horizon_days:
        return None
    return InflowContribution(fid, flow.currency, flow.amount, rate, flow.amount * rate)


def currency_row(
    currency: str,
    assets: Sequence[HqlaAsset],
    outflows: Sequence[OutflowContribution],
    inflows: Sequence[InflowContribution],
    cap: Decimal,
    rule_ids: tuple[uuid.UUID, ...],
) -> LcrRow:
    """One currency's LCR from its contributions (also the engine's post-check)."""
    totals = {level: ZERO for level in HqlaLevel}
    counts = dict.fromkeys(HqlaLevel, 0)
    for a in assets:
        if a.level is not None:
            totals[a.level] += a.hqla_value
            counts[a.level] += 1
    hqla = totals[HqlaLevel.L1] + totals[HqlaLevel.L2A] + totals[HqlaLevel.L2B]
    outflow_balance = sum((o.balance for o in outflows), ZERO)
    outflow_total = sum((o.outflow for o in outflows), ZERO)
    gross = sum((i.amount for i in inflows), ZERO)
    before = sum((i.inflow for i in inflows), ZERO)
    cap_amount = cap * outflow_total
    applied = before > cap_amount
    after = cap_amount if applied else before
    net = outflow_total - after
    warnings: list[LcrWarningCode] = []
    ratio: Decimal | None = None
    if net > ZERO:
        ratio = divide(hqla, net, RATIO_ROUNDING)
    else:
        warnings.append(LcrWarningCode.LCR_DENOMINATOR_ZERO)
    if counts[HqlaLevel.L2A] or counts[HqlaLevel.L2B]:
        warnings.append(LcrWarningCode.LCR_LEVEL_CAPS_NOT_APPLIED)
    return LcrRow(
        currency=currency,
        hqla_level1=totals[HqlaLevel.L1],
        hqla_level2a=totals[HqlaLevel.L2A],
        hqla_level2b=totals[HqlaLevel.L2B],
        hqla_total=hqla,
        level1_count=counts[HqlaLevel.L1],
        level2a_count=counts[HqlaLevel.L2A],
        level2b_count=counts[HqlaLevel.L2B],
        excluded_asset_count=sum(1 for a in assets if a.level is None),
        outflow_balance=outflow_balance,
        outflows=outflow_total,
        outflow_count=len(outflows),
        inflows_gross=gross,
        inflows_before_cap=before,
        inflow_cap_rate=cap,
        inflow_cap_amount=cap_amount,
        inflows_after_cap=after,
        inflow_cap_applied=applied,
        inflow_count=len(inflows),
        net_outflows=net,
        ratio=ratio,
        warnings=tuple(warnings),
        rule_version_ids=rule_ids,
    )


def lcr_rows(
    assets: Sequence[HqlaAsset],
    outflows: Sequence[OutflowContribution],
    inflows: Sequence[InflowContribution],
    cap: Decimal,
    rule_ids: tuple[uuid.UUID, ...],
) -> tuple[LcrRow, ...]:
    """Every currency with an asset row, an outflow or an inflow, in currency order."""
    currencies = sorted(
        {a.currency for a in assets}
        | {o.currency for o in outflows}
        | {i.currency for i in inflows}
    )
    return tuple(
        currency_row(
            c,
            [a for a in assets if a.currency == c],
            [o for o in outflows if o.currency == c],
            [i for i in inflows if i.currency == c],
            cap,
            rule_ids,
        )
        for c in currencies
    )


def run(inputs: ModuleInputs, parameters: PinnedParameters) -> ModuleResult:
    """The LCR of the inputs under the pinned parameters (pure)."""
    try:
        p = parse(parameters)
    except ParameterError as exc:
        return _blocked(W.PARAMETERS_INVALID, "A pinned LCR rule is not valid.", reason=str(exc))
    instruments, ambiguous = _instruments(inputs.instruments)
    if ambiguous:
        return _blocked(
            W.AMBIGUOUS_INSTRUMENT_KEY,
            "An instrument key names more than one version in the snapshot.",
        )
    assets: list[HqlaAsset] = []
    outflows: list[OutflowContribution] = []
    inflows: list[InflowContribution] = []
    out_of_scope: list[uuid.UUID] = []
    warnings: list[ModuleWarning] = []
    for position in inputs.positions:
        placed = place_position(position, instruments, p)
        if isinstance(placed, HqlaAsset):
            assets.append(placed)
        elif isinstance(placed, OutflowContribution):
            outflows.append(placed)
        else:
            warnings.append(placed)
    for flow in inputs.cash_flows:
        found = place_flow(flow, inputs.reporting_date, instruments, p)
        if found is None:
            out_of_scope.append(flow.cashflow_event_id)
        elif isinstance(found, InflowContribution):
            inflows.append(found)
        else:
            warnings.append(found)
    return ModuleResult(
        warnings=tuple(warnings),
        exclusion_codes=EXCLUSIONS,
        lcr=lcr_rows(assets, outflows, inflows, p.inflow_cap, p.rule_ids),
        hqla_assets=tuple(assets),
        outflows=tuple(outflows),
        inflows=tuple(inflows),
        out_of_scope=tuple(out_of_scope),
    )


MODULE = RegisteredModule(spec=SPEC, context=exact_context, rounding=RATIO_ROUNDING, function=run)
