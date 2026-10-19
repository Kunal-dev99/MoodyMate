"""domain.service

Projection of validated imports into the WS3 domain tables (BE-14.3, D-29) and the instrument
catalogue (BE-14.4).

Projection (`POST /imports/{import_id}/projection`, `domain:project`, audited
`import.project`):

- Eligible: an import with a manifest, status COMPLETED, COMPLETED_WITH_WARNINGS or
  QUARANTINED, pinned to a schema version projector v1 was written for (`instrument`,
  `position`, `cashflow_event` and, Feature 04, `counterparty` and `credit_exposure`, version
  1). The credit codes are `projection_unsupported` while CREDIT_PROJECTION_ENABLED is off
  (D-37 §7). Only its accepted rows (canonical records)
  are projected: exactly one version or event row each, so rows are conserved.
- Idempotent: a second call returns the existing projection and writes nothing.
- Refusals, each audited DENIED with its code: 404 `not_found` (absent and foreign alike,
  D-04), 409 `domain_projection_disabled` (DOMAIN_PROJECTION_ENABLED off),
  409 `projection_unsupported` and 409 `not_validated`.
- Fields map through the frozen spec of `PROJECTOR_VERSION` (app.domain.constants): a
  schema field projects into its typed column only when its pinned type is the column's,
  so a STRING is never parsed (`instrument` v1 `maturity_date` stays NULL and is counted
  TYPE_NOT_PROJECTED). Extension-eligible instrument fields become attribute values; every
  other field stays in the canonical record, which every row names.
- Decimal throughout, never float; amounts are stored exactly as the canonical text says.

Credit vocabulary fields (segment, facility type, origination grade, collateral type) are
kept as given and counted UNKNOWN_VALUE outside the seeded `credit_vocabulary` (D-37 §3).

The catalogue reads (instruments; Feature 04: credit vocabularies, counterparties and credit
exposures, BE-19.5) are tenant-scoped; values are masked by the router from the
classification of each value's schema field, in the schema version the row pins.
"""

import re
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.classification import UNCLASSIFIED
from app.core.errors import ApiError
from app.core.principal import Principal
from app.db.base import Base
from app.domain.constants import (
    CREDIT_SCHEMA_CODES,
    INSTRUMENT_TYPE_FIELD,
    PROJECT_IMPORT,
    PROJECTABLE_STATUSES,
    PROJECTION_SPECS,
    PROJECTOR_VERSION,
    ColumnKind,
    CreditVocabularyKind,
    DomainSchemaCode,
    ProjectionRefusal,
    ProjectionSpec,
)
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
    PositionVersion,
)
from app.domain.repository import (
    VERSIONED,
    CounterpartyFilter,
    DomainRepository,
    ExposureFilter,
    IdentityRef,
    InstrumentFilter,
)
from app.governance.constants import AuditOutcome, ClassificationCode
from app.governance.models import SchemaField
from app.governance.service import SchemaService, audit_by, record_denial
from app.ingestion.constants import ImportStatus
from app.ingestion.models import CanonicalRecord, ImportJob, ImportManifest
from app.ingestion.service import ImportService

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2 (counterparty v1 `country`)
_INTEGER = re.compile(r"-?[0-9]+")  # the canonical INTEGER text (D-22)
_BOOLEANS = {"true": True, "false": False}  # the canonical BOOLEAN text (D-22)
_CLASS_RANK = {c.value: i for i, c in enumerate(ClassificationCode)}


# --- the pure projection core (no I/O; holds no float) -----------------------------------------


@dataclass(frozen=True)
class PinnedField:
    """What projection and masking need of one field of the pinned schema version."""

    name: str
    data_type: str
    classification: str
    unit: str | None
    extension_eligible: bool


@dataclass
class Projected:
    """One canonical record, projected: its keys, typed columns and attribute texts."""

    record: CanonicalRecord
    business_key: str
    instrument_key: str | None
    columns: dict[str, Any]
    attributes: dict[str, str]


@dataclass
class Notes:
    """The value-free counts of one projection (D-29 §2)."""

    type_not_projected: Counter[str] = field(default_factory=Counter)
    unknown_values: Counter[str] = field(default_factory=Counter)


def typed(kind: ColumnKind, text: str) -> Any:
    """Canonical typed text (D-22) as the column's Python value: exact Decimal or date. The
    validator already proved the text; anything else is a defect and raises (fail closed)."""
    if kind is ColumnKind.DECIMAL:
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("canonical decimal text expected") from exc
        if not value.is_finite():
            raise ValueError("a finite decimal expected")
        return value
    if kind is ColumnKind.DATE:
        try:
            return date.fromisoformat(text)
        except ValueError:
            # date.fromisoformat's message repeats the text: never let a value reach a log
            # or a traceback (no cause kept either).
            raise ValueError("canonical ISO date text expected") from None
    if kind is ColumnKind.INTEGER:
        if not _INTEGER.fullmatch(text):
            raise ValueError("canonical integer text expected")
        return int(text)
    if kind is ColumnKind.BOOLEAN:
        if text not in _BOOLEANS:
            raise ValueError("canonical boolean text expected")
        return _BOOLEANS[text]
    return text


def project_record(
    spec: ProjectionSpec,
    fields: Mapping[str, PinnedField],
    record: CanonicalRecord,
    notes: Notes,
) -> Projected:
    """Project one canonical record by the frozen spec (D-29 §4)."""
    payload: Mapping[str, str | None] = record.payload
    business_key = payload.get(spec.business_key)
    if business_key is None:
        raise ValueError("an accepted record always carries its business key")
    columns: dict[str, Any] = {}
    for column in spec.columns:
        pinned = fields.get(column.field)
        text = payload.get(column.field)
        value: Any = None
        if text is not None and pinned is not None:
            if pinned.data_type != column.kind.value:
                notes.type_not_projected[column.field] += 1  # never parsed (D-29 §4)
            elif column.vocabulary is not None and text not in column.vocabulary:
                notes.unknown_values[column.field] += 1
            elif column.column == "currency" and not _CURRENCY.match(text):
                notes.unknown_values[column.field] += 1
            elif column.column == "country" and not _COUNTRY.match(text):
                notes.unknown_values[column.field] += 1
            else:
                value = typed(column.kind, text)
        columns[column.column] = value
    attributes: dict[str, str] = {}
    if spec.attributes:
        for name, pinned in fields.items():
            text = payload.get(name)
            if pinned.extension_eligible and name not in spec.mapped_fields and text is not None:
                attributes[name] = text
    return Projected(
        record=record,
        business_key=business_key,
        instrument_key=payload.get(spec.instrument_key) if spec.instrument_key else None,
        columns=columns,
        attributes=attributes,
    )


def canonical_only(spec: ProjectionSpec, fields: Mapping[str, PinnedField]) -> list[str]:
    """Pinned fields with no typed column and no attribute: kept in the canonical record."""
    return sorted(
        name
        for name, pinned in fields.items()
        if name not in spec.mapped_fields and not (spec.attributes and pinned.extension_eligible)
    )


# The projector's pure parts, for the test that proves they hold no float (as BE-11.2).
PROJECTOR_PARTS = ("typed", "project_record", "canonical_only")


# --- views returned to the router ------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionView:
    projection: DomainProjection
    job: ImportJob
    manifest: ImportManifest
    schema_version: int


@dataclass(frozen=True)
class SchemaPins:
    """Pinned schema versions: id -> version number and fields (for labels and masking)."""

    codes: Mapping[uuid.UUID, str]  # schema code
    versions: Mapping[uuid.UUID, int]
    fields: Mapping[uuid.UUID, Mapping[str, PinnedField]]

    def classes(self, schema_version_id: uuid.UUID) -> dict[str, str]:
        return {n: f.classification for n, f in self.fields.get(schema_version_id, {}).items()}

    def most_sensitive(self, schema_version_id: uuid.UUID) -> str:
        """The most sensitive classification of the pinned version's fields (every field a
        canonical payload can hold). An unknown code ranks above all; a version with no
        known fields is `unclassified`, which no role is cleared for (fail closed)."""
        codes = set(self.classes(schema_version_id).values())
        if not codes:
            return UNCLASSIFIED
        return max(codes, key=lambda c: _CLASS_RANK.get(c, len(_CLASS_RANK)))


@dataclass(frozen=True)
class InstrumentRow:
    instrument: Instrument
    versions: Sequence[InstrumentVersion]  # oldest first
    source_code: str


@dataclass(frozen=True)
class InstrumentCatalogue:
    rows: list[InstrumentRow]
    total: int
    instrument_types: list[InstrumentType]
    pins: SchemaPins


@dataclass(frozen=True)
class AttributeView:
    code: str
    data_type: str
    value_text: str


@dataclass(frozen=True)
class InstrumentView:
    row: InstrumentRow
    attributes: Mapping[uuid.UUID, list[AttributeView]]  # version id -> its attributes
    cash_flows: list[CashflowEvent]
    pins: SchemaPins


@dataclass(frozen=True)
class CounterpartyRow:
    counterparty: Counterparty
    versions: Sequence[CounterpartyVersion]  # oldest first
    source_code: str


@dataclass(frozen=True)
class ExposureRow:
    exposure: CreditExposure
    versions: Sequence[CreditExposureVersion]  # oldest first
    source_code: str


@dataclass(frozen=True)
class CreditPage:
    """A page of counterparties or exposures, with the pins and vocabulary to show them."""

    counterparties: list[CounterpartyRow]
    exposures: list[ExposureRow]
    total: int
    pins: SchemaPins
    vocabulary: Mapping[CreditVocabularyKind, frozenset[str]]


@dataclass(frozen=True)
class CounterpartyView:
    row: CounterpartyRow
    exposure_versions: list[tuple[CreditExposureVersion, str]]  # with the exposure's key
    pins: SchemaPins
    vocabulary: Mapping[CreditVocabularyKind, frozenset[str]]


@dataclass(frozen=True)
class ExposureView:
    row: ExposureRow
    pins: SchemaPins
    vocabulary: Mapping[CreditVocabularyKind, frozenset[str]]


def _pinned(field_row: SchemaField) -> PinnedField:
    return PinnedField(
        name=field_row.name,
        data_type=field_row.data_type,
        classification=field_row.classification,
        unit=field_row.unit,
        extension_eligible=field_row.extension_eligible,
    )


class DomainService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = DomainRepository(session, principal.scope)
        self.imports = ImportService(session, principal)
        self.schemas = SchemaService(session, principal)

    async def _pins(self) -> SchemaPins:
        codes: dict[uuid.UUID, str] = {}
        versions: dict[uuid.UUID, int] = {}
        fields: dict[uuid.UUID, dict[str, PinnedField]] = {}
        for schema in await self.schemas.list_schemas():
            for version in schema.versions:
                codes[version.id] = schema.code
                versions[version.id] = version.version
                fields[version.id] = {f.name: _pinned(f) for f in version.fields}
        return SchemaPins(codes, versions, fields)

    # --- projection (BE-14.3) ------------------------------------------------------------

    async def _refuse(self, error: ApiError, import_id: uuid.UUID) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await record_denial(
            self.session, self.principal, PROJECT_IMPORT, "import_job", str(import_id), error.code
        )
        raise error

    async def project(
        self, import_id: uuid.UUID, enabled: bool, credit_enabled: bool = True
    ) -> tuple[ProjectionView, bool]:
        """Project the import once (D-29), in one transaction; the view and whether it was
        projected now (False: the existing projection, nothing written). With
        `credit_enabled` off (CREDIT_PROJECTION_ENABLED, D-37 §7) the credit codes are
        `projection_unsupported`, the pre-Feature-04 behaviour."""
        if not enabled:
            await self._refuse(
                ApiError(
                    409,
                    ProjectionRefusal.DOMAIN_PROJECTION_DISABLED,
                    "Domain projection is switched off in this environment; existing domain "
                    "rows stay readable.",
                ),
                import_id,
            )
        job = await self.imports.locked(import_id)
        if job is None:
            await self._refuse(ApiError(404, "not_found", "Import not found."), import_id)
        pins = await self._pins()
        schema_code = pins.codes.get(job.schema_version_id)
        schema_version = pins.versions.get(job.schema_version_id, 0)
        supported = {c.value: c for c in DomainSchemaCode}
        code = supported.get(schema_code) if schema_code else None
        if code in CREDIT_SCHEMA_CODES and not credit_enabled:
            await self._refuse(self._unsupported(schema_code, schema_version), job.id)
        existing = await self.repo.projection_for(job.id)
        if existing is not None:
            manifest = await self.imports.manifest(job)
            assert manifest is not None  # a projection is written only with one (DB trigger)
            return ProjectionView(existing, job, manifest, schema_version), False

        # A code without a projector v1 spec is projection_unsupported, never a KeyError.
        spec = PROJECTION_SPECS.get(code) if code is not None else None
        if spec is None or schema_version not in spec.schema_versions:
            await self._refuse(self._unsupported(schema_code, schema_version), job.id)
        manifest = await self.imports.manifest(job)
        status = ImportStatus(job.status)
        if manifest is None or status not in PROJECTABLE_STATUSES:
            await self._refuse(
                ApiError(
                    409,
                    ProjectionRefusal.NOT_VALIDATED,
                    "Only a validated import (COMPLETED, COMPLETED_WITH_WARNINGS or QUARANTINED, "
                    "with a manifest) can be projected.",
                    {"status": status.value},
                ),
                job.id,
            )
        projection = await self._write(job, manifest, spec, pins.fields[job.schema_version_id])
        await self.session.commit()
        return ProjectionView(projection, job, manifest, schema_version), True

    @staticmethod
    def _unsupported(schema_code: str | None, schema_version: int) -> ApiError:
        return ApiError(
            409,
            ProjectionRefusal.PROJECTION_UNSUPPORTED,
            "Projector version 1 projects instrument, position, cashflow_event, counterparty "
            "and credit_exposure imports of schema version 1 only (the credit schemas while "
            "CREDIT_PROJECTION_ENABLED is on); another schema or version needs a new "
            "projector version (D-29, D-37).",
            {
                "schema_code": schema_code,
                "schema_version": schema_version,
                "projector_version": PROJECTOR_VERSION,
            },
        )

    async def _write(
        self,
        job: ImportJob,
        manifest: ImportManifest,
        spec: ProjectionSpec,
        fields: Mapping[str, PinnedField],
    ) -> DomainProjection:
        await self.repo.lock_tenant()
        records = await self.imports.accepted_records(job)
        if len(records) != manifest.accepted_count:  # the manifest is written with them
            raise RuntimeError("canonical records do not match the manifest's accepted count")
        notes = Notes()
        projected = [project_record(spec, fields, r, notes) for r in records]
        actor = self.principal.actor
        tenant = job.tenant_id  # D-04: copied from the import (canonical_record has none)

        new_identities = 0
        identities: dict[str, IdentityRef] = {}
        versioned = VERSIONED.get(spec.schema_code)  # None: cash-flow events
        kind = versioned.identity if versioned else None
        new_identity_rows: list[dict[str, Any]] = []
        if versioned is not None:
            identities = await self.repo.identities(
                versioned, job.source_system_id, (p.business_key for p in projected)
            )
            for p in projected:
                if p.business_key not in identities:
                    ref = IdentityRef(uuid.uuid4(), 0)
                    identities[p.business_key] = ref
                    new_identity_rows.append(
                        {
                            "id": ref.id,
                            "tenant_id": tenant,
                            "source_system_id": job.source_system_id,
                            "business_key": p.business_key,
                            "created_by": actor,
                        }
                    )
            new_identities = len(new_identity_rows)

        types = {t.code: t.id for t in await self.repo.instrument_types()}
        if spec.schema_code is DomainSchemaCode.INSTRUMENT:
            for p in projected:
                code = p.columns.get("instrument_type_code")
                if code is not None and code not in types:
                    notes.unknown_values[INSTRUMENT_TYPE_FIELD] += 1
        # Credit vocabulary fields are kept as given and counted when unknown (D-37 §3).
        checked = [c for c in spec.columns if c.credit_vocabulary is not None]
        if checked:
            vocabulary = await self.credit_vocabulary_codes()
            for p in projected:
                for column in checked:
                    value = p.columns.get(column.column)
                    assert column.credit_vocabulary is not None
                    if value is not None and value not in vocabulary[column.credit_vocabulary]:
                        notes.unknown_values[column.field] += 1

        attribute_rows: list[dict[str, Any]] = []
        definitions = {d.code: d.id for d in await self.repo.attribute_definitions()}
        new_definitions: list[dict[str, Any]] = []
        for name in sorted({a for p in projected for a in p.attributes}):
            if name not in definitions:
                definitions[name] = uuid.uuid4()
                new_definitions.append(
                    {
                        "id": definitions[name],
                        "tenant_id": tenant,
                        "code": name,
                        "data_type": fields[name].data_type,
                        "description": "Extension-eligible instrument field (PROVISIONAL).",
                        "provisional": True,
                        "created_by": actor,
                    }
                )

        summary: dict[str, Any] = {
            "records": len(records),
            "rows_projected": len(projected),
            "new_identities": new_identities,
            "attribute_values": sum(len(p.attributes) for p in projected),
            "type_not_projected": dict(sorted(notes.type_not_projected.items())),
            "unknown_values": dict(sorted(notes.unknown_values.items())),
            "canonical_only_fields": canonical_only(spec, fields),
        }
        projection = DomainProjection(
            id=uuid.uuid4(),
            tenant_id=tenant,
            import_job_id=job.id,
            import_manifest_id=manifest.id,
            schema_code=spec.schema_code.value,
            schema_version_id=job.schema_version_id,
            projector_version=PROJECTOR_VERSION,
            record_count=len(projected),
            summary=summary,
            created_by=actor,
        )
        self.repo.add(projection)
        await self.repo.flush()
        if kind is not None:
            await self.repo.insert_rows(kind, new_identity_rows)
        await self.repo.insert_rows(InstrumentAttributeDefinition, new_definitions)

        rows: list[dict[str, Any]] = []
        for p in projected:
            record = p.record
            row: dict[str, Any] = {
                "id": uuid.uuid4(),
                "tenant_id": tenant,
                "import_job_id": job.id,
                "domain_projection_id": projection.id,
                "canonical_record_id": record.id,
                "source_record_id": record.source_record_id,
                "source_ordinal": record.ordinal,
                "schema_version_id": record.schema_version_id,
                "payload_sha256": record.payload_sha256,
                "created_by": actor,
                **p.columns,
            }
            if versioned is not None:
                ref = identities[p.business_key]
                version = ref.last_version + 1
                identities[p.business_key] = IdentityRef(ref.id, version)
                row["version"] = version
                row[versioned.link] = ref.id
                if kind is Instrument:
                    row["instrument_type_id"] = types.get(p.columns["instrument_type_code"] or "")
                if spec.instrument_key is not None:
                    row["instrument_key"] = p.instrument_key
            else:
                row["source_system_id"] = job.source_system_id
                row["business_key"] = p.business_key
                row["instrument_key"] = p.instrument_key
            rows.append(row)
            for name, value_text in p.attributes.items():
                attribute_rows.append(
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": tenant,
                        "instrument_version_id": row["id"],
                        "attribute_definition_id": definitions[name],
                        "value_text": value_text,
                        "created_by": actor,
                    }
                )
        target: type[Base] = versioned.version if versioned else CashflowEvent
        await self.repo.insert_rows(target, rows)
        await self.repo.insert_rows(InstrumentAttributeValue, attribute_rows)

        details: dict[str, Any] = {
            "projection_id": str(projection.id),
            "schema_code": spec.schema_code.value,
            "projector_version": PROJECTOR_VERSION,
            "rows_projected": len(projected),
            "new_identities": new_identities,
            "type_not_projected": sum(notes.type_not_projected.values()),
            "unknown_values": sum(notes.unknown_values.values()),
        }
        audit_by(
            self.session,
            self.principal,
            PROJECT_IMPORT,
            "import_job",
            outcome=AuditOutcome.SUCCESS,
            resource_id=str(job.id),
            details=details,
            details_classification=dict.fromkeys(details, ClassificationCode.PUBLIC.value),
        )
        return projection

    async def projection(self, import_id: uuid.UUID) -> ProjectionView:
        """An import's projection (GET /imports/{import_id}/projection, read only): 404
        `not_found` alike for an absent and a foreign import (D-04), 404
        `projection_not_found` before it is projected."""
        job = await self.imports.get(import_id)
        existing = await self.repo.projection_for(job.id)
        manifest = await self.imports.manifest(job)
        if existing is None or manifest is None:
            raise ApiError(
                404,
                "projection_not_found",
                "This import has not been projected into the domain yet.",
            )
        pins = await self._pins()
        return ProjectionView(existing, job, manifest, pins.versions.get(job.schema_version_id, 0))

    # --- the instrument catalogue (BE-14.4) ------------------------------------------------

    async def _rows(self, instruments: list[Instrument]) -> list[InstrumentRow]:
        versions: dict[uuid.UUID, list[InstrumentVersion]] = {}
        for v in await self.repo.instrument_versions([i.id for i in instruments]):
            versions.setdefault(v.instrument_id, []).append(v)
        codes = await self.imports.source_codes({i.source_system_id for i in instruments})
        return [
            InstrumentRow(i, versions.get(i.id, []), codes.get(i.source_system_id, ""))
            for i in instruments
        ]

    async def catalogue(self, f: InstrumentFilter, limit: int, offset: int) -> InstrumentCatalogue:
        instruments, total = await self.repo.instrument_page(f, limit, offset)
        return InstrumentCatalogue(
            await self._rows(instruments),
            total,
            await self.repo.instrument_types(),
            await self._pins(),
        )

    # --- for WS3 dataset snapshots and runs (Phase 15, D-30, D-31): read only -------------

    async def schema_pins(self) -> SchemaPins:
        """Every schema version of the tenant with its fields' types and classifications."""
        return await self._pins()

    async def instrument_type_codes(self) -> list[str]:
        """The tenant's instrument type vocabulary (a rule parameter key vocabulary)."""
        return [t.code for t in await self.repo.instrument_types()]

    async def projected_import_ids(self) -> list[uuid.UUID]:
        """The tenant's projected imports (the GET /imports `projected` filter)."""
        return await self.repo.projected_import_ids()

    async def projection_of(self, job: ImportJob) -> DomainProjection | None:
        """The projection of an import already reached through this scope."""
        return await self.repo.projection_for(job.id)

    async def ambiguous_keys(self, projection: DomainProjection) -> int:
        """Business keys with more than one version in the projection (D-30 §3)."""
        return await self.repo.ambiguous_keys(projection)

    async def import_cash_flows(self, import_id: uuid.UUID) -> list[CashflowEvent]:
        return await self.repo.import_cash_flows(import_id)

    async def import_instruments(self, import_id: uuid.UUID) -> list[tuple[InstrumentVersion, str]]:
        return await self.repo.import_instruments(import_id)

    async def import_positions(self, import_id: uuid.UUID) -> list[tuple[PositionVersion, str]]:
        return await self.repo.import_positions(import_id)

    async def positions_by_id(self, ids: Sequence[uuid.UUID]) -> list[tuple[PositionVersion, str]]:
        return await self.repo.positions_by_id(ids)

    async def cash_flows_by_id(self, ids: Sequence[uuid.UUID]) -> list[CashflowEvent]:
        return await self.repo.cash_flows_by_id(ids)

    async def instrument_versions_by_id(
        self, ids: Sequence[uuid.UUID]
    ) -> list[tuple[InstrumentVersion, str]]:
        return await self.repo.instrument_versions_by_id(ids)

    async def instrument(self, instrument_id: uuid.UUID) -> InstrumentView:
        """One instrument with its versions, attributes and cash flows: 404 alike for an
        absent and a foreign id (D-04)."""
        instrument = await self.repo.instrument(instrument_id)
        if instrument is None:
            raise ApiError(404, "not_found", "Instrument not found.")
        [row] = await self._rows([instrument])
        attributes: dict[uuid.UUID, list[AttributeView]] = {}
        for value, definition in await self.repo.attribute_values([v.id for v in row.versions]):
            attributes.setdefault(value.instrument_version_id, []).append(
                AttributeView(definition.code, definition.data_type, value.value_text)
            )
        return InstrumentView(
            row,
            attributes,
            await self.repo.cashflows_for(instrument.business_key, instrument.source_system_id),
            await self._pins(),
        )

    # --- Feature 04 credit reads (BE-19.5, D-37, D-43): tenant-scoped, masked by the router -

    async def credit_vocabulary(self) -> list[CreditVocabulary]:
        """The tenant's seeded PROVISIONAL credit vocabularies (D-37 §5)."""
        return await self.repo.credit_vocabulary()

    async def credit_vocabulary_codes(self) -> dict[CreditVocabularyKind, frozenset[str]]:
        """Each credit vocabulary's codes (for `*_known` and UNKNOWN_VALUE counts)."""
        codes: dict[CreditVocabularyKind, set[str]] = {k: set() for k in CreditVocabularyKind}
        for entry in await self.repo.credit_vocabulary():
            codes[CreditVocabularyKind(entry.vocabulary)].add(entry.code)
        return {k: frozenset(v) for k, v in codes.items()}

    async def _counterparty_rows(self, items: list[Counterparty]) -> list[CounterpartyRow]:
        versions: dict[uuid.UUID, list[CounterpartyVersion]] = {}
        for v in await self.repo.counterparty_versions([c.id for c in items]):
            versions.setdefault(v.counterparty_id, []).append(v)
        codes = await self.imports.source_codes({c.source_system_id for c in items})
        return [
            CounterpartyRow(c, versions.get(c.id, []), codes.get(c.source_system_id, ""))
            for c in items
        ]

    async def _exposure_rows(self, items: list[CreditExposure]) -> list[ExposureRow]:
        versions: dict[uuid.UUID, list[CreditExposureVersion]] = {}
        for v, _ in await self.repo.exposure_versions([e.id for e in items]):
            versions.setdefault(v.credit_exposure_id, []).append(v)
        codes = await self.imports.source_codes({e.source_system_id for e in items})
        return [
            ExposureRow(e, versions.get(e.id, []), codes.get(e.source_system_id, "")) for e in items
        ]

    async def counterparties(self, f: CounterpartyFilter, limit: int, offset: int) -> CreditPage:
        items, total = await self.repo.counterparty_page(f, limit, offset)
        return CreditPage(
            await self._counterparty_rows(items),
            [],
            total,
            await self._pins(),
            await self.credit_vocabulary_codes(),
        )

    async def counterparty(self, counterparty_id: uuid.UUID) -> CounterpartyView:
        """One counterparty with its versions and the exposure versions naming its business
        key in the tenant: 404 alike for an absent and a foreign id (D-04)."""
        counterparty = await self.repo.counterparty(counterparty_id)
        if counterparty is None:
            raise ApiError(404, "not_found", "Counterparty not found.")
        [row] = await self._counterparty_rows([counterparty])
        return CounterpartyView(
            row,
            await self.repo.exposure_versions_naming(counterparty.business_key),
            await self._pins(),
            await self.credit_vocabulary_codes(),
        )

    async def exposures(self, f: ExposureFilter, limit: int, offset: int) -> CreditPage:
        items, total = await self.repo.exposure_page(f, limit, offset)
        return CreditPage(
            [],
            await self._exposure_rows(items),
            total,
            await self._pins(),
            await self.credit_vocabulary_codes(),
        )

    async def exposure(self, exposure_id: uuid.UUID) -> ExposureView:
        """One credit exposure with its versions: 404 alike for an absent and a foreign id
        (D-04)."""
        exposure = await self.repo.exposure(exposure_id)
        if exposure is None:
            raise ApiError(404, "not_found", "Credit exposure not found.")
        [row] = await self._exposure_rows([exposure])
        return ExposureView(row, await self._pins(), await self.credit_vocabulary_codes())
