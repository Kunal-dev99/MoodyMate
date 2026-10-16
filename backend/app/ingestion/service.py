"""ingestion.service

Source registration (BE-7.4, DI-01): a source system, its profile and versioned source
contracts. Bounded file import (BE-8.4, DI-02): one upload becomes an import job with a
write-once raw artifact and one immutable `source_record` per row, synchronously (D-21).

Every write is audited in the same transaction and committed here (D-10). Every refused
write is audited too, as DENIED with its reason code (D-24 §4), through `record_denial`
after a rollback. Target schemas are checked through the governance
service, never its repository.
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.artifacts import ArtifactStore
from app.core.correlation import get_correlation_id
from app.core.errors import ApiError
from app.core.principal import Principal
from app.governance.constants import ClassificationCode
from app.governance.service import (
    SchemaService,
    audit_by,
    effective_version,
    known_classifications,
    record_denial,
)
from app.ingestion.adapters import (
    AdapterError,
    AdapterOptions,
    Limits,
    ParsedUpload,
    SourceRow,
    parse_upload,
)
from app.ingestion.constants import (
    CONTRACT_TRANSITIONS,
    IMPORT_TRANSITIONS,
    USABLE_SOURCE_KINDS,
    ContractStatus,
    CsvDelimiter,
    ImportStatus,
    RetentionState,
    SourceFormat,
    SourceKind,
    UploadRefusal,
)
from app.ingestion.models import (
    ImportArtifact,
    ImportBatch,
    ImportJob,
    SourceContract,
    SourceProfile,
    SourceSystem,
)
from app.ingestion.repository import ImportRepository, SourceRepository


@dataclass(frozen=True)
class ContractSpec:
    """The content of a contract version (what its hash covers), plus its summary."""

    schema_id: uuid.UUID
    expected_columns: list[dict[str, Any]]  # [{"name": str, "required": bool}]
    business_key_columns: list[str]
    control_total_columns: list[str]
    change_summary: str | None = None


@dataclass(frozen=True)
class ProfileSpec:
    format: SourceFormat
    encoding: str
    delimiter: str | None
    sheet_name: str | None
    cadence: str | None
    owner: str
    default_classification: str


def contract_sha256(
    schema_id: uuid.UUID | str,
    expected_columns: list[dict[str, Any]],
    business_key_columns: list[str],
    control_total_columns: list[str],
) -> str:
    """Content hash of a contract. Columns are hashed by name, so their order in the file
    is not part of the contract. Migration 0011 holds a frozen copy of this algorithm;
    test_sources keeps the two equal."""
    payload = {
        "schema_id": str(schema_id),
        "expected_columns": sorted(
            ({"name": c["name"], "required": bool(c["required"])} for c in expected_columns),
            key=lambda c: c["name"],
        ),
        "business_key_columns": sorted(business_key_columns),
        "control_total_columns": sorted(control_total_columns),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def spec_sha256(spec: ContractSpec) -> str:
    return contract_sha256(
        spec.schema_id,
        spec.expected_columns,
        spec.business_key_columns,
        spec.control_total_columns,
    )


def current_contract(contracts: list[SourceContract]) -> SourceContract | None:
    """The one version that is not SUPERSEDED (a partial unique index keeps it single)."""
    return next((c for c in contracts if c.status != ContractStatus.SUPERSEDED), None)


def profile_of(source: SourceSystem) -> SourceProfile:
    """Phase 7 registers one profile per source; it is the first one."""
    return source.profiles[0]


# Audit actions of the two writes; a refusal is recorded under the same action, DENIED.
CREATE_SOURCE = "source.create"
CREATE_CONTRACT = "source_contract.create"


def _public(*keys: str) -> dict[str, str]:
    return dict.fromkeys(keys, ClassificationCode.PUBLIC)


class SourceService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = SourceRepository(session, principal.scope)
        self.schemas = SchemaService(session, principal)

    # --- reads --------------------------------------------------------------------------

    async def page(self, limit: int, offset: int) -> tuple[list[SourceSystem], int]:
        """Sources with a profile (the query filters, so `total` matches the items)."""
        return await self.repo.page_by_code(limit, offset)

    async def get(self, source_id: uuid.UUID) -> SourceSystem:
        source = await self.repo.fresh(source_id)
        if source is None or not source.profiles:
            # Same response whether the source is absent or in another tenant (D-04).
            raise ApiError(404, "not_found", "Source not found.")
        return source

    async def schema_codes(self) -> dict[uuid.UUID, str]:
        """Codes of the caller's schemas, to name each contract's target."""
        return {s.id: s.code for s in await self.schemas.list_schemas()}

    # --- writes ---------------------------------------------------------------------------

    async def _refuse(
        self, action: str, resource_id: uuid.UUID | None, error: ApiError
    ) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await record_denial(
            self.session,
            self.principal,
            action,
            "source_system",
            str(resource_id) if resource_id else None,
            error.code,
        )
        raise error

    async def _target_schema(
        self, schema_id: uuid.UUID, action: str, resource_id: uuid.UUID | None
    ) -> str:
        """The target must exist in the caller's tenant and have an ACTIVE version."""
        try:
            schema = await self.schemas.get_schema(schema_id)  # 404 outside the tenant
        except ApiError as exc:
            await self._refuse(action, resource_id, exc)
        if effective_version(schema) is None:
            await self._refuse(
                action,
                resource_id,
                ApiError(
                    409,
                    "no_active_version",
                    "The target schema has no ACTIVE version.",
                    {"schema_id": str(schema_id)},
                ),
            )
        return schema.code

    async def create(
        self,
        code: str,
        name: str,
        description: str | None,
        kind: SourceKind,
        profile: ProfileSpec,
        contract: ContractSpec,
    ) -> SourceSystem:
        """System, profile and contract v1 in one transaction (audited `source.create`).
        The profile is always provisional (D2): the registrant cannot approve it."""
        if kind not in USABLE_SOURCE_KINDS:
            await self._refuse(
                CREATE_SOURCE,
                None,
                ApiError(
                    422,
                    "unsupported_source_kind",
                    "Only FILE_DROP sources can be registered in this release.",
                    {"kind": kind},
                ),
            )
        if profile.default_classification not in await known_classifications(self.session):
            await self._refuse(
                CREATE_SOURCE,
                None,
                ApiError(
                    422,
                    "unknown_classification",
                    "Unknown classification.",
                    {"classification": profile.default_classification},
                ),
            )
        schema_code = await self._target_schema(contract.schema_id, CREATE_SOURCE, None)
        if await self.repo.code_taken(code):
            await self._refuse(CREATE_SOURCE, None, _duplicate(code))

        actor = self.principal.actor
        source = SourceSystem(
            tenant_id=self.principal.tenant_id,
            code=code,
            name=name,
            kind=kind,
            description=description,
            created_by=actor,
        )
        self.repo.add(source)
        await self._flush_or_conflict(code)
        values = asdict(profile)
        if profile.format == SourceFormat.CSV and profile.delimiter is None:
            values["delimiter"] = CsvDelimiter.COMMA
        prof = SourceProfile(
            source_system_id=source.id, created_by=actor, provisional=True, **values
        )
        self.repo.add(prof)
        await self.repo.flush()
        sha = spec_sha256(contract)
        v1 = self._contract(prof.id, 1, contract, sha, supersedes=None)
        self.repo.add(v1)
        await self.repo.flush()
        audit_by(
            self.session,
            self.principal,
            CREATE_SOURCE,
            "source_system",
            resource_id=str(source.id),
            details={
                "code": code,
                "kind": kind,
                "format": profile.format,
                "schema": schema_code,
                "contract_id": str(v1.id),
                "contract_version": 1,
                "content_sha256": sha,
            },
            details_classification=_public(
                "code",
                "kind",
                "format",
                "schema",
                "contract_id",
                "contract_version",
                "content_sha256",
            ),
        )
        await self.session.commit()
        return await self.get(source.id)

    async def add_contract(self, source_id: uuid.UUID, contract: ContractSpec) -> SourceContract:
        """The next version, PROVISIONAL; the current one becomes SUPERSEDED, unedited."""
        source = await self.repo.lock(source_id)
        if source is None or not source.profiles:
            # The requested id is audited: absent and foreign look the same (D-04).
            await self._refuse(
                CREATE_CONTRACT, source_id, ApiError(404, "not_found", "Source not found.")
            )
        schema_code = await self._target_schema(contract.schema_id, CREATE_CONTRACT, source.id)
        prof = profile_of(source)
        contracts = await self.repo.contracts(prof.id)
        current = current_contract(contracts)
        sha = spec_sha256(contract)
        if current is not None and current.content_sha256 == sha:
            await self._refuse(
                CREATE_CONTRACT,
                source.id,
                ApiError(422, "no_change", "The contract equals the current version."),
            )
        if current is not None:
            to = ContractStatus.SUPERSEDED
            if to not in CONTRACT_TRANSITIONS[ContractStatus(current.status)]:
                await self._refuse(
                    CREATE_CONTRACT,
                    source.id,
                    ApiError(409, "invalid_status", f"Cannot supersede a {current.status}."),
                )
            current.status = to
            await self.repo.flush()  # before the insert: one current version per profile
        version = max((c.version for c in contracts), default=0) + 1
        new = self._contract(
            prof.id, version, contract, sha, supersedes=current.id if current else None
        )
        self.repo.add(new)
        await self.repo.flush()
        audit_by(
            self.session,
            self.principal,
            CREATE_CONTRACT,
            "source_system",
            resource_id=str(source.id),
            details={
                "contract_id": str(new.id),
                "contract_version": version,
                "schema": schema_code,
                "content_sha256": sha,
                "supersedes": str(current.id) if current else None,
            },
            details_classification=_public(
                "contract_id", "contract_version", "schema", "content_sha256", "supersedes"
            ),
        )
        if current is not None:
            audit_by(
                self.session,
                self.principal,
                "source_contract.supersede",
                "source_system",
                resource_id=str(source.id),
                details={
                    "contract_id": str(current.id),
                    "contract_version": current.version,
                    "superseded_by": str(new.id),
                },
                details_classification=_public("contract_id", "contract_version", "superseded_by"),
            )
        await self.session.commit()
        return new

    # --- helpers ---------------------------------------------------------------------------

    def _contract(
        self,
        profile_id: uuid.UUID,
        version: int,
        spec: ContractSpec,
        sha: str,
        supersedes: uuid.UUID | None,
    ) -> SourceContract:
        return SourceContract(
            source_profile_id=profile_id,
            version=version,
            status=ContractStatus.PROVISIONAL,
            schema_id=spec.schema_id,
            expected_columns=[
                {"name": c["name"], "required": bool(c["required"])} for c in spec.expected_columns
            ],
            business_key_columns=list(spec.business_key_columns),
            control_total_columns=list(spec.control_total_columns),
            content_sha256=sha,
            supersedes_id=supersedes,
            change_summary=spec.change_summary,
            created_by=self.principal.actor,
        )

    async def _flush_or_conflict(self, code: str) -> None:
        """A concurrent registration of the same code loses on the unique constraint."""
        try:
            await self.repo.flush()
        except IntegrityError:
            await self._refuse(CREATE_SOURCE, None, _duplicate(code))


def _duplicate(code: str) -> ApiError:
    return ApiError(409, "duplicate_code", "A source with this code exists.", {"code": code})


# --- Phase 8: imports (BE-8.4, BE-8.5) ---------------------------------------------------------

# Audit action of an upload; a refusal is recorded under the same action, DENIED (D-24 §4).
CREATE_IMPORT = "import.create"


def move(job: ImportJob, to: ImportStatus) -> None:
    """BE-8.5: change an import's status only along IMPORT_TRANSITIONS (the database
    trigger enforces the same table)."""
    current = ImportStatus(job.status)
    if to not in IMPORT_TRANSITIONS[current]:
        raise ApiError(
            409,
            "invalid_status",
            f"An import cannot move from {current} to {to}.",
            {"from": current.value, "to": to.value},
        )
    job.status = to


@dataclass(frozen=True)
class UploadSpec:
    """One POST /imports, as validated by the router (never trusted for scope)."""

    source_id: uuid.UUID
    data: bytes
    filename: str | None
    idempotency_key: str
    synthetic_attested: bool
    expected_row_count: int | None = None
    control_totals: dict[str, str] | None = None  # column -> decimal text


@dataclass(frozen=True)
class ImportPolicy:
    """What the deployment decides: the effective data class (D-14), the D-16 limits and
    where raw artifacts go (D-15)."""

    data_class: str
    limits: Limits
    store: ArtifactStore


def request_sha256(spec: UploadSpec, file_sha256: str) -> str:
    """What a replay under the same Idempotency-Key must repeat (D-21): the source, the
    file's bytes and the declared figures. The file name is not part of it."""
    payload = {
        "source_id": str(spec.source_id),
        "file_sha256": file_sha256,
        "synthetic_attested": spec.synthetic_attested,
        "expected_row_count": spec.expected_row_count,
        "control_totals": spec.control_totals or {},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def row_sha256(row: SourceRow) -> str:
    text = json.dumps(
        {"values": row.values, "extras": row.extras},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _flags(row: SourceRow) -> list[dict[str, Any]]:
    return [{"code": f.code.value, "column": f.column} for f in row.flags]


@dataclass(frozen=True)
class ImportLabels:
    """Names for the ids an import pins, resolved for the caller's tenant."""

    source_codes: dict[uuid.UUID, str]
    contract_versions: dict[uuid.UUID, int]
    schema_versions: dict[uuid.UUID, tuple[str, int]]  # version id -> (schema code, version)


class ImportService:
    """Upload → parse → store → RECEIVED in one transaction (BE-8.4, D-21). Every refused
    upload is audited DENIED with its reason code (D-24 §4); malformed requests (422
    validation_error) are refused by the router before reaching here and are not."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = ImportRepository(session, principal.scope)
        self.sources = SourceService(session, principal)
        self.schemas = SchemaService(session, principal)

    # --- reads --------------------------------------------------------------------------

    async def page(
        self,
        limit: int,
        offset: int,
        source_id: uuid.UUID | None = None,
        status: ImportStatus | None = None,
    ) -> tuple[list[ImportJob], int]:
        return await self.repo.page_filtered(limit, offset, source_id, status)

    async def get(self, import_id: uuid.UUID) -> ImportJob:
        job = await self.repo.fresh(import_id)
        if job is None:
            # Same response whether the import is absent or in another tenant (D-04).
            raise ApiError(404, "not_found", "Import not found.")
        return job

    async def labels(self, jobs: list[ImportJob]) -> ImportLabels:
        versions = {
            v.id: (s.code, v.version) for s in await self.schemas.list_schemas() for v in s.versions
        }
        return ImportLabels(
            await self.repo.source_codes({j.source_system_id for j in jobs}),
            await self.repo.contract_versions({j.source_contract_id for j in jobs}),
            versions,
        )

    # --- writes -------------------------------------------------------------------------

    async def refuse(self, error: ApiError, resource_id: uuid.UUID | None = None) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await record_denial(
            self.session,
            self.principal,
            CREATE_IMPORT,
            "import_job",
            str(resource_id) if resource_id else None,
            error.code,
        )
        raise error

    async def create(self, spec: UploadSpec, policy: ImportPolicy) -> tuple[ImportJob, bool]:
        """The import and whether it was created now (False: an idempotent replay)."""
        if policy.data_class == "synthetic" and not spec.synthetic_attested:
            await self.refuse(
                ApiError(
                    422,
                    UploadRefusal.SYNTHETIC_ATTESTATION_REQUIRED,
                    "This environment holds synthetic data only: confirm the file is "
                    "synthetic (synthetic_attested=true).",
                )
            )
        file_sha = hashlib.sha256(spec.data).hexdigest()
        fingerprint = request_sha256(spec, file_sha)
        replay = await self._replay(spec.idempotency_key, fingerprint)
        if replay is not None:
            return replay, False

        try:
            source = await self.sources.get(spec.source_id)
        except ApiError as exc:
            await self.refuse(exc)
        profile = profile_of(source)
        contract = current_contract(profile.contracts)
        if contract is None:
            await self.refuse(
                ApiError(409, UploadRefusal.NO_CURRENT_CONTRACT, "The source has no contract.")
            )
        schema = await self.schemas.get_schema(contract.schema_id)
        version = effective_version(schema)
        if version is None:
            await self.refuse(
                ApiError(
                    409,
                    UploadRefusal.NO_ACTIVE_VERSION,
                    "The contract's target schema has no ACTIVE version.",
                    {"schema_id": str(schema.id)},
                )
            )
        unknown = sorted(set(spec.control_totals or {}) - set(contract.control_total_columns))
        if unknown:
            await self.refuse(
                ApiError(
                    422,
                    UploadRefusal.UNKNOWN_CONTROL_TOTAL_COLUMN,
                    "Control totals may only name the contract's control-total columns.",
                    {"columns": unknown},
                )
            )
        options = AdapterOptions(
            delimiter=profile.delimiter or CsvDelimiter.COMMA, sheet_name=profile.sheet_name
        )
        try:
            parsed: ParsedUpload = await run_in_threadpool(
                parse_upload, spec.data, SourceFormat(profile.format), options, policy.limits
            )
        except AdapterError as exc:
            await self.refuse(ApiError(exc.status_code, exc.code, exc.message, exc.details))
        try:
            stored = await run_in_threadpool(policy.store.put, spec.data)
        except ApiError as exc:  # artifact_integrity_error: fail closed
            await self.refuse(exc)

        actor = self.principal.actor
        job = ImportJob(
            tenant_id=self.principal.tenant_id,
            source_system_id=source.id,
            source_contract_id=contract.id,
            schema_version_id=version.id,
            status=ImportStatus.CREATED,
            format=profile.format,
            data_class=policy.data_class,
            synthetic_attested=spec.synthetic_attested,
            synthetic_marker=parsed.marker_present,
            idempotency_key=spec.idempotency_key,
            request_sha256=fingerprint,
            correlation_id=get_correlation_id(),
            expected_row_count=spec.expected_row_count,
            control_totals=spec.control_totals,
            row_count=len(parsed.rows),
            created_by=actor,
        )
        self.repo.add(job)
        try:
            await self.repo.flush()
        except IntegrityError:
            # A concurrent request with the same key won: answer as its replay would.
            await self.session.rollback()
            replay = await self._replay(spec.idempotency_key, fingerprint)
            if replay is None:
                raise
            return replay, False
        batch = ImportBatch(
            import_job_id=job.id,
            batch_number=1,
            sheet_name=parsed.sheet_name,
            columns=parsed.columns,
            row_count=len(parsed.rows),
            created_by=actor,
        )
        self.repo.add(
            ImportArtifact(
                import_job_id=job.id,
                sha256=stored.sha256,
                size_bytes=stored.size,
                detected_mime=parsed.media_type,
                original_filename=spec.filename,
                storage_ref=stored.ref,
                classification=profile.default_classification,
                retention_state=RetentionState.RETAINED,
                created_by=actor,
            ),
            batch,
        )
        await self.repo.flush()
        await self.repo.add_records(
            [
                {
                    "id": uuid.uuid4(),
                    "batch_id": batch.id,
                    "ordinal": row.ordinal,
                    "locator": row.locator,
                    "raw_values": row.values,
                    "extra_values": row.extras,
                    "flags": _flags(row),
                    "row_sha256": row_sha256(row),
                    "created_by": actor,
                }
                for row in parsed.rows
            ]
        )
        move(job, ImportStatus.RECEIVED)
        await self.repo.flush()
        details: dict[str, Any] = {
            "source_id": str(source.id),
            "source_code": source.code,
            "format": profile.format,
            "status": job.status,
            "row_count": job.row_count,
            "sha256": stored.sha256,
            "size_bytes": stored.size,
            "data_class": policy.data_class,
            "synthetic_attested": spec.synthetic_attested,
            "synthetic_marker": parsed.marker_present,
            "contract_id": str(contract.id),
            "schema_version_id": str(version.id),
            "expected_row_count": spec.expected_row_count,
            "control_total_columns": sorted(spec.control_totals or {}),
        }
        audit_by(
            self.session,
            self.principal,
            CREATE_IMPORT,
            "import_job",
            resource_id=str(job.id),
            details=details,
            details_classification=_public(*details),
        )
        await self.session.commit()
        return await self.get(job.id), True

    async def _replay(self, key: str, fingerprint: str) -> ImportJob | None:
        """D-21: the same key with the same request is the original import; with anything
        else it is a 409 idempotency_conflict (audited)."""
        existing = await self.repo.by_idempotency_key(self.principal.actor, key)
        if existing is None:
            return None
        if existing.request_sha256 != fingerprint:
            await self.refuse(
                ApiError(
                    409,
                    UploadRefusal.IDEMPOTENCY_CONFLICT,
                    "This Idempotency-Key was already used for a different upload.",
                    {"import_id": str(existing.id)},
                ),
                existing.id,
            )
        return existing
