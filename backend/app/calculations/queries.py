"""calculations.queries (BE-35.2: split from calculations.service, which re-exports it)

The run views returned to the router and the run requests (`RunSpec`, `RerunSpec`,
`ScenarioRunSpec`), and `RunQueries`: the base of `CalculationService` (its session, scope
and services) with the reads of BE-15.5, BE-16.4, BE-17.3 and BE-25.4. Every read is scoped
by the principal (D-04): absent and foreign runs are the same 404.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations import scenario_overlay
from app.calculations.constants import (
    CONTEXT_AMOUNT_UNIT,
    CONTEXT_CONSOLIDATION,
    CONTEXT_CURRENCY_BASIS,
    CONTEXT_DAYS_UNIT,
    CONTEXT_RATIO_UNITS,
    CREATE_RUN,
    CalculationRunStatus,
)
from app.calculations.executor import INLINE, Admission, RunExecutor
from app.calculations.models import (
    CalculationInputRef,
    CalculationRun,
    CalculationScenarioPin,
    CalculationWarning,
    DatasetSnapshot,
    DatasetSnapshotMember,
    HqlaAssetResult,
    IntermediateValue,
    LcrResult,
    LiquidityGapResult,
    ScenarioContribution,
)
from app.calculations.modules.base import RegisteredModule
from app.calculations.repository import CalculationRepository
from app.calculations.results import ladder_classes, liquidity_classes
from app.calculations.snapshots import SnapshotService
from app.core.canonical import HashKind, sha256_for
from app.core.classification import most_sensitive, storable
from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.refusals import refuse
from app.domain.models import CashflowEvent, InstrumentVersion, PositionVersion
from app.domain.service import DomainService, SchemaPins
from app.rules.constants import (
    LCR_HQLA,
    LCR_INFLOW_HORIZON_DAYS,
    MATURITY_BUCKETS,
    MATURITY_LADDER,
    ValidationStatus,
    validation_status_of,
)
from app.rules.models import AssumptionSet, MaturityBucketDefinition, RuleDefinition, RuleVersion
from app.rules.service import ModuleView, RulesService


@dataclass(frozen=True)
class RunSpec:
    dataset_snapshot_id: uuid.UUID
    module_version_id: uuid.UUID
    reporting_date: date

    def sha256(self) -> str:
        """What a replay of the Idempotency-Key must repeat."""
        return sha256_for(
            HashKind.CALCULATION_RUN_REQUEST,
            {
                "dataset_snapshot_id": str(self.dataset_snapshot_id),
                "module_version_id": str(self.module_version_id),
                "reporting_date": self.reporting_date.isoformat(),
            },
        )


@dataclass(frozen=True)
class RerunSpec:
    """A re-run of a run on its exact pins (BE-16.3)."""

    rerun_of: uuid.UUID

    def sha256(self) -> str:
        return sha256_for(HashKind.CALCULATION_RUN_REQUEST, {"rerun_of": str(self.rerun_of)})


@dataclass(frozen=True)
class ScenarioRunSpec:
    """A stressed run (BE-25.3, D-45 §1, §4, §7): the baseline whose pins it copies, the
    scenario version and the shock subset it applies (1-based declared positions), and the
    approval state at run time. Its fingerprint covers the version, the baseline and the
    subset."""

    baseline_run_id: uuid.UUID
    scenario_run_id: uuid.UUID
    scenario_id: uuid.UUID
    scenario_version_id: uuid.UUID
    version: int
    shocks: tuple[Mapping[str, Any], ...]
    shocks_sha256: str
    shock_subset: tuple[int, ...]
    approval_state: str

    @property
    def combined(self) -> bool:
        return len(self.shock_subset) == len(self.shocks)

    def sha256(self) -> str:
        return sha256_for(
            HashKind.CALCULATION_RUN_REQUEST,
            {
                "baseline_run_id": str(self.baseline_run_id),
                "scenario_version_id": str(self.scenario_version_id),
                "shocks_sha256": self.shocks_sha256,
                "shock_subset": list(self.shock_subset),
            },
        )

    def manifest_section(self) -> dict[str, Any]:
        """The manifest's `scenario` section (D-45 §4): pinned content only."""
        return {
            "scenario_id": str(self.scenario_id),
            "scenario_version_id": str(self.scenario_version_id),
            "version": self.version,
            "shocks_sha256": self.shocks_sha256,
            "shocks": [dict(s) for s in self.shocks],
            "shock_subset": list(self.shock_subset),
            "approval_state": self.approval_state,
            "baseline_run_id": str(self.baseline_run_id),
        }

    @classmethod
    def of(cls, run: CalculationRun, pin: CalculationScenarioPin) -> "ScenarioRunSpec":
        """The spec a stressed run pinned (for its re-runs, D-45 §8)."""
        section = run.manifest["scenario"]
        return cls(
            baseline_run_id=uuid.UUID(section["baseline_run_id"]),
            scenario_run_id=pin.scenario_run_id,
            scenario_id=uuid.UUID(section["scenario_id"]),
            scenario_version_id=uuid.UUID(section["scenario_version_id"]),
            version=int(section["version"]),
            shocks=tuple(section["shocks"]),
            shocks_sha256=section["shocks_sha256"],
            shock_subset=tuple(int(n) for n in section["shock_subset"]),
            approval_state=section["approval_state"],
        )


def is_stressed(run: CalculationRun) -> bool:
    """A stressed run's manifest has a `scenario` section; no other run's does (D-45 §4)."""
    return "scenario" in run.manifest


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
    scenario: CalculationScenarioPin | None = None  # stressed runs only (Phase 25)


@dataclass(frozen=True)
class InstrumentRef:
    instrument_id: uuid.UUID
    business_key: str


@dataclass(frozen=True)
class ContributionRow:
    value: IntermediateValue | ScenarioContribution  # stressed runs: their parts
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
    # Stressed runs (D-45): what they apply, and (new ones) the baseline whose pins they copy.
    scenario: ScenarioRunSpec | None = None
    baseline: CalculationRun | None = None


@dataclass(frozen=True)
class BaselineCheck:
    """Whether a run can be a scenario baseline (D-45 §10): `refusal` is None, `not_found`,
    `invalid_baseline` or `baseline_not_reproducible`."""

    run: CalculationRun | None
    refusal: str | None
    pinned: "_Pinned | None" = None


@dataclass(frozen=True)
class ScenarioMember:
    """One member run of a scenario run (re-runs excluded)."""

    pin: CalculationScenarioPin
    run: CalculationRun


@dataclass(frozen=True)
class ScenarioPartRow:
    """One changed part for the comparison drill-down (D-46 §4), with its source lineage."""

    value: ScenarioContribution
    event: CashflowEvent
    instrument: InstrumentRef | None
    instrument_classes: Mapping[str, str]


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
    scenario: ScenarioRunSpec | None = None  # a stressed run (D-45)
    # Pool mode (BE-33.3): the monotonic time (ns) by which the run must end (None: inline).
    deadline: int | None = None


class _Held:
    """The run's admission to the executor (BE-33.1): taken after the replay check, before
    anything is written; released when the run ends, by its background task once handed off
    (respond-async), never by the caller that passed it in."""

    def __init__(self, admission: Admission | None) -> None:
        self.admission = admission
        self.owned = admission is None

    def hand_off(self) -> Admission:
        assert self.admission is not None
        self.owned = False
        return self.admission

    def release(self) -> None:
        if self.owned and self.admission is not None:
            self.admission.release()


@dataclass(frozen=True)
class _Stress:
    """A stressed run's overlay and what it was computed from (for the post-checks)."""

    overlay: scenario_overlay.Overlay
    directions: Mapping[str, Any]
    buckets: tuple[Any, ...]
    shocks: tuple[scenario_overlay.Shock, ...]
    source_system_code: str | None


# --- calculation runs (BE-15.4, BE-15.5) ------------------------------------------------------

RUN_NOT_FOUND = "Calculation run not found."


class RunQueries:
    """The base of `CalculationService`: its collaborators and the run reads."""

    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        executor: RunExecutor | None = None,
        *,
        system_actor: str | None = None,
    ) -> None:
        """`executor`: where module runs execute (BE-33.1; None: inline). `system_actor`: a
        system job (the run sweeper) acting in `principal`'s tenant, recorded as the actor
        with no user."""
        self.session = session
        self.principal = principal
        self.executor = executor or INLINE
        self.actor = system_actor or principal.actor
        self.actor_user_id = None if system_actor else principal.user_id
        self.repo = CalculationRepository(session, principal.scope)
        self.snapshots = SnapshotService(session, principal)
        self.rules = RulesService(session, principal)
        self.domain = DomainService(session, principal)

    async def _refuse(self, error: ApiError, resource_id: uuid.UUID | None = None) -> NoReturn:
        await refuse(
            self.session,
            self.principal,
            error,
            action=CREATE_RUN,
            resource_type="calculation_run",
            resource_id=resource_id,
        )

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

    @staticmethod
    def validation_status(run: CalculationRun) -> ValidationStatus:
        """D-56 §4: the status of the module version the run pinned (MODULE_VALIDATION), for
        the response wrapper only; never written to the manifest or the result document."""
        m = run.manifest["module"]
        return validation_status_of(m["code"], m["version"])

    @staticmethod
    def result_context(run: CalculationRun) -> dict[str, Any]:
        """D-56 §5 (BE-30.3): a result read's `context` block, JSON-ready. It is read from the
        run's pins (the stored manifest and the written-once outcome), never computed: module,
        engine and rule versions, the reporting date and the D-35 statement come from the
        manifest, the currencies from the outcome (empty until the run ends). Response wrapper
        only: not hashed, so manifest_sha256 and result_sha256 are unchanged."""
        manifest = run.manifest
        module = manifest["module"]
        outcome = run.outcome or {}
        return {
            "units": {
                "amount": CONTEXT_AMOUNT_UNIT,
                "ratio": CONTEXT_RATIO_UNITS.get(module["code"]),
                "days": CONTEXT_DAYS_UNIT,
            },
            "currency": {
                "basis": CONTEXT_CURRENCY_BASIS,
                "codes": sorted(outcome.get("currencies", [])),
            },
            "as_of_date": manifest["reporting_date"],
            "methodology": {
                "module_code": module["code"],
                "module_version": module["version"],
                "engine_version": manifest["engine_version"],
                "rule_versions": [
                    {
                        "rule_code": r["rule_code"],
                        "rule_version_id": r["rule_version_id"],
                        "version": r["version"],
                    }
                    for r in manifest["rule_set"]["members"]
                ],
            },
            "validation_status": validation_status_of(module["code"], module["version"]).value,
            "statement": manifest["labels"]["statement"],
            "consolidation": CONTEXT_CONSOLIDATION,
        }

    async def _run(self, run_id: uuid.UUID) -> CalculationRun:
        run = await self.repo.fresh(run_id)
        if run is None:
            raise ApiError(404, "not_found", RUN_NOT_FOUND)
        return run

    async def get(self, run_id: uuid.UUID) -> RunRow:
        """One run with its input refs and warnings: 404 alike for an absent and a foreign
        run (D-04)."""
        run = await self._run(run_id)
        pins = await self.repo.scenario_pins([run.id]) if is_stressed(run) else []
        return RunRow(
            run,
            self._module_of(run, await self._modules()),
            await self.repo.input_refs(run.id),
            await self.repo.warnings(run.id),
            pins[0] if pins else None,
        )

    async def shown_manifest(self, run: CalculationRun) -> dict[str, Any]:
        """The run's manifest as the caller may read it (D-33). A stressed run's pinned shocks
        keep shocks_sha256 (the pin, over the stored keys) but each picked instrument key the
        caller is not cleared for reads as a MaskedValue, exactly as the scenario detail masks
        it. Non-stressed manifests are returned as stored."""
        section = run.manifest.get("scenario")
        if section is None:
            return run.manifest
        [shocks] = await self.domain.mask_instrument_keys([list(section["shocks"])])
        return {**run.manifest, "scenario": {**section, "shocks": shocks}}

    async def page(
        self,
        limit: int,
        offset: int,
        module_code: str | None,
        status: CalculationRunStatus | None,
        stressed: bool | None = None,
    ) -> tuple[list[RunRow], int]:
        modules = await self._modules()
        ids = None
        if module_code is not None:
            ids = [i for i, m in modules.items() if m.code == module_code]
        runs, total = await self.repo.newest(limit, offset, ids, status, stressed)
        pins = {
            p.calculation_run_id: p
            for p in await self.repo.scenario_pins([r.id for r in runs if is_stressed(r)])
        }
        return [
            RunRow(r, self._module_of(r, modules), scenario=pins.get(r.id)) for r in runs
        ], total

    async def results(
        self,
        run_id: uuid.UUID,
        currency: str | None,
        bucket_code: str | None,
        limit: int,
        offset: int,
    ) -> ResultsView:
        run = await self._run(run_id)
        values: Sequence[IntermediateValue | ScenarioContribution]
        if is_stressed(run):  # D-45 §5: a stressed run's parts are its contributions
            values, total = await self.repo.scenario_contributions(
                run.id, currency, bucket_code, limit, offset
            )
        else:
            values, total = await self.repo.intermediates(
                run.id, currency, bucket_code, limit, offset
            )
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
        changed: list[tuple[uuid.UUID, str, str, str, int]] = []
        if is_stressed(run):  # D-46 §3: a stressed run counts distinct flows, not parts
            parts = await self.repo.scenario_part_counts(run.id)
            # ... and masks a part changed by too few flows, so that stressed - baseline does
            # not give back the delta the comparison masks (whoever reads the stressed run).
            row_ids = {(r.currency, r.bucket_code): r.id for r in rows}
            found = await self.repo.scenario_changed_counts(run.id)
            for unit, bucket, direction, origin, n, codes in found:
                row_id = row_ids.get((unit, bucket))
                if row_id is not None:
                    changed.append((row_id, direction, origin, storable(most_sensitive(codes)), n))
        else:
            parts = await self.repo.part_counts(run.id)
        return LadderView(
            row,
            version_id,
            await self.rules.bucket_definitions(version_id),
            rows,
            ladder_classes(rows, parts, min_aggregate_count, changed),
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

    async def scenario_members(
        self, scenario_run_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[ScenarioMember]]:
        """Each scenario run's member runs (re-runs excluded): single-shock runs by shock,
        then the combined run."""
        found: dict[uuid.UUID, list[ScenarioMember]] = {}
        for pin, run in await self.repo.scenario_members(scenario_run_ids):
            found.setdefault(pin.scenario_run_id, []).append(ScenarioMember(pin, run))
        for members in found.values():
            members.sort(key=lambda m: (m.pin.combined, list(m.pin.shock_subset)))
        return found

    async def scenario_changed(
        self, run_id: uuid.UUID
    ) -> list[tuple[str, str, str, uuid.UUID, str]]:
        """(currency, bucket code, direction, cash-flow event, classification) per changed
        non-zero part, under its bucket before and after (D-46 §3)."""
        return await self.repo.scenario_changed(run_id)

    async def scenario_parts(
        self,
        run_id: uuid.UUID,
        currency: str | None,
        bucket_code: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ScenarioPartRow], int]:
        """The drill-down (D-46 §4): a stressed run's changed parts (moved, stayed, added) in
        a bucket before or after, with each part's cash-flow event and instrument."""
        values, total = await self.repo.scenario_contributions(
            run_id, currency, bucket_code, limit, offset, changed_only=True
        )
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
        rows = []
        for v in values:
            instrument = versions.get(v.instrument_version_id) if v.instrument_version_id else None
            rows.append(
                ScenarioPartRow(
                    v,
                    events[v.cashflow_event_id],
                    InstrumentRef(instrument[0].instrument_id, instrument[1])
                    if instrument
                    else None,
                    pins.classes(instrument[0].schema_version_id) if instrument else {},
                )
            )
        return rows, total

    # --- for the rule registry reads (BE-16.2) ----------------------------------------------

    async def runs_in_rule_sets(
        self, rule_set_ids: Sequence[uuid.UUID], limit: int
    ) -> tuple[int, list[CalculationRun]]:
        """How many runs pin one of these rule sets, and the newest `limit` of them."""
        return await self.repo.in_rule_sets(rule_set_ids, limit)
