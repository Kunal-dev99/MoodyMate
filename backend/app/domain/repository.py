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
from app.domain.constants import DomainSchemaCode
from app.domain.models import (
    CashflowEvent,
    Counterparty,
    CounterpartyVersion,
    CreditExposure,
    CreditExposureVersion,
    CreditVocabulary,
    DomainProjection,
    Instrument,
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
    InstrumentType,
    InstrumentVersion,
    Position,
    PositionVersion,
)

IdentityModel = type[Instrument] | type[Position] | type[Counterparty] | type[CreditExposure]
VersionModel = (
    type[InstrumentVersion]
    | type[PositionVersion]
    | type[CounterpartyVersion]
    | type[CreditExposureVersion]
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


@dataclass(frozen=True)
class Versioned:
    """A schema whose rows version a stable identity (D-29 §3): its identity and version
    models and the version column naming the identity."""

    identity: IdentityModel
    version: VersionModel
    link: str


# Cash-flow events have no identity: each is one row of its own.
VERSIONED: dict[DomainSchemaCode, Versioned] = {
    DomainSchemaCode.INSTRUMENT: Versioned(Instrument, InstrumentVersion, "instrument_id"),
    DomainSchemaCode.POSITION: Versioned(Position, PositionVersion, "position_id"),
    DomainSchemaCode.COUNTERPARTY: Versioned(Counterparty, CounterpartyVersion, "counterparty_id"),
    DomainSchemaCode.CREDIT_EXPOSURE: Versioned(
        CreditExposure, CreditExposureVersion, "credit_exposure_id"
    ),
}


@dataclass(frozen=True)
class CounterpartyFilter:
    segment: str | None = None
    country: str | None = None
    source_id: uuid.UUID | None = None
    import_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ExposureFilter:
    currency: str | None = None
    facility_type: str | None = None
    counterparty_key: str | None = None
    source_id: uuid.UUID | None = None
    import_id: uuid.UUID | None = None


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

    async def projected_import_ids(self) -> list[uuid.UUID]:
        """The imports of the tenant that have a projection."""
        result = await self.session.execute(
            select(DomainProjection.import_job_id).where(
                DomainProjection.tenant_id == self.scope.tenant_id
            )
        )
        return list(result.scalars())

    async def projection_for(self, import_id: uuid.UUID) -> DomainProjection | None:
        result = await self.session.execute(
            self.scoped().where(DomainProjection.import_job_id == import_id)
        )
        return result.scalar_one_or_none()

    async def identities(
        self, versioned: Versioned, source_id: uuid.UUID, keys: Iterable[str]
    ) -> dict[str, IdentityRef]:
        """Existing identities of these business keys in the source, with their last version."""
        keys = sorted(set(keys))
        if not keys:
            return {}
        kind, versions = versioned.identity, versioned.version
        link = getattr(versions, versioned.link)
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

    # --- for WS3 dataset snapshots and runs (Phase 15, D-30, D-31): read only ---------------

    async def ambiguous_keys(self, projection: DomainProjection) -> int:
        """Business keys with more than one version row in this projection's import (one
        key, two candidates inside a snapshot: D-30 §3)."""
        versioned = VERSIONED.get(DomainSchemaCode(projection.schema_code))
        if versioned is None:  # cash-flow events: each row is its own
            return 0
        kind, versions = versioned.identity, versioned.version
        rows = (
            select(kind.business_key)
            .join(versions, getattr(versions, versioned.link) == kind.id)
            .where(
                versions.tenant_id == self.scope.tenant_id,
                versions.domain_projection_id == projection.id,
            )
            .group_by(kind.business_key)
            .having(func.count() > 1)
        )
        return int(
            await self.session.scalar(select(func.count()).select_from(rows.subquery())) or 0
        )

    async def import_cash_flows(self, import_id: uuid.UUID) -> list[CashflowEvent]:
        """An import's projected cash-flow events in source order."""
        result = await self.session.execute(
            select(CashflowEvent)
            .where(
                CashflowEvent.tenant_id == self.scope.tenant_id,
                CashflowEvent.import_job_id == import_id,
            )
            .order_by(CashflowEvent.source_ordinal, CashflowEvent.id)
        )
        return list(result.scalars())

    async def import_instruments(self, import_id: uuid.UUID) -> list[tuple[InstrumentVersion, str]]:
        """An import's projected instrument versions with their business keys, in source
        order."""
        result = await self.session.execute(
            select(InstrumentVersion, Instrument.business_key)
            .join(Instrument, Instrument.id == InstrumentVersion.instrument_id)
            .where(
                InstrumentVersion.tenant_id == self.scope.tenant_id,
                Instrument.tenant_id == self.scope.tenant_id,
                InstrumentVersion.import_job_id == import_id,
            )
            .order_by(InstrumentVersion.source_ordinal, InstrumentVersion.id)
        )
        return [(row[0], row[1]) for row in result]

    async def import_positions(self, import_id: uuid.UUID) -> list[tuple[PositionVersion, str]]:
        """An import's projected position versions with their business keys, in source
        order (Phase 17, lcr_hqla)."""
        result = await self.session.execute(
            select(PositionVersion, Position.business_key)
            .join(Position, Position.id == PositionVersion.position_id)
            .where(
                PositionVersion.tenant_id == self.scope.tenant_id,
                Position.tenant_id == self.scope.tenant_id,
                PositionVersion.import_job_id == import_id,
            )
            .order_by(PositionVersion.source_ordinal, PositionVersion.id)
        )
        return [(row[0], row[1]) for row in result]

    async def positions_by_id(self, ids: Sequence[uuid.UUID]) -> list[tuple[PositionVersion, str]]:
        if not ids:
            return []
        result = await self.session.execute(
            select(PositionVersion, Position.business_key)
            .join(Position, Position.id == PositionVersion.position_id)
            .where(
                PositionVersion.tenant_id == self.scope.tenant_id,
                Position.tenant_id == self.scope.tenant_id,
                PositionVersion.id.in_(ids),
            )
        )
        return [(row[0], row[1]) for row in result]

    async def cash_flows_by_id(self, ids: Sequence[uuid.UUID]) -> list[CashflowEvent]:
        if not ids:
            return []
        result = await self.session.execute(
            select(CashflowEvent).where(
                CashflowEvent.tenant_id == self.scope.tenant_id, CashflowEvent.id.in_(ids)
            )
        )
        return list(result.scalars())

    async def instrument_versions_by_id(
        self, ids: Sequence[uuid.UUID]
    ) -> list[tuple[InstrumentVersion, str]]:
        if not ids:
            return []
        result = await self.session.execute(
            select(InstrumentVersion, Instrument.business_key)
            .join(Instrument, Instrument.id == InstrumentVersion.instrument_id)
            .where(
                InstrumentVersion.tenant_id == self.scope.tenant_id,
                Instrument.tenant_id == self.scope.tenant_id,
                InstrumentVersion.id.in_(ids),
            )
        )
        return [(row[0], row[1]) for row in result]

    # --- Feature 04 credit reads (BE-19.5, D-37): read only, in the caller's tenant ----------

    async def credit_vocabulary(self) -> list[CreditVocabulary]:
        result = await self.session.execute(
            select(CreditVocabulary)
            .where(CreditVocabulary.tenant_id == self.scope.tenant_id)
            .order_by(
                CreditVocabulary.vocabulary,
                CreditVocabulary.rank.nulls_last(),
                CreditVocabulary.code,
            )
        )
        return list(result.scalars())

    def _counterparties(self, f: CounterpartyFilter) -> Select[tuple[Counterparty]]:
        """Counterparties of the tenant with at least one version matching every filter."""
        query = select(Counterparty).where(Counterparty.tenant_id == self.scope.tenant_id)
        if f.source_id is not None:
            query = query.where(Counterparty.source_system_id == f.source_id)
        conditions = [
            CounterpartyVersion.counterparty_id == Counterparty.id,
            CounterpartyVersion.tenant_id == self.scope.tenant_id,
        ]
        if f.segment is not None:
            conditions.append(CounterpartyVersion.segment == f.segment)
        if f.country is not None:
            conditions.append(CounterpartyVersion.country == f.country)
        if f.import_id is not None:
            conditions.append(CounterpartyVersion.import_job_id == f.import_id)
        return query.where(exists().where(*conditions))

    async def counterparty_page(
        self, f: CounterpartyFilter, limit: int, offset: int
    ) -> tuple[list[Counterparty], int]:
        query = self._counterparties(f)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(Counterparty.business_key, Counterparty.id).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def counterparty(self, counterparty_id: uuid.UUID) -> Counterparty | None:
        result = await self.session.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id, Counterparty.tenant_id == self.scope.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def counterparty_versions(self, ids: Sequence[uuid.UUID]) -> list[CounterpartyVersion]:
        """Every version of these counterparties, oldest first per counterparty."""
        if not ids:
            return []
        result = await self.session.execute(
            select(CounterpartyVersion)
            .where(
                CounterpartyVersion.tenant_id == self.scope.tenant_id,
                CounterpartyVersion.counterparty_id.in_(ids),
            )
            .order_by(CounterpartyVersion.counterparty_id, CounterpartyVersion.version)
        )
        return list(result.scalars())

    def _exposures(self, f: ExposureFilter) -> Select[tuple[CreditExposure]]:
        """Credit exposures of the tenant with at least one version matching every filter."""
        query = select(CreditExposure).where(CreditExposure.tenant_id == self.scope.tenant_id)
        if f.source_id is not None:
            query = query.where(CreditExposure.source_system_id == f.source_id)
        conditions = [
            CreditExposureVersion.credit_exposure_id == CreditExposure.id,
            CreditExposureVersion.tenant_id == self.scope.tenant_id,
        ]
        if f.currency is not None:
            conditions.append(CreditExposureVersion.currency == f.currency)
        if f.facility_type is not None:
            conditions.append(CreditExposureVersion.facility_type == f.facility_type)
        if f.counterparty_key is not None:
            conditions.append(CreditExposureVersion.counterparty_key == f.counterparty_key)
        if f.import_id is not None:
            conditions.append(CreditExposureVersion.import_job_id == f.import_id)
        return query.where(exists().where(*conditions))

    async def exposure_page(
        self, f: ExposureFilter, limit: int, offset: int
    ) -> tuple[list[CreditExposure], int]:
        query = self._exposures(f)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(CreditExposure.business_key, CreditExposure.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def exposure(self, exposure_id: uuid.UUID) -> CreditExposure | None:
        result = await self.session.execute(
            select(CreditExposure).where(
                CreditExposure.id == exposure_id, CreditExposure.tenant_id == self.scope.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def exposure_versions(
        self, ids: Sequence[uuid.UUID]
    ) -> list[tuple[CreditExposureVersion, str]]:
        """Every version of these exposures with its exposure's business key, oldest first
        per exposure."""
        if not ids:
            return []
        result = await self.session.execute(
            select(CreditExposureVersion, CreditExposure.business_key)
            .join(CreditExposure, CreditExposure.id == CreditExposureVersion.credit_exposure_id)
            .where(
                CreditExposureVersion.tenant_id == self.scope.tenant_id,
                CreditExposure.tenant_id == self.scope.tenant_id,
                CreditExposureVersion.credit_exposure_id.in_(ids),
            )
            .order_by(CreditExposureVersion.credit_exposure_id, CreditExposureVersion.version)
        )
        return [(row[0], row[1]) for row in result]

    async def exposure_versions_naming(
        self, counterparty_key: str
    ) -> list[tuple[CreditExposureVersion, str]]:
        """Credit-exposure versions naming this counterparty business key, in the tenant
        (any source: the reference resolves within a snapshot and the tenant, D-37 §4)."""
        result = await self.session.execute(
            select(CreditExposureVersion, CreditExposure.business_key)
            .join(CreditExposure, CreditExposure.id == CreditExposureVersion.credit_exposure_id)
            .where(
                CreditExposureVersion.tenant_id == self.scope.tenant_id,
                CreditExposure.tenant_id == self.scope.tenant_id,
                CreditExposureVersion.counterparty_key == counterparty_key,
            )
            .order_by(
                CreditExposure.business_key,
                CreditExposureVersion.version,
                CreditExposureVersion.id,
            )
        )
        return [(row[0], row[1]) for row in result]
