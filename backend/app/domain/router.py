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

- Feature 04 (Phase 19, D-37, D-43), all `domain:read`: GET /credit-vocabularies,
  GET /counterparties, GET /counterparties/{counterparty_id}, GET /credit-exposures and
  GET /credit-exposures/{exposure_id}. Confidential credit fields (D-43 §1) are masked on read.
- Phase 20 (D-39, D-40): POST /counterparties/{counterparty_id}/ratings (`rating:propose`),
  POST /obligor-ratings/{rating_id}/approval (`rating:approve`), GET /obligor-ratings and
  GET /obligor-ratings/{rating_id} (`domain:read`), and the rating history in GET
  /counterparties/{counterparty_id}. RATING_CHANGE_ENABLED=false answers 409
  rating_change_disabled to both writes (audited); the rationale and decision comment are
  confidential (D-43 §2).

Tenant scope comes from the principal (D-04): another tenant's id is the same 404 as a
random one.
"""

import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.classification import UNCLASSIFIED, MaskedValue, mask_mapping
from app.core.idempotency import mark_replayed
from app.core.principal import Principal
from app.db.session import get_session
from app.domain.constants import (
    RATING_CLASSIFICATIONS,
    CreditVocabularyKind,
    DomainSchemaCode,
    ObligorRatingStatus,
)
from app.domain.models import (
    CashflowEvent,
    CounterpartyVersion,
    CreditExposureVersion,
    InstrumentVersion,
)
from app.domain.repository import CounterpartyFilter, ExposureFilter, InstrumentFilter
from app.domain.schemas import (
    AttributeOut,
    CashFlowOut,
    CounterpartyDetail,
    CounterpartyPage,
    CounterpartySummary,
    CounterpartyVersionOut,
    CreditExposureDetail,
    CreditExposurePage,
    CreditExposureSummary,
    CreditExposureVersionOut,
    CreditVocabularyEntryOut,
    CreditVocabularyList,
    InstrumentDetail,
    InstrumentPage,
    InstrumentSummary,
    InstrumentTypeOut,
    InstrumentVersionOut,
    ObligorRatingCreate,
    ObligorRatingDecision,
    ObligorRatingOut,
    ObligorRatingPage,
    ProjectionOut,
    ProjectionSummary,
    Value,
)
from app.domain.service import (
    AttributeView,
    CounterpartyRow,
    DomainService,
    ExposureRow,
    InstrumentRow,
    ProjectionView,
    RatingRow,
    SchemaPins,
)
from app.governance.constants import FieldType, Permission
from app.ingestion.constants import ImportStatus

SessionDep = Annotated[AsyncSession, Depends(get_session)]
DomainReader = Annotated[Principal, Depends(require_permission(Permission.DOMAIN_READ))]
DomainProjector = Annotated[Principal, Depends(require_permission(Permission.DOMAIN_PROJECT))]
RatingProposer = Annotated[Principal, Depends(require_permission(Permission.RATING_PROPOSE))]
RatingApprover = Annotated[Principal, Depends(require_permission(Permission.RATING_APPROVE))]

router = APIRouter(tags=["domain"])
MASKED_HASH = "masked"  # a string, so the payload_sha256 field keeps its type


Shown = Decimal | date | str | int | bool | None


def _text(value: Shown) -> str | int | bool | None:
    """Exact decimal text (never exponent form) or an ISO date; integers and booleans (days
    past due, the default flag) as they are."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _shown(
    values: Mapping[str, Shown],
    fields: Mapping[str, str],
    pins: SchemaPins,
    schema_version_id: uuid.UUID,
    clear: frozenset[str],
) -> dict[str, Any]:
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
    settings = request.app.state.settings
    view, created = await DomainService(session, principal).project(
        import_id, settings.DOMAIN_PROJECTION_ENABLED, settings.CREDIT_PROJECTION_ENABLED
    )
    if not created:
        mark_replayed(response)
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
            v.payload_sha256
            if pins.payload_classification(v.schema_version_id) in clear
            else MASKED_HASH
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


# --- Feature 04 credit reads (BE-19.5; contract first, implemented in Phase 19) -------------


_VocabularyCodes = Mapping[CreditVocabularyKind, frozenset[str]]


def _hash(
    payload_sha256: str, pins: SchemaPins, version_id: uuid.UUID, clear: frozenset[str]
) -> str:
    """A small payload's hash can be brute-forced to its masked values: shown only to a
    caller cleared for every field the payload may hold."""
    return payload_sha256 if pins.payload_classification(version_id) in clear else MASKED_HASH


def _known(
    shown: Mapping[str, Any], key: str, kind: CreditVocabularyKind, vocabulary: _VocabularyCodes
) -> bool:
    """The value, as shown to the caller, is in its vocabulary. A masked or absent value is
    not known (nothing about a masked value is revealed)."""
    value = shown.get(key)
    return isinstance(value, str) and value in vocabulary.get(kind, frozenset())


def _business_key(
    key: str, field: str, pins: SchemaPins, version_id: uuid.UUID, clear: frozenset[str]
) -> Value:
    shown = _shown({"business_key": key}, {"business_key": field}, pins, version_id, clear)
    value = shown["business_key"]
    assert value is not None
    return value  # type: ignore[no-any-return]


@router.get(
    "/credit-vocabularies",
    response_model=CreditVocabularyList,
    operation_id="listCreditVocabularies",
)
async def list_credit_vocabularies(
    principal: DomainReader, session: SessionDep
) -> CreditVocabularyList:
    """The seeded PROVISIONAL credit vocabularies of the caller's tenant (D-37 §5): rating
    grades (with rank and default flag), credit segments, facility types and collateral
    types."""
    entries = await DomainService(session, principal).credit_vocabulary()
    return CreditVocabularyList(
        items=[
            CreditVocabularyEntryOut(
                vocabulary=CreditVocabularyKind(e.vocabulary),
                code=e.code,
                name=e.name,
                rank=e.rank,
                is_default_grade=e.is_default_grade,
                provisional=e.provisional,
            )
            for e in entries
        ]
    )


# Output key -> the schema field it is projected from (masking, D-43 §1).
_COUNTERPARTY_FIELDS = {
    "counterparty_name": "counterparty_name",
    "segment": "segment",
    "country": "country",
}


def _counterparty_values(v: CounterpartyVersion) -> dict[str, Shown]:
    return {"counterparty_name": v.counterparty_name, "segment": v.segment, "country": v.country}


def _counterparty_summary(
    row: CounterpartyRow, pins: SchemaPins, clear: frozenset[str], vocabulary: _VocabularyCodes
) -> CounterpartySummary:
    latest = row.versions[-1]
    shown = _shown(
        _counterparty_values(latest), _COUNTERPARTY_FIELDS, pins, latest.schema_version_id, clear
    )
    return CounterpartySummary(
        counterparty_id=row.counterparty.id,
        business_key=_business_key(
            row.counterparty.business_key,
            "counterparty_id",
            pins,
            latest.schema_version_id,
            clear,
        ),
        source_id=row.counterparty.source_system_id,
        source_code=row.source_code,
        version_count=len(row.versions),
        latest_version=latest.version,
        latest_import_id=latest.import_job_id,
        segment_known=_known(shown, "segment", CreditVocabularyKind.CREDIT_SEGMENT, vocabulary),
        created_at=row.counterparty.created_at,
        **shown,
    )


@router.get("/counterparties", response_model=CounterpartyPage, operation_id="listCounterparties")
async def list_counterparties(
    principal: DomainReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    segment: Annotated[
        str | None, Query(max_length=100, description="Counterparties with a version in it.")
    ] = None,
    country: Annotated[
        str | None,
        Query(pattern=r"^[A-Z]{2}$", description="Counterparties with a version in it."),
    ] = None,
    source_id: uuid.UUID | None = None,
    import_id: Annotated[
        uuid.UUID | None, Query(description="Counterparties with a version from this import.")
    ] = None,
) -> CounterpartyPage:
    """The counterparties of the caller's tenant, by business key. A filter matches a
    counterparty when any of its versions matches."""
    page = await DomainService(session, principal).counterparties(
        CounterpartyFilter(segment, country, source_id, import_id), limit, offset
    )
    clear = principal.clear_classifications
    return CounterpartyPage(
        items=[
            _counterparty_summary(r, page.pins, clear, page.vocabulary) for r in page.counterparties
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


_EXPOSURE_FIELDS = {
    "counterparty_key": "counterparty_id",
    "instrument_key": "instrument_id",
    "as_of_date": "as_of_date",
    "currency": "currency",
    "facility_type": "facility_type",
    "drawn_amount": "drawn_amount",
    "undrawn_amount": "undrawn_amount",
    "days_past_due": "days_past_due",
    "default_flag": "default_flag",
    "origination_date": "origination_date",
    "origination_grade": "origination_grade",
    "maturity_date": "maturity_date",
    "effective_interest_rate": "effective_interest_rate",
    "collateral_type": "collateral_type",
    "collateral_value": "collateral_value",
}
_EXPOSURE_SUMMARY = (
    "counterparty_key",
    "as_of_date",
    "currency",
    "facility_type",
    "drawn_amount",
    "days_past_due",
    "default_flag",
)


def _exposure_values(v: CreditExposureVersion) -> dict[str, Shown]:
    return {key: getattr(v, key) for key in _EXPOSURE_FIELDS}


def _exposure_version(
    v: CreditExposureVersion,
    business_key: str,
    pins: SchemaPins,
    clear: frozenset[str],
    vocabulary: _VocabularyCodes,
) -> CreditExposureVersionOut:
    shown = _shown(_exposure_values(v), _EXPOSURE_FIELDS, pins, v.schema_version_id, clear)
    rate = pins.fields.get(v.schema_version_id, {}).get("effective_interest_rate")
    return CreditExposureVersionOut(
        version_id=v.id,
        version=v.version,
        credit_exposure_id=v.credit_exposure_id,
        business_key=_business_key(business_key, "exposure_id", pins, v.schema_version_id, clear),
        import_id=v.import_job_id,
        projection_id=v.domain_projection_id,
        canonical_record_id=v.canonical_record_id,
        source_record_id=v.source_record_id,
        source_ordinal=v.source_ordinal,
        schema_version_id=v.schema_version_id,
        schema_version=pins.versions.get(v.schema_version_id),
        payload_sha256=_hash(v.payload_sha256, pins, v.schema_version_id, clear),
        projected_at=v.created_at,
        facility_type_known=_known(
            shown, "facility_type", CreditVocabularyKind.FACILITY_TYPE, vocabulary
        ),
        origination_grade_known=_known(
            shown, "origination_grade", CreditVocabularyKind.RATING_GRADE, vocabulary
        ),
        collateral_type_known=_known(
            shown, "collateral_type", CreditVocabularyKind.COLLATERAL_TYPE, vocabulary
        ),
        effective_interest_rate_unit=rate.unit if rate else None,
        **shown,
    )


def _counterparty_version(
    v: CounterpartyVersion, pins: SchemaPins, clear: frozenset[str], vocabulary: _VocabularyCodes
) -> CounterpartyVersionOut:
    shown = _shown(_counterparty_values(v), _COUNTERPARTY_FIELDS, pins, v.schema_version_id, clear)
    return CounterpartyVersionOut(
        version_id=v.id,
        version=v.version,
        import_id=v.import_job_id,
        projection_id=v.domain_projection_id,
        canonical_record_id=v.canonical_record_id,
        source_record_id=v.source_record_id,
        source_ordinal=v.source_ordinal,
        schema_version_id=v.schema_version_id,
        schema_version=pins.versions.get(v.schema_version_id),
        payload_sha256=_hash(v.payload_sha256, pins, v.schema_version_id, clear),
        projected_at=v.created_at,
        segment_known=_known(shown, "segment", CreditVocabularyKind.CREDIT_SEGMENT, vocabulary),
        **shown,
    )


@router.get(
    "/counterparties/{counterparty_id}",
    response_model=CounterpartyDetail,
    operation_id="getCounterparty",
)
async def get_counterparty(
    counterparty_id: uuid.UUID, principal: DomainReader, session: SessionDep
) -> CounterpartyDetail:
    """One counterparty: every version (oldest first) with its import, source row ordinal and
    canonical record, plus the credit-exposure versions naming its business key in the
    tenant. 404 `not_found` alike for an absent and another tenant's id (D-04)."""
    view = await DomainService(session, principal).counterparty(counterparty_id)
    clear = principal.clear_classifications
    row = view.row
    latest = row.versions[-1]
    return CounterpartyDetail(
        counterparty_id=row.counterparty.id,
        business_key=_business_key(
            row.counterparty.business_key,
            "counterparty_id",
            view.pins,
            latest.schema_version_id,
            clear,
        ),
        source_id=row.counterparty.source_system_id,
        source_code=row.source_code,
        created_at=row.counterparty.created_at,
        versions=[
            _counterparty_version(v, view.pins, clear, view.vocabulary) for v in row.versions
        ],
        exposure_versions=[
            _exposure_version(v, key, view.pins, clear, view.vocabulary)
            for v, key in view.exposure_versions
        ],
        ratings=[_rating(r, view.pins, clear) for r in view.ratings],
    )


def _exposure_summary(
    row: ExposureRow, pins: SchemaPins, clear: frozenset[str]
) -> CreditExposureSummary:
    latest = row.versions[-1]
    values = _exposure_values(latest)
    shown = _shown(
        {k: values[k] for k in _EXPOSURE_SUMMARY},
        {k: _EXPOSURE_FIELDS[k] for k in _EXPOSURE_SUMMARY},
        pins,
        latest.schema_version_id,
        clear,
    )
    return CreditExposureSummary(
        credit_exposure_id=row.exposure.id,
        business_key=_business_key(
            row.exposure.business_key, "exposure_id", pins, latest.schema_version_id, clear
        ),
        source_id=row.exposure.source_system_id,
        source_code=row.source_code,
        version_count=len(row.versions),
        latest_version=latest.version,
        latest_import_id=latest.import_job_id,
        created_at=row.exposure.created_at,
        **shown,
    )


@router.get(
    "/credit-exposures", response_model=CreditExposurePage, operation_id="listCreditExposures"
)
async def list_credit_exposures(
    principal: DomainReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    currency: Annotated[
        str | None,
        Query(pattern=r"^[A-Z]{3}$", description="Exposures with a version in this currency."),
    ] = None,
    facility_type: Annotated[
        str | None, Query(max_length=100, description="Exposures with a version of this type.")
    ] = None,
    counterparty_key: Annotated[
        str | None,
        Query(max_length=200, description="Exposures with a version naming this counterparty."),
    ] = None,
    source_id: uuid.UUID | None = None,
    import_id: Annotated[
        uuid.UUID | None, Query(description="Exposures with a version from this import.")
    ] = None,
) -> CreditExposurePage:
    """The credit exposures of the caller's tenant, by business key. A filter matches an
    exposure when any of its versions matches."""
    page = await DomainService(session, principal).exposures(
        ExposureFilter(currency, facility_type, counterparty_key, source_id, import_id),
        limit,
        offset,
    )
    clear = principal.clear_classifications
    return CreditExposurePage(
        items=[_exposure_summary(r, page.pins, clear) for r in page.exposures],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/credit-exposures/{exposure_id}",
    response_model=CreditExposureDetail,
    operation_id="getCreditExposure",
)
async def get_credit_exposure(
    exposure_id: uuid.UUID, principal: DomainReader, session: SessionDep
) -> CreditExposureDetail:
    """One credit exposure: every version (oldest first) with its import id, source row
    ordinal and canonical record id. 404 `not_found` alike for an absent and another
    tenant's id (D-04)."""
    view = await DomainService(session, principal).exposure(exposure_id)
    clear = principal.clear_classifications
    row = view.row
    latest = row.versions[-1]
    return CreditExposureDetail(
        credit_exposure_id=row.exposure.id,
        business_key=_business_key(
            row.exposure.business_key, "exposure_id", view.pins, latest.schema_version_id, clear
        ),
        source_id=row.exposure.source_system_id,
        source_code=row.source_code,
        created_at=row.exposure.created_at,
        versions=[
            _exposure_version(v, row.exposure.business_key, view.pins, clear, view.vocabulary)
            for v in row.versions
        ],
    )


# --- Phase 20: the obligor rating workflow (BE-20.2, BE-20.3, D-39) -------------------------


def _rating(row: RatingRow, pins: SchemaPins, clear: frozenset[str]) -> ObligorRatingOut:
    """One rating version, masked (D-43 §2): the grade is internal, the rationale and the
    decision comment confidential; the counterparty key is masked as the counterparty's
    business key (its latest version's schema field; unknown: unclassified, fail closed)."""
    r = row.rating
    shown = mask_mapping(
        {"grade": r.grade, "rationale": r.rationale, "decision_comment": r.decision_comment},
        RATING_CLASSIFICATIONS,
        clear,
    )
    key: Value = (
        _business_key(
            row.counterparty_key, "counterparty_id", pins, row.key_schema_version_id, clear
        )
        if row.key_schema_version_id is not None
        else MaskedValue(classification=UNCLASSIFIED)
    )
    return ObligorRatingOut(
        rating_id=r.id,
        counterparty_id=r.counterparty_id,
        counterparty_key=key,
        grade=shown["grade"],
        grade_rank=row.grade_rank if isinstance(shown["grade"], str) else None,
        effective_from=r.effective_from,
        rationale=shown["rationale"],
        status=ObligorRatingStatus(r.status),
        proposed_by=r.proposed_by,
        proposer_user_id=r.proposer_user_id,
        proposed_at=r.proposed_at,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        decision_comment=shown["decision_comment"],
        superseded_at=r.superseded_at,
        superseded_by_rating_id=r.superseded_by_rating_id,
    )


@router.post(
    "/counterparties/{counterparty_id}/ratings",
    response_model=ObligorRatingOut,
    status_code=201,
    operation_id="proposeObligorRating",
)
async def propose_obligor_rating(
    request: Request,
    counterparty_id: uuid.UUID,
    body: ObligorRatingCreate,
    principal: RatingProposer,
    session: SessionDep,
) -> ObligorRatingOut:
    """Propose a rating for one counterparty (D-39 §1, §3): an immutable version written
    IN_REVIEW, audited `obligor_rating.propose`. Refusals, each audited DENIED: 404
    `not_found` alike for an absent and another tenant's counterparty (D-04), 409
    `rating_change_disabled` while RATING_CHANGE_ENABLED is off, 422 `invalid_grade` (not a
    `rating_grade` code of the tenant)."""
    view = await DomainService(session, principal).propose_rating(
        counterparty_id,
        body.grade,
        body.effective_from,
        body.rationale,
        request.app.state.settings.RATING_CHANGE_ENABLED,
    )
    return _rating(view.row, view.pins, principal.clear_classifications)


@router.post(
    "/obligor-ratings/{rating_id}/approval",
    response_model=ObligorRatingOut,
    operation_id="decideObligorRating",
)
async def decide_obligor_rating(
    request: Request,
    rating_id: uuid.UUID,
    body: ObligorRatingDecision,
    principal: RatingApprover,
    session: SessionDep,
) -> ObligorRatingOut:
    """Maker-checker (D-39 §3). APPROVE makes an IN_REVIEW version ACTIVE and any ACTIVE
    version for the same counterparty and `effective_from` SUPERSEDED, in one transaction;
    REJECT ends it REJECTED. Audited `obligor_rating.approve` / `.reject` / `.supersede`.
    Refusals, each audited DENIED: 404 `not_found` (absent or another tenant's), 409
    `rating_change_disabled`, 409 `invalid_status` (not IN_REVIEW), 409 `self_approval` (the
    proposer), 409 `stale_version` (another version for the same counterparty and date was
    activated after this one was proposed: propose again)."""
    view = await DomainService(session, principal).decide_rating(
        rating_id, body.decision, body.comment, request.app.state.settings.RATING_CHANGE_ENABLED
    )
    return _rating(view.row, view.pins, principal.clear_classifications)


@router.get("/obligor-ratings", response_model=ObligorRatingPage, operation_id="listObligorRatings")
async def list_obligor_ratings(
    principal: DomainReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[
        ObligorRatingStatus | None,
        Query(description="Only versions in this status (IN_REVIEW for the review queue)."),
    ] = None,
    counterparty_id: Annotated[
        uuid.UUID | None, Query(description="Only this counterparty's versions.")
    ] = None,
) -> ObligorRatingPage:
    """The obligor rating versions of the caller's tenant (every status unless filtered),
    newest proposal first."""
    page = await DomainService(session, principal).ratings(status, counterparty_id, limit, offset)
    clear = principal.clear_classifications
    return ObligorRatingPage(
        items=[_rating(r, page.pins, clear) for r in page.rows],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/obligor-ratings/{rating_id}",
    response_model=ObligorRatingOut,
    operation_id="getObligorRating",
)
async def get_obligor_rating(
    rating_id: uuid.UUID, principal: DomainReader, session: SessionDep
) -> ObligorRatingOut:
    """One rating version. 404 `not_found` alike for an absent and another tenant's id
    (D-04)."""
    view = await DomainService(session, principal).rating(rating_id)
    return _rating(view.row, view.pins, principal.clear_classifications)
