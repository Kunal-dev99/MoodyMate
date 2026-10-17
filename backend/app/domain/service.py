"""domain.service

Projection of validated imports into the WS3 domain tables (BE-14.3, D-29) and the instrument
catalogue (BE-14.4).

Projection (`POST /imports/{import_id}/projection`, `domain:project`, audited
`import.project`):

- Eligible: an import with a manifest, status COMPLETED, COMPLETED_WITH_WARNINGS or
  QUARANTINED, pinned to a schema version projector v1 was written for (`instrument`,
  `position` or `cashflow_event`, version 1). Only its accepted rows (canonical records)
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

The catalogue reads are tenant-scoped; values are masked by the router from the
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
    INSTRUMENT_TYPE_FIELD,
    PROJECT_IMPORT,
    PROJECTABLE_STATUSES,
    PROJECTION_SPECS,
    PROJECTOR_VERSION,
    ColumnKind,
    DomainSchemaCode,
    ProjectionRefusal,
    ProjectionSpec,
)
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
from app.domain.repository import DomainRepository, IdentityRef, InstrumentFilter
from app.governance.constants import AuditOutcome, ClassificationCode
from app.governance.models import SchemaField
from app.governance.service import SchemaService, audit_by, record_denial
from app.ingestion.constants import ImportStatus
from app.ingestion.models import CanonicalRecord, ImportJob, ImportManifest
from app.ingestion.service import ImportService

_CURRENCY = re.compile(r"^[A-Z]{3}$")
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

    async def project(self, import_id: uuid.UUID, enabled: bool) -> tuple[ProjectionView, bool]:
        """Project the import once (D-29), in one transaction; the view and whether it was
        projected now (False: the existing projection, nothing written)."""
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
        existing = await self.repo.projection_for(job.id)
        if existing is not None:
            manifest = await self.imports.manifest(job)
            assert manifest is not None  # a projection is written only with one (DB trigger)
            return ProjectionView(existing, job, manifest, schema_version), False

        supported = {c.value: c for c in DomainSchemaCode}
        spec = PROJECTION_SPECS[supported[schema_code]] if schema_code in supported else None
        if spec is None or schema_version not in spec.schema_versions:
            await self._refuse(
                ApiError(
                    409,
                    ProjectionRefusal.PROJECTION_UNSUPPORTED,
                    "Projector version 1 projects instrument, position and cashflow_event "
                    "imports of schema version 1 only; another schema or version needs a new "
                    "projector version (D-29).",
                    {
                        "schema_code": schema_code,
                        "schema_version": schema_version,
                        "projector_version": PROJECTOR_VERSION,
                    },
                ),
                job.id,
            )
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
        kind: type[Instrument] | type[Position] | None = None
        if spec.schema_code is DomainSchemaCode.INSTRUMENT:
            kind = Instrument
        elif spec.schema_code is DomainSchemaCode.POSITION:
            kind = Position
        new_identity_rows: list[dict[str, Any]] = []
        if kind is not None:
            identities = await self.repo.identities(
                kind, job.source_system_id, (p.business_key for p in projected)
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
            if kind is not None:
                ref = identities[p.business_key]
                version = ref.last_version + 1
                identities[p.business_key] = IdentityRef(ref.id, version)
                row["version"] = version
                if kind is Instrument:
                    row["instrument_id"] = ref.id
                    row["instrument_type_id"] = types.get(p.columns["instrument_type_code"] or "")
                else:
                    row["position_id"] = ref.id
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
        target: type[Base] = CashflowEvent
        if kind is Instrument:
            target = InstrumentVersion
        elif kind is Position:
            target = PositionVersion
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
