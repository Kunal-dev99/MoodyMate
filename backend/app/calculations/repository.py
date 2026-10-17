"""calculations.repository

The only code that touches the DB session for snapshots and runs. Every query filters on the
caller's tenant (D-04): every table here carries `tenant_id`. Nothing is deleted; the only
update is a run's single move from RUNNING to its terminal status (`finish`), which the DB
trigger also enforces.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, cast, func, insert, select, text, update

from app.calculations.constants import CalculationRunStatus
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
from app.db.base import Base
from app.db.repository import M, ScopedRepository


class _Writes(ScopedRepository[M]):
    async def lock(self, key: str) -> None:
        """Serialise writers of one content-addressed or keyed row within the tenant for this
        transaction (a second writer then finds the first one's row)."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{key}:{self.scope.tenant_id}"},
        )

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
    ) -> tuple[list[CalculationRun], int]:
        query = self.scoped()
        if module_version_ids is not None:
            query = query.where(CalculationRun.module_version_id.in_(module_version_ids))
        if status is not None:
            query = query.where(CalculationRun.status == status.value)
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
                CalculationRun.started_at
                < func.clock_timestamp()
                - func.make_interval(0, 0, 0, 0, 0, 0, cast(seconds, Integer)),
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
            raise RuntimeError("the run is not RUNNING")

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
        """Per ladder row, direction, origin and classification: how many contributions."""
        iv = IntermediateValue
        result = await self.session.execute(
            select(
                iv.liquidity_gap_result_id, iv.direction, iv.origin, iv.classification, func.count()
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
