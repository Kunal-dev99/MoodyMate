"""rules.assumptions_service

Read-only assumption sets (BE-16.4, D-32 §6): seeded by migration 0018, tenant-scoped (every
row carries `tenant_id`, D-04), never authored in Feature 03.

- `assumption_sets()`: every assumption set version of the tenant, by code then version.
- `assumption_set(id)`: one version plus every version of the same code; 404 `not_found`
  alike for an absent and a foreign id (no existence oracle). The message carries no value.

Assumption sets hold no classified field (empty `assumptions` in v1), so D-33 masking does
not apply here.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.principal import Principal
from app.db.repository import ScopedRepository
from app.rules.models import AssumptionSet

ASSUMPTION_SET_NOT_FOUND = "Assumption set not found."


class AssumptionSetRepository(ScopedRepository[AssumptionSet]):
    """Scoped reads of `assumption_set` (the rules repository has only the ACTIVE lookup)."""

    model = AssumptionSet

    async def all(self, code: str | None = None) -> list[AssumptionSet]:
        query = self.scoped().order_by(AssumptionSet.code, AssumptionSet.version)
        if code is not None:
            query = query.where(AssumptionSet.code == code)
        return list((await self.session.execute(query)).scalars())


class AssumptionSetService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.repo = AssumptionSetRepository(session, principal.scope)

    async def assumption_sets(self) -> list[AssumptionSet]:
        return await self.repo.all()

    async def assumption_set(
        self, assumption_set_id: uuid.UUID
    ) -> tuple[AssumptionSet, list[AssumptionSet]]:
        found = await self.repo.get(assumption_set_id)
        if found is None:
            raise ApiError(404, "not_found", ASSUMPTION_SET_NOT_FOUND)
        return found, await self.repo.all(found.code)
