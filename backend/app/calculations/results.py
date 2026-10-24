"""calculations.results (BE-35.2: split from calculations.service, which re-exports it)

The pure helpers of a run's outputs: no I/O, Decimal only. The post-checks (D-31 §4, D-45 §6),
the canonical result document and `result_sha256` (`reproduce`, D-31 §5), and the per-figure
classifications of the ladder and liquidity reads (D-33). Moved verbatim: every hash is
unchanged.
"""

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.calculations import scenario_overlay
from app.calculations.constants import (
    CalculationRunStatus,
    CalculationWarningCode,
    LcrWarningCode,
    PostCheck,
    WarningSeverity,
)
from app.calculations.models import LcrResult, LiquidityGapResult
from app.calculations.modules.base import (
    Contribution,
    GapRow,
    HqlaAsset,
    LcrRow,
    ModuleInputs,
    ModuleResult,
    ModuleWarning,
    divide,
)
from app.core.canonical import HashKind, sha256_for
from app.core.classification import most_sensitive, storable
from app.core.decimals import DeclaredRounding, decimal_text
from app.governance.constants import ClassificationCode
from app.rules.constants import HqlaLevel
from app.rules.models import MaturityBucketDefinition

W = CalculationWarningCode
SUCCEEDED = frozenset(
    {CalculationRunStatus.SUCCEEDED, CalculationRunStatus.SUCCEEDED_WITH_WARNINGS}
)
PUBLIC = ClassificationCode.PUBLIC.value


def _public(details: Mapping[str, Any]) -> dict[str, str]:
    return dict.fromkeys(details, PUBLIC)


def post_checks(
    inputs: ModuleInputs, result: ModuleResult, buckets: Sequence[MaturityBucketDefinition]
) -> dict[PostCheck, bool]:
    """The engine's checks of a module result (D-31 §3), recomputed from the contributions
    and never taken from the module's own sums. Run under the exact context."""
    ids = [f.cashflow_event_id for f in inputs.cash_flows]
    used = [c.cashflow_event_id for c in result.contributions]
    excluded = [
        w.entity_id
        for w in result.warnings
        if w.code in result.exclusion_codes and w.entity_id is not None
    ]
    coverage = (
        len(set(ids)) == len(ids)
        and len(set(used)) == len(used)
        and len(set(excluded)) == len(excluded)
        and not set(used) & set(excluded)
        and set(used) | set(excluded) == set(ids)
    )
    defined = {(b.code, b.sequence) for b in buckets}
    bucket_defined = all(
        (c.bucket.code, c.bucket.sequence) in defined for c in result.contributions
    ) and all((g.bucket.code, g.bucket.sequence) in defined for g in result.liquidity_gap)

    zero = Decimal(0)
    parts: dict[tuple[str, str, str, str], Decimal] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for c in result.contributions:
        key = (c.currency, c.bucket.code, c.direction.value, c.origin.value)
        parts[key] = parts.get(key, zero) + c.amount
        counts[(c.currency, c.bucket.code)] += 1
    rows = {(g.currency, g.bucket.code): g for g in result.liquidity_gap}
    totals = len(rows) == len(result.liquidity_gap) and set(counts) <= set(rows)
    for (currency, code), g in rows.items():

        def part(
            direction: str, origin: str, currency: str = currency, code: str = code
        ) -> Decimal:
            return parts.get((currency, code, direction, origin), zero)

        i_c, i_b = part("INFLOW", "CONTRACTUAL"), part("INFLOW", "BEHAVIOURAL")
        o_c, o_b = part("OUTFLOW", "CONTRACTUAL"), part("OUTFLOW", "BEHAVIOURAL")
        totals = totals and (
            (g.inflows_contractual, g.inflows_behavioural) == (i_c, i_b)
            and (g.outflows_contractual, g.outflows_behavioural) == (o_c, o_b)
            and g.inflows == i_c + i_b
            and g.outflows == o_c + o_b
            and g.net_gap == g.inflows - g.outflows
            and g.contribution_count == counts[(currency, code)]
        )
    cumulative_ok = True
    for currency in sorted({g.currency for g in result.liquidity_gap}):
        running = zero
        for g in sorted(
            (g for g in result.liquidity_gap if g.currency == currency),
            key=lambda g: g.bucket.sequence,
        ):
            running += g.net_gap
            cumulative_ok = cumulative_ok and g.cumulative_gap == running
    return {
        PostCheck.COVERAGE: coverage,
        PostCheck.BUCKET_DEFINED: bucket_defined,
        PostCheck.BUCKET_TOTALS: totals,
        PostCheck.CUMULATIVE_GAP: cumulative_ok,
    }


def lcr_post_checks(
    inputs: ModuleInputs, result: ModuleResult, rounding: DeclaredRounding | None
) -> dict[PostCheck, bool]:
    """lcr_hqla's checks (D-31 §3), recomputed from the contributions and never taken from
    the module's own sums: every position and cash flow is used once, out of scope by rule,
    or excluded with a warning code; every asset row's value follows from its balance and
    haircut and every HQLA level total is the sum of its included rows; every currency's
    outflows, capped inflows, denominator, ratio and warnings follow from the contributions.
    Run under the exact context."""
    zero = Decimal(0)
    ids = [p.position_version_id for p in inputs.positions]
    ids += [f.cashflow_event_id for f in inputs.cash_flows]
    used = [a.position_version_id for a in result.hqla_assets]
    used += [o.position_version_id for o in result.outflows]
    used += [i.cashflow_event_id for i in result.inflows]
    used += list(result.out_of_scope)
    excluded = [
        w.entity_id
        for w in result.warnings
        if w.code in result.exclusion_codes and w.entity_id is not None
    ]
    coverage = (
        len(set(ids)) == len(ids)
        and len(set(used)) == len(used)
        and len(set(excluded)) == len(excluded)
        and not set(used) & set(excluded)
        and set(used) | set(excluded) == set(ids)
    )
    rows = {r.currency: r for r in result.lcr}
    currencies = (
        {a.currency for a in result.hqla_assets}
        | {o.currency for o in result.outflows}
        | {i.currency for i in result.inflows}
    )
    caps = {r.inflow_cap_rate for r in result.lcr}
    hqla_ok = len(rows) == len(result.lcr) and set(rows) == currencies and len(caps) <= 1
    # A module that divides declares its rounding (D-31 §2): without one, nothing passes.
    figures_ok = hqla_ok and rounding is not None
    for a in result.hqla_assets:
        if a.level is None:
            hqla_ok = hqla_ok and a.haircut is None and a.hqla_value == zero
            hqla_ok = hqla_ok and a.exclusion_reason is not None
        else:
            hqla_ok = hqla_ok and a.haircut is not None and a.exclusion_reason is None
            haircut = a.haircut if a.haircut is not None else zero
            hqla_ok = hqla_ok and a.hqla_value == a.balance * (1 - haircut)
    for currency, r in rows.items():
        mine = [a for a in result.hqla_assets if a.currency == currency]
        by_level = {level: [a.hqla_value for a in mine if a.level == level] for level in HqlaLevel}
        hqla_ok = hqla_ok and (
            (r.hqla_level1, r.hqla_level2a, r.hqla_level2b)
            == tuple(sum(by_level[level], zero) for level in HqlaLevel)
            and (r.level1_count, r.level2a_count, r.level2b_count)
            == tuple(len(by_level[level]) for level in HqlaLevel)
            and r.hqla_total == r.hqla_level1 + r.hqla_level2a + r.hqla_level2b
            and r.excluded_asset_count == sum(1 for a in mine if a.level is None)
            and (r.level1_nonzero_count, r.level2a_nonzero_count, r.level2b_nonzero_count)
            == tuple(sum(1 for v in by_level[level] if v != zero) for level in HqlaLevel)
        )
        out = [o for o in result.outflows if o.currency == currency]
        into = [i for i in result.inflows if i.currency == currency]
        outflows = sum((o.balance * o.run_off for o in out), zero)
        before = sum((i.amount * i.rate for i in into), zero)
        cap_amount = r.inflow_cap_rate * outflows
        after = min(before, cap_amount)
        net = outflows - after
        ratio = None
        if net > zero and rounding is not None:
            ratio = divide(r.hqla_total, net, rounding)
        expected = [LcrWarningCode.LCR_DENOMINATOR_ZERO] if net <= zero else []
        if r.level2a_count or r.level2b_count:
            expected.append(LcrWarningCode.LCR_LEVEL_CAPS_NOT_APPLIED)
        figures_ok = figures_ok and (
            all(o.outflow == o.balance * o.run_off for o in out)
            and all(i.inflow == i.amount * i.rate for i in into)
            and r.outflow_balance == sum((o.balance for o in out), zero)
            and (r.outflows, r.outflow_count) == (outflows, len(out))
            and r.inflows_gross == sum((i.amount for i in into), zero)
            and (r.inflows_before_cap, r.inflow_count) == (before, len(into))
            and r.outflow_nonzero_count == sum(1 for o in out if o.balance != zero)
            and r.inflow_nonzero_count == sum(1 for i in into if i.amount != zero)
            and (r.inflow_cap_amount, r.inflows_after_cap) == (cap_amount, after)
            and r.inflow_cap_applied == (before > cap_amount)
            and r.net_outflows == net
            and r.ratio == ratio
            and list(r.warnings) == expected
        )
    return {
        PostCheck.COVERAGE: coverage,
        PostCheck.HQLA_TOTALS: hqla_ok,
        PostCheck.LCR_FIGURES: figures_ok,
    }


def _lcr_document(r: LcrRow) -> dict[str, Any]:
    figures = {
        name: decimal_text(getattr(r, name))
        for name in (
            "hqla_level1",
            "hqla_level2a",
            "hqla_level2b",
            "hqla_total",
            "outflow_balance",
            "outflows",
            "inflows_gross",
            "inflows_before_cap",
            "inflow_cap_rate",
            "inflow_cap_amount",
            "inflows_after_cap",
            "net_outflows",
        )
    }
    return {
        "currency": r.currency,
        **figures,
        "ratio": decimal_text(r.ratio) if r.ratio is not None else None,
        "inflow_cap_applied": r.inflow_cap_applied,
        "counts": [
            r.level1_count,
            r.level2a_count,
            r.level2b_count,
            r.excluded_asset_count,
            r.outflow_count,
            r.inflow_count,
        ],
        "nonzero_counts": [
            r.level1_nonzero_count,
            r.level2a_nonzero_count,
            r.level2b_nonzero_count,
            r.outflow_nonzero_count,
            r.inflow_nonzero_count,
        ],
        "warnings": [w.value for w in r.warnings],
        "rule_version_ids": [str(i) for i in r.rule_version_ids],
    }


def _asset_document(a: HqlaAsset) -> dict[str, Any]:
    return {
        "position_version_id": str(a.position_version_id),
        "instrument_version_id": str(a.instrument_version_id),
        "currency": a.currency,
        "instrument_type": a.instrument_type,
        "balance": decimal_text(a.balance),
        "level": a.level.value if a.level else None,
        "haircut": decimal_text(a.haircut) if a.haircut is not None else None,
        "hqla_value": decimal_text(a.hqla_value),
        "exclusion_reason": a.exclusion_reason.value if a.exclusion_reason else None,
        "rule_version_ids": [str(i) for i in a.rule_version_ids],
    }


def _gap_document(g: GapRow) -> dict[str, Any]:
    return {
        "currency": g.currency,
        "bucket_code": g.bucket.code,
        "bucket_sequence": g.bucket.sequence,
        "inflows": decimal_text(g.inflows),
        "outflows": decimal_text(g.outflows),
        "net_gap": decimal_text(g.net_gap),
        "cumulative_gap": decimal_text(g.cumulative_gap),
        "inflows_contractual": decimal_text(g.inflows_contractual),
        "inflows_behavioural": decimal_text(g.inflows_behavioural),
        "outflows_contractual": decimal_text(g.outflows_contractual),
        "outflows_behavioural": decimal_text(g.outflows_behavioural),
        "contribution_count": g.contribution_count,
    }


def _contribution_document(c: Contribution) -> dict[str, Any]:
    return {
        "cashflow_event_id": str(c.cashflow_event_id),
        "instrument_version_id": str(c.instrument_version_id),
        "currency": c.currency,
        "bucket_code": c.bucket.code,
        "direction": c.direction.value,
        "origin": c.origin.value,
        "value": decimal_text(c.amount),
        "days_after_reporting_date": c.days_after_reporting_date,
        "rule_version_ids": [str(r) for r in c.rule_version_ids],
    }


def _part_document(p: scenario_overlay.Part) -> dict[str, Any]:
    return {
        "part_id": str(p.part_id),
        "cashflow_event_id": str(p.cashflow_event_id),
        "part_path": p.path,
        "shock_sequences": list(p.shock_sequences),
        "change": p.change.value,
        "days_before": p.days_before,
        "days_after": p.days_after,
        "value": decimal_text(p.amount),
    }


def _application_document(a: scenario_overlay.Application) -> dict[str, Any]:
    return {
        "cashflow_event_id": str(a.cashflow_event_id),
        "shock_sequence": a.shock_sequence,
        "template": a.template.value,
        "currency": a.currency,
        "parts_matched": a.parts_matched,
        "parts_after": a.parts_after,
        "moved_amount": decimal_text(a.moved_amount),
        "added_amount": decimal_text(a.added_amount),
        "delay_days": a.delay_days,
        "moved_to_day": a.moved_to_day,
    }


@dataclass(frozen=True)
class ScenarioOutputs:
    """A stressed run's parts (those the module placed in a bucket) and applications, as
    stored and hashed (D-45 §5)."""

    parts: tuple[scenario_overlay.Part, ...] = ()
    applications: tuple[scenario_overlay.Application, ...] = ()


def _warning_document(w: ModuleWarning) -> dict[str, Any]:
    return {
        "code": w.code.value,
        "severity": w.severity.value,
        "entity_type": w.entity_type,
        "entity_id": str(w.entity_id) if w.entity_id else None,
        "details": dict(w.details),
    }


def kept_lcr(
    status: CalculationRunStatus, lcr: Sequence[LcrRow], assets: Sequence[HqlaAsset]
) -> tuple[list[LcrRow], list[HqlaAsset]]:
    """The lcr_hqla outputs a finishing run stores (and hashes): only when it SUCCEEDED."""
    if status not in SUCCEEDED:
        return [], []
    return sorted(lcr, key=lambda r: r.currency), list(assets)


def reproduce(
    expected: str | None,
    status: CalculationRunStatus,
    gap: Sequence[GapRow],
    contributions: Sequence[Contribution],
    warnings: Sequence[ModuleWarning],
    checks: Mapping[PostCheck, bool],
    *,
    lcr: Sequence[LcrRow] = (),
    assets: Sequence[HqlaAsset] = (),
    scenario: ScenarioOutputs | None = None,
) -> tuple[
    CalculationRunStatus,
    list[GapRow],
    list[Contribution],
    list[ModuleWarning],
    dict[PostCheck, bool],
    str,
]:
    """The stored outputs and `result_sha256` of a finishing run (results only when it
    SUCCEEDED). A re-run (`expected`: its original's result_sha256, D-31 §5) that does not
    reproduce it ends FAILED with a POST_CHECK_FAILED warning and no result: never a
    silent, different success. The lcr_hqla outputs (`lcr`, `assets`) are hashed too; the
    caller stores `kept_lcr(status, ...)` of the returned status. A stressed run (`scenario`
    not None) hashes its parts and applications as well, only when it SUCCEEDED."""
    keep = status in SUCCEEDED
    kept_gap = sorted(gap, key=lambda g: (g.currency, g.bucket.sequence)) if keep else []
    kept = list(contributions) if keep else []
    found = list(warnings)
    stressed = None
    if scenario is not None:
        stressed = scenario if keep else ScenarioOutputs()
    sha = sha256_for(
        HashKind.CALCULATION_RESULT,
        result_document(
            status, kept_gap, kept, found, *kept_lcr(status, lcr, assets), scenario=stressed
        ),
    )
    if expected is None:
        return status, kept_gap, kept, found, dict(checks), sha
    if sha == expected:
        return status, kept_gap, kept, found, {**checks, PostCheck.RERUN_REPRODUCED: True}, sha
    failed = CalculationRunStatus.FAILED
    found.append(
        ModuleWarning(
            W.POST_CHECK_FAILED,
            WarningSeverity.ERROR,
            "The re-run did not reproduce its original run's result_sha256: the run is FAILED "
            "and no result is stored (D-31 §5).",
            details={"check": PostCheck.RERUN_REPRODUCED.value},
        )
    )
    empty = ScenarioOutputs() if scenario is not None else None
    sha = sha256_for(
        HashKind.CALCULATION_RESULT, result_document(failed, [], [], found, scenario=empty)
    )
    return failed, [], [], found, {**checks, PostCheck.RERUN_REPRODUCED: False}, sha


def result_document(
    status: CalculationRunStatus,
    gap: Sequence[GapRow],
    contributions: Sequence[Contribution],
    warnings: Sequence[ModuleWarning],
    lcr: Sequence[LcrRow] = (),
    assets: Sequence[HqlaAsset] = (),
    *,
    scenario: ScenarioOutputs | None = None,
) -> dict[str, Any]:
    """The canonical serialisation of every stored output (D-31 §4): what `result_sha256`
    hashes. Ids are domain and rule ids (stable across runs), never row ids or times;
    warning messages are presentation and are left out. The lcr_hqla outputs are added
    only when there are some, so a maturity-ladder result hashes as it always did. A
    stressed run (D-45 §5) adds its parts and applications under `scenario`; no other run
    has that key."""
    document: dict[str, Any] = {
        "status": status.value,
        "liquidity_gap": [_gap_document(g) for g in gap],
        "contributions": [_contribution_document(c) for c in contributions],
        "warnings": [_warning_document(w) for w in warnings],
    }
    if lcr or assets:
        document["lcr"] = [_lcr_document(r) for r in lcr]
        document["hqla_assets"] = [_asset_document(a) for a in assets]
    if scenario is not None:
        document["scenario"] = {
            "parts": [_part_document(p) for p in scenario.parts],
            "applications": [_application_document(a) for a in scenario.applications],
        }
    return document


# A ladder row's direction/origin parts, and the figures derived from them.
LADDER_PARTS = {
    "inflows_contractual": ("INFLOW", "CONTRACTUAL"),
    "inflows_behavioural": ("INFLOW", "BEHAVIOURAL"),
    "outflows_contractual": ("OUTFLOW", "CONTRACTUAL"),
    "outflows_behavioural": ("OUTFLOW", "BEHAVIOURAL"),
}


def ladder_classes(
    rows: Sequence[LiquidityGapResult],
    parts: Iterable[tuple[uuid.UUID, str, str, str, int]],
    minimum: int,
    changed: Iterable[tuple[uuid.UUID, str, str, str, int]] = (),
) -> dict[uuid.UUID, dict[str, str]]:
    """D-33 minimum aggregation (CALC_MIN_AGGREGATE_COUNT, PROVISIONAL): the classification
    of every figure of every ladder row, for masking on read.

    Aggregates are `internal` (D-33 option A), but a figure built from 1 to `minimum` - 1
    contributions would show a single cash flow's (confidential) amount, e.g. with a
    reporting date one day before a due date. So:
    - a direction/origin part with 0 < count < minimum non-zero contributions takes its
      contributions' classification; a part with no non-zero contribution stays as it is
      (it is 0). Zero contributions do not count: two of them must not unmask a third;
    - inflows, outflows and the net gap take the most sensitive classification of what they
      are derived from;
    - the cumulative gap carries the most sensitive classification so far in its currency,
      since it is derived from every earlier net gap.

    A stressed run also passes `changed` (D-46 §3): per row, direction and origin, the
    distinct changed non-zero flows (counted under their bucket before and after) and their
    most sensitive classification. On top of the rule above, a part changed by 0 < count <
    minimum flows takes that classification too, whatever its value (zero included), so
    stressed - baseline cannot give back a delta the comparison masks.
    """
    counts: dict[tuple[uuid.UUID, str, str], tuple[int, str]] = {}
    for row_id, direction, origin, code, n in parts:
        known = counts.get((row_id, direction, origin))
        if known is not None:
            n, code = n + known[0], storable(most_sensitive([code, known[1]]))
        counts[(row_id, direction, origin)] = (n, code)
    few = {
        (row_id, direction, origin): code
        for row_id, direction, origin, code, n in changed
        if 0 < n < minimum
    }
    classes: dict[uuid.UUID, dict[str, str]] = {}
    carried: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: (r.currency, r.bucket_sequence)):
        mine = {}
        for name, (direction, origin) in LADDER_PARTS.items():
            n, code = counts.get((row.id, direction, origin), (0, row.classification))
            mine[name] = code if 0 < n < minimum else row.classification
            if (row.id, direction, origin) in few:
                mine[name] = storable(
                    most_sensitive([mine[name], few[(row.id, direction, origin)]])
                )
        mine["inflows"] = storable(
            most_sensitive([mine["inflows_contractual"], mine["inflows_behavioural"]])
        )
        mine["outflows"] = storable(
            most_sensitive([mine["outflows_contractual"], mine["outflows_behavioural"]])
        )
        mine["net_gap"] = storable(most_sensitive([mine["inflows"], mine["outflows"]]))
        carried[row.currency] = storable(
            most_sensitive([carried.get(row.currency, row.classification), mine["net_gap"]])
        )
        mine["cumulative_gap"] = carried[row.currency]
        classes[row.id] = mine
    return classes


def _clear_if_zero(value: Decimal | None, code: str, aggregate: str) -> str:
    """D-33 amendment: zero figures (and a NULL ratio) stay clear."""
    return aggregate if value is None or value == 0 else code


def liquidity_classes(row: LcrResult, minimum: int) -> dict[str, str]:
    """D-33 minimum aggregation (CALC_MIN_AGGREGATE_COUNT, PROVISIONAL) for one currency's
    LCR, for masking on read. The row is `internal` (an aggregate), but:
    - an HQLA level, the outflows or the inflows built from 1 to `minimum` - 1 non-zero
      contributions take the contributions' classification (a zero contribution does not
      count: two zero rows must not unmask a single balance);
    - derived figures take the most sensitive part they are built from: the HQLA total
      (its levels), the cap amount (outflows), the inflows after the cap and whether the
      cap binds (inflows before the cap and outflows), the denominator (outflows and
      inflows after the cap) and the ratio (HQLA total and denominator);
    - zero figures and a NULL ratio stay clear.
    """
    agg, contributed = row.classification, row.contribution_classification

    def part(count: int, value: Decimal) -> str:
        return _clear_if_zero(value, contributed if 0 < count < minimum else agg, agg)

    c = {
        "hqla_level1": part(row.level1_nonzero_count, row.hqla_level1),
        "hqla_level2a": part(row.level2a_nonzero_count, row.hqla_level2a),
        "hqla_level2b": part(row.level2b_nonzero_count, row.hqla_level2b),
        "outflow_balance": part(row.outflow_nonzero_count, row.outflow_balance),
        "outflows": part(row.outflow_nonzero_count, row.outflows),
        "inflows_gross": part(row.inflow_nonzero_count, row.inflows_gross),
        "inflows_before_cap": part(row.inflow_nonzero_count, row.inflows_before_cap),
    }
    levels = storable(most_sensitive([c["hqla_level1"], c["hqla_level2a"], c["hqla_level2b"]]))
    c["hqla_total"] = _clear_if_zero(row.hqla_total, levels, agg)
    c["inflow_cap_amount"] = _clear_if_zero(row.inflow_cap_amount, c["outflows"], agg)
    capped = storable(most_sensitive([c["inflows_before_cap"], c["outflows"]]))
    c["inflow_cap_applied"] = capped
    c["inflows_after_cap"] = _clear_if_zero(row.inflows_after_cap, capped, agg)
    net = storable(most_sensitive([c["outflows"], c["inflows_after_cap"]]))
    c["net_outflows"] = _clear_if_zero(row.net_outflows, net, agg)
    ratio = storable(most_sensitive([c["hqla_total"], c["net_outflows"]]))
    c["ratio"] = _clear_if_zero(row.ratio, ratio, agg)
    return c


def definitions_match(
    definitions: Sequence[MaturityBucketDefinition], parameters: Mapping[str, Any]
) -> bool:
    """The materialised buckets are exactly the pinned DAY_BUCKETS version's (D-32 §5)."""
    stored = [
        (d.sequence, d.code, d.label, d.lower_exclusive_days, d.upper_inclusive_days)
        for d in definitions
    ]
    raw = parameters.get("buckets")
    if not isinstance(raw, list):
        return False
    pinned = [
        (i, b.get("code"), b.get("label"), b.get("lower_exclusive"), b.get("upper_inclusive"))
        for i, b in enumerate(raw, start=1)
        if isinstance(b, dict)
    ]
    return stored == pinned
