"""domain.repository

The only code that touches the DB session for this module. Every query applies the
principal's scope (D-04): every domain table carries `tenant_id`, and every query here
filters on the caller's tenant. Nothing here updates or deletes (the tables are
append-only).
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, exists, func, insert, select, text

from app.db.base import Base
from app.db.repository import ScopedRepository
from app.domain.models import (
    CashflowEvent,
    DomainProjection,
    Instrument,
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
    InstrumentType,
    InstrumentVersion,
    Position,
    PositionVersion,
)


@dataclass(frozen=True)
class InstrumentFilter:
    instrument_type: str | None = None
    currency: str | None = None
    source_id: uuid.UUID | None = None
    import_id: uuid.UUID | None = None


@dataclass(frozen=True)
class IdentityRef:
    id: uuid.UUID
    last_version: int


class DomainRepository(ScopedRepository[DomainProjection]):
    model = DomainProjection

    # --- projection (writes happen only for an import the service holds locked) -----------

    async def lock_tenant(self) -> None:
        """Serialise projections within the tenant for this transaction, so version numbers
        of one identity are never taken twice."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"domain_projection:{self.scope.tenant_id}"},
        )

    async def projection_for(self, import_id: uuid.UUID) -> DomainProjection | None:
        result = await self.session.execute(
            self.scoped().where(DomainProjection.import_job_id == import_id)
        )
        return result.scalar_one_or_none()

    async def identities(
        self, kind: type[Instrument] | type[Position], source_id: uuid.UUID, keys: Iterable[str]
    ) -> dict[str, IdentityRef]:
        """Existing identities of these business keys in the source, with their last version."""
        keys = sorted(set(keys))
        if not keys:
            return {}
        versions: type[InstrumentVersion] | type[PositionVersion]
        if kind is Instrument:
            versions, link = InstrumentVersion, InstrumentVersion.instrument_id
        else:
            versions, link = PositionVersion, PositionVersion.position_id
        result = await self.session.execute(
            select(kind.id, kind.business_key, func.coalesce(func.max(versions.version), 0))
            .outerjoin(versions, link == kind.id)
            .where(
                kind.tenant_id == self.scope.tenant_id,
                kind.source_system_id == source_id,
                kind.business_key.in_(keys),
            )
            .group_by(kind.id, kind.business_key)
        )
        return {row[1]: IdentityRef(row[0], int(row[2])) for row in result}

    async def instrument_types(self) -> list[InstrumentType]:
        result = await self.session.execute(
            select(InstrumentType)
            .where(InstrumentType.tenant_id == self.scope.tenant_id)
            .order_by(InstrumentType.code)
        )
        return list(result.scalars())

    async def attribute_definitions(self) -> list[InstrumentAttributeDefinition]:
        result = await self.session.execute(
            select(InstrumentAttributeDefinition)
            .where(InstrumentAttributeDefinition.tenant_id == self.scope.tenant_id)
            .order_by(InstrumentAttributeDefinition.code)
        )
        return list(result.scalars())

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def insert_rows(self, model: type[Base], rows: list[dict[str, Any]]) -> None:
        """One multi-row INSERT (rows built by the service, all in the caller's tenant)."""
        if rows:
            await self.session.execute(insert(model), rows)

    async def flush(self) -> None:
        await self.session.flush()

    # --- the instrument catalogue ---------------------------------------------------------

    def _instruments(self, f: InstrumentFilter) -> Select[tuple[Instrument]]:
        """Instruments of the tenant with at least one version matching every filter."""
        query = select(Instrument).where(Instrument.tenant_id == self.scope.tenant_id)
        if f.source_id is not None:
            query = query.where(Instrument.source_system_id == f.source_id)
        conditions = [
            InstrumentVersion.instrument_id == Instrument.id,
            InstrumentVersion.tenant_id == self.scope.tenant_id,
        ]
        if f.instrument_type is not None:
            conditions.append(InstrumentVersion.instrument_type_code == f.instrument_type)
        if f.currency is not None:
            conditions.append(InstrumentVersion.currency == f.currency)
        if f.import_id is not None:
            conditions.append(InstrumentVersion.import_job_id == f.import_id)
        return query.where(exists().where(*conditions))

    async def instrument_page(
        self, f: InstrumentFilter, limit: int, offset: int
    ) -> tuple[list[Instrument], int]:
        query = self._instruments(f)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(Instrument.business_key, Instrument.id).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def instrument(self, instrument_id: uuid.UUID) -> Instrument | None:
        result = await self.session.execute(
            select(Instrument).where(
                Instrument.id == instrument_id, Instrument.tenant_id == self.scope.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def instrument_versions(
        self, instrument_ids: Sequence[uuid.UUID]
    ) -> list[InstrumentVersion]:
        """Every version of these instruments, oldest first per instrument."""
        if not instrument_ids:
            return []
        result = await self.session.execute(
            select(InstrumentVersion)
            .where(
                InstrumentVersion.tenant_id == self.scope.tenant_id,
                InstrumentVersion.instrument_id.in_(instrument_ids),
            )
            .order_by(InstrumentVersion.instrument_id, InstrumentVersion.version)
        )
        return list(result.scalars())

    async def attribute_values(
        self, version_ids: Sequence[uuid.UUID]
    ) -> list[tuple[InstrumentAttributeValue, InstrumentAttributeDefinition]]:
        if not version_ids:
            return []
        result = await self.session.execute(
            select(InstrumentAttributeValue, InstrumentAttributeDefinition)
            .join(
                InstrumentAttributeDefinition,
                InstrumentAttributeDefinition.id
                == InstrumentAttributeValue.attribute_definition_id,
            )
            .where(
                InstrumentAttributeValue.tenant_id == self.scope.tenant_id,
                InstrumentAttributeDefinition.tenant_id == self.scope.tenant_id,
                InstrumentAttributeValue.instrument_version_id.in_(version_ids),
            )
            .order_by(InstrumentAttributeDefinition.code)
        )
        return [(row[0], row[1]) for row in result]

    async def cashflows_for(self, instrument_key: str, source_id: uuid.UUID) -> list[CashflowEvent]:
        """Projected cash-flow events of the instrument's own source naming its business key,
        in the tenant (a key is unique per source only, D-29 §3)."""
        result = await self.session.execute(
            select(CashflowEvent)
            .where(
                CashflowEvent.tenant_id == self.scope.tenant_id,
                CashflowEvent.source_system_id == source_id,
                CashflowEvent.instrument_key == instrument_key,
            )
            .order_by(CashflowEvent.due_date, CashflowEvent.business_key, CashflowEvent.id)
        )
        return list(result.scalars())
