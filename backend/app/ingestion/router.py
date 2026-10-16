"""ingestion.router

Source registration (BE-7.4), import (BE-8.4) and import profile (BE-9.3) endpoints. Every
route declares its permission (D-04, the Feature 02 endpoint table); tenant scope comes from
the principal.
Writes are refused with 409 `ingestion_disabled` (audited) while INGESTION_ENABLED is off
(BE-7.3).

POST /imports reads its multipart body itself, after authorization, so that the D-16 byte
limit is enforced while streaming: a declared or received size beyond the limit is refused
with 413 `payload_too_large` before the rest of the body is read.
"""

import json
import unicodedata
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from python_multipart.exceptions import MultipartParseError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.core.auth import get_principal, require_permission
from app.core.classification import mask_mapping
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.governance.service import record_denial
from app.ingestion.adapters import Limits
from app.ingestion.constants import ImportStatus, UploadRefusal
from app.ingestion.models import ImportJob, SourceContract, SourceSystem
from app.ingestion.schemas import (
    ArtifactOut,
    BatchOut,
    ColumnProfile,
    ContractColumn,
    ContractIn,
    ContractOut,
    ImportDetail,
    ImportForm,
    ImportPage,
    ImportProfile,
    ImportSummary,
    ProfileOut,
    ProfileSummary,
    SourceCreate,
    SourceDetail,
    SourcePage,
    SourceSummary,
    TopValue,
)
from app.ingestion.service import (
    ContractSpec,
    ImportLabels,
    ImportPolicy,
    ImportService,
    ProfileSpec,
    ProfileView,
    SourceService,
    UploadSpec,
    current_contract,
    profile_of,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SourceReader = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
SourceManager = Annotated[Principal, Depends(require_permission(Permission.SOURCE_MANAGE))]
ImportReader = Annotated[Principal, Depends(require_permission(Permission.IMPORT_READ))]
ImportWriter = Annotated[Principal, Depends(require_permission(Permission.IMPORT_WRITE))]

router = APIRouter(tags=["ingestion"])


async def ingestion_enabled(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDep,
) -> None:
    """WS2 is read-only while INGESTION_ENABLED is false. The refusal is audited like any
    other denied request (BE-3.2). Declared after the permission, so a caller without it
    still gets 403."""
    if not request.app.state.settings.INGESTION_ENABLED:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", request.url.path)
        await record_denial(
            session,
            principal,
            f"{request.method} {endpoint}",
            "endpoint",
            endpoint,
            "ingestion_disabled",
        )
        raise ApiError(
            409, "ingestion_disabled", "Ingestion changes are switched off in this environment."
        )


IngestionEnabled = Annotated[None, Depends(ingestion_enabled)]


def _contract_spec(body: ContractIn) -> ContractSpec:
    return ContractSpec(
        schema_id=body.schema_id,
        expected_columns=[c.model_dump() for c in body.expected_columns],
        business_key_columns=list(body.business_key_columns),
        control_total_columns=list(body.control_total_columns),
        change_summary=body.change_summary,
    )


def _contract_out(c: SourceContract, codes: dict[uuid.UUID, str]) -> ContractOut:
    return ContractOut(
        id=c.id,
        version=c.version,
        status=c.status,
        schema_id=c.schema_id,
        schema_code=codes.get(c.schema_id, ""),
        expected_columns=[ContractColumn.model_validate(col) for col in c.expected_columns],
        business_key_columns=c.business_key_columns,
        control_total_columns=c.control_total_columns,
        content_sha256=c.content_sha256,
        supersedes_id=c.supersedes_id,
        change_summary=c.change_summary,
        created_by=c.created_by,
        created_at=c.created_at,
    )


def _summary_fields(s: SourceSystem, codes: dict[uuid.UUID, str]) -> SourceSummary:
    p = profile_of(s)
    current = current_contract(p.contracts)
    return SourceSummary(
        id=s.id,
        code=s.code,
        name=s.name,
        description=s.description,
        kind=s.kind,
        provisional=p.provisional,
        profile=ProfileOut.model_validate(p, from_attributes=True),
        current_contract=_contract_out(current, codes) if current else None,
        contract_count=len(p.contracts),
        created_by=s.created_by,
        created_at=s.created_at,
    )


def _detail(s: SourceSystem, codes: dict[uuid.UUID, str]) -> SourceDetail:
    return SourceDetail(
        **_summary_fields(s, codes).model_dump(),
        contracts=[_contract_out(c, codes) for c in profile_of(s).contracts],
    )


@router.get("/sources", response_model=SourcePage, operation_id="listSources")
async def list_sources(
    principal: SourceReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SourcePage:
    service = SourceService(session, principal)
    sources, total = await service.page(limit, offset)
    codes = await service.schema_codes()
    return SourcePage(
        items=[_summary_fields(s, codes) for s in sources], total=total, limit=limit, offset=offset
    )


@router.post("/sources", response_model=SourceDetail, status_code=201, operation_id="createSource")
async def create_source(
    body: SourceCreate, principal: SourceManager, session: SessionDep, _: IngestionEnabled
) -> SourceDetail:
    service = SourceService(session, principal)
    p = body.profile
    source = await service.create(
        body.code,
        body.name,
        body.description,
        body.kind,
        ProfileSpec(
            format=p.format,
            encoding=p.encoding,
            delimiter=p.delimiter,
            sheet_name=p.sheet_name,
            cadence=p.cadence,
            owner=p.owner,
            default_classification=p.default_classification,
        ),
        _contract_spec(body.contract),
    )
    return _detail(source, await service.schema_codes())


@router.get("/sources/{source_id}", response_model=SourceDetail, operation_id="getSource")
async def get_source(
    source_id: uuid.UUID, principal: SourceReader, session: SessionDep
) -> SourceDetail:
    service = SourceService(session, principal)
    return _detail(await service.get(source_id), await service.schema_codes())


@router.post(
    "/sources/{source_id}/contracts",
    response_model=ContractOut,
    status_code=201,
    operation_id="createSourceContract",
)
async def create_source_contract(
    source_id: uuid.UUID,
    body: ContractIn,
    principal: SourceManager,
    session: SessionDep,
    _: IngestionEnabled,
) -> ContractOut:
    service = SourceService(session, principal)
    contract = await service.add_contract(source_id, _contract_spec(body))
    return _contract_out(contract, await service.schema_codes())


# --- Phase 8: imports (BE-8.4) ------------------------------------------------------------------

# Multipart framing (boundaries, part headers) and the small form fields, on top of the
# file's own D-16 byte limit. Also the cap on any non-file field.
FORM_OVERHEAD = 64 * 1024
FILENAME_MAX = 255

UPLOAD_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "title": "ImportUpload",
                "type": "object",
                "required": ["source_id", "file"],
                "properties": {
                    "source_id": {"type": "string", "format": "uuid"},
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": "The file, in the source profile's format (CSV, XLSX "
                        "or JSON; detected from content). At most IMPORT_MAX_BYTES (10 MiB). "
                        f"Its name is kept, at most {FILENAME_MAX} characters.",
                    },
                    "synthetic_attested": {
                        "type": "boolean",
                        "default": False,
                        "description": "Required true while the environment's X-Data-Class "
                        "is synthetic (D-16 §5): you confirm the file holds no client data.",
                    },
                    "expected_row_count": {"type": "integer", "minimum": 0},
                    "control_totals": {
                        "type": "string",
                        "description": "JSON object of the contract's control-total columns "
                        'to exact decimal text, e.g. {"notional": "1234.50"}.',
                    },
                },
            }
        }
    },
}


class _TooLarge(MultiPartException):
    def __init__(self) -> None:
        super().__init__(UploadRefusal.PAYLOAD_TOO_LARGE)


async def _bounded(stream: AsyncIterator[bytes], limit: int) -> AsyncGenerator[bytes, None]:
    """Pass the body through, refusing as soon as more than `limit` bytes arrived."""
    received = 0
    async for chunk in stream:
        received += len(chunk)
        if received > limit:
            raise _TooLarge()
        yield chunk


def _malformed(message: str, *loc: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", *loc), "msg": message, "type": "value_error"}])


def _filename(name: str | None) -> str | None:
    """The file's base name, bounded and free of control characters."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base:
        return None
    if len(base) > FILENAME_MAX:
        raise _malformed(f"The file name is longer than {FILENAME_MAX} characters.", "file")
    if any(unicodedata.category(c) == "Cc" for c in base):
        raise _malformed("The file name contains control characters.", "file")
    return base


def _form_fields(form: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        k: v for k, v in form.multi_items() if isinstance(v, str) and k in ImportForm.model_fields
    }
    if fields.get("control_totals") in ("", None):
        fields.pop("control_totals", None)
    elif "control_totals" in fields:
        try:
            fields["control_totals"] = json.loads(fields["control_totals"])
        except ValueError:
            raise _malformed("control_totals must be a JSON object.", "control_totals") from None
    if fields.get("expected_row_count") == "":
        fields.pop("expected_row_count")
    return fields


async def read_upload(request: Request, limits: Limits) -> tuple[ImportForm, bytes, str | None]:
    """The validated form, the file's bytes and its name. Raises _TooLarge (413) while
    streaming, or RequestValidationError (422) for a malformed body."""
    body_limit = limits.max_bytes + FORM_OVERHEAD
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise _malformed("Expected a multipart/form-data body.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > body_limit:
        raise _TooLarge()  # refused before any of the body is read
    parser = MultiPartParser(
        request.headers,
        _bounded(request.stream(), body_limit),
        max_files=1,
        max_fields=10,
        max_part_size=FORM_OVERHEAD,
    )
    try:
        form = await parser.parse()
    except _TooLarge:
        raise
    except (MultiPartException, MultipartParseError):
        raise _malformed("The multipart body is malformed.") from None
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _malformed("A file is required.", "file")
        filename = _filename(upload.filename)
        try:
            fields = ImportForm.model_validate(_form_fields(form))
        except ValidationError as exc:
            raise RequestValidationError(
                [
                    {"loc": ("body", *e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            ) from None
        return fields, await upload.read(), filename
    finally:
        await form.close()


def limits_from(settings: Settings) -> Limits:
    return Limits(
        max_bytes=settings.IMPORT_MAX_BYTES,
        max_rows=settings.IMPORT_MAX_ROWS,
        max_columns=settings.IMPORT_MAX_COLUMNS,
        max_cell_chars=settings.IMPORT_MAX_CELL_CHARS,
        max_xlsx_uncompressed_bytes=settings.IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES,
        max_xlsx_ratio=settings.IMPORT_MAX_XLSX_RATIO,
    )


def _import_fields(job: ImportJob, labels: ImportLabels, principal: Principal) -> ImportSummary:
    artifact = job.artifact
    schema = labels.schema_versions.get(job.schema_version_id)
    totals = job.control_totals
    return ImportSummary(
        id=job.id,
        status=ImportStatus(job.status),
        source_id=job.source_system_id,
        source_code=labels.source_codes.get(job.source_system_id, ""),
        format=job.format,
        row_count=job.row_count,
        expected_row_count=job.expected_row_count,
        control_totals=(
            mask_mapping(
                totals,
                dict.fromkeys(totals, artifact.classification),
                principal.clear_classifications,
            )
            if totals is not None
            else None
        ),
        data_class=job.data_class,
        synthetic_attested=job.synthetic_attested,
        synthetic_marker=job.synthetic_marker,
        source_contract_id=job.source_contract_id,
        contract_version=labels.contract_versions.get(job.source_contract_id),
        schema_version_id=job.schema_version_id,
        schema_code=schema[0] if schema else None,
        schema_version=schema[1] if schema else None,
        mapping_version_id=job.mapping_version_id,
        artifact=ArtifactOut.model_validate(artifact, from_attributes=True),
        retry_of_import_id=job.retry_of_import_id,
        correlation_id=job.correlation_id,
        created_by=job.created_by,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _import_detail(job: ImportJob, labels: ImportLabels, principal: Principal) -> ImportDetail:
    return ImportDetail(
        **_import_fields(job, labels, principal).model_dump(),
        batches=[
            BatchOut(
                batch_number=b.batch_number,
                sheet_name=b.sheet_name,
                row_count=b.row_count,
                column_count=len(b.columns),
            )
            for b in job.batches
        ],
    )


@router.post(
    "/imports",
    response_model=ImportDetail,
    status_code=201,
    operation_id="createImport",
    openapi_extra={"requestBody": UPLOAD_BODY},
    responses={
        200: {
            "model": ImportDetail,
            "description": "Idempotent replay: this key was used for the same upload; the "
            "original import is returned (header Idempotent-Replayed: true).",
        }
    },
)
async def create_import(
    request: Request,
    response: Response,
    principal: ImportWriter,
    session: SessionDep,
    _: IngestionEnabled,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r"^[!-~]+$",
            description="Unique per upload (per user). A retry with the same key and the "
            "same upload returns the original import; with a different upload, 409 "
            "idempotency_conflict (D-21).",
        ),
    ],
) -> ImportDetail:
    """Upload a file for a source (multipart). Processed synchronously (D-21): the raw
    artifact is stored, every row becomes a source record and the import is RECEIVED."""
    settings: Settings = request.app.state.settings
    limits = limits_from(settings)
    service = ImportService(session, principal)
    try:
        form, data, filename = await read_upload(request, limits)
    except _TooLarge:
        await service.refuse(
            ApiError(
                413,
                UploadRefusal.PAYLOAD_TOO_LARGE,
                f"The file is larger than {limits.max_bytes} bytes.",
                {"limit": limits.max_bytes},
            )
        )
    job, created = await service.create(
        UploadSpec(
            source_id=form.source_id,
            data=data,
            filename=filename,
            idempotency_key=idempotency_key,
            synthetic_attested=form.synthetic_attested,
            expected_row_count=form.expected_row_count,
            control_totals=form.control_totals,
        ),
        ImportPolicy(
            settings.data_class,
            limits,
            request.app.state.artifact_store,
            settings.IMPORT_PROFILING_ENABLED,
        ),
    )
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return _import_detail(job, await service.labels([job]), principal)


@router.get("/imports", response_model=ImportPage, operation_id="listImports")
async def list_imports(
    principal: ImportReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    source_id: uuid.UUID | None = None,
    status: ImportStatus | None = None,
) -> ImportPage:
    """Newest first; filter by source and status."""
    service = ImportService(session, principal)
    jobs, total = await service.page(limit, offset, source_id, status)
    labels = await service.labels(jobs)
    return ImportPage(
        items=[_import_fields(j, labels, principal) for j in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/imports/{import_id}", response_model=ImportDetail, operation_id="getImport")
async def get_import(
    import_id: uuid.UUID, principal: ImportReader, session: SessionDep
) -> ImportDetail:
    service = ImportService(session, principal)
    job = await service.get(import_id)
    return _import_detail(job, await service.labels([job]), principal)


# --- Phase 9: the import's profile (BE-9.3) -----------------------------------------------------


def _column_profile(
    column: dict[str, Any], view: ProfileView, clear: frozenset[str]
) -> ColumnProfile:
    """One stored column observation as the caller may see it: its values (min, max, top
    values) masked through mask_mapping by the column's classification (BE-3.5)."""
    name = column["name"]
    code = view.classification(name)
    bounds = mask_mapping(
        {"min": column["min"], "max": column["max"]}, {"min": code, "max": code}, clear
    )
    tops = [
        TopValue(
            value=mask_mapping({"value": top["value"]}, {"value": code}, clear)["value"],
            count=top["count"],
            truncated=top["truncated"],
        )
        for top in column["top_values"]
    ]
    types = column["candidate_types"]
    return ColumnProfile(
        name=name,
        position=column["position"],
        classification=code,
        value_count=column["value_count"],
        null_count=column["null_count"],
        null_rate=column["null_rate"],
        distinct_count=column["distinct_count"],
        distinct_capped=column["distinct_capped"],
        candidate_types=types,
        suggested_type=types[0] if types else None,
        min=bounds["min"],
        max=bounds["max"],
        max_length=column["max_length"],
        max_precision=column["max_precision"],
        max_scale=column["max_scale"],
        date_formats=column["date_formats"],
        currency_pattern_hits=column["currency_pattern_hits"],
        top_values=tops,
        top_values_exact=column["top_values_exact"],
        in_contract=view.in_contract(name),
        suggested_target_field=view.suggested_target(name),
    )


@router.get(
    "/imports/{import_id}/profile", response_model=ImportProfile, operation_id="getImportProfile"
)
async def get_import_profile(
    import_id: uuid.UUID, principal: ImportReader, session: SessionDep
) -> ImportProfile:
    """The import's field profile (DI-06): per-column observations and read-only
    suggestions. Values are masked by each column's classification; nothing is changed."""
    view = await ImportService(session, principal).profile_view(import_id)
    snap, job = view.snapshot, view.job
    clear = principal.clear_classifications
    return ImportProfile(
        import_id=job.id,
        import_status=ImportStatus(job.status),
        snapshot_id=snap.id,
        profiler_version=snap.profiler_version,
        content_sha256=snap.content_sha256,
        profiled_at=snap.created_at,
        row_count=snap.row_count,
        column_count=snap.column_count,
        source_contract_id=job.source_contract_id,
        contract_version=view.contract_version,
        schema_version_id=job.schema_version_id,
        schema_id=view.schema_id,
        schema_code=view.schema_code,
        schema_version=view.schema_version,
        summary=ProfileSummary.model_validate(snap.summary),
        columns=[_column_profile(c, view, clear) for c in snap.columns],
    )
