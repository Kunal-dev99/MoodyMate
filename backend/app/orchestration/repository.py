"""orchestration.repository

The only code that touches the DB session for this module. Every query applies the
principal's scope (D-04): every scenario table carries `tenant_id`, and every query here
filters on the caller's tenant. Nothing here deletes; the only updates are a DRAFT's edit and
a version's status moves (D-44 §5), which the 0026 trigger guards as well. Scenario runs
(Phase 25) are inserted once and never changed (the 0027 trigger).
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import exists, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import DataScope
from app.orchestration.constants import ScenarioOrigin, ScenarioVersionStatus
from app.orchestration.models import Scenario, ScenarioChange, ScenarioRun, ScenarioVersion


class ScenarioRepository:
    def __init__(self, session: AsyncSession, scope: DataScope) -> None:
        self.session = session
        self.scope = scope

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def flush(self) -> None:
        await self.session.flush()

    async def lock(self, key: str) -> None:
        """Serialise writes on one key within the tenant for this transaction."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{key}:{self.scope.tenant_id}"},
        )

    # --- scenarios ------------------------------------------------------------------------

    async def scenario(self, scenario_id: uuid.UUID) -> Scenario | None:
        result = await self.session.execute(
            select(Scenario)
            .where(Scenario.id == scenario_id, Scenario.tenant_id == self.scope.tenant_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def code_taken(self, code: str) -> bool:
        found = await self.session.scalar(
            select(
                exists().where(Scenario.tenant_id == self.scope.tenant_id, Scenario.code == code)
            )
        )
        return bool(found)

    async def scenario_page(
        self,
        statuses: Sequence[ScenarioVersionStatus],
        origin: ScenarioOrigin | None,
        author_user_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Scenario], int]:
        """Scenarios of the tenant with at least one version matching every filter, newest
        first (then by id)."""
        conditions = [
            ScenarioVersion.scenario_id == Scenario.id,
            ScenarioVersion.tenant_id == self.scope.tenant_id,
            ScenarioVersion.status.in_([s.value for s in statuses]),
        ]
        if origin is not None:
            conditions.append(ScenarioVersion.origin == origin.value)
        if author_user_id is not None:
            conditions.append(ScenarioVersion.author_user_id == author_user_id)
        query = select(Scenario).where(
            Scenario.tenant_id == self.scope.tenant_id, exists().where(*conditions)
        )
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(Scenario.created_at.desc(), Scenario.id).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    # --- versions -------------------------------------------------------------------------

    async def versions(self, scenario_ids: Sequence[uuid.UUID]) -> list[ScenarioVersion]:
        """Every version of these scenarios, by scenario then version number."""
        if not scenario_ids:
            return []
        result = await self.session.execute(
            select(ScenarioVersion)
            .where(
                ScenarioVersion.tenant_id == self.scope.tenant_id,
                ScenarioVersion.scenario_id.in_(scenario_ids),
            )
            .order_by(ScenarioVersion.scenario_id, ScenarioVersion.version)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def version(
        self, scenario_id: uuid.UUID, version: int, for_update: bool = False
    ) -> ScenarioVersion | None:
        query = select(ScenarioVersion).where(
            ScenarioVersion.tenant_id == self.scope.tenant_id,
            ScenarioVersion.scenario_id == scenario_id,
            ScenarioVersion.version == version,
        )
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def last_version(self, scenario_id: uuid.UUID) -> int:
        found = await self.session.scalar(
            select(func.max(ScenarioVersion.version)).where(
                ScenarioVersion.tenant_id == self.scope.tenant_id,
                ScenarioVersion.scenario_id == scenario_id,
            )
        )
        return int(found or 0)

    async def update_version(self, version_id: uuid.UUID, values: dict[str, Any]) -> None:
        """One DRAFT edit or one status move (D-44 §5); timestamps on the database clock."""
        result = await self.session.execute(
            update(ScenarioVersion)
            .where(
                ScenarioVersion.id == version_id,
                ScenarioVersion.tenant_id == self.scope.tenant_id,
            )
            .values(**values, updated_at=func.now())
        )
        assert getattr(result, "rowcount", 1) == 1

    # --- changes --------------------------------------------------------------------------

    async def insert_changes(self, rows: list[dict[str, Any]]) -> None:
        """The materialised shocks of a version leaving DRAFT (built by the service, in the
        caller's tenant)."""
        if rows:
            await self.session.execute(insert(ScenarioChange), rows)

    async def changes(self, version_ids: Sequence[uuid.UUID]) -> list[ScenarioChange]:
        if not version_ids:
            return []
        result = await self.session.execute(
            select(ScenarioChange)
            .where(
                ScenarioChange.tenant_id == self.scope.tenant_id,
                ScenarioChange.scenario_version_id.in_(version_ids),
            )
            .order_by(ScenarioChange.scenario_version_id, ScenarioChange.shock_index)
        )
        return list(result.scalars())

    # --- scenario runs (Phase 25, D-45 §9) ------------------------------------------------

    async def scenario_run(self, scenario_run_id: uuid.UUID) -> ScenarioRun | None:
        result = await self.session.execute(
            select(ScenarioRun).where(
                ScenarioRun.id == scenario_run_id, ScenarioRun.tenant_id == self.scope.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def scenario_run_by_key(self, actor: str, key: str) -> ScenarioRun | None:
        result = await self.session.execute(
            select(ScenarioRun).where(
                ScenarioRun.tenant_id == self.scope.tenant_id,
                ScenarioRun.created_by == actor,
                ScenarioRun.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def scenario_run_page(
        self,
        scenario_id: uuid.UUID | None,
        baseline_run_id: uuid.UUID | None,
        approval_state: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ScenarioRun], int]:
        query = select(ScenarioRun).where(ScenarioRun.tenant_id == self.scope.tenant_id)
        if scenario_id is not None:
            query = query.where(ScenarioRun.scenario_id == scenario_id)
        if baseline_run_id is not None:
            query = query.where(ScenarioRun.baseline_run_id == baseline_run_id)
        if approval_state is not None:
            query = query.where(ScenarioRun.approval_state == approval_state)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(ScenarioRun.created_at.desc(), ScenarioRun.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def scenarios(self, scenario_ids: Sequence[uuid.UUID]) -> list[Scenario]:
        if not scenario_ids:
            return []
        result = await self.session.execute(
            select(Scenario).where(
                Scenario.tenant_id == self.scope.tenant_id, Scenario.id.in_(scenario_ids)
            )
        )
        return list(result.scalars())

    async def versions_by_id(self, version_ids: Sequence[uuid.UUID]) -> list[ScenarioVersion]:
        if not version_ids:
            return []
        result = await self.session.execute(
            select(ScenarioVersion).where(
                ScenarioVersion.tenant_id == self.scope.tenant_id,
                ScenarioVersion.id.in_(version_ids),
            )
        )
        return list(result.scalars())
