"""mapping.repository

The only code that touches the DB session for this module; every query applies the
principal's scope (D-04). Versions, rules and validations are reached only through a
`field_mapping` of the caller's tenant.
"""

import uuid

from sqlalchemy import Select, func, select

from app.db.repository import ScopedRepository
from app.mapping.constants import MappingVersionStatus
from app.mapping.models import FieldMapping, MappingValidation, MappingVersion


class MappingRepository(ScopedRepository[FieldMapping]):
    model = FieldMapping

    def _filtered(
        self, source_id: uuid.UUID | None, schema_id: uuid.UUID | None
    ) -> Select[tuple[FieldMapping]]:
        query = self.scoped()
        if source_id is not None:
            query = query.where(FieldMapping.source_system_id == source_id)
        if schema_id is not None:
            query = query.where(FieldMapping.schema_id == schema_id)
        return query

    async def page_filtered(
        self, limit: int, offset: int, source_id: uuid.UUID | None, schema_id: uuid.UUID | None
    ) -> tuple[list[FieldMapping], int]:
        query = self._filtered(source_id, schema_id)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(FieldMapping.code, FieldMapping.id).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def fresh(self, mapping_id: uuid.UUID) -> FieldMapping | None:
        """Reloaded with its versions and their rules, so writes made in this session show."""
        result = await self.session.execute(
            self.scoped()
            .where(FieldMapping.id == mapping_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock(self, mapping_id: uuid.UUID) -> FieldMapping | None:
        """Serialises versioning, submission and activation per mapping."""
        result = await self.session.execute(
            self.scoped()
            .where(FieldMapping.id == mapping_id)
            .with_for_update(of=FieldMapping)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def code_taken(self, code: str) -> bool:
        found = await self.session.scalar(
            select(FieldMapping.id).where(
                FieldMapping.tenant_id == self.scope.tenant_id, FieldMapping.code == code
            )
        )
        return found is not None

    async def for_contract(self, contract_id: uuid.UUID) -> FieldMapping | None:
        result = await self.session.execute(
            self.scoped().where(FieldMapping.source_contract_id == contract_id)
        )
        return result.scalar_one_or_none()

    async def version_of(self, mapping_id: uuid.UUID, number: int) -> MappingVersion | None:
        """A version of a mapping already reached through this scope, locked."""
        result = await self.session.execute(
            select(MappingVersion)
            .where(MappingVersion.field_mapping_id == mapping_id, MappingVersion.version == number)
            .with_for_update(of=MappingVersion)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def scoped_version(
        self, version_id: uuid.UUID
    ) -> tuple[FieldMapping, MappingVersion] | None:
        """A version of a mapping of the caller's tenant, with its mapping."""
        result = await self.session.execute(
            select(FieldMapping, MappingVersion)
            .join(MappingVersion, MappingVersion.field_mapping_id == FieldMapping.id)
            .where(FieldMapping.tenant_id == self.scope.tenant_id, MappingVersion.id == version_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def active_for(
        self, contract_id: uuid.UUID, schema_version_id: uuid.UUID
    ) -> MappingVersion | None:
        """D-21/D-18 §4: the ACTIVE version of the contract's mapping, if it pins this
        schema version."""
        result = await self.session.execute(
            select(MappingVersion)
            .join(FieldMapping, FieldMapping.id == MappingVersion.field_mapping_id)
            .where(
                FieldMapping.tenant_id == self.scope.tenant_id,
                FieldMapping.source_contract_id == contract_id,
                MappingVersion.status == MappingVersionStatus.ACTIVE,
                MappingVersion.schema_version_id == schema_version_id,
            )
        )
        return result.scalar_one_or_none()

    async def validation(self, validation_id: uuid.UUID) -> MappingValidation | None:
        """A validation of a mapping of the caller's tenant."""
        result = await self.session.execute(
            select(MappingValidation)
            .join(MappingVersion, MappingVersion.id == MappingValidation.mapping_version_id)
            .join(FieldMapping, FieldMapping.id == MappingVersion.field_mapping_id)
            .where(
                FieldMapping.tenant_id == self.scope.tenant_id,
                MappingValidation.id == validation_id,
            )
        )
        return result.scalar_one_or_none()

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def flush(self) -> None:
        await self.session.flush()
