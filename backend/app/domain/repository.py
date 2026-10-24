"""domain.repository

The only code that touches the DB session for this module. Every query applies the
principal's scope (D-04): every domain table carries `tenant_id`, and every query here
filters on the caller's tenant. Nothing here deletes, and nothing updates except an obligor
rating version's one status move (Phase 20); every other table is append-only.

Phase 31 (D-58): legal entities and identity links are written set-based (INSERT ... SELECT,
ON CONFLICT DO NOTHING), only while the service holds the tenant's projection lock.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Select, and_, exists, func, insert, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql.elements import ColumnElement

from app.db.base import Base
from app.db.repository import ScopedRepository
from app.domain.constants import (
    RESOLVER_VERSION,
    CreditVocabularyKind,
    DomainSchemaCode,
    IdentityLinkMethod,
    IdentitySourceKind,
    IdentityTargetKind,
    ObligorRatingStatus,
)
from app.domain.models import (
    CashflowEvent,
    Counterparty,
    CounterpartyVersion,
    CreditExposure,
    CreditExposureVersion,
    CreditVocabulary,
    DomainIdentityLink,
    DomainProjection,
    Instrument,
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
    InstrumentType,
    InstrumentVersion,
    LegalEntity,
    ObligorRating,
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

    async def instrument_versions_by_key(self, keys: Sequence[str]) -> list[InstrumentVersion]:
        """Every version of the tenant's instruments with one of these business keys (any
        source), oldest first per instrument; each carries `instrument_id` for grouping."""
        if not keys:
            return []
        result = await self.session.execute(
            select(InstrumentVersion)
            .join(Instrument, Instrument.id == InstrumentVersion.instrument_id)
            .where(
                Instrument.tenant_id == self.scope.tenant_id,
                InstrumentVersion.tenant_id == self.scope.tenant_id,
                Instrument.business_key.in_(list(keys)),
            )
            .order_by(InstrumentVersion.instrument_id, InstrumentVersion.version)
        )
        return list(result.scalars())

    async def instruments_by_id(self, ids: Sequence[uuid.UUID]) -> list[Instrument]:
        if not ids:
            return []
        result = await self.session.execute(
            select(Instrument).where(
                Instrument.tenant_id == self.scope.tenant_id, Instrument.id.in_(list(ids))
            )
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

    async def counterparties_by_id(self, ids: Sequence[uuid.UUID]) -> list[Counterparty]:
        if not ids:
            return []
        result = await self.session.execute(
            select(Counterparty).where(
                Counterparty.tenant_id == self.scope.tenant_id, Counterparty.id.in_(ids)
            )
        )
        return list(result.scalars())

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

    # --- Phase 20: obligor ratings (BE-20.2, BE-20.3, D-39), in the caller's tenant ----------
    # The only update is a rating version's status move (`move_rating`), which the 0023
    # lifecycle trigger also enforces; nothing is deleted.

    async def lock_counterparty_ratings(self, counterparty_id: uuid.UUID) -> None:
        """Serialise the rating writes of one counterparty within the tenant for this
        transaction (so two approvals for one date cannot both find no ACTIVE version)."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"obligor_rating:{counterparty_id}:{self.scope.tenant_id}"},
        )

    async def rating_grades(self) -> dict[str, int]:
        """The tenant's `rating_grade` codes and their ranks (higher is worse)."""
        result = await self.session.execute(
            select(CreditVocabulary.code, CreditVocabulary.rank).where(
                CreditVocabulary.tenant_id == self.scope.tenant_id,
                CreditVocabulary.vocabulary == CreditVocabularyKind.RATING_GRADE.value,
            )
        )
        return {row[0]: int(row[1]) for row in result if row[1] is not None}

    async def rating(self, rating_id: uuid.UUID, fresh: bool = False) -> ObligorRating | None:
        query = select(ObligorRating).where(
            ObligorRating.id == rating_id, ObligorRating.tenant_id == self.scope.tenant_id
        )
        if fresh:
            query = query.execution_options(populate_existing=True)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def active_rating(
        self, counterparty_id: uuid.UUID, effective_from: date
    ) -> ObligorRating | None:
        """The ACTIVE version for this counterparty and date (at most one: partial unique
        index)."""
        result = await self.session.execute(
            select(ObligorRating)
            .where(
                ObligorRating.tenant_id == self.scope.tenant_id,
                ObligorRating.counterparty_id == counterparty_id,
                ObligorRating.effective_from == effective_from,
                ObligorRating.status == ObligorRatingStatus.ACTIVE.value,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def move_rating(
        self,
        rating_id: uuid.UUID,
        status: ObligorRatingStatus,
        *,
        decided_by: str | None = None,
        comment: str | None = None,
        superseded_by: uuid.UUID | None = None,
    ) -> None:
        """A rating version's one status move (D-39 §2). A decision records who, when
        (database clock) and why; a supersession when and by which version."""
        values: dict[str, object] = {"status": status.value, "updated_at": func.now()}
        if decided_by is not None:
            values |= {
                "decided_by": decided_by,
                "decided_at": func.clock_timestamp(),
                "decision_comment": comment,
            }
        if superseded_by is not None:
            values |= {
                "superseded_at": func.clock_timestamp(),
                "superseded_by_rating_id": superseded_by,
            }
        result = await self.session.execute(
            update(ObligorRating)
            .where(ObligorRating.id == rating_id, ObligorRating.tenant_id == self.scope.tenant_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 1) != 1:
            raise RuntimeError("obligor rating not found in scope")
        await self.session.flush()

    async def rating_page(
        self,
        status: ObligorRatingStatus | None,
        counterparty_id: uuid.UUID | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ObligorRating], int]:
        """Rating versions of the tenant, newest proposal first (then by id)."""
        query = select(ObligorRating).where(ObligorRating.tenant_id == self.scope.tenant_id)
        if status is not None:
            query = query.where(ObligorRating.status == status.value)
        if counterparty_id is not None:
            query = query.where(ObligorRating.counterparty_id == counterparty_id)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(ObligorRating.proposed_at.desc(), ObligorRating.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def rating_history(self, counterparty_id: uuid.UUID) -> list[ObligorRating]:
        """Every version of one counterparty, by `effective_from` descending, then
        `proposed_at` descending (then by id)."""
        result = await self.session.execute(
            select(ObligorRating)
            .where(
                ObligorRating.tenant_id == self.scope.tenant_id,
                ObligorRating.counterparty_id == counterparty_id,
            )
            .order_by(
                ObligorRating.effective_from.desc(),
                ObligorRating.proposed_at.desc(),
                ObligorRating.id,
            )
        )
        return list(result.scalars())

    async def ratings_in_effect(
        self, counterparty_ids: Sequence[uuid.UUID], at: date
    ) -> list[ObligorRating]:
        """D-39 §4: for each counterparty, the ACTIVE version with the greatest
        `effective_from` on or before `at` (none before its first)."""
        if not counterparty_ids:
            return []
        result = await self.session.execute(
            select(ObligorRating)
            .where(
                ObligorRating.tenant_id == self.scope.tenant_id,
                ObligorRating.counterparty_id.in_(counterparty_ids),
                ObligorRating.status == ObligorRatingStatus.ACTIVE.value,
                ObligorRating.effective_from <= at,
            )
            .distinct(ObligorRating.counterparty_id)
            .order_by(ObligorRating.counterparty_id, ObligorRating.effective_from.desc())
        )
        return list(result.scalars())


# --- Phase 31: legal entities and identity links (BE-31.4, BE-31.5, D-58) -------------------

LinkSourceModel = type[PositionVersion] | type[CashflowEvent] | type[CreditExposureVersion]
LINK_SOURCES: dict[IdentitySourceKind, LinkSourceModel] = {
    IdentitySourceKind.POSITION_VERSION: PositionVersion,
    IdentitySourceKind.CASHFLOW_EVENT: CashflowEvent,
    IdentitySourceKind.CREDIT_EXPOSURE: CreditExposureVersion,
}
LinkTargetModel = type[Instrument] | type[Counterparty] | type[LegalEntity]
LINK_TARGETS: dict[IdentityTargetKind, tuple[LinkTargetModel, str]] = {
    IdentityTargetKind.INSTRUMENT: (Instrument, "business_key"),
    IdentityTargetKind.COUNTERPARTY: (Counterparty, "business_key"),
    IdentityTargetKind.LEGAL_ENTITY: (LegalEntity, "code"),
}


@dataclass(frozen=True)
class LinkPair:
    """One (source kind, target kind) a link joins, with the source row's key column."""

    source_kind: IdentitySourceKind
    target_kind: IdentityTargetKind
    key_field: str


@dataclass(frozen=True)
class LinkCounts:
    """Value-free counts of one pair over the rows examined (D-58 §3, §5)."""

    rows: int
    linked: int
    unresolved: int
    ambiguous: int
    no_key: int
    linked_now_ambiguous: int


@dataclass(frozen=True)
class LinkFilter:
    source_kind: IdentitySourceKind | None = None
    source_id: uuid.UUID | None = None
    target_kind: IdentityTargetKind | None = None
    target_id: uuid.UUID | None = None


class IdentityRepository(ScopedRepository[LegalEntity]):
    """Legal entities and identity links of the caller's tenant (D-04): every query filters
    on the tenant, and every write is an INSERT of rows of that tenant."""

    model = LegalEntity

    def _tenant(self) -> ColumnElement[uuid.UUID]:
        return literal(self.scope.tenant_id, UUID(as_uuid=True))

    # --- writes (the service holds the tenant's projection lock) ---------------------------

    async def register_legal_entities(self, projection_id: uuid.UUID | None, actor: str) -> int:
        """A legal entity for every (position source, `legal_entity_ref`) of the tenant's
        position versions (only this projection's, when given) that has none yet. Returns
        the number written."""
        pv, pos = PositionVersion, Position
        conditions = [
            pv.tenant_id == self.scope.tenant_id,
            pos.tenant_id == self.scope.tenant_id,
            pv.legal_entity_ref.is_not(None),
            pv.legal_entity_ref != "",
        ]
        if projection_id is not None:
            conditions.append(pv.domain_projection_id == projection_id)
        rows = (
            select(
                func.gen_random_uuid(),
                self._tenant(),
                pos.source_system_id,
                pv.legal_entity_ref,
                literal(True),
                literal(actor),
            )
            .join(pos, pos.id == pv.position_id)
            .where(*conditions)
            .group_by(pos.source_system_id, pv.legal_entity_ref)
        )
        e = LegalEntity
        statement = (
            pg_insert(e)
            .from_select(
                [e.id, e.tenant_id, e.source_system_id, e.code, e.provisional, e.created_by],
                rows,
            )
            .on_conflict_do_nothing()
        )
        result = await self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    def _candidates(self, pair: LinkPair, unique: bool) -> Any:
        """The tenant's identities of the pair's target kind per key: with their count, or
        (unique) only the keys exactly one identity has, with that identity's id."""
        target, key_attr = LINK_TARGETS[pair.target_kind]
        key = getattr(target, key_attr)
        if unique:
            return (
                select(
                    key.label("key"),
                    func.array_agg(target.id, type_=ARRAY(UUID(as_uuid=True)))[1].label(
                        "target_id"
                    ),
                )
                .where(target.tenant_id == self.scope.tenant_id)
                .group_by(key)
                .having(func.count() == 1)
                .subquery()
            )
        return (
            select(key.label("key"), func.count().label("n"))
            .where(target.tenant_id == self.scope.tenant_id)
            .group_by(key)
            .subquery()
        )

    def _linked(self, pair: LinkPair, source_id: Any) -> ColumnElement[bool]:
        link = DomainIdentityLink
        return and_(
            link.tenant_id == self.scope.tenant_id,
            link.source_kind == pair.source_kind.value,
            link.source_row_id == source_id,
            link.target_kind == pair.target_kind.value,
        )

    async def write_links(self, pair: LinkPair, projection_id: uuid.UUID | None, actor: str) -> int:
        """A link for every source row of the tenant (only this projection's, when given)
        with no link of the pair yet whose key exactly one identity of the tenant has (D-58
        §3). Returns the number written; an existing link is never changed."""
        source = LINK_SOURCES[pair.source_kind]
        key = getattr(source, pair.key_field)
        candidates = self._candidates(pair, unique=True)
        conditions = [
            source.tenant_id == self.scope.tenant_id,
            key != "",
            ~exists().where(self._linked(pair, source.id)),
        ]
        if projection_id is not None:
            conditions.append(source.domain_projection_id == projection_id)
        rows = (
            select(
                func.gen_random_uuid(),
                self._tenant(),
                literal(pair.source_kind.value),
                source.id,
                literal(pair.target_kind.value),
                candidates.c.target_id,
                literal(IdentityLinkMethod.BUSINESS_KEY_UNIQUE.value),
                literal(RESOLVER_VERSION),
                literal(actor),
            )
            .select_from(source)
            .join(candidates, candidates.c.key == key)
            .where(*conditions)
        )
        link = DomainIdentityLink
        statement = (
            pg_insert(link)
            .from_select(
                [
                    link.id,
                    link.tenant_id,
                    link.source_kind,
                    link.source_row_id,
                    link.target_kind,
                    link.target_id,
                    link.method,
                    link.resolver_version,
                    link.created_by,
                ],
                rows,
            )
            .on_conflict_do_nothing()
        )
        result = await self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    async def link_counts(self, pair: LinkPair, projection_id: uuid.UUID | None) -> LinkCounts:
        """How the tenant's source rows of the pair (only this projection's, when given)
        stand: linked, or why not (no key, no identity, several identities), and how many
        linked rows' keys have since become ambiguous (D-58 §5)."""
        source = LINK_SOURCES[pair.source_kind]
        key = getattr(source, pair.key_field)
        candidates = self._candidates(pair, unique=False)
        link = DomainIdentityLink
        linked, unlinked = link.id.is_not(None), link.id.is_(None)
        has_key = and_(key.is_not(None), key != "")
        n = func.coalesce(candidates.c.n, 0)
        query = (
            select(
                func.count().label("rows"),
                func.count().filter(linked).label("linked"),
                func.count().filter(and_(unlinked, has_key, n == 0)).label("unresolved"),
                func.count().filter(and_(unlinked, has_key, n > 1)).label("ambiguous"),
                func.count().filter(and_(unlinked, or_(key.is_(None), key == ""))).label("no_key"),
                func.count().filter(and_(linked, n > 1)).label("linked_now_ambiguous"),
            )
            .select_from(source)
            .outerjoin(link, self._linked(pair, source.id))
            .outerjoin(candidates, candidates.c.key == key)
            .where(source.tenant_id == self.scope.tenant_id)
        )
        if projection_id is not None:
            query = query.where(source.domain_projection_id == projection_id)
        row = (await self.session.execute(query)).one()
        return LinkCounts(
            rows=int(row.rows),
            linked=int(row.linked),
            unresolved=int(row.unresolved),
            ambiguous=int(row.ambiguous),
            no_key=int(row.no_key),
            linked_now_ambiguous=int(row.linked_now_ambiguous),
        )

    # --- reads ------------------------------------------------------------------------------

    async def legal_entity_page(
        self, source_id: uuid.UUID | None, limit: int, offset: int
    ) -> tuple[list[LegalEntity], int]:
        query = select(LegalEntity).where(LegalEntity.tenant_id == self.scope.tenant_id)
        if source_id is not None:
            query = query.where(LegalEntity.source_system_id == source_id)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(LegalEntity.code, LegalEntity.source_system_id, LegalEntity.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def legal_entity(self, legal_entity_id: uuid.UUID) -> LegalEntity | None:
        result = await self.session.execute(
            select(LegalEntity).where(
                LegalEntity.id == legal_entity_id, LegalEntity.tenant_id == self.scope.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def legal_entity_schema_versions(
        self, ids: Sequence[uuid.UUID]
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """(legal entity id, schema version id) for every pinned position schema version
        whose rows name the entity's code from the entity's source, in the tenant."""
        if not ids:
            return []
        e, pv, pos = LegalEntity, PositionVersion, Position
        result = await self.session.execute(
            select(e.id, pv.schema_version_id)
            .distinct()
            .join(
                pos, and_(pos.source_system_id == e.source_system_id, pos.tenant_id == e.tenant_id)
            )
            .join(pv, and_(pv.position_id == pos.id, pv.legal_entity_ref == e.code))
            .where(
                e.tenant_id == self.scope.tenant_id,
                pv.tenant_id == self.scope.tenant_id,
                e.id.in_(list(ids)),
            )
        )
        return [(row[0], row[1]) for row in result]

    async def target_link_counts(
        self, target_kind: IdentityTargetKind, target_id: uuid.UUID
    ) -> dict[str, int]:
        """Links of the tenant to this identity, per source kind."""
        link = DomainIdentityLink
        result = await self.session.execute(
            select(link.source_kind, func.count())
            .where(
                link.tenant_id == self.scope.tenant_id,
                link.target_kind == target_kind.value,
                link.target_id == target_id,
            )
            .group_by(link.source_kind)
        )
        return {row[0]: int(row[1]) for row in result}

    async def link_page(
        self, f: LinkFilter, limit: int, offset: int
    ) -> tuple[list[DomainIdentityLink], int]:
        link = DomainIdentityLink
        query = select(link).where(link.tenant_id == self.scope.tenant_id)
        if f.source_kind is not None:
            query = query.where(link.source_kind == f.source_kind.value)
        if f.source_id is not None:
            query = query.where(link.source_row_id == f.source_id)
        if f.target_kind is not None:
            query = query.where(link.target_kind == f.target_kind.value)
        if f.target_id is not None:
            query = query.where(link.target_id == f.target_id)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(link.created_at, link.id).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)
