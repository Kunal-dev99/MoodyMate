"""domain.router

The WS3 domain endpoints of Phase 14 (the Feature 03 endpoint table, D-34):

- POST /imports/{import_id}/projection (`domain:project`, D-29): project a validated import
  into the domain tables, once. 201 when projected now; 200 with `Idempotent-Replayed: true`
  for the existing projection. 409 `domain_projection_disabled` while
  DOMAIN_PROJECTION_ENABLED is off. Every refusal is audited DENIED.
- GET /imports/{import_id}/projection (`domain:read`, added for the import page): the
  existing projection, read only; 404 `projection_not_found` before one exists.
- GET /instruments and GET /instruments/{instrument_id} (`domain:read`): the instrument
  catalogue, with every value masked by its pinned schema field's classification (BE-3.5).

Tenant scope comes from the principal (D-04): another tenant's id is the same 404 as a
random one.
"""

import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.classification import UNCLASSIFIED, mask_mapping
from app.core.principal import Principal
from app.db.session import get_session
from app.domain.constants import DomainSchemaCode
from app.domain.models import CashflowEvent, InstrumentVersion
from app.domain.repository import InstrumentFilter
from app.domain.schemas import (
    AttributeOut,
    CashFlowOut,
    InstrumentDetail,
    InstrumentPage,
    InstrumentSummary,
    InstrumentTypeOut,
    InstrumentVersionOut,
    ProjectionOut,
    ProjectionSummary,
    Value,
)
from app.domain.service import (
    AttributeView,
    DomainService,
    InstrumentRow,
    ProjectionView,
    SchemaPins,
)
from app.governance.constants import FieldType, Permission
from app.ingestion.constants import ImportStatus

SessionDep = Annotated[AsyncSession, Depends(get_session)]
DomainReader = Annotated[Principal, Depends(require_permission(Permission.DOMAIN_READ))]
DomainProjector = Annotated[Principal, Depends(require_permission(Permission.DOMAIN_PROJECT))]

router = APIRouter(tags=["domain"])
MASKED_HASH = "masked"  # a string, so the payload_sha256 field keeps its type


def _text(value: Decimal | date | str | None) -> str | None:
    """Exact decimal text (never exponent form) or an ISO date."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _shown(
    values: Mapping[str, Decimal | date | str | None],
    fields: Mapping[str, str],
    pins: SchemaPins,
    schema_version_id: uuid.UUID,
    clear: frozenset[str],
) -> dict[str, Value | None]:
    """`values` (output key -> value) masked by the classification of the schema field each
    is projected from (`fields`: output key -> field), in the pinned schema version. A field
    the version does not define is masked as unclassified (fail closed)."""
    classes = pins.classes(schema_version_id)
    shown = mask_mapping(
        {k: _text(v) for k, v in values.items()},
        {k: classes[f] for k, f in fields.items() if f in classes},
        clear,
    )
    return shown


@router.post(
    "/imports/{import_id}/projection",
    response_model=ProjectionOut,
    status_code=201,
    operation_id="projectImport",
    responses={
        200: {
            "model": ProjectionOut,
            "description": "Already projected: the existing projection is returned and nothing "
            "is written (header Idempotent-Replayed: true).",
        }
    },
)
async def project_import(
    request: Request,
    response: Response,
    import_id: uuid.UUID,
    principal: DomainProjector,
    session: SessionDep,
) -> ProjectionOut:
    """Project a validated import's accepted rows into immutable domain versions (D-29):
    one instrument version, position version or cash-flow event per canonical record, each
    keeping lineage to it. 409 `projection_unsupported` for another schema (or schema
    version), 409 `not_validated` before validation (or for a FAILED import), 409
    `domain_projection_disabled` while the switch is off; each refusal is audited DENIED."""
    enabled = request.app.state.settings.DOMAIN_PROJECTION_ENABLED
    view, created = await DomainService(session, principal).project(import_id, enabled)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return _projection(view)


def _projection(view: ProjectionView) -> ProjectionOut:
    p = view.projection
    return ProjectionOut(
        projection_id=p.id,
        import_id=view.job.id,
        import_status=ImportStatus(view.job.status),
        schema_code=DomainSchemaCode(p.schema_code),
        schema_version_id=p.schema_version_id,
        schema_version=view.schema_version,
        projector_version=p.projector_version,
        accepted_count=view.manifest.accepted_count,
        projected_count=p.record_count,
        summary=ProjectionSummary.model_validate(p.summary),
        projected_at=p.created_at,
        projected_by=p.created_by,
    )


@router.get(
    "/imports/{import_id}/projection",
    response_model=ProjectionOut,
    operation_id="getImportProjection",
)
async def get_import_projection(
    import_id: uuid.UUID, principal: DomainReader, session: SessionDep
) -> ProjectionOut:
    """The import's projection, if any (read only; the same body as the POST's): its counts
    and TYPE_NOT_PROJECTED summary. 404 `projection_not_found` until it is projected; 404
    `not_found` alike for an absent and another tenant's import (D-04)."""
    return _projection(await DomainService(session, principal).projection(import_id))


# --- the instrument catalogue (BE-14.4) ------------------------------------------------------

_SUMMARY_FIELDS = {
    "business_key": "instrument_id",
    "instrument_type": "instrument_type",
    "currency": "currency",
}


def _summary(row: InstrumentRow, pins: SchemaPins, clear: frozenset[str]) -> InstrumentSummary:
    latest = row.versions[-1]
    shown = _shown(
        {
            "business_key": row.instrument.business_key,
            "instrument_type": latest.instrument_type_code,
            "currency": latest.currency,
        },
        _SUMMARY_FIELDS,
        pins,
        latest.schema_version_id,
        clear,
    )
    business_key = shown["business_key"]
    assert business_key is not None
    return InstrumentSummary(
        instrument_id=row.instrument.id,
        business_key=business_key,
        source_id=row.instrument.source_system_id,
        source_code=row.source_code,
        version_count=len(row.versions),
        latest_version=latest.version,
        latest_import_id=latest.import_job_id,
        instrument_type=shown["instrument_type"],
        currency=shown["currency"],
        created_at=row.instrument.created_at,
    )


@router.get("/instruments", response_model=InstrumentPage, operation_id="listInstruments")
async def list_instruments(
    principal: DomainReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    instrument_type: Annotated[
        str | None, Query(max_length=100, description="Instruments with a version of this type.")
    ] = None,
    currency: Annotated[
        str | None,
        Query(pattern=r"^[A-Z]{3}$", description="Instruments with a version in this currency."),
    ] = None,
    source_id: uuid.UUID | None = None,
    import_id: Annotated[
        uuid.UUID | None, Query(description="Instruments with a version from this import.")
    ] = None,
) -> InstrumentPage:
    """The instrument catalogue of the caller's tenant, by business key. A filter matches an
    instrument when any of its versions matches."""
    view = await DomainService(session, principal).catalogue(
        InstrumentFilter(instrument_type, currency, source_id, import_id), limit, offset
    )
    clear = principal.clear_classifications
    return InstrumentPage(
        items=[_summary(r, view.pins, clear) for r in view.rows],
        total=view.total,
        limit=limit,
        offset=offset,
        instrument_types=[
            InstrumentTypeOut(code=t.code, name=t.name, provisional=t.provisional)
            for t in view.instrument_types
        ],
    )


_VERSION_FIELDS = {
    "instrument_type": "instrument_type",
    "currency": "currency",
    "notional": "notional",
    "interest_rate": "interest_rate",
    "maturity_date": "maturity_date",
}
_CASHFLOW_FIELDS = {
    "business_key": "cashflow_id",
    "due_date": "due_date",
    "amount": "amount",
    "currency": "currency",
    "cashflow_type": "cashflow_type",
    "origin": "origin",
}


def _cash_flow(event: CashflowEvent, pins: SchemaPins, clear: frozenset[str]) -> CashFlowOut:
    shown = _shown(
        {
            "business_key": event.business_key,
            "due_date": event.due_date,
            "amount": event.amount,
            "currency": event.currency,
            "cashflow_type": event.cashflow_type,
            "origin": event.origin,
        },
        _CASHFLOW_FIELDS,
        pins,
        event.schema_version_id,
        clear,
    )
    business_key = shown.pop("business_key")
    assert business_key is not None
    return CashFlowOut(
        cashflow_event_id=event.id,
        business_key=business_key,
        import_id=event.import_job_id,
        canonical_record_id=event.canonical_record_id,
        source_ordinal=event.source_ordinal,
        schema_version_id=event.schema_version_id,
        **shown,
    )


def _version(
    v: InstrumentVersion,
    attributes: list[AttributeView],
    cash_flows: list[CashFlowOut],
    pins: SchemaPins,
    clear: frozenset[str],
) -> InstrumentVersionOut:
    shown = _shown(
        {
            "instrument_type": v.instrument_type_code,
            "currency": v.currency,
            "notional": v.notional,
            "interest_rate": v.interest_rate,
            "maturity_date": v.maturity_date,
        },
        _VERSION_FIELDS,
        pins,
        v.schema_version_id,
        clear,
    )
    classes = pins.classes(v.schema_version_id)
    rate = pins.fields.get(v.schema_version_id, {}).get("interest_rate")
    attribute_out = []
    for attribute in attributes:
        # Unknown to the pinned version: masked as unclassified (fail closed).
        code = classes.get(attribute.code, UNCLASSIFIED)
        attribute_out.append(
            AttributeOut(
                code=attribute.code,
                data_type=FieldType(attribute.data_type),
                classification=code,
                value=mask_mapping({"v": attribute.value_text}, {"v": code}, clear)["v"],
            )
        )
    return InstrumentVersionOut(
        version_id=v.id,
        version=v.version,
        import_id=v.import_job_id,
        projection_id=v.domain_projection_id,
        canonical_record_id=v.canonical_record_id,
        source_record_id=v.source_record_id,
        source_ordinal=v.source_ordinal,
        schema_version_id=v.schema_version_id,
        schema_version=pins.versions.get(v.schema_version_id),
        # A small payload's hash can be brute-forced to its masked values: shown only to a
        # caller cleared for every field the payload may hold.
        payload_sha256=(
            v.payload_sha256 if pins.most_sensitive(v.schema_version_id) in clear else MASKED_HASH
        ),
        projected_at=v.created_at,
        instrument_type_known=v.instrument_type_id is not None,
        interest_rate_unit=rate.unit if rate else None,
        attributes=attribute_out,
        cash_flows=cash_flows,
        **shown,
    )


@router.get(
    "/instruments/{instrument_id}", response_model=InstrumentDetail, operation_id="getInstrument"
)
async def get_instrument(
    instrument_id: uuid.UUID, principal: DomainReader, session: SessionDep
) -> InstrumentDetail:
    """One instrument: every version (oldest first) with its attributes, import, source row
    ordinal, canonical record and the cash flows naming its business key."""
    view = await DomainService(session, principal).instrument(instrument_id)
    clear = principal.clear_classifications
    row = view.row
    cash_flows = [_cash_flow(e, view.pins, clear) for e in view.cash_flows]
    latest = row.versions[-1]
    business_key = _shown(
        {"business_key": row.instrument.business_key},
        {"business_key": "instrument_id"},
        view.pins,
        latest.schema_version_id,
        clear,
    )["business_key"]
    assert business_key is not None
    return InstrumentDetail(
        instrument_id=row.instrument.id,
        business_key=business_key,
        source_id=row.instrument.source_system_id,
        source_code=row.source_code,
        created_at=row.instrument.created_at,
        versions=[
            _version(v, view.attributes.get(v.id, []), cash_flows, view.pins, clear)
            for v in row.versions
        ],
    )
