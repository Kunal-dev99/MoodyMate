"""calculations.modules.maturity_ladder

Maturity ladder v1 (BE-15.4, FC-04, D-35 §1): an ILLUSTRATIVE prototype. Not a regulatory
calculation; no parity with any vendor system is claimed.

Per currency, with no FX. Each cash-flow event of the snapshot is, in this order:

1. excluded with MISSING_VALUE when its due date, amount or currency is missing;
2. excluded with UNKNOWN_ORIGIN when its origin is outside CONTRACTUAL / BEHAVIOURAL;
3. excluded with NEGATIVE_AMOUNT when its amount is below zero (v1 has no direction field:
   the direction comes from the instrument type, so a signed amount is ambiguous);
4. excluded with INSTRUMENT_NOT_IN_SNAPSHOT when its instrument key names no instrument
   version of the snapshot (D-30 §4);
5. excluded with UNMAPPED_INSTRUMENT_TYPE when the instrument's type is not a key of the
   pinned `cashflow_direction` map;
6. when due before the reporting date (past due), treated per the pinned
   `past_due_treatment`: EXCLUDE_WITH_WARNING excludes it with PAST_DUE_EXCLUDED;
   INCLUDE_IN_FIRST_BUCKET counts it in the first bucket with PAST_DUE_IN_FIRST_BUCKET;
7. otherwise placed by its calendar days after the reporting date in the pinned
   `maturity_buckets` bucket (lower, upper]; with none (e.g. due on the reporting date
   itself under the seed, whose first bucket is (0,1]) it is excluded with OUTSIDE_BUCKETS.

A placed cash flow is one contribution: its amount, in the bucket's inflows or outflows, and
in the contractual or behavioural part (both origins are included and shown separately). A
zero amount is placed like any other. Per currency, every bucket gets a row: inflows,
outflows, net gap (inflows - outflows) and the cumulative gap (running sum of net gaps in
bucket order). Sums only: exact under the declared context, nothing is rounded.

Pure: no I/O, no clock, no randomness, no environment, Decimal only.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.calculations.constants import (
    CalculationWarningCode,
    IntermediateCode,
    WarningSeverity,
)
from app.calculations.modules.base import (
    Bucket,
    CashFlowInput,
    Contribution,
    GapRow,
    InstrumentInput,
    ModuleInputs,
    ModuleResult,
    ModuleSpec,
    ModuleWarning,
    PinnedParameters,
    RegisteredModule,
)
from app.core.decimals import exact_context
from app.domain.constants import CashflowOrigin, DomainSchemaCode
from app.rules.constants import (
    CASHFLOW_DIRECTION,
    MATURITY_BUCKETS,
    MATURITY_LADDER,
    PAST_DUE_TREATMENT,
    CashflowDirection,
    PastDueTreatment,
)

W = CalculationWarningCode
CASHFLOW = "cashflow_event"
ZERO = Decimal(0)

EXCLUSIONS = frozenset(
    {
        W.MISSING_VALUE,
        W.UNKNOWN_ORIGIN,
        W.NEGATIVE_AMOUNT,
        W.INSTRUMENT_NOT_IN_SNAPSHOT,
        W.UNMAPPED_INSTRUMENT_TYPE,
        W.PAST_DUE_EXCLUDED,
        W.OUTSIDE_BUCKETS,
    }
)

SPEC = ModuleSpec(
    code=MATURITY_LADDER,
    version=1,
    name="Maturity ladder (illustrative)",
    description=(
        "Per-currency maturity ladder with no FX: cash flows bucketed by calendar days after "
        "the reporting date, direction by instrument type, past-due cash flows per the rule. "
        "Illustrative prototype; not a regulatory calculation (D-35)."
    ),
    engine_version="1",
    input_contract={
        "schemas": [
            {
                "schema_code": DomainSchemaCode.CASHFLOW_EVENT.value,
                "required": True,
                "fields": [
                    "cashflow_id",
                    "instrument_id",
                    "due_date",
                    "amount",
                    "currency",
                    "origin",
                ],
            },
            {
                "schema_code": DomainSchemaCode.INSTRUMENT.value,
                "required": True,
                "fields": ["instrument_id", "instrument_type"],
            },
        ],
        "rules": [MATURITY_BUCKETS, CASHFLOW_DIRECTION, PAST_DUE_TREATMENT],
        "assumption_keys": [],
        "parameters": ["reporting_date"],
    },
    output_contract={
        "results": ["liquidity_gap_result"],
        "intermediates": [IntermediateCode.CASHFLOW_CONTRIBUTION.value],
        "warning_codes": sorted(
            [
                *(c.value for c in EXCLUSIONS),
                W.PAST_DUE_IN_FIRST_BUCKET.value,
                W.PARAMETERS_INVALID.value,
                W.AMBIGUOUS_INSTRUMENT_KEY.value,
            ]
        ),
    },
)


class ParameterError(ValueError):
    """Pinned parameters that do not fit the module (value-free message)."""


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError("bucket bounds are whole days")
    return int(value)


def parse_buckets(parameters: Mapping[str, Any]) -> tuple[Bucket, ...]:
    """DAY_BUCKETS (D-32 §1): ordered, contiguous half-open (lower, upper] day intervals, the
    last one open, at least one, codes distinct."""
    raw = parameters.get("buckets")
    if not isinstance(raw, list) or not raw:
        raise ParameterError("at least one bucket is required")
    buckets: list[Bucket] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ParameterError("a bucket is an object")
        code, label = item.get("code"), item.get("label")
        if not isinstance(code, str) or not code or not isinstance(label, str) or not label:
            raise ParameterError("a bucket has a code and a label")
        lower = _int(item.get("lower_exclusive"))
        upper_raw = item.get("upper_inclusive")
        upper = None if upper_raw is None else _int(upper_raw)
        if upper is not None and upper <= lower:
            raise ParameterError("a bucket's upper bound is above its lower bound")
        if buckets:
            previous = buckets[-1]
            if previous.upper_inclusive is None or previous.upper_inclusive != lower:
                raise ParameterError("buckets are contiguous and only the last is open")
        buckets.append(Bucket(position, code, label, lower, upper))
    if buckets[-1].upper_inclusive is not None:
        raise ParameterError("the last bucket is open")
    if len({b.code for b in buckets}) != len(buckets):
        raise ParameterError("bucket codes are distinct")
    return tuple(buckets)


def parse_directions(parameters: Mapping[str, Any]) -> dict[str, CashflowDirection]:
    """ENUM_MAP instrument type -> INFLOW / OUTFLOW."""
    raw = parameters.get("map")
    if not isinstance(raw, dict) or not raw:
        raise ParameterError("the direction map has at least one entry")
    allowed = {d.value: d for d in CashflowDirection}
    directions: dict[str, CashflowDirection] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str) or value not in allowed:
            raise ParameterError("each instrument type maps to INFLOW or OUTFLOW")
        directions[key] = allowed[value]
    return directions


def parse_past_due(parameters: Mapping[str, Any]) -> PastDueTreatment:
    """ENUM: one of PastDueTreatment."""
    value = parameters.get("value")
    allowed = {t.value: t for t in PastDueTreatment}
    if not isinstance(value, str) or value not in allowed:
        raise ParameterError("the past-due treatment is one of its enum values")
    return allowed[value]


def _excluded(
    code: CalculationWarningCode, flow: CashFlowInput, message: str, **details: str
) -> ModuleWarning:
    return ModuleWarning(
        code, WarningSeverity.WARNING, message, CASHFLOW, flow.cashflow_event_id, details
    )


def _blocked(code: CalculationWarningCode, message: str, **details: str) -> ModuleResult:
    warning = ModuleWarning(code, WarningSeverity.ERROR, message, details=details)
    return ModuleResult(warnings=(warning,), blocked=True, exclusion_codes=EXCLUSIONS)


def _instruments(rows: Sequence[InstrumentInput]) -> tuple[dict[str, InstrumentInput], bool]:
    """Instruments by business key, and whether a key names more than one version."""
    by_key: dict[str, InstrumentInput] = {}
    ambiguous = False
    for row in rows:
        ambiguous = ambiguous or row.business_key in by_key
        by_key[row.business_key] = row
    return by_key, ambiguous


def place(
    flow: CashFlowInput,
    reporting_date: date,
    instruments: Mapping[str, InstrumentInput],
    directions: Mapping[str, CashflowDirection],
    past_due: PastDueTreatment,
    buckets: Sequence[Bucket],
    rule_ids: tuple[uuid.UUID, ...],
) -> tuple[Contribution | None, ModuleWarning | None]:
    """One cash flow: its contribution, its warning, or both (a past-due flow counted in the
    first bucket)."""
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
        return None, _excluded(
            W.MISSING_VALUE,
            flow,
            "A field the ladder needs is missing; excluded.",
            fields=",".join(missing),
        )
    origins = {o.value: o for o in CashflowOrigin}
    if flow.origin not in origins:
        return None, _excluded(
            W.UNKNOWN_ORIGIN, flow, "The origin is neither CONTRACTUAL nor BEHAVIOURAL; excluded."
        )
    if flow.amount < ZERO:
        return None, _excluded(
            W.NEGATIVE_AMOUNT,
            flow,
            "A negative amount is ambiguous: the direction comes from the instrument type; "
            "excluded.",
        )
    instrument = instruments.get(flow.instrument_key or "")
    if instrument is None:
        return None, _excluded(
            W.INSTRUMENT_NOT_IN_SNAPSHOT,
            flow,
            "The cash flow's instrument is not in the snapshot; excluded.",
        )
    direction = directions.get(instrument.instrument_type or "")
    if direction is None:
        return None, ModuleWarning(
            W.UNMAPPED_INSTRUMENT_TYPE,
            WarningSeverity.WARNING,
            "The instrument's type has no direction in the pinned cashflow_direction rule; "
            "excluded.",
            CASHFLOW,
            flow.cashflow_event_id,
            {"instrument_version_id": str(instrument.instrument_version_id)},
        )
    days = (flow.due_date - reporting_date).days
    # Methodology item (open, PROVISIONAL; the seeded behaviour is kept as the user chose):
    # under INCLUDE_IN_FIRST_BUCKET a past-due flow (days < 0) is counted in the first bucket,
    # while a flow due on the reporting date itself (day 0) is outside the seeded first
    # bucket (0,1] and excluded with OUTSIDE_BUCKETS. Overdue flows would be counted and
    # today's would not. The seeded EXCLUDE_WITH_WARNING excludes both. The methodology owner
    # decides whether day 0 belongs in the first bucket (a D-35 rule-version change).
    warning: ModuleWarning | None = None
    bucket: Bucket | None
    if days < 0:
        if past_due is PastDueTreatment.EXCLUDE_WITH_WARNING:
            return None, _excluded(
                W.PAST_DUE_EXCLUDED,
                flow,
                "Due before the reporting date (past due); excluded per the pinned "
                "past_due_treatment rule.",
            )
        bucket = buckets[0]
        warning = ModuleWarning(
            W.PAST_DUE_IN_FIRST_BUCKET,
            WarningSeverity.WARNING,
            "Due before the reporting date (past due); counted in the first bucket per the "
            "pinned past_due_treatment rule.",
            CASHFLOW,
            flow.cashflow_event_id,
        )
    else:
        bucket = next((b for b in buckets if b.holds(days)), None)
        if bucket is None:
            return None, _excluded(
                W.OUTSIDE_BUCKETS,
                flow,
                "Not past due and outside every bucket of the pinned maturity_buckets rule "
                "(e.g. due on the reporting date when the first bucket starts after it); "
                "excluded.",
            )
    contribution = Contribution(
        cashflow_event_id=flow.cashflow_event_id,
        instrument_version_id=instrument.instrument_version_id,
        currency=flow.currency,
        bucket=bucket,
        direction=direction,
        origin=origins[flow.origin or ""],
        amount=flow.amount,
        days_after_reporting_date=days,
        rule_version_ids=rule_ids,
    )
    return contribution, warning


def ladder(contributions: Sequence[Contribution], buckets: Sequence[Bucket]) -> tuple[GapRow, ...]:
    """Every bucket of every currency with a contribution: sums by direction and origin, the
    net gap and the cumulative gap, in (currency, bucket) order."""
    rows: list[GapRow] = []
    for currency in sorted({c.currency for c in contributions}):
        cumulative = ZERO
        mine = [c for c in contributions if c.currency == currency]
        for bucket in buckets:
            parts = {(d, o): ZERO for d in CashflowDirection for o in CashflowOrigin}
            count = 0
            for c in mine:
                if c.bucket.code == bucket.code:
                    parts[(c.direction, c.origin)] += c.amount
                    count += 1
            i_c = parts[(CashflowDirection.INFLOW, CashflowOrigin.CONTRACTUAL)]
            i_b = parts[(CashflowDirection.INFLOW, CashflowOrigin.BEHAVIOURAL)]
            o_c = parts[(CashflowDirection.OUTFLOW, CashflowOrigin.CONTRACTUAL)]
            o_b = parts[(CashflowDirection.OUTFLOW, CashflowOrigin.BEHAVIOURAL)]
            inflows, outflows = i_c + i_b, o_c + o_b
            net = inflows - outflows
            cumulative += net
            rows.append(
                GapRow(
                    currency, bucket, i_c, i_b, o_c, o_b, inflows, outflows, net, cumulative, count
                )
            )
    return tuple(rows)


def run(inputs: ModuleInputs, parameters: PinnedParameters) -> ModuleResult:
    """The maturity ladder of the inputs under the pinned parameters (pure)."""
    try:
        pins = {code: parameters[code] for code in SPEC.rules}
    except KeyError:
        return _blocked(W.PARAMETERS_INVALID, "A rule the module reads is not pinned.")
    try:
        buckets = parse_buckets(pins[MATURITY_BUCKETS].parameters)
    except ParameterError:
        return _blocked(
            W.PARAMETERS_INVALID,
            "The pinned bucket rule is not valid DAY_BUCKETS.",
            rule=MATURITY_BUCKETS,
        )
    try:
        directions = parse_directions(pins[CASHFLOW_DIRECTION].parameters)
    except ParameterError:
        return _blocked(
            W.PARAMETERS_INVALID,
            "The pinned direction rule is not a valid map.",
            rule=CASHFLOW_DIRECTION,
        )
    try:
        past_due = parse_past_due(pins[PAST_DUE_TREATMENT].parameters)
    except ParameterError:
        return _blocked(
            W.PARAMETERS_INVALID,
            "The pinned past-due rule is not a valid value.",
            rule=PAST_DUE_TREATMENT,
        )
    instruments, ambiguous = _instruments(inputs.instruments)
    if ambiguous:
        return _blocked(
            W.AMBIGUOUS_INSTRUMENT_KEY,
            "An instrument key names more than one version in the snapshot.",
        )
    rule_ids = tuple(sorted(pin.rule_version_id for pin in pins.values()))
    contributions: list[Contribution] = []
    warnings: list[ModuleWarning] = []
    for flow in inputs.cash_flows:
        contribution, warning = place(
            flow, inputs.reporting_date, instruments, directions, past_due, buckets, rule_ids
        )
        if contribution is not None:
            contributions.append(contribution)
        if warning is not None:
            warnings.append(warning)
    return ModuleResult(
        liquidity_gap=ladder(contributions, buckets),
        contributions=tuple(contributions),
        warnings=tuple(warnings),
        exclusion_codes=EXCLUSIONS,
    )


MODULE = RegisteredModule(spec=SPEC, context=exact_context, rounding=None, function=run)
