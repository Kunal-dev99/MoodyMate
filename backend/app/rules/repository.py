"""rules.repository

The only code that touches the DB session for the registry. Every query filters on the
caller's tenant (D-04): every registry table carries `tenant_id`. Nothing here updates or
deletes (definitions, module versions, rule sets and bucket definitions are append-only;
rule version moves arrive with Phase 16).
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import insert, select, text

from app.db.base import Base
from app.db.repository import ScopedRepository
from app.rules.constants import RuleVersionStatus
from app.rules.models import (
    AssumptionSet,
    CalculationModule,
    MaturityBucketDefinition,
    ModuleVersion,
    RuleDefinition,
    RuleSet,
    RuleVersion,
)


class RulesRepository(ScopedRepository[RuleDefinition]):
    model = RuleDefinition

    async def modules(
        self, module_version_id: uuid.UUID | None = None
    ) -> list[tuple[CalculationModule, ModuleVersion]]:
        """Module versions of the tenant (one, when an id is given), by code then version."""
        query = (
            select(CalculationModule, ModuleVersion)
            .join(ModuleVersion, ModuleVersion.module_id == CalculationModule.id)
            .where(
                CalculationModule.tenant_id == self.scope.tenant_id,
                ModuleVersion.tenant_id == self.scope.tenant_id,
            )
            .order_by(CalculationModule.code, ModuleVersion.version)
        )
        if module_version_id is not None:
            query = query.where(ModuleVersion.id == module_version_id)
        result = await self.session.execute(query)
        return [(row[0], row[1]) for row in result]

    async def definitions(self, codes: Sequence[str]) -> list[RuleDefinition]:
        if not codes:
            return []
        result = await self.session.execute(
            self.scoped().where(RuleDefinition.code.in_(codes)).order_by(RuleDefinition.code)
        )
        return list(result.scalars())

    async def active_versions(self, definition_ids: Sequence[uuid.UUID]) -> list[RuleVersion]:
        """The ACTIVE version of each definition (at most one each, D-32 §2)."""
        if not definition_ids:
            return []
        result = await self.session.execute(
            select(RuleVersion).where(
                RuleVersion.tenant_id == self.scope.tenant_id,
                RuleVersion.rule_definition_id.in_(definition_ids),
                RuleVersion.status == RuleVersionStatus.ACTIVE.value,
            )
        )
        return list(result.scalars())

    async def active_assumption_set(self, code: str) -> AssumptionSet | None:
        result = await self.session.execute(
            select(AssumptionSet).where(
                AssumptionSet.tenant_id == self.scope.tenant_id,
                AssumptionSet.code == code,
                AssumptionSet.status == RuleVersionStatus.ACTIVE.value,
            )
        )
        return result.scalar_one_or_none()

    async def lock(self, key: str) -> None:
        """Serialise writers of one content-addressed row within the tenant for this
        transaction (a second writer then finds the first one's row)."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{key}:{self.scope.tenant_id}"},
        )

    async def rule_set(self, content_sha256: str) -> RuleSet | None:
        result = await self.session.execute(
            select(RuleSet).where(
                RuleSet.tenant_id == self.scope.tenant_id,
                RuleSet.content_sha256 == content_sha256,
            )
        )
        return result.scalar_one_or_none()

    async def bucket_definitions(
        self, rule_version_id: uuid.UUID
    ) -> list[MaturityBucketDefinition]:
        result = await self.session.execute(
            select(MaturityBucketDefinition)
            .where(
                MaturityBucketDefinition.tenant_id == self.scope.tenant_id,
                MaturityBucketDefinition.rule_version_id == rule_version_id,
            )
            .order_by(MaturityBucketDefinition.sequence)
        )
        return list(result.scalars())

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def insert_rows(self, model: type[Base], rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(insert(model), rows)

    async def flush(self) -> None:
        await self.session.flush()
