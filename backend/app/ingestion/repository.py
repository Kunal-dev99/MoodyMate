"""ingestion.repository

The only code that touches the DB session for this module; every query applies the
principal's scope (D-04). Profiles and contracts are reached only through a source system
in the caller's tenant; artifacts, batches and records only through an import job in it.
"""

import uuid
from typing import Any

from sqlalchemy import Select, exists, func, insert, select

from app.db.repository import ScopedRepository
from app.ingestion.models import (
    ImportJob,
    SourceContract,
    SourceProfile,
    SourceRecord,
    SourceSystem,
)


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


class ImportRepository(ScopedRepository[ImportJob]):
    """Imports are tenant-scoped through `import_job.tenant_id`; artifacts, batches and
    records are reached only through a scoped job."""

    model = ImportJob

    def _filtered(
        self, source_id: uuid.UUID | None, status: str | None
    ) -> Select[tuple[ImportJob]]:
        query = self.scoped()
        if source_id is not None:
            query = query.where(ImportJob.source_system_id == source_id)
        if status is not None:
            query = query.where(ImportJob.status == status)
        return query

    async def page_filtered(
        self, limit: int, offset: int, source_id: uuid.UUID | None, status: str | None
    ) -> tuple[list[ImportJob], int]:
        query = self._filtered(source_id, status)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def fresh(self, import_id: uuid.UUID) -> ImportJob | None:
        result = await self.session.execute(
            self.scoped().where(ImportJob.id == import_id).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def by_idempotency_key(self, actor: str, key: str) -> ImportJob | None:
        """D-21: a key is unique per tenant and actor."""
        result = await self.session.execute(
            self.scoped()
            .where(ImportJob.created_by == actor, ImportJob.idempotency_key == key)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def source_codes(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        result = await self.session.execute(
            select(SourceSystem.id, SourceSystem.code).where(
                SourceSystem.tenant_id == self.scope.tenant_id, SourceSystem.id.in_(ids)
            )
        )
        return {row.id: row.code for row in result}

    async def contract_versions(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Contract versions of contracts the caller's (scoped) imports pin."""
        if not ids:
            return {}
        result = await self.session.execute(
            select(SourceContract.id, SourceContract.version)
            .join(SourceProfile, SourceProfile.id == SourceContract.source_profile_id)
            .join(SourceSystem, SourceSystem.id == SourceProfile.source_system_id)
            .where(SourceSystem.tenant_id == self.scope.tenant_id, SourceContract.id.in_(ids))
        )
        return {row.id: row.version for row in result}

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def add_records(self, rows: list[dict[str, Any]]) -> None:
        """One multi-row INSERT for a batch's records (up to the D-16 row limit)."""
        if rows:
            await self.session.execute(insert(SourceRecord), rows)

    async def flush(self) -> None:
        await self.session.flush()
