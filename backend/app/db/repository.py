"""Tenant-scoped repository base (BE-2.5, D-04).

Every query a `ScopedRepository` builds is filtered by the caller's `DataScope`, which
is derived from the authenticated principal. No method accepts a tenant from request
input, so a crafted ID in a URL can only ever match rows inside the caller's tenant.
"""

import uuid
from dataclasses import dataclass
from typing import Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import ScopedModel

M = TypeVar("M", bound=ScopedModel)


@dataclass(frozen=True)
class DataScope:
    tenant_id: uuid.UUID


class ScopedRepository(Generic[M]):
    model: type[M]

    def __init__(self, session: AsyncSession, scope: DataScope) -> None:
        self.session = session
        self.scope = scope

    def scoped(self) -> Select[tuple[M]]:
        return select(self.model).where(self.model.tenant_id == self.scope.tenant_id)

    async def get(self, entity_id: uuid.UUID) -> M | None:
        result = await self.session.execute(self.scoped().where(self.model.id == entity_id))
        return result.scalar_one_or_none()

    async def page(self, limit: int, offset: int) -> tuple[list[M], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(self.scoped().subquery())
        )
        result = await self.session.execute(
            self.scoped().order_by(self.model.created_at, self.model.id).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)
