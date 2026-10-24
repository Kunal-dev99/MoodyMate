"""calculations.execution (BE-35.2: split from calculations.service, which re-exports it)

`RunExecution`: a written RUNNING run executed and finished (D-31 §3-5). The inputs are loaded
from the pinned snapshot, the module runs as a pure function on the executor (inline or a
worker pool, D-59), the post-checks are recomputed independently, and the run ends once,
with its outputs and `result_sha256` (a stressed run with its parts and applications,
D-45 §5). Also the scenario validation counts (the overlay on a baseline's inputs).
"""

import logging
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from decimal import Context, localcontext
from types import MappingProxyType
from typing import Any

from app.calculations import scenario_overlay
from app.calculations.constants import (
    AGGREGATE_CLASSIFICATION,
    FINISH_RUN,
    CalculationRunStatus,
    IntermediateCode,
    PartChange,
    PostCheck,
    WarningSeverity,
)
from app.calculations.models import (
    CalculationWarning,
    DatasetSnapshotMember,
    HqlaAssetResult,
    IntermediateValue,
    LcrResult,
    LiquidityGapResult,
    ScenarioContribution,
    ScenarioShockApplication,
)
from app.calculations.modules.base import (
    CashFlowInput,
    Contribution,
    HqlaAsset,
    InstrumentInput,
    LcrRow,
    ModuleInputs,
    ModuleResult,
    ModuleWarning,
    PinnedParameters,
    PinnedRule,
    PositionInput,
)
from app.calculations.modules.maturity_ladder import ParameterError, parse_buckets, parse_directions
from app.calculations.queries import RunQueries, ScenarioRunSpec, _Pinned, _Run, _Stress
from app.calculations.repository import RunNotRunning
from app.calculations.results import (
    SUCCEEDED,
    ScenarioOutputs,
    W,
    _public,
    definitions_match,
    kept_lcr,
    lcr_post_checks,
    post_checks,
    reproduce,
)
from app.core.classification import most_sensitive, storable
from app.core.decimals import exact_context
from app.governance.constants import AuditOutcome, ClassificationCode
from app.governance.service import audit
from app.rules.constants import CASHFLOW_DIRECTION, MATURITY_BUCKETS
from app.rules.models import MaturityBucketDefinition
from app.rules.service import exact_json

logger = logging.getLogger("app.calculations")


class RunExecution(RunQueries):
    """Executing and finishing a run (the middle of `CalculationService`)."""

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
        stress: _Stress | None = None
        run_inputs = inputs
        if state.scenario is not None:  # D-45 §2: the module runs on the stressed copy
            staged = await self._stress(state, inputs)
            if isinstance(staged, ModuleWarning):
                await self._end(state, CalculationRunStatus.BLOCKED, ModuleResult(), [staged], {})
                return
            stress, run_inputs = staged, staged.overlay.inputs
        result = await self._module_run(state, run_inputs, parameters, state.implementation.context)
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
        unstressed = None
        if stress is not None:  # D-45 §6: the unstressed module run, for conservation
            unstressed = await self._module_run(state, inputs, parameters, exact_context)
        with localcontext(exact_context()):
            if "lcr_result" in spec.output_contract["results"]:
                checks = lcr_post_checks(inputs, result, state.implementation.rounding)
            else:
                checks = post_checks(run_inputs, result, buckets)
            if stress is not None:  # D-45 §6
                # SCENARIO_OVERLAY checks the stored rows against a recomputation: the overlay
                # recomputed from the unstressed inputs (through scenario_overlay.run_overlay,
                # the same seam the run used) equals the parts and applications the run stores.
                # SCENARIO_CONSERVATION checks the arithmetic: it recomputes the amounts from
                # the declared parameters, never from the overlay's own sums. So a tampered
                # run_overlay fails SCENARIO_CONSERVATION only (both sides of SCENARIO_OVERLAY
                # come from it).
                assert state.scenario is not None and unstressed is not None
                expected = scenario_overlay.run_overlay(
                    inputs,
                    stress.directions,
                    stress.buckets,
                    state.scenario.shocks,
                    state.scenario.shock_subset,
                    stress.source_system_code,
                )
                checks[PostCheck.SCENARIO_OVERLAY] = scenario_overlay.overlay_matches(
                    expected, stress.overlay
                )
                checks[PostCheck.SCENARIO_CONSERVATION] = scenario_overlay.conserved(
                    inputs, stress.shocks, stress.overlay, unstressed, result
                )
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
        await self._end(state, status, result, warnings, checks, buckets, stress)

    async def _module_run(
        self,
        state: _Run,
        inputs: ModuleInputs,
        parameters: PinnedParameters,
        context: Callable[[], Context],
    ) -> ModuleResult:
        """The module's pure run(), inline or in a worker (BE-33.1), by the run's deadline.

        In pool mode the session's read-only transaction ends first: waiting for a worker,
        and then for the worker (up to CALC_RUN_TIMEOUT_SECONDS), must not keep a pooled
        connection checked out, idle in transaction. Only reads are open here (the run row
        was committed RUNNING before `_execute`), and expire_on_commit=False keeps the rows
        `state` holds usable; the post-checks and `_finish` read and write in a new one."""
        if self.executor.pooled:
            await self.session.commit()
        return await self.executor.run(
            state.implementation, inputs, parameters, context, state.deadline
        )

    async def _stress(self, state: _Run, inputs: ModuleInputs) -> _Stress | ModuleWarning:
        """The overlay of a stressed run on its pinned inputs (D-45 §2), or the value-free
        PARAMETERS_INVALID warning that BLOCKS it (no CHECK is widened)."""
        assert state.scenario is not None
        scenario = state.scenario

        def blocked(rule: str | None = None) -> ModuleWarning:
            details = (
                {"rule": rule}
                if rule
                else {"scenario_version_id": str(scenario.scenario_version_id)}
            )
            return ModuleWarning(
                W.PARAMETERS_INVALID,
                WarningSeverity.ERROR,
                "The scenario overlay cannot be applied to the pinned inputs (D-45 §2).",
                details=details,
            )

        directions_version = state.versions.get(CASHFLOW_DIRECTION)
        bucket_version = state.versions.get(MATURITY_BUCKETS)
        if directions_version is None or bucket_version is None:
            return blocked()
        try:
            directions = parse_directions(directions_version.parameters)
        except ParameterError:
            return blocked(CASHFLOW_DIRECTION)
        try:
            buckets = parse_buckets(bucket_version.parameters)
        except ParameterError:
            return blocked(MATURITY_BUCKETS)
        code = await self._cash_flow_source_code(state.members)
        try:
            shocks = scenario_overlay.parse_shocks(scenario.shocks, scenario.shock_subset)
            # Through the module attribute: the BE-25.3 tampered-part test double replaces it.
            overlay = scenario_overlay.run_overlay(
                inputs, directions, buckets, scenario.shocks, scenario.shock_subset, code
            )
        except (scenario_overlay.OverlayError, ArithmeticError):
            return blocked()
        return _Stress(overlay, directions, buckets, shocks, code)

    async def _cash_flow_source_code(
        self, members: Mapping[str, DatasetSnapshotMember]
    ) -> str | None:
        """The source system code of the snapshot's cash-flow import (D-44 §3: a source
        selector matches all of a snapshot's cash flows or none in v1)."""
        member = members.get("cashflow_event")
        if member is None:
            return None
        codes = await self.snapshots.imports.source_codes({member.source_system_id})
        return codes.get(member.source_system_id)

    async def _end(
        self,
        state: _Run,
        status: CalculationRunStatus,
        result: ModuleResult,
        warnings: Sequence[ModuleWarning],
        checks: Mapping[PostCheck, bool],
        buckets: Sequence[MaturityBucketDefinition] = (),
        stress: _Stress | None = None,
    ) -> None:
        scenario = None
        if state.scenario is not None:  # a stressed run hashes its parts (none if blocked)
            scenario = ScenarioOutputs()
            if stress is not None:
                parts = stress.overlay.part_of()
                placed = [parts[c.cashflow_event_id] for c in result.contributions]
                scenario = ScenarioOutputs(tuple(placed), stress.overlay.applications)
        await self._finish(
            state.run_id, state.counts, status, result, warnings, checks, buckets, state, scenario
        )

    async def _fail_if_running(self, run_id: uuid.UUID, error: str) -> bool:
        """`_fail`, unless the run already ended (the sweeper, a timeout or another replay got
        there first): True when this call ended it."""
        try:
            await self._fail(run_id, {}, error)
        except RunNotRunning:
            await self.session.rollback()
            return False
        return True

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
        scenario: ScenarioOutputs | None = None,
    ) -> None:
        """Store the outputs (results only for a successful run, which needs its pinned
        `state`), end the run with its result hash and outcome, audit it and commit. A
        stressed run (`scenario`) stores its parts and applications instead of
        `intermediate_value` rows (D-45 §5)."""
        if scenario is None and state is not None and state.scenario is not None:
            scenario = ScenarioOutputs()
        status, gap, contributions, warnings, checks, result_sha256 = reproduce(
            state.expected_result_sha256 if state is not None else None,
            status,
            result.liquidity_gap,
            result.contributions,
            warnings,
            checks,
            lcr=result.lcr,
            assets=result.hqla_assets,
            scenario=scenario,
        )
        keep = status in SUCCEEDED
        lcr, assets = kept_lcr(status, result.lcr, result.hqla_assets)
        tenant, actor = self.principal.tenant_id, self.actor
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
        placed = len(contributions)
        if scenario is not None:  # stressed runs write no intermediate_value rows (D-45 §5)
            if contributions:
                assert state is not None
                await self._store_scenario(
                    run_id, contributions, scenario, classification, state, gap_ids
                )
            placed = len({p.cashflow_event_id for p in scenario.parts}) if contributions else 0
        intermediates = []
        for position, c in enumerate(contributions if scenario is None else [], start=1):
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
        in_scope, used, out_of_scope = counts.get("cashflow_event", 0), placed, 0
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
        audit(
            self.session,
            tenant_id=tenant,
            actor=actor,
            actor_user_id=self.actor_user_id,
            action=FINISH_RUN,
            resource_type="calculation_run",
            outcome=AuditOutcome.SUCCESS if keep else AuditOutcome.FAILURE,
            resource_id=str(run_id),
            details=details,
            details_classification=_public(details),
        )
        await self.session.commit()

    async def _store_scenario(
        self,
        run_id: uuid.UUID,
        contributions: Sequence[Contribution],
        scenario: ScenarioOutputs,
        classification: str,
        state: _Run,
        gap_ids: Mapping[tuple[str, str], uuid.UUID],
    ) -> None:
        """A stressed run's parts (one `scenario_contribution` per placed part, with its
        cash-flow event's lineage) and its applications (D-45 §5)."""
        tenant, actor = self.principal.tenant_id, self.actor
        bucket_version = state.versions[MATURITY_BUCKETS]
        buckets = parse_buckets(bucket_version.parameters)
        rows = []
        for position, (c, part) in enumerate(zip(contributions, scenario.parts, strict=True), 1):
            event = state.events[part.cashflow_event_id]
            before = c.bucket.code
            if part.change != PartChange.UNCHANGED:
                before = next(b.code for b in buckets if b.holds(part.days_before))
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "cashflow_event_id": part.cashflow_event_id,
                    "part_path": part.path,
                    "part_id": part.part_id,
                    "shock_sequences": list(part.shock_sequences),
                    "change": part.change.value,
                    "value": c.amount,
                    "unit": c.currency,
                    "classification": classification,
                    "liquidity_gap_result_id": gap_ids[(c.currency, c.bucket.code)],
                    "bucket_code": c.bucket.code,
                    "original_bucket_code": before,
                    "direction": c.direction.value,
                    "origin": c.origin.value,
                    "days_after_reporting_date": c.days_after_reporting_date,
                    "original_days": part.days_before
                    if part.change != PartChange.UNCHANGED
                    else c.days_after_reporting_date,
                    "rule_version_ids": [str(r) for r in c.rule_version_ids],
                    "instrument_version_id": c.instrument_version_id,
                    "canonical_record_id": event.canonical_record_id,
                    "import_job_id": event.import_job_id,
                    "source_record_id": event.source_record_id,
                    "source_ordinal": event.source_ordinal,
                    "created_by": actor,
                }
            )
        await self.repo.insert_rows(ScenarioContribution, rows)
        await self.repo.insert_rows(
            ScenarioShockApplication,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "calculation_run_id": run_id,
                    "sequence": position,
                    "cashflow_event_id": a.cashflow_event_id,
                    "shock_sequence": a.shock_sequence,
                    "template": a.template.value,
                    "unit": a.currency,
                    "parts_matched": a.parts_matched,
                    "parts_after": a.parts_after,
                    "moved_amount": a.moved_amount,
                    "added_amount": a.added_amount,
                    "delay_days": a.delay_days,
                    "moved_to_day": a.moved_to_day,
                    "classification": classification,
                    "created_by": actor,
                }
                for position, a in enumerate(scenario.applications, start=1)
            ],
        )

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
        tenant, actor = self.principal.tenant_id, self.actor
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
        return storable(most_sensitive(codes))

    # --- for the scenario engine and comparison (Phase 25, D-45, D-46) -----------------------

    async def scenario_counts(
        self, pinned: _Pinned, shocks: Sequence[Mapping[str, Any]]
    ) -> list[scenario_overlay.ShockCount] | None:
        """The validation dry run (D-45 §10): the overlay of each shock ALONE on the
        baseline's pinned inputs, with its value-free counts. Nothing is written. None when
        the pinned rules cannot carry the overlay (a run would be BLOCKED)."""
        members = {m.schema_code: m for m in await self.snapshots.members(pinned.snapshot.id)}
        state = _Run(
            uuid.uuid4(),
            pinned.reporting_date,
            pinned.view,
            pinned.implementation,
            members,
            pinned.versions,
            await self.domain.schema_pins(),
        )
        inputs = await self._inputs(state)
        found: list[scenario_overlay.ShockCount] = []
        for n in range(1, len(shocks) + 1):
            state.scenario = ScenarioRunSpec(
                uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1,
                tuple(shocks), "0" * 64, (n,), "EXPLORATION",
            )  # fmt: skip
            staged = await self._stress(state, inputs)
            if isinstance(staged, ModuleWarning):
                return None
            found += staged.overlay.counts
        return found
