"""calculations.runs (BE-35.2: split from calculations.service, which re-exports it)

`CalculationService`: `POST /calculation-runs` (BE-15.4), re-runs (D-31 §5) and stressed runs
(D-45). Refused before any run is written (409, audited DENIED): `calculations_disabled`,
`idempotency_conflict`, 404 `not_found` and `module_not_available`; then the pins, the
manifest (`manifest_sha256` over the pinned content only) and the input refs are written
RUNNING and committed, and the run executes (`RunExecution`). Also the stale-run sweeper
(BE-33.3) and the `workers_busy` refusal (D-59 §4).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calculations.constants import (
    CREATE_RUN,
    ENGINE_VERSION,
    MANIFEST_VERSION,
    CalculationRunStatus,
    InputRefType,
    RunRefusal,
    RunUnavailable,
)
from app.calculations.execution import RunExecution
from app.calculations.executor import Admission, WorkersBusy
from app.calculations.models import (
    CalculationInputRef,
    CalculationRun,
    CalculationScenarioPin,
    DatasetSnapshot,
    DatasetSnapshotMember,
)
from app.calculations.modules import registered
from app.calculations.queries import (
    RUN_NOT_FOUND,
    BaselineCheck,
    RerunSpec,
    RunRow,
    RunSpec,
    ScenarioRunSpec,
    _Held,
    _Pinned,
    _Run,
    is_stressed,
)
from app.calculations.repository import RunNotRunning, StaleRunRepository
from app.calculations.results import SUCCEEDED, _public
from app.core import metrics
from app.core.canonical import HashKind, sha256_for
from app.core.correlation import correlation_id_var, get_correlation_id
from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.ratelimit import RETRY_AFTER
from app.domain.service import SchemaPins
from app.governance.service import audit_by
from app.rules.constants import ILLUSTRATIVE_STATEMENT, MATURITY_LADDER, ImplementationStatus
from app.rules.models import AssumptionSet, RuleDefinition, RuleVersion
from app.rules.service import ModuleView

logger = logging.getLogger("app.calculations")
STALE_RUN = "stale_run"
# BE-33.3: the run sweeper's audit actor, and the runs it ends per pass at most.
SWEEPER_ACTOR = "system:run-sweeper"
SWEEP_BATCH = 100


class CalculationService(RunExecution):
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

    async def _pins_of(self, original: CalculationRun) -> tuple[_Pinned | None, ApiError | None]:
        """What `original` pinned, resolved again (BE-16.3): the same snapshot, reporting date,
        module version, rule set (even SUPERSEDED versions) and assumption set, on the same
        engine version; or why that is not possible (no refusal is audited here)."""
        if (
            original.engine_version != ENGINE_VERSION
            or original.manifest.get("manifest_version") != MANIFEST_VERSION
        ):
            return None, self._not_reproducible(original.id, "engine_changed")
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
            return None, self._unavailable(original.module_version_id, "not_implemented")
        pinned = await self.rules.pinned(original.rule_set_id)
        assumption_set = await self.rules.assumption_set_by_id(original.assumption_set_id)
        if snapshot is None or assumption_set is None or not pinned:
            return None, self._not_reproducible(original.id, "pins_changed")
        return (
            _Pinned(
                snapshot,
                view,
                implementation,
                {d.code: d for d, _ in pinned},
                {d.code: v for d, v in pinned},
                assumption_set,
                original.reporting_date,
            ),
            None,
        )

    async def _rerun_pins(self, spec: RerunSpec) -> _Pinned:
        """A re-run pins exactly what its original pinned (BE-16.3); a re-run of a stressed
        run also re-pins its scenario version and subset, even once the version is CLOSED
        (D-45 §8)."""
        original = await self.repo.fresh(spec.rerun_of)
        if original is None:
            await self._refuse(ApiError(404, "not_found", RUN_NOT_FOUND), spec.rerun_of)
        pinned, error = await self._pins_of(original)
        if error is not None or pinned is None:
            error = error or self._not_reproducible(original.id, "pins_changed")
            unavailable = error.code == RunRefusal.MODULE_NOT_AVAILABLE
            await self._refuse(error, None if unavailable else original.id)
        scenario = None
        if is_stressed(original):
            found = await self.repo.scenario_pins([original.id])
            if len(found) != 1:
                await self._refuse(self._not_reproducible(original.id, "pins_changed"), original.id)
            scenario = ScenarioRunSpec.of(original, found[0])
        return replace(pinned, rerun_of=original, scenario=scenario)

    async def baseline_check(self, baseline_run_id: uuid.UUID) -> BaselineCheck:
        """Whether this run can be a scenario baseline (D-45 §1, §10): a SUCCEEDED (or
        SUCCEEDED_WITH_WARNINGS), non-stressed maturity_ladder run of the caller's tenant
        (else `not_found` or `invalid_baseline`) whose pins still resolve to the same content
        (else `baseline_not_reproducible`). Nothing is audited here."""
        run = await self.repo.fresh(baseline_run_id)
        if run is None:
            return BaselineCheck(None, "not_found")
        module = self._module_of(run, await self._modules())
        if (
            module.code != MATURITY_LADDER
            or run.status not in SUCCEEDED
            or is_stressed(run)
            or await self.repo.scenario_pins([run.id])
        ):
            return BaselineCheck(run, "invalid_baseline")
        pinned, error = await self._pins_of(run)
        if error is not None or pinned is None:
            return BaselineCheck(run, "baseline_not_reproducible")
        rule_set = await self.rules.rule_set_for(list(pinned.versions.values()))
        manifest = self._manifest(
            pinned.reporting_date,
            pinned.view,
            pinned.snapshot,
            {m.schema_code: m for m in await self.snapshots.members(pinned.snapshot.id)},
            rule_set.id,
            rule_set.content_sha256,
            pinned.definitions,
            pinned.versions,
            pinned.assumption_set,
            await self.domain.schema_pins(),
        )
        if (
            sha256_for(HashKind.CALCULATION_MANIFEST, manifest) != run.manifest_sha256
            or rule_set.id != run.rule_set_id
        ):
            return BaselineCheck(run, "baseline_not_reproducible")
        return BaselineCheck(run, None, pinned)

    async def _scenario_pins(self, spec: ScenarioRunSpec) -> _Pinned:
        """A stressed run copies its baseline's pins exactly (D-45 §1)."""
        check = await self.baseline_check(spec.baseline_run_id)
        if check.refusal == "not_found" or check.run is None:
            await self._refuse(ApiError(404, "not_found", RUN_NOT_FOUND), spec.baseline_run_id)
        if check.refusal is not None or check.pinned is None:
            await self._refuse(
                ApiError(
                    409,
                    check.refusal or "baseline_not_reproducible",
                    "The baseline cannot carry a stressed run (D-45 §10).",
                    {"baseline_run_id": str(spec.baseline_run_id)},
                ),
                spec.baseline_run_id,
            )
        return replace(check.pinned, scenario=spec, baseline=check.run)

    def _scenarios_disabled(self) -> ApiError:
        return ApiError(
            409,
            RunRefusal.SCENARIOS_DISABLED,
            "Stressed runs are switched off in this environment (SCENARIOS_ENABLED); existing "
            "runs stay readable.",
        )

    async def create(
        self,
        spec: RunSpec | RerunSpec | ScenarioRunSpec,
        idempotency_key: str,
        enabled: bool,
        stale_seconds: int = 900,
        scenarios_enabled: bool = True,
        *,
        admission: Admission | None = None,
        respond_async: bool = False,
    ) -> tuple[RunRow, bool]:
        """Pin, run and post-check the module; the finished run and whether it ran now
        (False: a replay of the key, the original run returned). A replay that finds its run
        still RUNNING after `stale_seconds` (CALC_RUN_STALE_SECONDS) ends it FAILED with
        ENGINE_ERROR first: its engine is presumed gone, and nothing else would end it.
        A `ScenarioRunSpec`, or a re-run of a stressed run, is a stressed run (D-45): refused
        409 `scenarios_disabled` while SCENARIOS_ENABLED is off.

        Phase 33 (D-59): a new run is admitted to the executor before anything is written
        (pool mode: 503 `workers_busy`, audited DENIED, when it is full) unless the caller
        passes the `admission` it holds (a scenario run's members). `respond_async` returns
        the run RUNNING once it is written and executes it in the background."""
        held = _Held(admission)
        try:
            return await self._create(
                spec, idempotency_key, enabled, stale_seconds, scenarios_enabled, held,
                respond_async,
            )  # fmt: skip
        finally:
            held.release()

    async def _create(
        self,
        spec: RunSpec | RerunSpec | ScenarioRunSpec,
        idempotency_key: str,
        enabled: bool,
        stale_seconds: int,
        scenarios_enabled: bool,
        held: _Held,
        respond_async: bool,
    ) -> tuple[RunRow, bool]:
        if not enabled:
            await self._refuse(
                ApiError(
                    409,
                    RunRefusal.CALCULATIONS_DISABLED,
                    "Calculations are switched off in this environment; existing snapshots "
                    "and runs stay readable.",
                )
            )
        if isinstance(spec, ScenarioRunSpec) and not scenarios_enabled:
            await self._refuse(self._scenarios_disabled())
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
                await self._fail_if_running(existing.id, STALE_RUN)
            return await self.get(existing.id), False
        if held.admission is None:  # D-59 §4: before anything is written
            try:
                held.admission = self.executor.admit()
            except WorkersBusy as busy:
                await self._refuse(workers_busy(busy.retry_after))

        if isinstance(spec, RerunSpec):
            pinned = await self._rerun_pins(spec)
        elif isinstance(spec, ScenarioRunSpec):
            pinned = await self._scenario_pins(spec)
        else:
            pinned = await self._new_pins(spec)
        if pinned.scenario is not None and not scenarios_enabled:
            await self._refuse(self._scenarios_disabled())
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
        baseline = pinned.baseline
        if baseline is not None and (
            sha256_for(HashKind.CALCULATION_MANIFEST, manifest) != baseline.manifest_sha256
            or rule_set.id != baseline.rule_set_id
        ):  # the stressed run's manifest equals its baseline's but for `scenario` (D-45 §4)
            await self._refuse(
                ApiError(
                    409,
                    "baseline_not_reproducible",
                    "The baseline's pins no longer resolve to the same content (D-45 §10).",
                    {"baseline_run_id": str(baseline.id)},
                ),
                baseline.id,
            )
        if pinned.scenario is not None:
            manifest["scenario"] = pinned.scenario.manifest_section()
        manifest_sha256 = sha256_for(HashKind.CALCULATION_MANIFEST, manifest)
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
        scenario = pinned.scenario
        if scenario is not None:  # D-45 §4: the scenario pin (its own table, D-01)
            await self.repo.insert_rows(
                CalculationScenarioPin,
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": self.principal.tenant_id,
                        "calculation_run_id": run.id,
                        "scenario_run_id": scenario.scenario_run_id,
                        "scenario_id": scenario.scenario_id,
                        "scenario_version_id": scenario.scenario_version_id,
                        "version": scenario.version,
                        "shocks_sha256": scenario.shocks_sha256,
                        "shock_subset": list(scenario.shock_subset),
                        "combined": scenario.combined,
                        "approval_state": scenario.approval_state,
                        "baseline_run_id": scenario.baseline_run_id,
                        "created_by": actor,
                    }
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
        if scenario is not None:  # value-free: ids, the subset and the shocks' sha256
            details |= {
                "scenario_run_id": str(scenario.scenario_run_id),
                "scenario_version_id": str(scenario.scenario_version_id),
                "shock_subset": ",".join(str(n) for n in scenario.shock_subset),
                "shocks_sha256": scenario.shocks_sha256,
                "baseline_run_id": str(scenario.baseline_run_id),
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
            scenario=scenario,
            deadline=self.executor.deadline(),
        )
        if respond_async:  # D-59 §2: answered RUNNING; executed by the background task
            self.executor.spawn(self._in_background(state, held.hand_off()))
            return await self.get(state.run_id), True
        started = time.perf_counter()
        with metrics.run_executing():  # BE-34.4: busy while it executes
            try:
                await self._execute(state)
            except BaseException as exc:  # the engine's own failure: recorded, never silent
                await self._record_failure(state.run_id, state.counts, exc)
                if not isinstance(exc, Exception):
                    raise  # cancellation and interpreter exits go on, once the run is ended
        out = await self.get(state.run_id)
        metrics.observe_run(out.run.status, time.perf_counter() - started)
        return out, True

    async def _in_background(self, state: _Run, admission: Admission) -> None:
        """A respond-async run (BE-33.2): executed in its own session, as the synchronous path
        does, its failure recorded the same way. If even that fails, the run stays RUNNING
        and the sweeper ends it (BE-33.3). The admission is released when it ends."""
        try:
            async with AsyncSession(self.session.bind, expire_on_commit=False) as session:
                service = CalculationService(session, self.principal, self.executor)
                started = time.perf_counter()
                with metrics.run_executing():  # BE-34.4: busy while it executes
                    try:
                        await service._execute(state)
                    except BaseException as exc:
                        await service._record_failure(state.run_id, state.counts, exc)
                        if not isinstance(exc, Exception):
                            raise
                done = await service.get(state.run_id)
                metrics.observe_run(done.run.status, time.perf_counter() - started)
        except Exception as exc:  # recorded where possible; never re-raised into the loop
            logger.warning(
                "calculation_background_error",
                extra={"run_id": str(state.run_id), "error": type(exc).__name__},
            )
        finally:
            admission.release()

    async def _record_failure(
        self, run_id: uuid.UUID, counts: Mapping[str, int], exc: BaseException
    ) -> None:
        """End the run FAILED with ENGINE_ERROR in a fresh session: the request's own session
        may be mid-transaction or unusable. If even that fails, the run stays RUNNING and the
        stale-run rule ends it on the next replay of its key."""
        # The type only: an exception's message or traceback could carry values.
        error = type(exc).__name__
        with contextlib.suppress(Exception):
            await self.session.rollback()
        if isinstance(exc, RunNotRunning):  # already ended elsewhere: one terminal move only
            logger.info("calculation_run_already_ended", extra={"run_id": str(run_id)})
            return
        logger.warning("calculation_engine_error", extra={"run_id": str(run_id), "error": error})
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


# --- Phase 33: refusal and run sweeper (BE-33.1, BE-33.3, D-59 §4) -----------------------------


def workers_busy(retry_after: int) -> ApiError:
    """503 `workers_busy` (D-59 §4): retryable, with Retry-After; the route audits it DENIED
    under its own action and writes no run."""
    return ApiError(
        503,
        RunUnavailable.WORKERS_BUSY,
        "Every calculation worker is busy and the queue is full. Retry after the time in "
        "Retry-After.",
        {"retry_after_seconds": retry_after},
        retryable=True,
        headers={RETRY_AFTER: str(retry_after)},
    )


def _system_principal(tenant_id: uuid.UUID) -> Principal:
    """The sweeper's scope in one tenant: no user, no role, no permission."""
    return Principal(
        user_id=uuid.UUID(int=0),
        tenant_id=tenant_id,
        issuer="system",
        subject=SWEEPER_ACTOR,
        display_name="Run sweeper",
        email=None,
        roles=frozenset(),
        permissions=frozenset(),
    )


async def sweep_stale_runs(
    sessions: async_sessionmaker[AsyncSession], stale_seconds: int
) -> list[uuid.UUID]:
    """End FAILED (ENGINE_ERROR, `stale_run`) every RUNNING run older than `stale_seconds`, as
    a replay of its key would, each in its own transaction in its own tenant, audited
    `calculation_run.finish` by `system:run-sweeper`. A run that ended meanwhile is skipped:
    the guarded update makes one terminal move only. The ids it ended."""
    token = correlation_id_var.set(str(uuid.uuid4()))
    try:
        async with sessions() as session:
            found = await StaleRunRepository(session).stale_runs(stale_seconds, SWEEP_BATCH)
        ended = []
        for run_id, tenant_id in found:
            async with sessions() as session:
                service = CalculationService(
                    session, _system_principal(tenant_id), system_actor=SWEEPER_ACTOR
                )
                if await service._fail_if_running(run_id, STALE_RUN):
                    ended.append(run_id)
        if ended:
            logger.info("calculation_runs_swept", extra={"count": len(ended)})
        return ended
    finally:
        correlation_id_var.reset(token)


async def run_sweeper(
    sessions: async_sessionmaker[AsyncSession], stale_seconds: int, interval_seconds: int
) -> None:
    """The periodic sweeper (BE-33.3): a pass every `interval_seconds` until cancelled. A
    failed pass is logged (its type only) and the next one runs."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await sweep_stale_runs(sessions, stale_seconds)
        except Exception as exc:
            logger.warning("calculation_sweep_error", extra={"error": type(exc).__name__})
