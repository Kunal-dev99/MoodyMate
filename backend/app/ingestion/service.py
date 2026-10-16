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
import re
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, NoReturn

from sqlalchemy import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.artifacts import ArtifactStore
from app.core.correlation import get_correlation_id
from app.core.errors import ApiError
from app.core.principal import Principal
from app.governance.constants import ClassificationCode, FieldType
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
    RowFlagCode,
    SourceFormat,
    SourceKind,
    UploadRefusal,
)
from app.ingestion.models import (
    DataProfileSnapshot,
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

    async def get_contract(self, contract_id: uuid.UUID) -> tuple[SourceSystem, SourceContract]:
        """A contract of the caller's tenant with its source (404 alike for absent and
        foreign, D-04). For the mapping module (BE-10.3)."""
        source_id = await self.repo.contract_source(contract_id)
        if source_id is None:
            raise ApiError(404, "not_found", "Source contract not found.")
        source = await self.get(source_id)
        return source, next(c for c in profile_of(source).contracts if c.id == contract_id)

    async def contract_labels(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, int]]:
        """Contract id -> (source code, contract version), within the caller's tenant."""
        return await self.repo.contract_labels(ids)

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
# Audit action of the profiling step that follows an upload (BE-9.2).
PROFILE_IMPORT = "import.profile"


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
    profiling: bool = True  # IMPORT_PROFILING_ENABLED (D-02)


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

    async def refuse(
        self,
        error: ApiError,
        resource_id: uuid.UUID | None = None,
        action: str = CREATE_IMPORT,
    ) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await record_denial(
            self.session,
            self.principal,
            action,
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
            replay_id = replay.id  # profile() may roll back, which expires `replay`
            if (
                policy.profiling
                and replay.status == ImportStatus.RECEIVED
                and await self.repo.snapshot(replay_id) is None
            ):
                # A profiling run that failed earlier is retried by replaying the upload.
                # (A profiled import is also RECEIVED once a usable mapping exists.)
                await self.profile(replay_id)
                return await self.get(replay_id), False
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
        # D-21: profiling runs synchronously after receipt, as its own step (and its own
        # transaction): if it fails, the upload stands as RECEIVED without a profile, and a
        # replay of the same upload retries it.
        if policy.profiling:
            await self.profile(job.id)
        return await self.get(job.id), True

    # --- profiling (BE-9.2, D-21) ------------------------------------------------------------

    async def has_usable_mapping(self, job: ImportJob) -> bool:
        """D-21: whether the import's source contract has a usable ACTIVE mapping version,
        one pinned to the schema version the import pins (D-18 §4). Asked of the mapping
        service (imported here, not at module level: mapping depends on ingestion)."""
        from app.mapping.service import MappingService

        found = await MappingService(self.session, self.principal).usable_version(
            job.source_contract_id, job.schema_version_id
        )
        return found is not None

    async def mapped_classifications(self, job: ImportJob) -> dict[str, str]:
        """Column -> the classification of the schema field its mapping targets (BE-9.3,
        P9-3): the version the import pinned (D-18 §5), else its usable ACTIVE version.
        Unmapped and PRESERVEd columns keep the source profile's default."""
        from app.mapping.service import MappingService

        return await MappingService(self.session, self.principal).column_classifications(
            job.source_contract_id, job.schema_version_id, job.mapping_version_id
        )

    async def snapshot(self, job: ImportJob) -> DataProfileSnapshot | None:
        """The stored profile of an import already reached through this scope."""
        return await self.repo.snapshot(job.id)

    def record_partitions(
        self, job: ImportJob, size: int
    ) -> AsyncIterator[Sequence[Row[tuple[dict[str, str], list[str], list[dict[str, Any]]]]]]:
        """An import's records (values, extras, flags) in file order, streamed in bounded
        partitions (for the mapping dry run, BE-10.4). Scoped through the import."""
        return self.repo.record_partitions(job.id, size)

    async def profile(self, import_id: uuid.UUID) -> DataProfileSnapshot:
        """Profile a RECEIVED import: RECEIVED -> PROFILING -> RECEIVED when a usable ACTIVE
        mapping exists, else MAPPING_REQUIRED (D-21). One transaction, audited
        `import.profile` with metadata only (no column names, no values). Idempotent: an
        import that has a profile gets it back unchanged."""
        job = await self.repo.lock(import_id)
        if job is None:
            await self.refuse(
                ApiError(404, "not_found", "Import not found."), import_id, PROFILE_IMPORT
            )
        existing = await self.repo.snapshot(job.id)
        if existing is not None:
            await self.session.rollback()  # release the lock; nothing changed
            return existing
        if job.status != ImportStatus.RECEIVED:
            await self.refuse(
                ApiError(
                    409,
                    "invalid_status",
                    f"Only a RECEIVED import can be profiled; this one is {job.status}.",
                    {"status": job.status},
                ),
                job.id,
                PROFILE_IMPORT,
            )
        move(job, ImportStatus.PROFILING)
        await self.repo.flush()  # each move is its own UPDATE: the trigger checks both
        columns = list(dict.fromkeys(c for batch in job.batches for c in batch.columns))
        profiler = Profiler(columns)
        async for partition in self.repo.record_partitions(job.id, PROFILE_FETCH_ROWS):
            # CPU-bound work off the event loop, one bounded partition at a time.
            await run_in_threadpool(profiler.add_all, [tuple(row) for row in partition])
        result = profiler.result()
        snapshot = DataProfileSnapshot(
            import_job_id=job.id,
            profiler_version=PROFILER_VERSION,
            row_count=result.row_count,
            column_count=len(result.columns),
            summary=result.summary,
            columns=result.columns,
            content_sha256=result.content_sha256,
            created_by=self.principal.actor,
        )
        self.repo.add(snapshot)
        usable = await self.has_usable_mapping(job)
        move(job, ImportStatus.RECEIVED if usable else ImportStatus.MAPPING_REQUIRED)
        await self.repo.flush()
        details: dict[str, Any] = {
            "status": job.status,
            "snapshot_id": str(snapshot.id),
            "content_sha256": result.content_sha256,
            "profiler_version": PROFILER_VERSION,
            "row_count": result.row_count,
            "column_count": len(result.columns),
            "usable_mapping": usable,
        }
        audit_by(
            self.session,
            self.principal,
            PROFILE_IMPORT,
            "import_job",
            resource_id=str(job.id),
            details=details,
            details_classification=_public(*details),
        )
        await self.session.commit()
        return snapshot

    async def profile_view(self, import_id: uuid.UUID) -> "ProfileView":
        """What GET /imports/{id}/profile shows (BE-9.3): the stored profile plus, per
        column, its classification and the read-only suggestions. Reads only."""
        job = await self.get(import_id)  # 404, the same for absent and foreign (D-04)
        snapshot = await self.repo.snapshot(job.id)
        if snapshot is None:
            raise ApiError(404, "profile_not_found", "This import has no profile.")
        source = await self.sources.get(job.source_system_id)
        profile = profile_of(source)
        contract = next(c for c in profile.contracts if c.id == job.source_contract_id)
        schema = await self.schemas.get_schema(contract.schema_id)
        version = next(v for v in schema.versions if v.id == job.schema_version_id)
        return ProfileView(
            job=job,
            snapshot=snapshot,
            contract_version=contract.version,
            schema_id=schema.id,
            schema_code=schema.code,
            schema_version=version.version,
            expected_columns=frozenset(c["name"] for c in contract.expected_columns),
            field_names=[f.name for f in version.fields],
            mapped=await self.mapped_classifications(job),
            default_classification=profile.default_classification,
        )

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


# --- Phase 9: field profiling (BE-9.2, BE-9.3) --------------------------------------------------
#
# The profiler is a pure, deterministic function of an import's records in file order: the
# same records always give the same observations and the same `content_sha256`. Arithmetic
# is Decimal only (never float). It observes and suggests; it never changes a schema, a
# contract or a record (DI-06).

PROFILER_VERSION = 1  # part of the hash: a change to the rules below is a new version
PROFILE_TOP_VALUES = 10  # top values kept per column
PROFILE_DISTINCT_CAP = 10_000  # distinct values tracked per column; beyond, counts are capped
PROFILE_VALUE_MAX_CHARS = 100  # a top value longer than this is stored truncated
PROFILE_FETCH_ROWS = 1_000  # records per streamed partition
_RATE = Decimal("0.0001")  # null rates: four decimal places, half-even
_RATE_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

_INTEGER = re.compile(r"[+-]?[0-9]+")
_DECIMAL = re.compile(r"[+-]?([0-9]+)(?:\.([0-9]+))?")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)
_BOOLEANS = frozenset({"true", "false"})
_ISO_4217 = re.compile(r"[A-Z]{3}")
_AMOUNT = r"[+-]?[0-9][0-9,]*(?:\.[0-9]+)?"
# "EUR 12.50", "€12.50", "12.50 EUR", "12.50€". A code before the amount needs a space, so
# identifiers such as "INS-000001" are not amounts.
_AMOUNT_WITH_CURRENCY = re.compile(
    rf"(?:[A-Z]{{3}} |[$€£¥] ?){_AMOUNT}|{_AMOUNT} ?(?:[A-Z]{{3}}|[$€£¥])"
)
_YMD = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_YMD_SLASH = re.compile(r"([0-9]{4})/([0-9]{2})/([0-9]{2})")
_XX_SLASH = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")
_DMY_DOT = re.compile(r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})")
_DMY_DASH = re.compile(r"([0-9]{2})-([0-9]{2})-([0-9]{4})")
# (label, pattern, positions of year, month, day among the groups). The order is the
# preference when several formats hold every value (day-first before month-first).
DATE_FORMATS: tuple[tuple[str, re.Pattern[str], tuple[int, int, int]], ...] = (
    ("YYYY-MM-DD", _YMD, (0, 1, 2)),
    ("YYYY/MM/DD", _YMD_SLASH, (0, 1, 2)),
    ("DD/MM/YYYY", _XX_SLASH, (2, 1, 0)),
    ("MM/DD/YYYY", _XX_SLASH, (2, 0, 1)),
    ("DD.MM.YYYY", _DMY_DOT, (2, 1, 0)),
    ("DD-MM-YYYY", _DMY_DASH, (2, 1, 0)),
)
# Candidate types, most specific first (the governance FieldType values).
TYPE_ORDER = (
    FieldType.INTEGER,
    FieldType.DECIMAL,
    FieldType.BOOLEAN,
    FieldType.DATE,
    FieldType.TIMESTAMP,
    FieldType.STRING,
)


def _parse_date(value: str) -> dict[str, date]:
    """The value as a calendar date under each format it is valid in."""
    found: dict[str, date] = {}
    for label, pattern, (y, m, d) in DATE_FORMATS:
        match = pattern.fullmatch(value)
        if match is None:
            continue
        parts = match.groups()
        try:
            found[label] = date(int(parts[y]), int(parts[m]), int(parts[d]))
        except ValueError:
            continue  # not a calendar date (e.g. 30 February)
    return found


def _timestamp(value: str) -> datetime | None:
    if _TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _numeric(value: str) -> tuple[Decimal, int, int] | None:
    """Plain decimal text (no exponent, grouping or spaces) as (value, integer digits,
    scale), as NUMERIC counts them."""
    match = _DECIMAL.fullmatch(value)
    if match is None:
        return None
    whole, fraction = match.group(1), match.group(2) or ""
    return Decimal(value), len(whole.lstrip("0")), len(fraction)


def _rate(part: int, whole: int) -> str | None:
    if whole == 0:
        return None
    with localcontext(_RATE_CONTEXT):
        return str((Decimal(part) / Decimal(whole)).quantize(_RATE))


class _ColumnProfile:
    """Running observations of one column. Memory is bounded by PROFILE_DISTINCT_CAP."""

    def __init__(self, name: str, position: int) -> None:
        self.name = name
        self.position = position
        self.values = 0
        self.nulls = 0
        self.max_length: int | None = None
        self.counts: dict[str, int] = {}
        self.capped = False
        self.integer = True
        self.boolean = True
        self.numeric = True
        self.num_min: tuple[Decimal, str] | None = None
        self.num_max: tuple[Decimal, str] | None = None
        self.int_digits = 0
        self.scale = 0
        self.dates: dict[str, int] = {}
        self.date_min: dict[str, date] = {}
        self.date_max: dict[str, date] = {}
        self.timestamp = True
        self.ts_kinds: set[bool] = set()  # aware?
        self.ts_min: datetime | None = None
        self.ts_max: datetime | None = None
        self.iso_codes = 0
        self.amounts = 0

    def add(self, value: str | None) -> None:
        if value is None or not value.strip():
            self.nulls += 1  # a missing cell, a JSON null, "" (XLSX pads) or only spaces
            return
        self.values += 1
        self.max_length = max(self.max_length or 0, len(value))
        if value in self.counts:
            self.counts[value] += 1
        elif len(self.counts) < PROFILE_DISTINCT_CAP:
            self.counts[value] = 1
        else:
            self.capped = True
        self.integer = self.integer and _INTEGER.fullmatch(value) is not None
        self.boolean = self.boolean and value.casefold() in _BOOLEANS
        if self.numeric:
            self._add_number(value)
        for label, day in _parse_date(value).items():
            self.dates[label] = self.dates.get(label, 0) + 1
            self.date_min[label] = min(self.date_min.get(label, day), day)
            self.date_max[label] = max(self.date_max.get(label, day), day)
        if self.timestamp:
            self._add_timestamp(value)
        if _ISO_4217.fullmatch(value):
            self.iso_codes += 1
        elif _AMOUNT_WITH_CURRENCY.fullmatch(value):
            self.amounts += 1

    def _add_number(self, value: str) -> None:
        parsed = _numeric(value)
        if parsed is None:
            self.numeric = False
            return
        number, digits, scale = parsed
        if self.num_min is None or number < self.num_min[0]:
            self.num_min = (number, value)  # the first text of the smallest value
        if self.num_max is None or number > self.num_max[0]:
            self.num_max = (number, value)
        self.int_digits = max(self.int_digits, digits)
        self.scale = max(self.scale, scale)

    def _add_timestamp(self, value: str) -> None:
        moment = _timestamp(value)
        if moment is None:
            self.timestamp = False
            return
        aware = moment.tzinfo is not None
        self.ts_kinds.add(aware)
        if len(self.ts_kinds) > 1:
            return  # naive and aware moments do not compare: no range
        if aware:
            moment = moment.astimezone(UTC)
        self.ts_min = moment if self.ts_min is None else min(self.ts_min, moment)
        self.ts_max = moment if self.ts_max is None else max(self.ts_max, moment)

    def _date_format(self) -> str | None:
        """The first format (in preference order) that holds every value, if any."""
        return next(
            (label for label, _, _ in DATE_FORMATS if self.dates.get(label) == self.values),
            None,
        )

    def candidates(self) -> list[FieldType]:
        if self.values == 0:
            return []
        holds = {
            FieldType.INTEGER: self.integer,
            FieldType.DECIMAL: self.numeric,
            FieldType.BOOLEAN: self.boolean,
            FieldType.DATE: self._date_format() is not None,
            FieldType.TIMESTAMP: self.timestamp,
            FieldType.STRING: True,
        }
        return [t for t in TYPE_ORDER if holds[t]]

    def _range(self, types: list[FieldType]) -> tuple[str | None, str | None]:
        label = self._date_format()
        if label is not None:
            return self.date_min[label].isoformat(), self.date_max[label].isoformat()
        if FieldType.TIMESTAMP in types and len(self.ts_kinds) == 1:
            assert self.ts_min is not None and self.ts_max is not None
            return self.ts_min.isoformat(), self.ts_max.isoformat()
        if FieldType.DECIMAL in types:
            assert self.num_min is not None and self.num_max is not None
            return self.num_min[1], self.num_max[1]
        return None, None

    def result(self, rows: int) -> dict[str, Any]:
        types = self.candidates()
        low, high = self._range(types)
        numeric = FieldType.DECIMAL in types
        top = sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))[:PROFILE_TOP_VALUES]
        return {
            "name": self.name,
            "position": self.position,
            "value_count": self.values,
            "null_count": self.nulls,
            "null_rate": _rate(self.nulls, rows),
            "distinct_count": len(self.counts),
            "distinct_capped": self.capped,
            "candidate_types": [t.value for t in types],
            "min": low,
            "max": high,
            "max_length": self.max_length,
            "max_precision": max(self.int_digits + self.scale, 1) if numeric else None,
            "max_scale": self.scale if numeric else None,
            "date_formats": dict(sorted(self.dates.items())),
            "currency_pattern_hits": {
                "ISO_4217_CODE": self.iso_codes,
                "AMOUNT_WITH_CURRENCY": self.amounts,
            },
            "top_values": [
                {
                    "value": value[:PROFILE_VALUE_MAX_CHARS],
                    "count": count,
                    "truncated": len(value) > PROFILE_VALUE_MAX_CHARS,
                }
                for value, count in top
            ],
            "top_values_exact": not self.capped,
        }


@dataclass(frozen=True)
class ProfileResult:
    row_count: int
    summary: dict[str, Any]
    columns: list[dict[str, Any]]
    content_sha256: str


def profile_sha256(row_count: int, summary: dict[str, Any], columns: list[dict[str, Any]]) -> str:
    """The hash of what was observed (not of ids or times)."""
    payload = {
        "profiler_version": PROFILER_VERSION,
        "row_count": row_count,
        "summary": summary,
        "columns": columns,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


ProfileInput = tuple[Mapping[str, str], Sequence[str], Sequence[Mapping[str, Any]]]


class Profiler:
    """Feed records (values, extras, flags) in file order, then take the `result()`.

    D-25 row semantics: a missing cell or JSON null is an empty value of that column; extra
    cells are counted per row (they belong to no column); a blank row counts as a row whose
    every column is empty. Nothing is dropped and nothing is invented."""

    def __init__(self, columns: Sequence[str]) -> None:
        self._columns = [_ColumnProfile(name, i) for i, name in enumerate(columns, start=1)]
        self._rows = 0
        self._blank = 0
        self._missing = 0
        self._extra_rows = 0
        self._max_extra = 0
        self._flags: dict[str, int] = {}

    def add(
        self, values: Mapping[str, str], extras: Sequence[str], flags: Sequence[Mapping[str, Any]]
    ) -> None:
        self._rows += 1
        codes = [str(flag["code"]) for flag in flags]
        for code in codes:
            self._flags[code] = self._flags.get(code, 0) + 1
        self._blank += RowFlagCode.BLANK_ROW in codes
        self._missing += RowFlagCode.MISSING_CELLS in codes
        if extras:
            self._extra_rows += 1
            self._max_extra = max(self._max_extra, len(extras))
        for column in self._columns:
            column.add(values.get(column.name))

    def add_all(self, rows: Iterable[ProfileInput]) -> None:
        for values, extras, flags in rows:
            self.add(values, extras, flags)

    def result(self) -> ProfileResult:
        summary: dict[str, Any] = {
            "blank_rows": self._blank,
            "rows_with_missing_cells": self._missing,
            "rows_with_extra_cells": self._extra_rows,
            "max_extra_cells": self._max_extra,
            "flag_counts": dict(sorted(self._flags.items())),
        }
        columns = [c.result(self._rows) for c in self._columns]
        return ProfileResult(
            self._rows, summary, columns, profile_sha256(self._rows, summary, columns)
        )


def profile_rows(columns: Sequence[str], rows: Iterable[ProfileInput]) -> ProfileResult:
    """The profile of these records (a pure function; BE-9.2)."""
    profiler = Profiler(columns)
    profiler.add_all(rows)
    return profiler.result()


# The profiler's parts, for the test that proves it holds no float (BE-9.2).
PROFILER_PARTS = (
    "_parse_date",
    "_timestamp",
    "_numeric",
    "_rate",
    "_ColumnProfile",
    "profile_sha256",
    "profile_rows",
)


def normalise_name(name: str) -> str:
    """A column or field name compared loosely: case-folded, every run of non-word
    characters (spaces, dashes, dots, underscores) one underscore, trimmed."""
    return re.sub(r"[\W_]+", "_", name.casefold()).strip("_")


def suggested_target(column: str, field_names: Sequence[str]) -> str | None:
    """The one field of the pinned schema version whose normalised name equals the
    column's (BE-9.3). Ambiguous or no match: no suggestion."""
    key = normalise_name(column)
    matches = [f for f in field_names if normalise_name(f) == key]
    return matches[0] if key and len(matches) == 1 else None


@dataclass(frozen=True)
class ProfileView:
    """An import's stored profile with what its suggestions are computed against: the
    pinned contract's expected columns and the pinned schema version's fields."""

    job: ImportJob
    snapshot: DataProfileSnapshot
    contract_version: int
    schema_id: uuid.UUID
    schema_code: str
    schema_version: int
    expected_columns: frozenset[str]
    field_names: list[str]
    mapped: dict[str, str]  # column -> classification of its mapped field (Phase 10)
    default_classification: str

    def classification(self, column: str) -> str:
        """BE-9.3: the mapped field's classification, else the source profile's default."""
        return self.mapped.get(column, self.default_classification)

    def in_contract(self, column: str) -> bool:
        return column in self.expected_columns

    def suggested_target(self, column: str) -> str | None:
        return suggested_target(column, self.field_names)
