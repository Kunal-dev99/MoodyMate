"""calculations.repository

The only code that touches the DB session for snapshots and runs. Every query filters on the
caller's tenant (D-04): every table here carries `tenant_id`. Nothing is deleted; the only
update is a run's single move from RUNNING to its terminal status (`finish`), which the DB
trigger also enforces.

One read is not tenant-scoped: `StaleRunRepository.stale_runs`, for the run sweeper
(BE-33.3), a system job with no principal. It returns only run ids and their tenants; each run
is then ended through a repository scoped to that run's own tenant.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, cast, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.constants import CalculationRunStatus
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
from app.core.locks import advisory_xact_lock
from app.db.base import Base
from app.db.repository import M, ScopedRepository


class RunNotRunning(RuntimeError):
    """The run already made its one terminal move (e.g. the sweeper or a replay ended it)."""

    def __init__(self) -> None:
        super().__init__("the run is not RUNNING")


def _older_than(seconds: int) -> Any:
    return CalculationRun.started_at < func.clock_timestamp() - func.make_interval(
        0, 0, 0, 0, 0, 0, cast(seconds, Integer)
    )


class StaleRunRepository:
    """BE-33.3: the sweeper's one cross-tenant read (no principal: a system job)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def stale_runs(self, seconds: int, limit: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """(run id, tenant id) of RUNNING runs started more than `seconds` ago (database
        clock), oldest first, at most `limit`."""
        result = await self.session.execute(
            select(CalculationRun.id, CalculationRun.tenant_id)
            .where(
                CalculationRun.status == CalculationRunStatus.RUNNING.value,
                _older_than(seconds),
            )
            .order_by(CalculationRun.started_at, CalculationRun.id)
            .limit(limit)
        )
        return [(run_id, tenant_id) for run_id, tenant_id in result.tuples()]


class _Writes(ScopedRepository[M]):
    async def lock(self, key: str) -> None:
        """Serialise writers of one content-addressed or keyed row within the tenant for this
        transaction (a second writer then finds the first one's row)."""
        await advisory_xact_lock(self.session, key, self.scope.tenant_id)

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def insert_rows(self, model: type[Base], rows: list[dict[str, Any]]) -> None:
        """One multi-row INSERT (rows built by the service, all in the caller's tenant)."""
        if rows:
            await self.session.execute(insert(model), rows)

    async def flush(self) -> None:
        await self.session.flush()


class SnapshotRepository(_Writes[DatasetSnapshot]):
    model = DatasetSnapshot

    async def by_sha(self, content_sha256: str) -> DatasetSnapshot | None:
        result = await self.session.execute(
            self.scoped().where(DatasetSnapshot.content_sha256 == content_sha256)
        )
        return result.scalar_one_or_none()

    async def newest(self, limit: int, offset: int) -> tuple[list[DatasetSnapshot], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(self.scoped().subquery())
        )
        result = await self.session.execute(
            self.scoped()
            .order_by(DatasetSnapshot.created_at.desc(), DatasetSnapshot.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def members(self, snapshot_ids: Sequence[uuid.UUID]) -> list[DatasetSnapshotMember]:
        """Members of these snapshots, by snapshot then schema code."""
        if not snapshot_ids:
            return []
        result = await self.session.execute(
            select(DatasetSnapshotMember)
            .where(
                DatasetSnapshotMember.tenant_id == self.scope.tenant_id,
                DatasetSnapshotMember.dataset_snapshot_id.in_(snapshot_ids),
            )
            .order_by(DatasetSnapshotMember.dataset_snapshot_id, DatasetSnapshotMember.schema_code)
        )
        return list(result.scalars())


class CalculationRepository(_Writes[CalculationRun]):
    model = CalculationRun

    async def fresh(self, run_id: uuid.UUID) -> CalculationRun | None:
        """The run as stored now (not a stale identity-map copy)."""
        result = await self.session.execute(
            self.scoped()
            .where(CalculationRun.id == run_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def by_key(self, actor: str, key: str) -> CalculationRun | None:
        result = await self.session.execute(
            self.scoped().where(
                CalculationRun.created_by == actor, CalculationRun.idempotency_key == key
            )
        )
        return result.scalar_one_or_none()

    async def newest(
        self,
        limit: int,
        offset: int,
        module_version_ids: Sequence[uuid.UUID] | None,
        status: CalculationRunStatus | None,
        stressed: bool | None = None,
    ) -> tuple[list[CalculationRun], int]:
        query = self.scoped()
        if module_version_ids is not None:
            query = query.where(CalculationRun.module_version_id.in_(module_version_ids))
        if status is not None:
            query = query.where(CalculationRun.status == status.value)
        if stressed is not None:  # a stressed run's manifest has a `scenario` key (D-45 §4)
            has_scenario = CalculationRun.manifest.has_key("scenario")
            query = query.where(has_scenario if stressed else ~has_scenario)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(CalculationRun.started_at.desc(), CalculationRun.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def stale(self, run_id: uuid.UUID, seconds: int) -> bool:
        """The run is RUNNING and started more than `seconds` ago (database clock)."""
        found = await self.session.scalar(
            select(CalculationRun.id).where(
                CalculationRun.id == run_id,
                CalculationRun.tenant_id == self.scope.tenant_id,
                CalculationRun.status == CalculationRunStatus.RUNNING.value,
                _older_than(seconds),
            )
        )
        return found is not None

    async def finish(
        self,
        run_id: uuid.UUID,
        status: CalculationRunStatus,
        result_sha256: str,
        outcome: dict[str, Any],
        warning_count: int,
    ) -> None:
        """The run's one move: RUNNING -> its terminal status, with its result hash, end time
        and outcome (the DB trigger refuses anything else)."""
        result = await self.session.execute(
            update(CalculationRun)
            .where(
                CalculationRun.id == run_id,
                CalculationRun.tenant_id == self.scope.tenant_id,
                CalculationRun.status == CalculationRunStatus.RUNNING.value,
            )
            .values(
                status=status.value,
                result_sha256=result_sha256,
                outcome=outcome,
                warning_count=warning_count,
                ended_at=func.clock_timestamp(),
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 1) != 1:
            raise RunNotRunning()

    async def _of_run(self, model: Any, run_id: uuid.UUID) -> list[Any]:
        result = await self.session.execute(
            select(model)
            .where(model.tenant_id == self.scope.tenant_id, model.calculation_run_id == run_id)
            .order_by(model.sequence)
        )
        return list(result.scalars())

    async def input_refs(self, run_id: uuid.UUID) -> list[CalculationInputRef]:
        return await self._of_run(CalculationInputRef, run_id)

    async def warnings(self, run_id: uuid.UUID) -> list[CalculationWarning]:
        return await self._of_run(CalculationWarning, run_id)

    async def gap_rows(self, run_id: uuid.UUID) -> list[LiquidityGapResult]:
        return await self._of_run(LiquidityGapResult, run_id)

    async def lcr_rows(self, run_id: uuid.UUID) -> list[LcrResult]:
        return await self._of_run(LcrResult, run_id)

    async def hqla_assets(self, run_id: uuid.UUID) -> list[HqlaAssetResult]:
        return await self._of_run(HqlaAssetResult, run_id)

    async def part_counts(self, run_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str, str, int]]:
        """Per ladder row, direction, origin and classification: how many contributions with a
        non-zero amount (D-33: a zero contribution does not count toward the minimum)."""
        iv = IntermediateValue
        result = await self.session.execute(
            select(
                iv.liquidity_gap_result_id,
                iv.direction,
                iv.origin,
                iv.classification,
                func.count().filter(iv.value != 0),
            )
            .where(iv.tenant_id == self.scope.tenant_id, iv.calculation_run_id == run_id)
            .group_by(iv.liquidity_gap_result_id, iv.direction, iv.origin, iv.classification)
        )
        return [(r[0], r[1], r[2], r[3], int(r[4])) for r in result]

    async def intermediates(
        self,
        run_id: uuid.UUID,
        currency: str | None,
        bucket_code: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[IntermediateValue], int]:
        query = select(IntermediateValue).where(
            IntermediateValue.tenant_id == self.scope.tenant_id,
            IntermediateValue.calculation_run_id == run_id,
        )
        if currency is not None:
            query = query.where(IntermediateValue.unit == currency)
        if bucket_code is not None:
            query = query.where(IntermediateValue.bucket_code == bucket_code)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(IntermediateValue.sequence).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    # --- Phase 16: runs pinning a rule set ----------------------------

    async def in_rule_sets(
        self, rule_set_ids: Sequence[uuid.UUID], limit: int
    ) -> tuple[int, list[CalculationRun]]:
        if not rule_set_ids:
            return 0, []
        query = self.scoped().where(CalculationRun.rule_set_id.in_(rule_set_ids))
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(CalculationRun.started_at.desc(), CalculationRun.id).limit(limit)
        )
        return int(total or 0), list(result.scalars())

    # --- Phase 25: stressed runs (D-45, D-46) ---------------------------------------------

    async def scenario_pins(self, run_ids: Sequence[uuid.UUID]) -> list[CalculationScenarioPin]:
        """The scenario pins of these runs (stressed runs only have one)."""
        if not run_ids:
            return []
        result = await self.session.execute(
            select(CalculationScenarioPin).where(
                CalculationScenarioPin.tenant_id == self.scope.tenant_id,
                CalculationScenarioPin.calculation_run_id.in_(run_ids),
            )
        )
        return list(result.scalars())

    async def scenario_members(
        self, scenario_run_ids: Sequence[uuid.UUID]
    ) -> list[tuple[CalculationScenarioPin, CalculationRun]]:
        """The member runs of these scenario runs: their pins and runs, re-runs excluded."""
        if not scenario_run_ids:
            return []
        pin, run = CalculationScenarioPin, CalculationRun
        result = await self.session.execute(
            select(pin, run)
            .join(run, run.id == pin.calculation_run_id)
            .where(
                pin.tenant_id == self.scope.tenant_id,
                run.tenant_id == self.scope.tenant_id,
                pin.scenario_run_id.in_(scenario_run_ids),
                run.rerun_of_run_id.is_(None),
            )
            .order_by(pin.scenario_run_id, run.started_at, run.id)
            .execution_options(populate_existing=True)
        )
        return [(p, r) for p, r in result.tuples()]

    async def scenario_part_counts(
        self, run_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, str, str, str, int]]:
        """As `part_counts`, for a stressed run: per ladder row, direction, origin and
        classification, the DISTINCT cash flows with a non-zero part (D-46 §3: two parts of
        one flow are one amount)."""
        sc = ScenarioContribution
        result = await self.session.execute(
            select(
                sc.liquidity_gap_result_id,
                sc.direction,
                sc.origin,
                sc.classification,
                func.count(sc.cashflow_event_id.distinct()).filter(sc.value != 0),
            )
            .where(sc.tenant_id == self.scope.tenant_id, sc.calculation_run_id == run_id)
            .group_by(sc.liquidity_gap_result_id, sc.direction, sc.origin, sc.classification)
        )
        return [(r[0], r[1], r[2], r[3], int(r[4])) for r in result]

    async def scenario_changed_counts(
        self, run_id: uuid.UUID
    ) -> list[tuple[str, str, str, str, int, tuple[str, ...]]]:
        """Per currency, bucket code, direction and origin of a stressed run: the number of
        DISTINCT changed non-zero flows there, and their classifications. A flow counts under
        its bucket before and after, as in `scenario_changed` (D-46 §3: a stressed figure
        changed by too few flows would give their amounts back as stressed - baseline)."""
        sc = ScenarioContribution
        result = await self.session.execute(
            select(
                sc.unit,
                sc.bucket_code,
                sc.original_bucket_code,
                sc.direction,
                sc.origin,
                sc.cashflow_event_id,
                sc.classification,
            ).where(  # fmt: skip
                sc.tenant_id == self.scope.tenant_id,
                sc.calculation_run_id == run_id,
                sc.change != "UNCHANGED",
                sc.value != 0,
            )
        )
        flows: dict[tuple[str, str, str, str], set[uuid.UUID]] = {}
        codes: dict[tuple[str, str, str, str], set[str]] = {}
        for unit, after, before, direction, origin, event_id, code in result.tuples():
            for bucket in {after, before}:
                key = (unit, bucket, direction, origin)
                flows.setdefault(key, set()).add(event_id)
                codes.setdefault(key, set()).add(code)
        return [(*key, len(flows[key]), tuple(sorted(codes[key]))) for key in sorted(flows)]

    async def scenario_changed(
        self, run_id: uuid.UUID
    ) -> list[tuple[str, str, str, uuid.UUID, str]]:
        """(currency, bucket code, direction, cash-flow event, classification) of every changed
        non-zero part, under its bucket before and after (D-46 §3: the deltas' distinct changed
        flows)."""
        sc = ScenarioContribution
        result = await self.session.execute(
            select(
                sc.unit,
                sc.bucket_code,
                sc.original_bucket_code,
                sc.direction,
                sc.cashflow_event_id,
                sc.classification,
            ).where(  # fmt: skip
                sc.tenant_id == self.scope.tenant_id,
                sc.calculation_run_id == run_id,
                sc.change != "UNCHANGED",
                sc.value != 0,
            )
        )
        found: set[tuple[str, str, str, uuid.UUID, str]] = set()
        for unit, after, before, direction, event_id, code in result.tuples():
            found.add((unit, after, direction, event_id, code))
            found.add((unit, before, direction, event_id, code))
        return sorted(found, key=lambda r: (r[0], r[1], r[2], str(r[3]), r[4]))

    async def scenario_contributions(
        self,
        run_id: uuid.UUID,
        currency: str | None,
        bucket_code: str | None,
        limit: int,
        offset: int,
        changed_only: bool = False,
    ) -> tuple[list[ScenarioContribution], int]:
        """A stressed run's parts (all, or the changed ones), by sequence. With `bucket_code`,
        the changed-parts drill-down (`changed_only`) matches the bucket before or after."""
        sc = ScenarioContribution
        query = select(sc).where(
            sc.tenant_id == self.scope.tenant_id, sc.calculation_run_id == run_id
        )
        if changed_only:
            query = query.where(sc.change != "UNCHANGED")
        if currency is not None:
            query = query.where(sc.unit == currency)
        if bucket_code is not None:
            query = query.where(
                or_(sc.bucket_code == bucket_code, sc.original_bucket_code == bucket_code)
                if changed_only
                else sc.bucket_code == bucket_code
            )
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(query.order_by(sc.sequence).limit(limit).offset(offset))
        return list(result.scalars()), int(total or 0)
