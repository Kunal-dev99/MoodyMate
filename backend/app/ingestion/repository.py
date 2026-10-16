"""ingestion.repository

The only code that touches the DB session for this module; every query applies the
principal's scope (D-04). Profiles and contracts are reached only through a source system
in the caller's tenant.
"""

import uuid

from sqlalchemy import Select, exists, func, select

from app.db.repository import ScopedRepository
from app.ingestion.models import SourceContract, SourceProfile, SourceSystem


class SourceRepository(ScopedRepository[SourceSystem]):
    model = SourceSystem

    def _with_profile(self) -> Select[tuple[SourceSystem]]:
        """Scoped sources that have a profile. A source is registered with its profile in one
        transaction; the filter sits in the query so LIMIT and COUNT agree (D12)."""
        return self.scoped().where(
            exists().where(SourceProfile.source_system_id == SourceSystem.id)
        )

    async def page_by_code(self, limit: int, offset: int) -> tuple[list[SourceSystem], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(self._with_profile().subquery())
        )
        result = await self.session.execute(
            self._with_profile()
            .order_by(SourceSystem.code, SourceSystem.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def fresh(self, source_id: uuid.UUID) -> SourceSystem | None:
        """Reloaded with its profiles and contracts, so writes made in this session show."""
        result = await self.session.execute(
            self.scoped()
            .where(SourceSystem.id == source_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def code_taken(self, code: str) -> bool:
        found = await self.session.scalar(
            select(SourceSystem.id).where(
                SourceSystem.tenant_id == self.scope.tenant_id, SourceSystem.code == code
            )
        )
        return found is not None

    async def lock(self, source_id: uuid.UUID) -> SourceSystem | None:
        """Serialises contract versioning per source (one current contract per profile)."""
        result = await self.session.execute(
            self.scoped()
            .where(SourceSystem.id == source_id)
            .with_for_update(of=SourceSystem)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def contracts(self, profile_id: uuid.UUID) -> list[SourceContract]:
        """A profile's contracts, freshly loaded, oldest first. Callers reach the profile
        through a scoped source system first, so this never crosses tenants."""
        result = await self.session.execute(
            select(SourceContract)
            .where(SourceContract.source_profile_id == profile_id)
            .order_by(SourceContract.version)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def flush(self) -> None:
        await self.session.flush()
