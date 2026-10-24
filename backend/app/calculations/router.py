"""calculations.router

The WS3 calculation endpoints of Phase 15 (the Feature 03 endpoint table, D-34):

- POST /dataset-snapshots (`calc:run`, D-30): pin projected imports, at most one per schema
  code. Content-addressed: 201 with a new snapshot, 200 with `Idempotent-Replayed: true` for
  the existing one of the same members. 409 `not_projected`, `duplicate_schema`,
  `reconciliation_broken`, `ambiguous_business_key` and `calculations_disabled`, each
  audited DENIED; audited `dataset_snapshot.create`.
- GET /dataset-snapshots, GET /dataset-snapshots/{snapshot_id} (`calc:read`).
- POST /calculation-runs (`calc:run`, `Idempotency-Key`, D-31): pin, run and post-check one
  module synchronously. 201 with the finished run (SUCCEEDED, SUCCEEDED_WITH_WARNINGS,
  FAILED or BLOCKED); 200 with `Idempotent-Replayed: true` for a replay of the key. 409
  `module_not_available`, `calculations_disabled`, `idempotency_conflict`; 422 for a bad
  body. Audited `calculation_run.create` (and `calculation_run.finish`).
- GET /calculation-runs (paginated; filter by module and status),
  GET /calculation-runs/{run_id} (the manifest of every pin, the input refs, the warnings),
  GET /calculation-runs/{run_id}/results (contributions with lineage),
  GET /calculation-runs/{run_id}/maturity-ladder and (Phase 17, BE-17.3)
  GET /calculation-runs/{run_id}/liquidity (`calc:read`).

Every decimal is exact text (`format(d, "f")`). Masking follows D-33: aggregates are
`internal`, contributions carry the most sensitive classification of the fields they read.
Tenant scope comes from the principal (D-04): another tenant's id is the same 404 as a random
one. Every run is labelled with the D-35 statement.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.constants import (
    CalculationRunStatus,
    CalculationWarningCode,
    HqlaExclusionReason,
    InputRefType,
    IntermediateCode,
    LcrWarningCode,
    WarningSeverity,
)
from app.calculations.schemas import (
    BucketOut,
    ContributionLineage,
    HqlaAssetLineage,
    HqlaAssetOut,
    HqlaLevelOut,
    InputRefOut,
    LadderCurrency,
    LadderRow,
    LiquidityCurrency,
    LiquidityOut,
    MaturityLadderOut,
    ResultContext,
    ResultItem,
    ResultPage,
    RunCreate,
    RunDetail,
    RunManifest,
    RunModuleRef,
    RunOutcome,
    RunPage,
    RunRerun,
    RunScenarioRef,
    RunSummary,
    SnapshotCreate,
    SnapshotMemberOut,
    SnapshotOut,
    SnapshotPage,
    Value,
    WarningOut,
)
from app.calculations.service import (
    AssetRow,
    CalculationService,
    ContributionRow,
    RerunSpec,
    RunRow,
    RunSpec,
    SnapshotService,
    SnapshotView,
)
from app.core.auth import require_permission
from app.core.classification import UNCLASSIFIED, mask_mapping, mask_value
from app.core.decimals import decimal_text
from app.core.idempotency import idempotency_key_header, mark_replayed
from app.core.principal import Principal
from app.db.session import get_session
from app.domain.constants import CashflowOrigin, DomainSchemaCode
from app.governance.constants import Permission
from app.orchestration.constants import APPROVAL_LABELS, ApprovalState
from app.reconciliation.constants import ReconciliationOutcome
from app.rules.constants import ILLUSTRATIVE_STATEMENT, CashflowDirection, HqlaLevel

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CalcReader = Annotated[Principal, Depends(require_permission(Permission.CALC_READ))]
CalcRunner = Annotated[Principal, Depends(require_permission(Permission.CALC_RUN))]

router = APIRouter(tags=["calculations"])

# --- dataset snapshots (BE-15.3) --------------------------------------------------------------


def _snapshot(view: SnapshotView) -> SnapshotOut:
    s = view.snapshot
    return SnapshotOut(
        snapshot_id=s.id,
        content_sha256=s.content_sha256,
        member_count=s.member_count,
        members=[
            SnapshotMemberOut(
                import_id=m.import_job_id,
                schema_code=DomainSchemaCode(m.schema_code),
                schema_version_id=m.schema_version_id,
                schema_version=view.schema_versions.get(m.schema_version_id),
                mapping_version_id=m.mapping_version_id,
                import_manifest_id=m.import_manifest_id,
                manifest_sha256=m.manifest_sha256,
                projection_id=m.domain_projection_id,
                projector_version=m.projector_version,
                source_id=m.source_system_id,
                source_code=view.source_codes.get(m.source_system_id, ""),
                accepted_count=m.accepted_count,
                quarantined_count=m.quarantined_count,
                rejected_count=m.rejected_count,
                reconciliation_outcome=ReconciliationOutcome(m.reconciliation_outcome),
            )
            for m in view.members
        ],
        created_at=s.created_at,
        created_by=s.created_by,
    )


@router.post(
    "/dataset-snapshots",
    response_model=SnapshotOut,
    status_code=201,
    operation_id="createDatasetSnapshot",
    responses={
        200: {
            "model": SnapshotOut,
            "description": "The same members already form a snapshot: it is returned and "
            "nothing is written (header Idempotent-Replayed: true).",
        }
    },
)
async def create_dataset_snapshot(
    request: Request,
    response: Response,
    body: SnapshotCreate,
    principal: CalcRunner,
    session: SessionDep,
) -> SnapshotOut:
    """Pin projected imports as one immutable dataset snapshot (D-30), at most one per schema
    code. QUARANTINED imports are allowed; their held counts travel into run manifests.
    Refusals (409, each audited DENIED): `not_projected`, `duplicate_schema`,
    `reconciliation_broken` (a D-28 BROKEN member), `ambiguous_business_key` (a business key
    with two versions in the snapshot) and `calculations_disabled`; 404 `not_found` alike for
    an absent and a foreign import."""
    enabled = request.app.state.settings.CALCULATIONS_ENABLED
    view, created = await SnapshotService(session, principal).create(body.import_ids, enabled)
    if not created:
        mark_replayed(response)
    return _snapshot(view)


@router.get("/dataset-snapshots", response_model=SnapshotPage, operation_id="listDatasetSnapshots")
async def list_dataset_snapshots(
    principal: CalcReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SnapshotPage:
    """The caller's tenant's snapshots, newest first."""
    views, total = await SnapshotService(session, principal).page(limit, offset)
    return SnapshotPage(
        items=[_snapshot(v) for v in views], total=total, limit=limit, offset=offset
    )


@router.get(
    "/dataset-snapshots/{snapshot_id}",
    response_model=SnapshotOut,
    operation_id="getDatasetSnapshot",
)
async def get_dataset_snapshot(
    snapshot_id: uuid.UUID, principal: CalcReader, session: SessionDep
) -> SnapshotOut:
    """One snapshot and its members; 404 alike for an absent and a foreign id (D-04)."""
    return _snapshot(await SnapshotService(session, principal).get(snapshot_id))


# --- calculation runs (BE-15.4, BE-15.5) ------------------------------------------------------


def _module(row: RunRow) -> RunModuleRef:
    return RunModuleRef(
        module_id=row.module.module_id,
        module_version_id=row.module.module_version_id,
        code=row.module.code,
        name=row.module.name,
        version=row.module.version,
    )


def _scenario(row: RunRow) -> RunScenarioRef | None:
    """Phase 25 (D-45 §4): a stressed run's scenario summary; None for every other run."""
    pin = row.scenario
    if pin is None:
        return None
    state = ApprovalState(pin.approval_state)
    return RunScenarioRef(
        scenario_run_id=pin.scenario_run_id,
        scenario_id=pin.scenario_id,
        scenario_version_id=pin.scenario_version_id,
        version=pin.version,
        shock_subset=list(pin.shock_subset),
        combined=pin.combined,
        approval_state=state.value,
        approval_label=APPROVAL_LABELS[state].value,
        baseline_run_id=pin.baseline_run_id,
    )


def _summary(row: RunRow) -> RunSummary:
    r = row.run
    return RunSummary(
        run_id=r.id,
        status=CalculationRunStatus(r.status),
        reporting_date=r.reporting_date,
        module=_module(row),
        dataset_snapshot_id=r.dataset_snapshot_id,
        rule_set_id=r.rule_set_id,
        assumption_set_id=r.assumption_set_id,
        engine_version=r.engine_version,
        manifest_sha256=r.manifest_sha256,
        result_sha256=r.result_sha256,
        warning_count=r.warning_count,
        rerun_of_run_id=r.rerun_of_run_id,
        started_at=r.started_at,
        ended_at=r.ended_at,
        created_by=r.created_by,
        illustrative=True,
        validation_status=CalculationService.validation_status(r),
        scenario=_scenario(row),
    )


def _context(row: RunRow) -> ResultContext:
    """D-56 §5 (BE-30.3): the run's context block, from its pins."""
    return ResultContext.model_validate(CalculationService.result_context(row.run))


def _detail(row: RunRow, manifest: dict[str, Any]) -> RunDetail:
    """`manifest` is the run's manifest as the caller may read it
    (CalculationService.shown_manifest: picked instrument keys masked, D-33)."""
    r = row.run
    return RunDetail(
        **_summary(row).model_dump(),
        manifest=RunManifest.model_validate(manifest),
        input_refs=[
            InputRefOut(
                sequence=i.sequence,
                ref_type=InputRefType(i.ref_type),
                ref_id=i.ref_id,
                label=i.label,
                sha256=i.sha256,
            )
            for i in row.input_refs
        ],
        warnings=[
            WarningOut(
                sequence=w.sequence,
                code=CalculationWarningCode(w.code),
                severity=WarningSeverity(w.severity),
                message=w.message,
                entity_type=w.entity_type,
                entity_id=w.entity_id,
                details=w.details,
            )
            for w in row.warnings
        ],
        outcome=RunOutcome.model_validate(r.outcome) if r.outcome is not None else None,
        correlation_id=r.correlation_id,
        statement=ILLUSTRATIVE_STATEMENT,
        context=_context(row),
    )


@router.post(
    "/calculation-runs",
    response_model=RunDetail,
    status_code=201,
    operation_id="createCalculationRun",
    responses={
        200: {
            "model": RunDetail,
            "description": "Idempotent replay: this key was used for the same request; the "
            "original run is returned (header Idempotent-Replayed: true).",
        }
    },
)
async def create_calculation_run(
    request: Request,
    response: Response,
    body: RunCreate | RunRerun,
    principal: CalcRunner,
    session: SessionDep,
    idempotency_key: Annotated[
        str,
        idempotency_key_header(
            "Unique per run request (per user). A retry with the same key and the "
            "same body returns the original run; with a different body, 409 "
            "idempotency_conflict (D-31 §5)."
        ),
    ],
) -> RunDetail:
    """Run a module on a dataset snapshot for a reporting date, synchronously (D-31). The
    engine pins the ACTIVE rule versions (as a content-addressed rule set), the baseline
    assumption set, the module and engine versions and the snapshot's members, writes the
    run RUNNING with its manifest, runs the module and the post-checks, and ends it
    SUCCEEDED, SUCCEEDED_WITH_WARNINGS, FAILED (a post-check failed) or BLOCKED (the input
    contract was not met). No AI is involved. A body of `rerun_of` alone re-runs that run on
    its exact original pins (BE-16.3), recording `rerun_of_run_id`. Refusals: 404 `not_found`
    (snapshot, or the run to re-run), 409 `rerun_not_reproducible`, 409
    `module_not_available` (absent, PLANNED, not implemented, or a rule without an ACTIVE
    version), 409 `calculations_disabled`, 409 `idempotency_conflict`; each audited DENIED. A
    replay whose run is still RUNNING after CALC_RUN_STALE_SECONDS ends it FAILED
    (ENGINE_ERROR) and returns it. A re-run of a stressed run (Phase 25, D-45 §8) re-pins its
    scenario version and shock subset, even once the version is CLOSED; while
    SCENARIOS_ENABLED is off it is refused 409 `scenarios_disabled`."""
    settings = request.app.state.settings
    spec: RunSpec | RerunSpec = (
        RerunSpec(body.rerun_of)
        if isinstance(body, RunRerun)
        else RunSpec(body.dataset_snapshot_id, body.module_version_id, body.reporting_date)
    )
    service = CalculationService(session, principal)
    row, created = await service.create(
        spec,
        idempotency_key,
        settings.CALCULATIONS_ENABLED,
        settings.CALC_RUN_STALE_SECONDS,
        settings.SCENARIOS_ENABLED,
    )
    if not created:
        mark_replayed(response)
    return _detail(row, await service.shown_manifest(row.run))


@router.get("/calculation-runs", response_model=RunPage, operation_id="listCalculationRuns")
async def list_calculation_runs(
    principal: CalcReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    module_code: Annotated[
        str | None, Query(max_length=100, description="Runs of this module (any version).")
    ] = None,
    status: CalculationRunStatus | None = None,
    stressed: Annotated[
        bool | None,
        Query(
            description="true: stressed runs only (Phase 25, D-45 §4); false: non-stressed runs "
            "only (e.g. a baseline picker); omitted: every run."
        ),
    ] = None,
) -> RunPage:
    """The caller's tenant's runs, newest first."""
    rows, total = await CalculationService(session, principal).page(
        limit, offset, module_code, status, stressed
    )
    return RunPage(items=[_summary(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get(
    "/calculation-runs/{run_id}", response_model=RunDetail, operation_id="getCalculationRun"
)
async def get_calculation_run(
    run_id: uuid.UUID, principal: CalcReader, session: SessionDep
) -> RunDetail:
    """One run: its manifest with every pinned id and version, its input refs, warnings and
    outcome counts. A stressed run's manifest.scenario.shocks mask each picked instrument key
    the caller is not cleared for as a MaskedValue (D-33, as the scenario detail does);
    shocks_sha256 is the pin over the stored keys. 404 alike for an absent and a foreign id
    (D-04)."""
    service = CalculationService(session, principal)
    row = await service.get(run_id)
    return _detail(row, await service.shown_manifest(row.run))


def _contribution(c: ContributionRow, clear: frozenset[str]) -> ResultItem:
    v = c.value
    classes = c.classes
    shown = mask_mapping(
        {
            "value": decimal_text(v.value),
            "cashflow_business_key": c.event.business_key,
            "due_date": c.event.due_date.isoformat() if c.event.due_date else "",
            "days": v.days_after_reporting_date,
        },
        {
            "value": v.classification,
            "cashflow_business_key": classes.get("cashflow_id", UNCLASSIFIED),
            "due_date": classes.get("due_date", UNCLASSIFIED),
            "days": classes.get("due_date", UNCLASSIFIED),
        },
        clear,
    )
    instrument_key: Value | None = None
    if c.instrument is not None:
        code = c.instrument_classes.get("instrument_id", UNCLASSIFIED)
        instrument_key = mask_value(c.instrument.business_key, code, clear)
    return ResultItem(
        intermediate_value_id=v.id,
        sequence=v.sequence,
        code=IntermediateCode(v.code),
        value=shown["value"],
        unit=v.unit,
        classification=v.classification,
        currency=v.unit,
        bucket_code=v.bucket_code,
        bucket_sequence=c.bucket_sequence,
        direction=CashflowDirection(v.direction),
        origin=CashflowOrigin(v.origin),
        days_after_reporting_date=shown["days"],
        liquidity_gap_result_id=v.liquidity_gap_result_id,
        rule_version_ids=[uuid.UUID(i) for i in v.rule_version_ids],
        lineage=ContributionLineage(
            cashflow_event_id=v.cashflow_event_id,
            cashflow_business_key=shown["cashflow_business_key"],
            due_date=shown["due_date"],
            instrument_id=c.instrument.instrument_id if c.instrument else None,
            instrument_version_id=v.instrument_version_id,
            instrument_business_key=instrument_key,
            canonical_record_id=v.canonical_record_id,
            import_id=v.import_job_id,
            source_record_id=v.source_record_id,
            source_ordinal=v.source_ordinal,
        ),
    )


@router.get(
    "/calculation-runs/{run_id}/results",
    response_model=ResultPage,
    operation_id="getCalculationRunResults",
)
async def get_calculation_run_results(
    run_id: uuid.UUID,
    principal: CalcReader,
    session: SessionDep,
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    bucket_code: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResultPage:
    """The run's intermediate values (one contribution per bucketed cash flow), each with its
    lineage: cash-flow event -> canonical record -> import and source row (D-31 §6). Filter
    by currency and bucket to drill down from a ladder row. The value is masked unless the
    caller is cleared for the most sensitive field it reads (D-33)."""
    view = await CalculationService(session, principal).results(
        run_id, currency, bucket_code, limit, offset
    )
    clear = principal.clear_classifications
    return ResultPage(
        run_id=view.run.id,
        status=CalculationRunStatus(view.run.status),
        items=[_contribution(c, clear) for c in view.items],
        total=view.total,
        limit=limit,
        offset=offset,
        statement=ILLUSTRATIVE_STATEMENT,
        validation_status=CalculationService.validation_status(view.run),
        context=ResultContext.model_validate(CalculationService.result_context(view.run)),
    )


@router.get(
    "/calculation-runs/{run_id}/maturity-ladder",
    response_model=MaturityLadderOut,
    operation_id="getMaturityLadder",
)
async def get_maturity_ladder(
    request: Request, run_id: uuid.UUID, principal: CalcReader, session: SessionDep
) -> MaturityLadderOut:
    """The run's maturity ladder: per currency and bucket, inflows, outflows, net gap and
    cumulative gap, with the contractual and behavioural parts (D-35). Aggregates are
    `internal` (D-33). 404 `maturity_ladder_not_found` for a run of another module."""
    minimum = request.app.state.settings.CALC_MIN_AGGREGATE_COUNT
    view = await CalculationService(session, principal).ladder(run_id, minimum)
    clear = principal.clear_classifications
    labels = {b.id: b.label for b in view.buckets}
    currencies: dict[str, list[LadderRow]] = {}
    for g in view.rows:
        amounts = {
            "inflows": g.inflows,
            "outflows": g.outflows,
            "net_gap": g.net_gap,
            "cumulative_gap": g.cumulative_gap,
            "inflows_contractual": g.inflows_contractual,
            "inflows_behavioural": g.inflows_behavioural,
            "outflows_contractual": g.outflows_contractual,
            "outflows_behavioural": g.outflows_behavioural,
        }
        # D-33 minimum aggregation: a figure from too few contributions is masked like them.
        shown = mask_mapping(
            {k: decimal_text(v) for k, v in amounts.items()}, view.classes[g.id], clear
        )
        currencies.setdefault(g.currency, []).append(
            LadderRow(
                liquidity_gap_result_id=g.id,
                bucket_code=g.bucket_code,
                bucket_sequence=g.bucket_sequence,
                bucket_label=labels.get(g.bucket_definition_id, g.bucket_code),
                classification=g.classification,
                contribution_count=g.contribution_count,
                **shown,
            )
        )
    return MaturityLadderOut(
        run_id=view.row.run.id,
        status=CalculationRunStatus(view.row.run.status),
        reporting_date=view.row.run.reporting_date,
        module=_module(view.row),
        bucket_rule_version_id=view.bucket_rule_version_id,
        buckets=[
            BucketOut(
                bucket_definition_id=b.id,
                sequence=b.sequence,
                code=b.code,
                label=b.label,
                lower_exclusive_days=b.lower_exclusive_days,
                upper_inclusive_days=b.upper_inclusive_days,
            )
            for b in view.buckets
        ],
        currencies=[LadderCurrency(currency=c, rows=rows) for c, rows in currencies.items()],
        min_aggregate_count=view.min_aggregate_count,
        illustrative=True,
        statement=ILLUSTRATIVE_STATEMENT,
        validation_status=CalculationService.validation_status(view.row.run),
        context=_context(view.row),
    )


# --- liquidity: the lcr_hqla read (BE-17.3) ---------------------------------------------------

LCR_LIMITATIONS = [
    "Level 2A/2B caps are not applied (LCR_LEVEL_CAPS_NOT_APPLIED when a Level 2 asset is "
    "included).",
    "The value of an asset is its position balance: the schema has no market value "
    "(PROVISIONAL).",
    "Per currency, with no FX conversion and no all-currency total.",
    "Inflows are cash flows due (0, horizon] calendar days after the reporting date; "
    "outflows are position balances x run-off. No other LCR categories are modelled.",
]


def _asset(a: AssetRow, clear: frozenset[str]) -> HqlaAssetOut:
    v = a.value
    pc, ic = a.position_classes, a.instrument_classes
    shown = mask_mapping(
        {
            "balance": decimal_text(v.balance),
            "hqla_value": decimal_text(v.hqla_value),
            "position_business_key": a.position_key,
            "instrument_business_key": a.instrument_key or "",
            "instrument_type": v.instrument_type,
        },
        {
            "balance": v.classification,
            "hqla_value": v.classification,
            "position_business_key": pc.get("position_id", UNCLASSIFIED),
            "instrument_business_key": ic.get("instrument_id", UNCLASSIFIED),
            "instrument_type": ic.get("instrument_type", UNCLASSIFIED),
        },
        clear,
    )
    return HqlaAssetOut(
        hqla_asset_result_id=v.id,
        sequence=v.sequence,
        included=v.included,
        level=HqlaLevel(v.level) if v.level else None,
        exclusion_reason=HqlaExclusionReason(v.exclusion_reason) if v.exclusion_reason else None,
        instrument_type=shown["instrument_type"],
        balance=shown["balance"],
        haircut=decimal_text(v.haircut) if v.haircut is not None else None,
        # An excluded row's value is 0 by rule, not an amount (D-33: zeros stay clear).
        hqla_value=shown["hqla_value"] if v.included else decimal_text(v.hqla_value),
        classification=v.classification,
        rule_version_ids=[uuid.UUID(i) for i in v.rule_version_ids],
        lineage=HqlaAssetLineage(
            position_version_id=v.position_version_id,
            position_id=a.position.position_id,
            position_business_key=shown["position_business_key"],
            instrument_id=a.instrument.instrument_id if a.instrument else None,
            instrument_version_id=v.instrument_version_id,
            instrument_business_key=shown["instrument_business_key"],
            canonical_record_id=v.canonical_record_id,
            import_id=v.import_job_id,
            source_record_id=v.source_record_id,
            source_ordinal=v.source_ordinal,
        ),
    )


@router.get(
    "/calculation-runs/{run_id}/liquidity",
    response_model=LiquidityOut,
    operation_id="getLiquidity",
)
async def get_liquidity(
    request: Request, run_id: uuid.UUID, principal: CalcReader, session: SessionDep
) -> LiquidityOut:
    """An lcr_hqla run's liquidity coverage, per currency: HQLA by level with its asset rows
    (included, or excluded with the reason), outflows, inflows before and after the cap, the
    denominator and the ratio (None, with LCR_DENOMINATOR_ZERO, when it is not computable).
    Exact decimal text. Aggregates are `internal`, asset rows carry the most sensitive
    classification they read, and a figure built from fewer than CALC_MIN_AGGREGATE_COUNT
    contributions is masked like them (D-33). An illustrative prototype, not regulatory
    (D-35). 404 alike for an absent and a foreign run; 404 `liquidity_not_found` for a run of
    another module."""
    minimum = request.app.state.settings.CALC_MIN_AGGREGATE_COUNT
    view = await CalculationService(session, principal).liquidity(run_id, minimum)
    clear = principal.clear_classifications
    currencies = []
    for r in view.rows:
        classes = view.classes[r.id]
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
                "inflow_cap_amount",
                "inflows_after_cap",
                "net_outflows",
            )
        }
        shown = mask_mapping(figures, classes, clear)
        ratio: Value | None = None
        if r.ratio is not None:
            ratio = mask_mapping({"ratio": decimal_text(r.ratio)}, classes, clear)["ratio"]
        applied = mask_mapping(
            {"applied": r.inflow_cap_applied}, {"applied": classes["inflow_cap_applied"]}, clear
        )
        levels = (
            (HqlaLevel.L1, "hqla_level1", r.level1_count),
            (HqlaLevel.L2A, "hqla_level2a", r.level2a_count),
            (HqlaLevel.L2B, "hqla_level2b", r.level2b_count),
        )
        currencies.append(
            LiquidityCurrency(
                lcr_result_id=r.id,
                currency=r.currency,
                classification=r.classification,
                hqla_total=shown["hqla_total"],
                hqla_levels=[
                    HqlaLevelOut(
                        level=level,
                        total=shown[name],
                        asset_count=count,
                        classification=classes[name],
                    )
                    for level, name, count in levels
                ],
                assets=[_asset(a, clear) for a in view.assets if a.value.lcr_result_id == r.id],
                excluded_asset_count=r.excluded_asset_count,
                outflow_balance=shown["outflow_balance"],
                outflows=shown["outflows"],
                outflow_count=r.outflow_count,
                inflows_gross=shown["inflows_gross"],
                inflows_before_cap=shown["inflows_before_cap"],
                inflow_cap_rate=decimal_text(r.inflow_cap_rate),
                inflow_cap_amount=shown["inflow_cap_amount"],
                inflows_after_cap=shown["inflows_after_cap"],
                inflow_cap_applied=applied["applied"],
                inflow_count=r.inflow_count,
                net_outflows=shown["net_outflows"],
                ratio=ratio,
                warnings=[LcrWarningCode(w) for w in r.warning_codes],
            )
        )
    return LiquidityOut(
        run_id=view.row.run.id,
        status=CalculationRunStatus(view.row.run.status),
        reporting_date=view.row.run.reporting_date,
        module=_module(view.row),
        inflow_horizon_days=view.horizon_days,
        currencies=currencies,
        min_aggregate_count=view.min_aggregate_count,
        limitations=LCR_LIMITATIONS,
        prototype=True,
        illustrative=True,
        statement=ILLUSTRATIVE_STATEMENT,
        validation_status=CalculationService.validation_status(view.row.run),
        context=_context(view.row),
    )
