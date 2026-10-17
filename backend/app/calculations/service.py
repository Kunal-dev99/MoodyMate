"""calculations.service

Dataset snapshots (BE-15.3, D-30) and the calculation engine (BE-15.4, D-31), plus the reads
of BE-15.5.

Snapshot (`POST /dataset-snapshots`, `calc:run`, audited `dataset_snapshot.create`): the
members are checked in a fixed order, and the first failing check refuses (409, audited
DENIED with its code): 404 `not_found` (absent and foreign alike), `not_projected`,
`duplicate_schema`, `reconciliation_broken` (a D-28 BROKEN member), `ambiguous_business_key`
(a business key with two versions in the snapshot). `content_sha256` hashes the members'
(schema_code, import_id, manifest_sha256), sorted by schema code, so the same members return
the existing snapshot and nothing is written.

Run (`POST /calculation-runs`, `calc:run`, `Idempotency-Key`, audited `calculation_run.create`
and `calculation_run.finish`). Refused before any run is written (409, audited DENIED):
`calculations_disabled`, `idempotency_conflict`, 404 `not_found` (snapshot) and
`module_not_available`. Then:

1. Pins: the ACTIVE version of every rule the module reads, written once as a
   content-addressed rule set (D-32 §4); the ACTIVE baseline assumption set; the module
   version; the engine version; the snapshot's members (each import manifest, projection,
   schema and mapping version, counts and reconciliation outcome).
2. The run is written RUNNING with its manifest (`manifest_sha256` over the pinned content
   only) and one `calculation_input_ref` per pin, and committed: a crash leaves evidence.
3. The module runs as a pure function over frozen inputs, sorted by source row, under its
   declared Decimal context (no AI, no I/O inside it).
4. Post-checks, recomputed independently of the module: every in-scope cash flow is bucketed
   once or excluded with a warning code; every contribution lands in a pinned bucket; the
   bucket totals equal the sum of their contributions; the cumulative gap is the running
   sum of the net gaps.
5. The run ends SUCCEEDED, SUCCEEDED_WITH_WARNINGS, FAILED (a post-check or the engine
   failed: warnings only, no results) or BLOCKED (the input contract or the pinned
   configuration was not met), with `result_sha256` over the canonical outputs. It never
   moves again (DB triggers).

Decimal only; every hash is over canonical JSON (sorted keys, exact decimal text).
"""

import contextlib
import logging
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from types import MappingProxyType
from typing import Any, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.constants import (
    AGGREGATE_CLASSIFICATION,
    CREATE_RUN,
    CREATE_SNAPSHOT,
    ENGINE_VERSION,
    FINISH_RUN,
    MANIFEST_VERSION,
    CalculationRunStatus,
    CalculationWarningCode,
    InputRefType,
    IntermediateCode,
    LcrWarningCode,
    PostCheck,
    RunRefusal,
    SnapshotRefusal,
    WarningSeverity,
)
from app.calculations.models import (
    CalculationInputRef,
    CalculationRun,
    CalculationWarning,
    DatasetSnapshot,
    DatasetSnapshotMember,
    HqlaAssetResult,
    IntermediateValue,
    LcrResult,
    LiquidityGapResult,
)
from app.calculations.modules import registered
from app.calculations.modules.base import (
    CashFlowInput,
    Contribution,
    GapRow,
    HqlaAsset,
    InstrumentInput,
    LcrRow,
    ModuleInputs,
    ModuleResult,
    ModuleWarning,
    PinnedRule,
    PositionInput,
    RegisteredModule,
    divide,
)
from app.calculations.repository import CalculationRepository, SnapshotRepository
from app.core.correlation import get_correlation_id
from app.core.decimals import DeclaredRounding, decimal_text, exact_context
from app.core.errors import ApiError
from app.core.principal import Principal
from app.domain.models import CashflowEvent, DomainProjection, InstrumentVersion, PositionVersion
from app.domain.service import DomainService, SchemaPins
from app.governance.constants import AuditOutcome, ClassificationCode
from app.governance.service import audit_by, record_denial
from app.ingestion.models import ImportJob, ImportManifest
from app.ingestion.service import ImportService
from app.reconciliation.constants import ReconciliationOutcome
from app.reconciliation.models import ReconciliationRun
from app.reconciliation.service import ReconciliationService
from app.rules.constants import (
    ILLUSTRATIVE_STATEMENT,
    LCR_HQLA,
    LCR_INFLOW_HORIZON_DAYS,
    MATURITY_BUCKETS,
    MATURITY_LADDER,
    HqlaLevel,
    ImplementationStatus,
)
from app.rules.models import AssumptionSet, MaturityBucketDefinition, RuleDefinition, RuleVersion
from app.rules.service import ModuleView, RulesService, canonical_sha256, exact_json

logger = logging.getLogger("app.calculations")
W = CalculationWarningCode
SUCCEEDED = frozenset(
    {CalculationRunStatus.SUCCEEDED, CalculationRunStatus.SUCCEEDED_WITH_WARNINGS}
)
_CLASS_RANK = {c.value: i for i, c in enumerate(ClassificationCode)}
PUBLIC = ClassificationCode.PUBLIC.value


def _public(details: Mapping[str, Any]) -> dict[str, str]:
    return dict.fromkeys(details, PUBLIC)


# --- pure helpers (no I/O; Decimal only) -------------------------------------------------------


def snapshot_sha256(members: Iterable[tuple[str, uuid.UUID, str]]) -> str:
    """A snapshot's identity (D-30 §1): the canonical JSON of its members' (schema_code,
    import_id, manifest_sha256), sorted by schema code."""
    entries = [
        {"schema_code": code, "import_id": str(import_id), "manifest_sha256": sha}
        for code, import_id, sha in members
    ]
    return canonical_sha256(sorted(entries, key=lambda e: (e["schema_code"], e["import_id"])))


def most_sensitive(codes: Iterable[str]) -> str:
    """The most sensitive classification of these codes. A code that is not a known one
    counts as `restricted`, the most sensitive known code (fail closed: it is stored, so it
    must be a real classification)."""
    known = [c if c in _CLASS_RANK else ClassificationCode.RESTRICTED.value for c in codes]
    return max(known, key=lambda c: _CLASS_RANK[c], default=ClassificationCode.RESTRICTED.value)


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
    caller stores `kept_lcr(status, ...)` of the returned status."""
    keep = status in SUCCEEDED
    kept_gap = sorted(gap, key=lambda g: (g.currency, g.bucket.sequence)) if keep else []
    kept = list(contributions) if keep else []
    found = list(warnings)
    sha = canonical_sha256(
        result_document(status, kept_gap, kept, found, *kept_lcr(status, lcr, assets))
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
    sha = canonical_sha256(result_document(failed, [], [], found))
    return failed, [], [], found, {**checks, PostCheck.RERUN_REPRODUCED: False}, sha


def result_document(
    status: CalculationRunStatus,
    gap: Sequence[GapRow],
    contributions: Sequence[Contribution],
    warnings: Sequence[ModuleWarning],
    lcr: Sequence[LcrRow] = (),
    assets: Sequence[HqlaAsset] = (),
) -> dict[str, Any]:
    """The canonical serialisation of every stored output (D-31 §4): what `result_sha256`
    hashes. Ids are domain and rule ids (stable across runs), never row ids or times;
    warning messages are presentation and are left out. The lcr_hqla outputs are added
    only when there are some, so a maturity-ladder result hashes as it always did."""
    document: dict[str, Any] = {
        "status": status.value,
        "liquidity_gap": [_gap_document(g) for g in gap],
        "contributions": [_contribution_document(c) for c in contributions],
        "warnings": [_warning_document(w) for w in warnings],
    }
    if lcr or assets:
        document["lcr"] = [_lcr_document(r) for r in lcr]
        document["hqla_assets"] = [_asset_document(a) for a in assets]
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
    """
    counts: dict[tuple[uuid.UUID, str, str], tuple[int, str]] = {}
    for row_id, direction, origin, code, n in parts:
        known = counts.get((row_id, direction, origin))
        if known is not None:
            n, code = n + known[0], most_sensitive([code, known[1]])
        counts[(row_id, direction, origin)] = (n, code)
    classes: dict[uuid.UUID, dict[str, str]] = {}
    carried: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: (r.currency, r.bucket_sequence)):
        mine = {}
        for name, (direction, origin) in LADDER_PARTS.items():
            n, code = counts.get((row.id, direction, origin), (0, row.classification))
            mine[name] = code if 0 < n < minimum else row.classification
        mine["inflows"] = most_sensitive([mine["inflows_contractual"], mine["inflows_behavioural"]])
        mine["outflows"] = most_sensitive(
            [mine["outflows_contractual"], mine["outflows_behavioural"]]
        )
        mine["net_gap"] = most_sensitive([mine["inflows"], mine["outflows"]])
        carried[row.currency] = most_sensitive(
            [carried.get(row.currency, row.classification), mine["net_gap"]]
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
    levels = most_sensitive([c["hqla_level1"], c["hqla_level2a"], c["hqla_level2b"]])
    c["hqla_total"] = _clear_if_zero(row.hqla_total, levels, agg)
    c["inflow_cap_amount"] = _clear_if_zero(row.inflow_cap_amount, c["outflows"], agg)
    capped = most_sensitive([c["inflows_before_cap"], c["outflows"]])
    c["inflow_cap_applied"] = capped
    c["inflows_after_cap"] = _clear_if_zero(row.inflows_after_cap, capped, agg)
    net = most_sensitive([c["outflows"], c["inflows_after_cap"]])
    c["net_outflows"] = _clear_if_zero(row.net_outflows, net, agg)
    ratio = most_sensitive([c["hqla_total"], c["net_outflows"]])
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


# --- views returned to the router ------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotView:
    snapshot: DatasetSnapshot
    members: list[DatasetSnapshotMember]  # by schema code
    source_codes: Mapping[uuid.UUID, str]
    schema_versions: Mapping[uuid.UUID, int]


@dataclass(frozen=True)
class RunSpec:
    dataset_snapshot_id: uuid.UUID
    module_version_id: uuid.UUID
    reporting_date: date

    def sha256(self) -> str:
        """What a replay of the Idempotency-Key must repeat."""
        return canonical_sha256(
            {
                "dataset_snapshot_id": str(self.dataset_snapshot_id),
                "module_version_id": str(self.module_version_id),
                "reporting_date": self.reporting_date.isoformat(),
            }
        )


@dataclass(frozen=True)
class RerunSpec:
    """A re-run of a run on its exact pins (BE-16.3)."""

    rerun_of: uuid.UUID

    def sha256(self) -> str:
        return canonical_sha256({"rerun_of": str(self.rerun_of)})


@dataclass(frozen=True)
class RunModule:
    module_id: uuid.UUID
    module_version_id: uuid.UUID
    code: str
    name: str
    version: int


@dataclass(frozen=True)
class RunRow:
    run: CalculationRun
    module: RunModule
    input_refs: Sequence[CalculationInputRef] = ()
    warnings: Sequence[CalculationWarning] = ()


@dataclass(frozen=True)
class InstrumentRef:
    instrument_id: uuid.UUID
    business_key: str


@dataclass(frozen=True)
class ContributionRow:
    value: IntermediateValue
    bucket_sequence: int
    event: CashflowEvent
    instrument: InstrumentRef | None
    classes: Mapping[str, str]  # cash-flow schema field -> classification (pinned version)
    instrument_classes: Mapping[str, str]


@dataclass(frozen=True)
class ResultsView:
    run: CalculationRun
    items: list[ContributionRow]
    total: int


@dataclass(frozen=True)
class AssetRow:
    value: HqlaAssetResult
    position: PositionVersion
    position_key: str
    instrument: InstrumentVersion | None
    instrument_key: str | None
    position_classes: Mapping[str, str]  # position schema field -> classification (pinned)
    instrument_classes: Mapping[str, str]


@dataclass(frozen=True)
class LiquidityView:
    row: RunRow
    horizon_days: int | None
    rows: list[LcrResult]
    assets: list[AssetRow]
    classes: Mapping[uuid.UUID, Mapping[str, str]]  # LCR row id -> figure -> class (D-33)
    min_aggregate_count: int


@dataclass(frozen=True)
class LadderView:
    row: RunRow
    bucket_rule_version_id: uuid.UUID
    buckets: list[MaturityBucketDefinition]
    rows: list[LiquidityGapResult]
    classes: Mapping[uuid.UUID, Mapping[str, str]]  # row id -> figure -> classification (D-33)
    min_aggregate_count: int


@dataclass(frozen=True)
class _Pinned:
    """What a run pins before its manifest is written: a new run's ACTIVE versions, or a
    re-run's original pins (`rerun_of`)."""

    snapshot: DatasetSnapshot
    view: ModuleView
    implementation: RegisteredModule
    definitions: dict[str, RuleDefinition]  # rule code -> definition
    versions: dict[str, RuleVersion]  # rule code -> pinned version
    assumption_set: AssumptionSet
    reporting_date: date
    rerun_of: CalculationRun | None = None


@dataclass
class _Run:
    """What the engine carries from the pinning transaction into execution (plain values:
    nothing here is read lazily after a commit or a rollback)."""

    run_id: uuid.UUID
    reporting_date: date
    module: ModuleView
    implementation: RegisteredModule
    members: dict[str, DatasetSnapshotMember]  # schema code -> member
    versions: dict[str, RuleVersion]  # rule code -> pinned version
    pins: SchemaPins
    # A re-run: its original's result_sha256, which it must reproduce (D-31 §5).
    expected_result_sha256: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    events: dict[uuid.UUID, CashflowEvent] = field(default_factory=dict)
    positions: dict[uuid.UUID, PositionVersion] = field(default_factory=dict)


# --- dataset snapshots (BE-15.3) ------------------------------------------------------------


class SnapshotService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = SnapshotRepository(session, principal.scope)
        self.imports = ImportService(session, principal)
        self.domain = DomainService(session, principal)
        self.reconciliation = ReconciliationService(session, principal)

    async def _refuse(self, error: ApiError, resource_id: uuid.UUID | None = None) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await record_denial(
            self.session,
            self.principal,
            CREATE_SNAPSHOT,
            "dataset_snapshot",
            str(resource_id) if resource_id else None,
            error.code,
        )
        raise error

    async def create(
        self, import_ids: Sequence[uuid.UUID], enabled: bool
    ) -> tuple[SnapshotView, bool]:
        """The snapshot of these imports and whether it was written now (False: the same
        members already form one, returned as is)."""
        if not enabled:
            await self._refuse(
                ApiError(
                    409,
                    SnapshotRefusal.CALCULATIONS_DISABLED,
                    "Calculations are switched off in this environment; existing snapshots "
                    "and runs stay readable.",
                )
            )
        jobs: list[ImportJob] = []
        for import_id in import_ids:
            job = await self.imports.find(import_id)
            if job is None:
                await self._refuse(ApiError(404, "not_found", "Import not found."), import_id)
            jobs.append(job)
        found: list[tuple[ImportJob, DomainProjection, ImportManifest]] = []
        for job in jobs:
            projection = await self.domain.projection_of(job)
            manifest = await self.imports.manifest(job)
            if projection is None or manifest is None:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.NOT_PROJECTED,
                        "Only projected imports can join a snapshot: project the import into "
                        "the domain first (D-29).",
                        {"import_id": str(job.id)},
                    ),
                    job.id,
                )
            found.append((job, projection, manifest))
        seen: dict[str, uuid.UUID] = {}
        for job, projection, _ in found:
            if projection.schema_code in seen:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.DUPLICATE_SCHEMA,
                        "A snapshot takes at most one import per schema code (D-30 §1).",
                        {
                            "schema_code": projection.schema_code,
                            "import_ids": [str(seen[projection.schema_code]), str(job.id)],
                        },
                    ),
                    job.id,
                )
            seen[projection.schema_code] = job.id
        reconciled: dict[uuid.UUID, ReconciliationRun] = {}
        for job, _, _ in found:
            run = await self.reconciliation.for_import(job)
            if run is None or run.outcome == ReconciliationOutcome.BROKEN:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.RECONCILIATION_BROKEN,
                        "The import's reconciliation is BROKEN (or missing): a gap that no held "
                        "row explains. It cannot be calculated on (D-28, D-30 §3).",
                        {"import_id": str(job.id), "outcome": run.outcome if run else None},
                    ),
                    job.id,
                )
            reconciled[job.id] = run
        for job, projection, _ in found:
            ambiguous = await self.domain.ambiguous_keys(projection)
            if ambiguous:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.AMBIGUOUS_BUSINESS_KEY,
                        "A business key resolves to more than one version inside the snapshot "
                        "(D-30 §3).",
                        {
                            "import_id": str(job.id),
                            "schema_code": projection.schema_code,
                            "ambiguous_keys": ambiguous,
                        },
                    ),
                    job.id,
                )

        sha = snapshot_sha256((p.schema_code, j.id, m.manifest_sha256) for j, p, m in found)
        await self.repo.lock(f"dataset_snapshot:{sha}")
        existing = await self.repo.by_sha(sha)
        if existing is not None:
            return await self._view(existing), False

        actor, tenant = self.principal.actor, self.principal.tenant_id
        snapshot = DatasetSnapshot(
            id=uuid.uuid4(),
            tenant_id=tenant,
            content_sha256=sha,
            member_count=len(found),
            created_by=actor,
        )
        self.repo.add(snapshot)
        await self.repo.flush()
        await self.repo.insert_rows(
            DatasetSnapshotMember,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "dataset_snapshot_id": snapshot.id,
                    "import_job_id": job.id,
                    "import_manifest_id": manifest.id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "domain_projection_id": projection.id,
                    "projector_version": projection.projector_version,
                    "schema_code": projection.schema_code,
                    "schema_version_id": manifest.schema_version_id,
                    "mapping_version_id": manifest.mapping_version_id,
                    "source_system_id": job.source_system_id,
                    "reconciliation_run_id": reconciled[job.id].id,
                    "reconciliation_outcome": reconciled[job.id].outcome,
                    "accepted_count": manifest.accepted_count,
                    "quarantined_count": manifest.quarantined_count,
                    "rejected_count": manifest.rejected_count,
                    "created_by": actor,
                }
                for job, projection, manifest in found
            ],
        )
        details: dict[str, Any] = {
            "dataset_snapshot_id": str(snapshot.id),
            "member_count": len(found),
            "schema_codes": sorted(seen),
            "content_sha256": sha,
        }
        audit_by(
            self.session,
            self.principal,
            CREATE_SNAPSHOT,
            "dataset_snapshot",
            resource_id=str(snapshot.id),
            details=details,
            details_classification=_public(details),
        )
        await self.session.commit()
        return await self._view(snapshot), True

    async def _views(self, snapshots: list[DatasetSnapshot]) -> list[SnapshotView]:
        members: dict[uuid.UUID, list[DatasetSnapshotMember]] = {}
        for m in await self.repo.members([s.id for s in snapshots]):
            members.setdefault(m.dataset_snapshot_id, []).append(m)
        sources = {m.source_system_id for ms in members.values() for m in ms}
        codes = await self.imports.source_codes(sources)
        pins = await self.domain.schema_pins()
        return [SnapshotView(s, members.get(s.id, []), codes, pins.versions) for s in snapshots]

    async def _view(self, snapshot: DatasetSnapshot) -> SnapshotView:
        [view] = await self._views([snapshot])
        return view

    async def page(self, limit: int, offset: int) -> tuple[list[SnapshotView], int]:
        snapshots, total = await self.repo.newest(limit, offset)
        return await self._views(snapshots), total

    async def get(self, snapshot_id: uuid.UUID) -> SnapshotView:
        """404 alike for an absent and a foreign snapshot (D-04)."""
        snapshot = await self.repo.get(snapshot_id)
        if snapshot is None:
            raise ApiError(404, "not_found", "Dataset snapshot not found.")
        return await self._view(snapshot)

    async def found(self, snapshot_id: uuid.UUID) -> DatasetSnapshot | None:
        return await self.repo.get(snapshot_id)

    async def members(self, snapshot_id: uuid.UUID) -> list[DatasetSnapshotMember]:
        return await self.repo.members([snapshot_id])


# --- calculation runs (BE-15.4, BE-15.5) ------------------------------------------------------

RUN_NOT_FOUND = "Calculation run not found."


class CalculationService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = CalculationRepository(session, principal.scope)
        self.snapshots = SnapshotService(session, principal)
        self.rules = RulesService(session, principal)
        self.domain = DomainService(session, principal)

    async def _refuse(self, error: ApiError, resource_id: uuid.UUID | None = None) -> NoReturn:
        await record_denial(
            self.session,
            self.principal,
            CREATE_RUN,
            "calculation_run",
            str(resource_id) if resource_id else None,
            error.code,
        )
        raise error

    def _unavailable(self, module_version_id: uuid.UUID, reason: str) -> ApiError:
        return ApiError(
            409,
            RunRefusal.MODULE_NOT_AVAILABLE,
            "This module version cannot run: it is not in the registry, is PLANNED, is not "
            "implemented in this release, or lacks an ACTIVE rule version or assumption set.",
            {"module_version_id": str(module_version_id), "reason": reason},
        )

    # --- create (BE-15.4) ---------------------------------------------------------------

    async def _new_pins(self, spec: RunSpec) -> _Pinned:
        """A new run pins the snapshot and the ACTIVE rule versions (D-31 §4)."""
        snapshot = await self.snapshots.found(spec.dataset_snapshot_id)
        if snapshot is None:
            await self._refuse(
                ApiError(404, "not_found", "Dataset snapshot not found."), spec.dataset_snapshot_id
            )
        view = await self.rules.module_view(spec.module_version_id)
        if view is None:
            await self._refuse(self._unavailable(spec.module_version_id, "not_found"))
        implementation = registered(view.module.code, view.version.version)
        if view.version.implementation_status != ImplementationStatus.AVAILABLE.value:
            reason = "planned"
        elif implementation is None:
            reason = "not_implemented"
        elif not view.runnable:
            reason = "configuration_incomplete"
        else:
            reason = ""
        if reason or implementation is None or view.assumption_set is None:
            await self._refuse(self._unavailable(view.version.id, reason or "not_implemented"))
        return _Pinned(
            snapshot,
            view,
            implementation,
            {r.definition.code: r.definition for r in view.rules},
            {r.definition.code: r.active for r in view.rules if r.active is not None},
            view.assumption_set,
            spec.reporting_date,
        )

    def _not_reproducible(self, run_id: uuid.UUID, reason: str) -> ApiError:
        return ApiError(
            409,
            RunRefusal.RERUN_NOT_REPRODUCIBLE,
            "This run cannot be re-run on its pins in this release (its engine or manifest "
            "version changed, or its pins no longer resolve to the same content).",
            {"run_id": str(run_id), "reason": reason},
        )

    async def _rerun_pins(self, spec: RerunSpec) -> _Pinned:
        """A re-run pins exactly what its original pinned (BE-16.3): the same snapshot,
        reporting date, module version, rule set (even SUPERSEDED versions) and assumption
        set, on the same engine version."""
        original = await self.repo.fresh(spec.rerun_of)
        if original is None:
            await self._refuse(ApiError(404, "not_found", RUN_NOT_FOUND), spec.rerun_of)
        if (
            original.engine_version != ENGINE_VERSION
            or original.manifest.get("manifest_version") != MANIFEST_VERSION
        ):
            await self._refuse(self._not_reproducible(original.id, "engine_changed"), original.id)
        snapshot = await self.snapshots.found(original.dataset_snapshot_id)
        view = await self.rules.module_view(original.module_version_id)
        implementation = (
            registered(view.module.code, view.version.version) if view is not None else None
        )
        if (
            view is None
            or implementation is None
            or view.version.implementation_status != ImplementationStatus.AVAILABLE.value
        ):
            await self._refuse(self._unavailable(original.module_version_id, "not_implemented"))
        pinned = await self.rules.pinned(original.rule_set_id)
        assumption_set = await self.rules.assumption_set_by_id(original.assumption_set_id)
        if snapshot is None or assumption_set is None or not pinned:
            await self._refuse(self._not_reproducible(original.id, "pins_changed"), original.id)
        return _Pinned(
            snapshot,
            view,
            implementation,
            {d.code: d for d, _ in pinned},
            {d.code: v for d, v in pinned},
            assumption_set,
            original.reporting_date,
            original,
        )

    async def create(
        self,
        spec: RunSpec | RerunSpec,
        idempotency_key: str,
        enabled: bool,
        stale_seconds: int = 900,
    ) -> tuple[RunRow, bool]:
        """Pin, run and post-check the module; the finished run and whether it ran now
        (False: a replay of the key, the original run returned). A replay that finds its run
        still RUNNING after `stale_seconds` (CALC_RUN_STALE_SECONDS) ends it FAILED with
        ENGINE_ERROR first: its engine is presumed gone, and nothing else would end it."""
        if not enabled:
            await self._refuse(
                ApiError(
                    409,
                    RunRefusal.CALCULATIONS_DISABLED,
                    "Calculations are switched off in this environment; existing snapshots "
                    "and runs stay readable.",
                )
            )
        actor = self.principal.actor
        fingerprint = spec.sha256()
        await self.repo.lock(f"calculation_run:{actor}:{idempotency_key}")
        existing = await self.repo.by_key(actor, idempotency_key)
        if existing is not None:
            if existing.request_sha256 != fingerprint:
                await self._refuse(
                    ApiError(
                        409,
                        RunRefusal.IDEMPOTENCY_CONFLICT,
                        "This Idempotency-Key was already used for a different run request.",
                        {"run_id": str(existing.id)},
                    ),
                    existing.id,
                )
            if existing.status == CalculationRunStatus.RUNNING.value and await self.repo.stale(
                existing.id, stale_seconds
            ):
                await self._fail(existing.id, {}, "stale_run")
            return await self.get(existing.id), False

        pinned = (
            await self._rerun_pins(spec)
            if isinstance(spec, RerunSpec)
            else await self._new_pins(spec)
        )
        snapshot, view, assumption_set = pinned.snapshot, pinned.view, pinned.assumption_set
        members = {m.schema_code: m for m in await self.snapshots.members(snapshot.id)}
        versions = pinned.versions
        rule_set = await self.rules.rule_set_for(list(versions.values()))
        pins = await self.domain.schema_pins()
        manifest = self._manifest(
            pinned.reporting_date,
            view,
            snapshot,
            members,
            rule_set.id,
            rule_set.content_sha256,
            pinned.definitions,
            versions,
            assumption_set,
            pins,
        )
        manifest_sha256 = canonical_sha256(manifest)
        original = pinned.rerun_of
        if original is not None and (
            manifest_sha256 != original.manifest_sha256 or rule_set.id != original.rule_set_id
        ):
            await self._refuse(self._not_reproducible(original.id, "pins_changed"), original.id)
        run = CalculationRun(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            dataset_snapshot_id=snapshot.id,
            module_version_id=view.version.id,
            rule_set_id=rule_set.id,
            assumption_set_id=assumption_set.id,
            reporting_date=pinned.reporting_date,
            engine_version=ENGINE_VERSION,
            status=CalculationRunStatus.RUNNING.value,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            warning_count=0,
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
            rerun_of_run_id=original.id if original is not None else None,
            correlation_id=get_correlation_id(),
            created_by=actor,
        )
        self.repo.add(run)
        await self.repo.flush()
        refs = self._input_refs(
            snapshot,
            members,
            view,
            rule_set.id,
            rule_set.content_sha256,
            versions,
            assumption_set,
            pins,
        )
        await self.repo.insert_rows(
            CalculationInputRef,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": self.principal.tenant_id,
                    "calculation_run_id": run.id,
                    "sequence": position,
                    "ref_type": ref_type.value,
                    "ref_id": ref_id,
                    "label": label,
                    "sha256": sha,
                    "created_by": actor,
                }
                for position, (ref_type, ref_id, label, sha) in enumerate(refs, start=1)
            ],
        )
        details: dict[str, Any] = {
            "run_id": str(run.id),
            "module_code": view.module.code,
            "module_version": view.version.version,
            "dataset_snapshot_id": str(snapshot.id),
            "rule_set_id": str(rule_set.id),
            "assumption_set_id": str(assumption_set.id),
            "engine_version": ENGINE_VERSION,
            "manifest_sha256": manifest_sha256,
            "rerun_of_run_id": str(original.id) if original is not None else None,
        }
        audit_by(
            self.session,
            self.principal,
            CREATE_RUN,
            "calculation_run",
            resource_id=str(run.id),
            details=details,
            details_classification=_public(details),
        )
        # D-31 §3: the run exists, RUNNING, before anything is computed.
        await self.session.commit()

        state = _Run(
            run.id,
            pinned.reporting_date,
            view,
            pinned.implementation,
            members,
            versions,
            pins,
            original.result_sha256 if original is not None else None,
        )
        try:
            await self._execute(state)
        except BaseException as exc:  # the engine's own failure: recorded, never silent
            await self._record_failure(state.run_id, state.counts, exc)
            if not isinstance(exc, Exception):
                raise  # cancellation and interpreter exits go on, once the run is ended
        return await self.get(state.run_id), True

    async def _record_failure(
        self, run_id: uuid.UUID, counts: Mapping[str, int], exc: BaseException
    ) -> None:
        """End the run FAILED with ENGINE_ERROR in a fresh session: the request's own session
        may be mid-transaction or unusable. If even that fails, the run stays RUNNING and the
        stale-run rule ends it on the next replay of its key."""
        # The type only: an exception's message or traceback could carry values.
        error = type(exc).__name__
        logger.warning("calculation_engine_error", extra={"run_id": str(run_id), "error": error})
        with contextlib.suppress(Exception):
            await self.session.rollback()
        try:
            async with AsyncSession(self.session.bind) as fresh:
                await CalculationService(fresh, self.principal)._fail(run_id, counts, error)
        except Exception as again:
            logger.warning(
                "calculation_engine_error_unrecorded",
                extra={"run_id": str(run_id), "error": type(again).__name__},
            )
            if isinstance(exc, Exception):
                raise exc from None

    def _manifest(
        self,
        reporting_date: date,
        view: ModuleView,
        snapshot: DatasetSnapshot,
        members: Mapping[str, DatasetSnapshotMember],
        rule_set_id: uuid.UUID,
        rule_set_sha256: str,
        definitions: Mapping[str, RuleDefinition],
        versions: Mapping[str, RuleVersion],
        assumption_set: AssumptionSet,
        pins: SchemaPins,
    ) -> dict[str, Any]:
        """Every pin (D-31 §4), pinned content only: no run id, actor, status or time."""
        flags = [(view.version.illustrative, view.version.provisional)]
        flags += [(v.illustrative, v.provisional) for v in versions.values()]
        flags.append((assumption_set.illustrative, assumption_set.provisional))
        return {
            "manifest_version": MANIFEST_VERSION,
            "engine_version": ENGINE_VERSION,
            "reporting_date": reporting_date.isoformat(),
            "module": {
                "module_id": str(view.module.id),
                "module_version_id": str(view.version.id),
                "code": view.module.code,
                "version": view.version.version,
                "engine_version": view.version.engine_version,
            },
            "dataset_snapshot": {
                "dataset_snapshot_id": str(snapshot.id),
                "content_sha256": snapshot.content_sha256,
                "members": [
                    {
                        "schema_code": code,
                        "import_id": str(m.import_job_id),
                        "import_manifest_id": str(m.import_manifest_id),
                        "manifest_sha256": m.manifest_sha256,
                        "projection_id": str(m.domain_projection_id),
                        "projector_version": m.projector_version,
                        "schema_version_id": str(m.schema_version_id),
                        "schema_version": pins.versions.get(m.schema_version_id),
                        "mapping_version_id": str(m.mapping_version_id),
                        "accepted_count": m.accepted_count,
                        "quarantined_count": m.quarantined_count,
                        "rejected_count": m.rejected_count,
                        "reconciliation_outcome": m.reconciliation_outcome,
                    }
                    for code, m in sorted(members.items())
                ],
            },
            "rule_set": {
                "rule_set_id": str(rule_set_id),
                "content_sha256": rule_set_sha256,
                "members": [
                    {
                        "rule_id": str(definitions[code].id),
                        "rule_code": code,
                        "rule_version_id": str(v.id),
                        "version": v.version,
                        "parameters_sha256": v.parameters_sha256,
                        "parameters": v.parameters,
                    }
                    for code, v in sorted(versions.items())
                ],
            },
            "assumption_set": {
                "assumption_set_id": str(assumption_set.id),
                "code": assumption_set.code,
                "version": assumption_set.version,
                "content_sha256": assumption_set.content_sha256,
            },
            "labels": {
                "illustrative": any(i for i, _ in flags),
                "provisional": any(p for _, p in flags),
                "statement": ILLUSTRATIVE_STATEMENT,
            },
        }

    def _input_refs(
        self,
        snapshot: DatasetSnapshot,
        members: Mapping[str, DatasetSnapshotMember],
        view: ModuleView,
        rule_set_id: uuid.UUID,
        rule_set_sha256: str,
        versions: Mapping[str, RuleVersion],
        assumption_set: AssumptionSet,
        pins: SchemaPins,
    ) -> list[tuple[InputRefType, uuid.UUID | None, str, str | None]]:
        """One row per pin (D-31 §4), in a fixed order."""
        refs: list[tuple[InputRefType, uuid.UUID | None, str, str | None]] = [
            (
                InputRefType.DATASET_SNAPSHOT,
                snapshot.id,
                "dataset snapshot",
                snapshot.content_sha256,
            )
        ]
        for code, m in sorted(members.items()):
            number = pins.versions.get(m.schema_version_id)
            refs += [
                (
                    InputRefType.IMPORT_MANIFEST,
                    m.import_manifest_id,
                    f"{code} import manifest",
                    m.manifest_sha256,
                ),
                (
                    InputRefType.DOMAIN_PROJECTION,
                    m.domain_projection_id,
                    f"{code} projection (projector v{m.projector_version})",
                    None,
                ),
                (InputRefType.SCHEMA_VERSION, m.schema_version_id, f"{code} v{number}", None),
                (
                    InputRefType.MAPPING_VERSION,
                    m.mapping_version_id,
                    f"{code} mapping version",
                    None,
                ),
            ]
        refs += [
            (
                InputRefType.MODULE_VERSION,
                view.version.id,
                f"{view.module.code} v{view.version.version}",
                None,
            ),
            (InputRefType.ENGINE, None, f"engine {ENGINE_VERSION}", None),
            (InputRefType.RULE_SET, rule_set_id, "rule set", rule_set_sha256),
            *(
                (InputRefType.RULE_VERSION, v.id, f"{code} v{v.version}", v.parameters_sha256)
                for code, v in sorted(versions.items())
            ),
            (
                InputRefType.ASSUMPTION_SET,
                assumption_set.id,
                f"{assumption_set.code} v{assumption_set.version}",
                assumption_set.content_sha256,
            ),
        ]
        return refs

    # --- the engine ----------------------------------------------------------------------

    async def _inputs(self, state: _Run) -> ModuleInputs:
        """The snapshot's rows as frozen typed inputs, in source row order."""
        flows: list[CashFlowInput] = []
        cash = state.members.get("cashflow_event")
        if cash is not None:
            events = await self.domain.import_cash_flows(cash.import_job_id)
            state.events = {e.id: e for e in events}
            flows = [
                CashFlowInput(e.id, e.instrument_key, e.due_date, e.amount, e.currency, e.origin)
                for e in events
            ]
        instruments: list[InstrumentInput] = []
        held = state.members.get("instrument")
        if held is not None:
            instruments = [
                InstrumentInput(v.id, v.instrument_id, key, v.instrument_type_code)
                for v, key in await self.domain.import_instruments(held.import_job_id)
            ]
        state.counts = {"cashflow_event": len(flows), "instrument": len(instruments)}
        positions: list[PositionInput] = []
        held_positions = state.members.get("position")
        if held_positions is not None and "position" in state.implementation.spec.fields_read():
            rows = await self.domain.import_positions(held_positions.import_job_id)
            state.positions = {v.id: v for v, _ in rows}
            positions = [
                PositionInput(v.id, v.position_id, key, v.instrument_key, v.balance, v.currency)
                for v, key in rows
            ]
            state.counts["position"] = len(positions)
        return ModuleInputs(
            state.reporting_date, tuple(flows), tuple(instruments), tuple(positions)
        )

    async def _execute(self, state: _Run) -> None:
        """Run the module on the pinned inputs, post-check it and end the run (one
        transaction)."""
        spec = state.implementation.spec
        missing = [c for c in spec.required_schemas if c not in state.members]
        if missing:
            warnings = [
                ModuleWarning(
                    W.INPUT_SCHEMA_MISSING,
                    WarningSeverity.ERROR,
                    "A schema the module requires is not in the snapshot.",
                    details={"schema_code": code},
                )
                for code in missing
            ]
            await self._end(state, CalculationRunStatus.BLOCKED, ModuleResult(), warnings, {})
            return
        if not all(exact_json(v.parameters) for v in state.versions.values()):
            warning = ModuleWarning(
                W.PARAMETERS_INVALID,
                WarningSeverity.ERROR,
                "A pinned rule parameter holds an inexact JSON number; decimals are exact text "
                "(D-32 §1).",
            )
            await self._end(state, CalculationRunStatus.BLOCKED, ModuleResult(), [warning], {})
            return
        inputs = await self._inputs(state)
        parameters = {
            code: PinnedRule(v.id, MappingProxyType(dict(v.parameters)))
            for code, v in state.versions.items()
        }
        with localcontext(state.implementation.context()):
            result = state.implementation.run(inputs, parameters)
        if result.blocked:
            await self._end(state, CalculationRunStatus.BLOCKED, result, list(result.warnings), {})
            return
        buckets: list[MaturityBucketDefinition] = []
        bucket_version = state.versions.get(MATURITY_BUCKETS)
        if bucket_version is not None:
            buckets = await self.rules.bucket_definitions(bucket_version.id)
            if not definitions_match(buckets, bucket_version.parameters):
                warning = ModuleWarning(
                    W.BUCKET_DEFINITIONS_MISMATCH,
                    WarningSeverity.ERROR,
                    "The pinned bucket rule version has no matching materialised bucket "
                    "definitions (D-32 §5).",
                    details={"rule_version_id": str(bucket_version.id)},
                )
                await self._end(state, CalculationRunStatus.BLOCKED, result, [warning], {})
                return
        with localcontext(exact_context()):
            if "lcr_result" in spec.output_contract["results"]:
                checks = lcr_post_checks(inputs, result, state.implementation.rounding)
            else:
                checks = post_checks(inputs, result, buckets)
        warnings = list(result.warnings)
        if not all(checks.values()):
            warnings += [
                ModuleWarning(
                    W.POST_CHECK_FAILED,
                    WarningSeverity.ERROR,
                    "A post-run check failed: the run is FAILED and no result is stored.",
                    details={"check": check.value},
                )
                for check, passed in checks.items()
                if not passed
            ]
            status = CalculationRunStatus.FAILED
        elif any(w.severity != WarningSeverity.INFO for w in warnings) or any(
            r.warnings for r in result.lcr
        ):
            status = CalculationRunStatus.SUCCEEDED_WITH_WARNINGS
        else:
            status = CalculationRunStatus.SUCCEEDED
        await self._end(state, status, result, warnings, checks, buckets)

    async def _end(
        self,
        state: _Run,
        status: CalculationRunStatus,
        result: ModuleResult,
        warnings: Sequence[ModuleWarning],
        checks: Mapping[PostCheck, bool],
        buckets: Sequence[MaturityBucketDefinition] = (),
    ) -> None:
        await self._finish(
            state.run_id, state.counts, status, result, warnings, checks, buckets, state
        )

    async def _fail(self, run_id: uuid.UUID, counts: Mapping[str, int], error: str) -> None:
        """End a RUNNING run FAILED with one value-free ENGINE_ERROR warning, and commit."""
        warning = ModuleWarning(
            W.ENGINE_ERROR,
            WarningSeverity.ERROR,
            "The engine stopped before the run ended; no result was stored.",
            details={"error": error},
        )
        await self._finish(
            run_id, counts, CalculationRunStatus.FAILED, ModuleResult(), [warning], {}
        )

    async def _finish(
        self,
        run_id: uuid.UUID,
        counts: Mapping[str, int],
        status: CalculationRunStatus,
        result: ModuleResult,
        warnings: Sequence[ModuleWarning],
        checks: Mapping[PostCheck, bool],
        buckets: Sequence[MaturityBucketDefinition] = (),
        state: _Run | None = None,
    ) -> None:
        """Store the outputs (results only for a successful run, which needs its pinned
        `state`), end the run with its result hash and outcome, audit it and commit."""
        status, gap, contributions, warnings, checks, result_sha256 = reproduce(
            state.expected_result_sha256 if state is not None else None,
            status,
            result.liquidity_gap,
            result.contributions,
            warnings,
            checks,
            lcr=result.lcr,
            assets=result.hqla_assets,
        )
        keep = status in SUCCEEDED
        lcr, assets = kept_lcr(status, result.lcr, result.hqla_assets)
        tenant, actor = self.principal.tenant_id, self.principal.actor
        bucket_ids = {b.code: b.id for b in buckets}
        gap_ids: dict[tuple[str, str], uuid.UUID] = {}
        gap_rows = []
        for position, g in enumerate(gap, start=1):
            gap_ids[(g.currency, g.bucket.code)] = row_id = uuid.uuid4()
            gap_rows.append(
                {
                    "id": row_id,
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "currency": g.currency,
                    "bucket_definition_id": bucket_ids[g.bucket.code],
                    "bucket_sequence": g.bucket.sequence,
                    "bucket_code": g.bucket.code,
                    "inflows": g.inflows,
                    "outflows": g.outflows,
                    "net_gap": g.net_gap,
                    "cumulative_gap": g.cumulative_gap,
                    "inflows_contractual": g.inflows_contractual,
                    "inflows_behavioural": g.inflows_behavioural,
                    "outflows_contractual": g.outflows_contractual,
                    "outflows_behavioural": g.outflows_behavioural,
                    "contribution_count": g.contribution_count,
                    "classification": AGGREGATE_CLASSIFICATION,
                    "created_by": actor,
                }
            )
        await self.repo.insert_rows(LiquidityGapResult, gap_rows)
        # Read only when there is something to classify: after an engine failure the session
        # was rolled back and the pinned rows must not be reloaded.
        if (contributions or assets or lcr) and state is None:
            raise RuntimeError("results are stored only with the run's pinned state")
        classification = (
            self._contribution_classification(state)
            if state is not None and (contributions or assets or lcr)
            else ""
        )
        intermediates = []
        for position, c in enumerate(contributions, start=1):
            assert state is not None
            event = state.events[c.cashflow_event_id]
            intermediates.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "code": IntermediateCode.CASHFLOW_CONTRIBUTION.value,
                    "value": c.amount,
                    "unit": c.currency,
                    "classification": classification,
                    "liquidity_gap_result_id": gap_ids[(c.currency, c.bucket.code)],
                    "bucket_code": c.bucket.code,
                    "direction": c.direction.value,
                    "origin": c.origin.value,
                    "days_after_reporting_date": c.days_after_reporting_date,
                    "rule_version_ids": [str(r) for r in c.rule_version_ids],
                    "cashflow_event_id": c.cashflow_event_id,
                    "instrument_version_id": c.instrument_version_id,
                    "canonical_record_id": event.canonical_record_id,
                    "import_job_id": event.import_job_id,
                    "source_record_id": event.source_record_id,
                    "source_ordinal": event.source_ordinal,
                    "created_by": actor,
                }
            )
        await self.repo.insert_rows(IntermediateValue, intermediates)
        await self._store_lcr(run_id, lcr, assets, classification, state)
        await self.repo.insert_rows(
            CalculationWarning,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "code": w.code.value,
                    "severity": w.severity.value,
                    "message": w.message,
                    "entity_type": w.entity_type,
                    "entity_id": w.entity_id,
                    "details": dict(w.details),
                    "created_by": actor,
                }
                for position, w in enumerate(warnings, start=1)
            ],
        )
        excluded = sum(
            1 for w in warnings if w.code in result.exclusion_codes and w.entity_id is not None
        )
        in_scope, used, out_of_scope = counts.get("cashflow_event", 0), len(contributions), 0
        if "position" in counts:  # lcr_hqla: positions and cash flows
            in_scope += counts["position"]
            if keep:
                used = len(assets) + len(result.outflows) + len(result.inflows)
                out_of_scope = len(result.out_of_scope)
        lcr_warnings = Counter(w.value for r in lcr for w in r.warnings)
        outcome: dict[str, Any] = {
            "inputs": dict(sorted(counts.items())),
            "in_scope": in_scope,
            "bucketed": used,
            "excluded": excluded,
            "out_of_scope": out_of_scope,
            "warnings_by_code": dict(
                sorted((Counter(w.code.value for w in warnings) + lcr_warnings).items())
            ),
            "currencies": sorted({g.currency for g in gap} | {r.currency for r in lcr}),
            "post_checks": [{"check": c.value, "passed": p} for c, p in checks.items()],
        }
        await self.repo.finish(run_id, status, result_sha256, outcome, len(warnings))
        details: dict[str, Any] = {
            "run_id": str(run_id),
            "status": status.value,
            "warning_count": len(warnings),
            "result_sha256": result_sha256,
        }
        audit_by(
            self.session,
            self.principal,
            FINISH_RUN,
            "calculation_run",
            outcome=AuditOutcome.SUCCESS if keep else AuditOutcome.FAILURE,
            resource_id=str(run_id),
            details=details,
            details_classification=_public(details),
        )
        await self.session.commit()

    async def _store_lcr(
        self,
        run_id: uuid.UUID,
        lcr: Sequence[LcrRow],
        assets: Sequence[HqlaAsset],
        classification: str,
        state: _Run | None,
    ) -> dict[str, uuid.UUID]:
        """lcr_hqla: one lcr_result row per currency, then one hqla_asset_result row per
        asset position, under its currency's row and with its position's lineage."""
        if not lcr:
            return {}
        assert state is not None
        tenant, actor = self.principal.tenant_id, self.principal.actor
        ids = {r.currency: uuid.uuid4() for r in lcr}
        await self.repo.insert_rows(
            LcrResult,
            [
                {
                    "id": ids[r.currency],
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "currency": r.currency,
                    "hqla_level1": r.hqla_level1,
                    "hqla_level2a": r.hqla_level2a,
                    "hqla_level2b": r.hqla_level2b,
                    "hqla_total": r.hqla_total,
                    "level1_count": r.level1_count,
                    "level2a_count": r.level2a_count,
                    "level2b_count": r.level2b_count,
                    "excluded_asset_count": r.excluded_asset_count,
                    "outflow_balance": r.outflow_balance,
                    "outflows": r.outflows,
                    "outflow_count": r.outflow_count,
                    "inflows_gross": r.inflows_gross,
                    "inflows_before_cap": r.inflows_before_cap,
                    "inflow_cap_rate": r.inflow_cap_rate,
                    "inflow_cap_amount": r.inflow_cap_amount,
                    "inflows_after_cap": r.inflows_after_cap,
                    "inflow_cap_applied": r.inflow_cap_applied,
                    "inflow_count": r.inflow_count,
                    "level1_nonzero_count": r.level1_nonzero_count,
                    "level2a_nonzero_count": r.level2a_nonzero_count,
                    "level2b_nonzero_count": r.level2b_nonzero_count,
                    "outflow_nonzero_count": r.outflow_nonzero_count,
                    "inflow_nonzero_count": r.inflow_nonzero_count,
                    "net_outflows": r.net_outflows,
                    "ratio": r.ratio,
                    "warning_codes": [w.value for w in r.warnings],
                    "rule_version_ids": [str(i) for i in r.rule_version_ids],
                    "classification": AGGREGATE_CLASSIFICATION,
                    "contribution_classification": classification,
                    "created_by": actor,
                }
                for position, r in enumerate(lcr, start=1)
            ],
        )
        rows = []
        for position, a in enumerate(assets, start=1):
            held = state.positions[a.position_version_id]
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "lcr_result_id": ids[a.currency],
                    "currency": a.currency,
                    "included": a.included,
                    "level": a.level.value if a.level else None,
                    "exclusion_reason": a.exclusion_reason.value if a.exclusion_reason else None,
                    "instrument_type": a.instrument_type,
                    "balance": a.balance,
                    "haircut": a.haircut,
                    "hqla_value": a.hqla_value,
                    "rule_version_ids": [str(i) for i in a.rule_version_ids],
                    "classification": classification,
                    "position_version_id": a.position_version_id,
                    "instrument_version_id": a.instrument_version_id,
                    "canonical_record_id": held.canonical_record_id,
                    "import_job_id": held.import_job_id,
                    "source_record_id": held.source_record_id,
                    "source_ordinal": held.source_ordinal,
                    "created_by": actor,
                }
            )
        await self.repo.insert_rows(HqlaAssetResult, rows)
        return ids

    def _contribution_classification(self, state: _Run) -> str:
        """D-33: a contribution carries the most sensitive classification among the fields
        the module reads, in the schema versions the snapshot pins."""
        codes: list[str] = []
        for schema_code, fields in state.implementation.spec.fields_read().items():
            member = state.members.get(schema_code)
            if member is None:
                continue
            classes = state.pins.classes(member.schema_version_id)
            codes += [classes.get(f, ClassificationCode.RESTRICTED.value) for f in fields]
        return most_sensitive(codes)

    # --- reads (BE-15.5) ------------------------------------------------------------------

    async def _modules(self) -> dict[uuid.UUID, RunModule]:
        return {
            v.version.id: RunModule(
                v.module.id, v.version.id, v.module.code, v.module.name, v.version.version
            )
            for v in await self.rules.catalogue()
        }

    @staticmethod
    def _module_of(run: CalculationRun, modules: Mapping[uuid.UUID, RunModule]) -> RunModule:
        known = modules.get(run.module_version_id)
        if known is not None:
            return known
        m = run.manifest["module"]  # the pinned copy
        return RunModule(
            uuid.UUID(m["module_id"]), run.module_version_id, m["code"], m["code"], m["version"]
        )

    async def _run(self, run_id: uuid.UUID) -> CalculationRun:
        run = await self.repo.fresh(run_id)
        if run is None:
            raise ApiError(404, "not_found", RUN_NOT_FOUND)
        return run

    async def get(self, run_id: uuid.UUID) -> RunRow:
        """One run with its input refs and warnings: 404 alike for an absent and a foreign
        run (D-04)."""
        run = await self._run(run_id)
        return RunRow(
            run,
            self._module_of(run, await self._modules()),
            await self.repo.input_refs(run.id),
            await self.repo.warnings(run.id),
        )

    async def page(
        self,
        limit: int,
        offset: int,
        module_code: str | None,
        status: CalculationRunStatus | None,
    ) -> tuple[list[RunRow], int]:
        modules = await self._modules()
        ids = None
        if module_code is not None:
            ids = [i for i, m in modules.items() if m.code == module_code]
        runs, total = await self.repo.newest(limit, offset, ids, status)
        return [RunRow(r, self._module_of(r, modules)) for r in runs], total

    async def results(
        self,
        run_id: uuid.UUID,
        currency: str | None,
        bucket_code: str | None,
        limit: int,
        offset: int,
    ) -> ResultsView:
        run = await self._run(run_id)
        values, total = await self.repo.intermediates(run.id, currency, bucket_code, limit, offset)
        sequences = {g.id: g.bucket_sequence for g in await self.repo.gap_rows(run.id)}
        events = {
            e.id: e
            for e in await self.domain.cash_flows_by_id([v.cashflow_event_id for v in values])
        }
        versions = {
            v.id: (v, key)
            for v, key in await self.domain.instrument_versions_by_id(
                [v.instrument_version_id for v in values if v.instrument_version_id]
            )
        }
        pins = await self.domain.schema_pins()
        items = []
        for v in values:
            event = events[v.cashflow_event_id]
            instrument = versions.get(v.instrument_version_id) if v.instrument_version_id else None
            items.append(
                ContributionRow(
                    v,
                    sequences.get(v.liquidity_gap_result_id, 0),
                    event,
                    InstrumentRef(instrument[0].instrument_id, instrument[1])
                    if instrument
                    else None,
                    pins.classes(event.schema_version_id),
                    pins.classes(instrument[0].schema_version_id) if instrument else {},
                )
            )
        return ResultsView(run, items, total)

    async def ladder(self, run_id: uuid.UUID, min_aggregate_count: int = 3) -> LadderView:
        run = await self._run(run_id)
        row = RunRow(run, self._module_of(run, await self._modules()))
        pinned = [
            m for m in run.manifest["rule_set"]["members"] if m["rule_code"] == MATURITY_BUCKETS
        ]
        if row.module.code != MATURITY_LADDER or not pinned:
            raise ApiError(
                404, "maturity_ladder_not_found", "This run is not a maturity ladder run."
            )
        version_id = uuid.UUID(pinned[0]["rule_version_id"])
        rows = await self.repo.gap_rows(run.id)
        parts = await self.repo.part_counts(run.id)
        return LadderView(
            row,
            version_id,
            await self.rules.bucket_definitions(version_id),
            rows,
            ladder_classes(rows, parts, min_aggregate_count),
            min_aggregate_count,
        )

    async def liquidity(self, run_id: uuid.UUID, min_aggregate_count: int = 3) -> LiquidityView:
        """An lcr_hqla run's LCR per currency with its asset rows (BE-17.3): 404 alike for an
        absent and a foreign run (D-04), 404 `liquidity_not_found` for another module's."""
        run = await self._run(run_id)
        row = RunRow(run, self._module_of(run, await self._modules()))
        if row.module.code != LCR_HQLA:
            raise ApiError(404, "liquidity_not_found", "This run is not an LCR/HQLA run.")
        horizon = None
        for member in run.manifest["rule_set"]["members"]:
            if member["rule_code"] == LCR_INFLOW_HORIZON_DAYS:
                value = member["parameters"].get("value")
                horizon = value if isinstance(value, int) else None
        rows = await self.repo.lcr_rows(run.id)
        values = await self.repo.hqla_assets(run.id)
        positions = {
            v.id: (v, key)
            for v, key in await self.domain.positions_by_id([a.position_version_id for a in values])
        }
        instruments = {
            v.id: (v, key)
            for v, key in await self.domain.instrument_versions_by_id(
                [a.instrument_version_id for a in values]
            )
        }
        pins = await self.domain.schema_pins()
        assets = []
        for a in values:
            position, position_key = positions[a.position_version_id]
            instrument = instruments.get(a.instrument_version_id)
            assets.append(
                AssetRow(
                    a,
                    position,
                    position_key,
                    instrument[0] if instrument else None,
                    instrument[1] if instrument else None,
                    pins.classes(position.schema_version_id),
                    pins.classes(instrument[0].schema_version_id) if instrument else {},
                )
            )
        return LiquidityView(
            row,
            horizon,
            rows,
            assets,
            {r.id: liquidity_classes(r, min_aggregate_count) for r in rows},
            min_aggregate_count,
        )

    # --- for the rule registry reads (BE-16.2) ----------------------------------------------

    async def runs_in_rule_sets(
        self, rule_set_ids: Sequence[uuid.UUID], limit: int
    ) -> tuple[int, list[CalculationRun]]:
        """How many runs pin one of these rule sets, and the newest `limit` of them."""
        return await self.repo.in_rule_sets(rule_set_ids, limit)
