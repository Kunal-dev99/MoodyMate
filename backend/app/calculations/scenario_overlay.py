"""calculations.scenario_overlay

The pure, split-aware scenario overlay (BE-25.2, D-45 §2, design §2.4-2.5 and §5).
PROVISIONAL / ILLUSTRATIVE: an illustrative stress exploration, not a regulatory stress test.

`apply(inputs, directions, buckets, shocks, subset, source_system_code) -> Overlay` maps the
unstressed module inputs to a scenario-specific copy: the stressed inputs, the parts every
shocked cash flow was split into, one application per (flow, shock), and value-free counts
per shock. The maturity ladder module then runs unchanged on the stressed inputs.

- **Parts.** Each matching flow may be split into parts. A part is a `CashFlowInput` with a
  deterministic id: `uuid5(PART_NAMESPACE, "<event id>/<part path>")`. A flow no shock
  touches keeps its own event id and the root path "0". A path is the root followed by one
  segment per shock that split or changed the part: `/<shock sequence><M|S|A>` (moved,
  stayed, added). So the unchanged module and the COVERAGE post-check see unique ids, and
  the engine maps every part back to its `cashflow_event_id`. Only shocked parts get uuid5
  ids; unchanged flows keep their event id and path "0".
- **Templates (D-44 §2).**
  - `DELAYED_INFLOWS`: f x amount moves `delay_days` later and amount - (f x amount) stays
    (f = 1: the whole part moves, no split).
  - `DEPOSIT_WITHDRAWAL`: the part stays and an added part of x x amount is due on the same
    day, so the flow's outflow is amount x (1 + x). The only template that changes amounts.
    It is stored as two parts: a STAYED part (`/<n>S`, the original amount) plus an ADDED
    part (`/<n>A`, x x amount), never as one enlarged part.
  - `FUNDING_UNAVAILABLE`: for parts due in days (0, unavailable_days], f x amount moves to
    day unavailable_days + 1 and the rest stays. Parts due later are untouched.
- **Exact.** moved = f x amount and stayed = amount - moved, under `exact_context()`:
  nothing is rounded (a fraction of scale 4 times an amount is exact).
- **Order.** Shocks apply in their declared order (only those of the run's `subset`), to
  parts: a part can be split again. At most 5 shocks and 32 parts per flow.
- **Scope.** Only flows that are complete (due date, amount >= 0, currency, a known origin,
  an instrument of the snapshot with a mapped type) and placed in a bucket (not past due,
  not outside the buckets) are shocked. Others pass through unchanged and are counted.
- **Direction.** From the pinned `cashflow_direction` map. A shock whose instrument type has
  the wrong direction under the pinned map matches nothing; the flows are counted.
- **Failure.** `OverlayError` (value-free): the engine BLOCKS the run with the existing
  PARAMETERS_INVALID code (no CHECK is widened).

Pure: no I/O, no clock, no randomness, no environment. Decimal only; no AI on this path (D-45 §11).
The checks `overlay_matches` and `conserved` are the SCENARIO_OVERLAY and
SCENARIO_CONSERVATION post-checks (D-45 §6), recomputed independently of the overlay used.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from app.calculations.modules.base import Bucket, CashFlowInput, ModuleInputs, ModuleResult
from app.core.decimals import exact_context
from app.domain.constants import CashflowOrigin
from app.orchestration.constants import (
    MAX_PARTS_PER_FLOW,
    MAX_SHOCKS,
    PartChange,
    ScenarioTemplate,
)
from app.rules.constants import CashflowDirection

T = ScenarioTemplate
ZERO = Decimal(0)
ONE = Decimal(1)
ROOT = "0"
# Fixed namespace of part ids (D-45 §2): never change it, or re-runs stop reproducing.
PART_NAMESPACE = uuid.UUID("2f0d7c4e-9a51-4c8e-b7d3-25a6e0f41c93")
TEMPLATE_DIRECTION = {
    T.DELAYED_INFLOWS: CashflowDirection.INFLOW,
    T.DEPOSIT_WITHDRAWAL: CashflowDirection.OUTFLOW,
    T.FUNDING_UNAVAILABLE: CashflowDirection.INFLOW,
}
TIMING_TEMPLATES = frozenset({T.DELAYED_INFLOWS, T.FUNDING_UNAVAILABLE})
_SEGMENT = {PartChange.MOVED: "M", PartChange.STAYED: "S", PartChange.ADDED: "A"}


class OverlayError(ValueError):
    """The overlay cannot be applied (value-free message): the run is BLOCKED."""


# --- shocks ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Shock:
    """One parsed shock of a version: `sequence` is its 1-based declared position."""

    sequence: int
    template: ScenarioTemplate
    instrument_type: str
    fraction: Decimal  # affected_fraction, or increase_fraction for DEPOSIT_WITHDRAWAL
    days: int  # delay_days, or unavailable_days; 0 for DEPOSIT_WITHDRAWAL
    currency: str | None = None
    origin: str | None = None
    due_from_day: int | None = None
    due_to_day: int | None = None
    instrument_keys: frozenset[str] | None = None
    source_system_code: str | None = None

    @property
    def direction(self) -> CashflowDirection:
        return TEMPLATE_DIRECTION[self.template]


def _fraction(raw: Any) -> Decimal:
    if not isinstance(raw, str):
        raise OverlayError("a fraction is exact decimal text")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise OverlayError("a fraction is exact decimal text") from exc
    if not value.is_finite() or not ZERO < value <= ONE:
        raise OverlayError("a fraction is in (0, 1]")
    return value


def _whole(raw: Any, low: int, high: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not low <= raw <= high:
        raise OverlayError("a day count is a whole number in its bounds")
    return int(raw)


def _optional_whole(raw: Any) -> int | None:
    return None if raw is None else _whole(raw, 0, 3650)


def parse_shock(sequence: int, raw: Mapping[str, Any]) -> Shock:
    """One stored (normalised, D-44 §4) shock as the overlay reads it."""
    try:
        template = T(raw["template"])
    except (KeyError, ValueError, TypeError) as exc:
        raise OverlayError("an unknown template") from exc
    parameters = raw.get("parameters") or {}
    selector = raw.get("selector") or {}
    if not isinstance(parameters, Mapping) or not isinstance(selector, Mapping):
        raise OverlayError("parameters and selector are objects")
    if parameters.get("delay_calendar", "CALENDAR_DAYS") != "CALENDAR_DAYS":
        raise OverlayError("CALENDAR_DAYS only in v1")
    keys = selector.get("instrument_keys")
    if keys is not None and (
        not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)
    ):
        raise OverlayError("instrument keys are text")
    currency, origin = selector.get("currency"), selector.get("origin")
    if not all(v is None or isinstance(v, str) for v in (currency, origin)):
        raise OverlayError("selector codes are text")
    due_from = _optional_whole(selector.get("due_from_day"))
    due_to = _optional_whole(selector.get("due_to_day"))
    if template is T.DELAYED_INFLOWS:
        kind, fraction = parameters.get("cashflow_type"), parameters.get("affected_fraction")
        days = _whole(parameters.get("delay_days"), 1, 3650)
    elif template is T.DEPOSIT_WITHDRAWAL:
        kind, fraction = parameters.get("cashflow_type"), parameters.get("increase_fraction")
        days = 0
    else:
        kind, fraction = selector.get("instrument_type"), parameters.get("affected_fraction")
        days = _whole(parameters.get("unavailable_days"), 1, 365)
    source = selector.get("source_system_code")
    if not isinstance(kind, str) or not (source is None or isinstance(source, str)):
        raise OverlayError("an instrument type and a source code are text")
    return Shock(
        sequence=sequence,
        template=template,
        instrument_type=kind,
        fraction=_fraction(fraction),
        days=days,
        currency=currency,
        origin=origin,
        due_from_day=due_from,
        due_to_day=due_to,
        instrument_keys=frozenset(keys) if keys else None,
        source_system_code=source,
    )


def parse_shocks(shocks: Sequence[Mapping[str, Any]], subset: Sequence[int]) -> tuple[Shock, ...]:
    """The shocks of `subset` (1-based declared positions, ascending, distinct), in order."""
    if not 1 <= len(shocks) <= MAX_SHOCKS:
        raise OverlayError("a version has 1 to 5 shocks")
    if (
        not subset
        or list(subset) != sorted(set(subset))
        or not set(subset) <= set(range(1, len(shocks) + 1))
    ):
        raise OverlayError("the subset names distinct shocks of the version, ascending")
    return tuple(parse_shock(n, shocks[n - 1]) for n in subset)


# --- outputs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """One part of a cash flow in the stressed inputs (an unchanged flow is one root part)."""

    part_id: uuid.UUID
    cashflow_event_id: uuid.UUID
    path: str
    shock_sequences: tuple[int, ...]
    change: PartChange
    days_before: int
    days_after: int
    amount: Decimal
    flow: CashFlowInput  # the stressed input row (id = part_id)


@dataclass(frozen=True)
class Application:
    """What one shock did to one cash flow (D-45 §5, `scenario_shock_application`)."""

    cashflow_event_id: uuid.UUID
    shock_sequence: int
    template: ScenarioTemplate
    currency: str
    parts_matched: int  # parts of the flow the shock matched
    parts_after: int  # parts of the flow after the shock
    moved_amount: Decimal  # timing templates: the amount now due later
    added_amount: Decimal  # DEPOSIT_WITHDRAWAL: the extra outflow
    delay_days: int | None  # DELAYED_INFLOWS: how much later
    moved_to_day: int | None  # FUNDING_UNAVAILABLE: the day the moved amount is due


@dataclass(frozen=True)
class ShockCount:
    """Value-free counts of one shock (public, D-46 §3), each shock applied alone to the
    baseline inputs. `skipped_past_due`: matching flows due on or before the reporting date
    (days <= 0), never shocked; `skipped_incomplete`: matching incomplete flows."""

    shock_sequence: int
    template: ScenarioTemplate
    matched_flows: int
    parts: int
    direction_mismatches: int
    skipped_past_due: int
    skipped_incomplete: int


@dataclass(frozen=True)
class Overlay:
    inputs: ModuleInputs  # the stressed inputs (instruments and positions as given)
    parts: tuple[Part, ...]  # every input row, in stressed input order
    applications: tuple[Application, ...]
    counts: tuple[ShockCount, ...]

    def part_of(self) -> dict[uuid.UUID, Part]:
        return {p.part_id: p for p in self.parts}


def part_id(cashflow_event_id: uuid.UUID, path: str) -> uuid.UUID:
    """The deterministic id of a part (D-45 §2); the root part keeps the event id."""
    if path == ROOT:
        return cashflow_event_id
    return uuid.uuid5(PART_NAMESPACE, f"{cashflow_event_id}/{path}")


def change_of(path: str) -> PartChange:
    """A part's change: ADDED if any segment adds, MOVED if any moves, else STAYED (UNCHANGED
    for the root)."""
    segments = path.split("/")[1:]
    if not segments:
        return PartChange.UNCHANGED
    kinds = {s[-1] for s in segments}
    if "A" in kinds:
        return PartChange.ADDED
    return PartChange.MOVED if "M" in kinds else PartChange.STAYED


# --- the overlay ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Flow:
    flow: CashFlowInput
    instrument_type: str | None
    direction: CashflowDirection | None
    days: int | None
    complete: bool
    bucketed: bool


def _flows(
    inputs: ModuleInputs, directions: Mapping[str, CashflowDirection], buckets: Sequence[Bucket]
) -> list[_Flow]:
    types: dict[str, str | None] = {}
    for instrument in inputs.instruments:
        types[instrument.business_key] = instrument.instrument_type
    origins = {o.value for o in CashflowOrigin}
    found = []
    for flow in inputs.cash_flows:
        kind = types.get(flow.instrument_key or "")
        direction = directions.get(kind or "")
        days = (flow.due_date - inputs.reporting_date).days if flow.due_date is not None else None
        complete = (
            days is not None
            and flow.amount is not None
            and flow.amount >= ZERO
            and flow.currency is not None
            and flow.origin in origins
            and (flow.instrument_key or "") in types
            and direction is not None
        )
        bucketed = complete and days is not None and any(b.holds(days) for b in buckets)
        found.append(_Flow(flow, kind, direction, days, complete, bucketed))
    return found


def _selected(shock: Shock, f: _Flow, days: int | None, source_system_code: str | None) -> bool:
    """The selector's fields that the flow (or part) has match, the type included; the
    direction is checked by the caller."""
    flow = f.flow
    if f.instrument_type != shock.instrument_type:
        return False
    if shock.currency is not None and flow.currency != shock.currency:
        return False
    if shock.origin is not None and flow.origin != shock.origin:
        return False
    if shock.instrument_keys is not None and flow.instrument_key not in shock.instrument_keys:
        return False
    if shock.source_system_code is not None and shock.source_system_code != source_system_code:
        return False
    if days is not None:
        if shock.due_from_day is not None and days < shock.due_from_day:
            return False
        if shock.due_to_day is not None and days > shock.due_to_day:
            return False
        if shock.template is T.FUNDING_UNAVAILABLE and not 0 < days <= shock.days:
            return False
    return True


@dataclass(frozen=True)
class _Part:
    path: str
    days: int
    amount: Decimal


def _apply_one(shock: Shock, part: _Part) -> tuple[list[_Part], Decimal, Decimal]:
    """A matched part's children, the amount moved and the amount added."""
    n = shock.sequence
    if shock.template is T.DEPOSIT_WITHDRAWAL:
        added = shock.fraction * part.amount
        return (
            [
                _Part(f"{part.path}/{n}S", part.days, part.amount),
                _Part(f"{part.path}/{n}A", part.days, added),
            ],
            ZERO,
            added,
        )
    to_day = part.days + shock.days if shock.template is T.DELAYED_INFLOWS else shock.days + 1
    moved = shock.fraction * part.amount
    if shock.fraction == ONE:
        return [_Part(f"{part.path}/{n}M", to_day, part.amount)], part.amount, ZERO
    return (
        [
            _Part(f"{part.path}/{n}M", to_day, moved),
            _Part(f"{part.path}/{n}S", part.days, part.amount - moved),
        ],
        moved,
        ZERO,
    )


def apply(
    inputs: ModuleInputs,
    directions: Mapping[str, CashflowDirection],
    buckets: Sequence[Bucket],
    shocks: Sequence[Shock],
    source_system_code: str | None = None,
) -> Overlay:
    """The stressed copy of `inputs` under `shocks` (already the run's subset, in order).
    Run it under `exact_context()`."""
    flows = _flows(inputs, directions, buckets)
    stressed: list[CashFlowInput] = []
    parts_out: list[Part] = []
    applications: list[Application] = []
    tallies = {s.sequence: [0, 0, 0, 0, 0] for s in shocks}  # matched, parts, dir, past, inc
    for f in flows:
        flow = f.flow
        for shock in shocks:  # counts of what the shock skips, per flow
            tally = tallies[shock.sequence]
            if not f.complete:
                if f.instrument_type is not None and _selected(shock, f, None, source_system_code):
                    tally[4] += 1
            elif f.direction != shock.direction:
                if _selected(shock, f, f.days, source_system_code):
                    tally[2] += 1
            elif not f.bucketed and _selected(shock, f, f.days, source_system_code):
                tally[3] += 1
        if not f.bucketed or f.days is None or flow.amount is None or flow.due_date is None:
            parts_out.append(
                Part(flow.cashflow_event_id, flow.cashflow_event_id, ROOT, (), PartChange.UNCHANGED,
                     f.days or 0, f.days or 0, flow.amount or ZERO, flow)
            )  # fmt: skip
            stressed.append(flow)
            continue
        current = [_Part(ROOT, f.days, flow.amount)]
        for shock in shocks:
            if f.direction != shock.direction:
                continue
            matched = {p.path for p in current if _selected(shock, f, p.days, source_system_code)}
            if not matched:
                continue
            nxt: list[_Part] = []
            moved_total, added_total, produced = ZERO, ZERO, 0
            for p in current:
                if p.path in matched:
                    children, moved, added = _apply_one(shock, p)
                    nxt += children
                    produced += len(children)
                    moved_total += moved
                    added_total += added
                else:
                    nxt.append(p)
            if len(nxt) > MAX_PARTS_PER_FLOW:
                raise OverlayError("a cash flow would split into more than 32 parts")
            current = nxt
            tally = tallies[shock.sequence]
            tally[0] += 1
            tally[1] += produced
            applications.append(
                Application(
                    cashflow_event_id=flow.cashflow_event_id,
                    shock_sequence=shock.sequence,
                    template=shock.template,
                    currency=flow.currency or "",
                    parts_matched=len(matched),
                    parts_after=len(current),
                    moved_amount=moved_total,
                    added_amount=added_total,
                    delay_days=shock.days if shock.template is T.DELAYED_INFLOWS else None,
                    moved_to_day=shock.days + 1
                    if shock.template is T.FUNDING_UNAVAILABLE
                    else None,
                )
            )
        for p in sorted(current, key=lambda p: p.path):
            pid = part_id(flow.cashflow_event_id, p.path)
            try:
                due = inputs.reporting_date + timedelta(days=p.days)
            except OverflowError as exc:
                raise OverlayError("a delay moves a cash flow past the last date") from exc
            row = replace(flow, cashflow_event_id=pid, due_date=due, amount=p.amount)
            sequences = tuple(int(s[:-1]) for s in p.path.split("/")[1:])
            parts_out.append(
                Part(pid, flow.cashflow_event_id, p.path, sequences, change_of(p.path),
                     f.days, p.days, p.amount, row)
            )  # fmt: skip
            stressed.append(row)
    counts = tuple(ShockCount(s.sequence, s.template, *tallies[s.sequence]) for s in shocks)
    return Overlay(
        ModuleInputs(inputs.reporting_date, tuple(stressed), inputs.instruments, inputs.positions),
        tuple(parts_out),
        tuple(applications),
        counts,
    )


# --- the post-checks (D-45 §6) ----------------------------------------------------------------


def _part_key(p: Part) -> tuple[Any, ...]:
    return (
        p.part_id,
        p.cashflow_event_id,
        p.path,
        p.shock_sequences,
        p.change,
        p.days_before,
        p.days_after,
        p.amount,
        p.flow,
    )


def overlay_matches(expected: Overlay, used: Overlay) -> bool:
    """SCENARIO_OVERLAY: the overlay recomputed from the unstressed inputs (`expected`) equals
    the parts and applications the run used and stores."""
    return (
        [_part_key(p) for p in expected.parts] == [_part_key(p) for p in used.parts]
        and expected.applications == used.applications
        and expected.inputs.cash_flows == used.inputs.cash_flows
    )


def conserved(
    inputs: ModuleInputs,
    shocks: Sequence[Shock],
    used: Overlay,
    baseline: ModuleResult,
    stressed: ModuleResult,
) -> bool:
    """SCENARIO_CONSERVATION, recomputed from the declared parameters, never from the overlay's
    own sums:
    - per flow, its parts sum exactly to its amount for the timing templates and to amount x
      (1 + x) for each DEPOSIT_WITHDRAWAL that applied to it, and its applications declare
      exactly that added amount;
    - per currency and direction, the stressed total over all buckets equals the baseline
      total plus the declared added amounts (timing changes when money is due, not how much).
    Run under `exact_context()`."""
    by_sequence = {s.sequence: s for s in shocks}
    sums: dict[uuid.UUID, Decimal] = {}
    for p in used.parts:
        sums[p.cashflow_event_id] = sums.get(p.cashflow_event_id, ZERO) + p.amount
    applied: dict[uuid.UUID, list[Application]] = {}
    for a in used.applications:
        if a.shock_sequence not in by_sequence:
            return False
        applied.setdefault(a.cashflow_event_id, []).append(a)
    flows = {f.cashflow_event_id: f for f in inputs.cash_flows}
    if set(sums) != set(flows) or not set(applied) <= set(flows):
        return False
    for event_id, flow in flows.items():
        amount = flow.amount if flow.amount is not None else ZERO
        expected = amount
        declared = ZERO
        for a in sorted(applied.get(event_id, []), key=lambda a: a.shock_sequence):
            declared += a.added_amount
            if a.template is T.DEPOSIT_WITHDRAWAL:
                expected = expected * (ONE + by_sequence[a.shock_sequence].fraction)
            elif a.added_amount != ZERO:
                return False
        if sums[event_id] != expected or expected - amount != declared:
            return False
    totals: dict[tuple[str, str], Decimal] = {}
    for g in baseline.liquidity_gap:
        totals[(g.currency, "INFLOW")] = totals.get((g.currency, "INFLOW"), ZERO) + g.inflows
        totals[(g.currency, "OUTFLOW")] = totals.get((g.currency, "OUTFLOW"), ZERO) + g.outflows
    for a in used.applications:
        direction = TEMPLATE_DIRECTION[a.template].value
        totals[(a.currency, direction)] = totals.get((a.currency, direction), ZERO) + a.added_amount
    after: dict[tuple[str, str], Decimal] = {}
    for g in stressed.liquidity_gap:
        after[(g.currency, "INFLOW")] = after.get((g.currency, "INFLOW"), ZERO) + g.inflows
        after[(g.currency, "OUTFLOW")] = after.get((g.currency, "OUTFLOW"), ZERO) + g.outflows
    keys = set(totals) | set(after)
    return all(totals.get(k, ZERO) == after.get(k, ZERO) for k in keys)


def subsets(shock_count: int) -> list[tuple[int, ...]]:
    """D-45 §7: k > 1 shocks give k + 1 runs (each alone, then all combined); k = 1 one."""
    combined = tuple(range(1, shock_count + 1))
    if shock_count == 1:
        return [combined]
    return [(n,) for n in combined] + [combined]


def run_overlay(
    inputs: ModuleInputs,
    directions: Mapping[str, CashflowDirection],
    buckets: Sequence[Bucket],
    shocks: Sequence[Mapping[str, Any]],
    subset: Sequence[int],
    source_system_code: str | None = None,
) -> Overlay:
    """`apply` of the parsed subset, under the exact context (a convenience for callers)."""
    parsed = parse_shocks(shocks, subset)
    with localcontext(exact_context()):
        return apply(inputs, directions, buckets, parsed, source_system_code)
