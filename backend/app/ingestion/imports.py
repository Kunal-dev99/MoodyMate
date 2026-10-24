"""ingestion.imports (BE-35.2: split from ingestion.service, which re-exports it)

Bounded file import (BE-8.4, DI-02): the upload request and policy (`UploadSpec`,
`ImportPolicy`, the D-16 `limits_from` settings, served by `GET config/limits`, BE-35.3), the
request and row hashes, the import lifecycle move, and `ImportReads`: the base of
`ImportService` with its reads and its audited refusal. Every read is scoped by the
principal (D-04).
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NoReturn

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.artifacts import ArtifactStore
from app.core.canonical import HashKind, sha256_for
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.refusals import refuse
from app.governance.service import SchemaService
from app.ingestion.adapters import Limits, SourceRow
from app.ingestion.constants import (
    CREATE_IMPORT,
    IMPORT_TRANSITIONS,
    ImportStatus,
    ValidationCode,
    ValidationSeverity,
)
from app.ingestion.contracts import SourceService
from app.ingestion.models import (
    CanonicalRecord,
    DataProfileSnapshot,
    ImportJob,
    ImportManifest,
    ValidationIssue,
)
from app.ingestion.repository import ImportRepository

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
    # D-20 §3: a corrected file for an earlier import of the same source.
    retry_of_import_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DriftThresholds:
    """BE-12.3: the PROVISIONAL drift thresholds (DRIFT_NULL_RATE_THRESHOLD,
    DRIFT_RANGE_THRESHOLD), passed to the mapping module's drift detection."""

    null_rate: Decimal
    range_share: Decimal


@dataclass(frozen=True)
class ImportPolicy:
    """What the deployment decides: the effective data class (D-14), the D-16 limits,
    where raw artifacts go (D-15), and whether profiling and drift detection run."""

    data_class: str
    limits: Limits
    store: ArtifactStore
    profiling: bool = True  # IMPORT_PROFILING_ENABLED (D-02)
    drift: DriftThresholds | None = None  # None: DRIFT_DETECTION_ENABLED is off (D-02)


def limits_from(settings: Settings) -> Limits:
    """The D-16 §1 / D-25 §1 bounds `POST imports` enforces: this deployment's IMPORT_MAX_*
    settings (each at most its hard ceiling). `GET config/limits` serves them (BE-35.3)."""
    return Limits(
        max_bytes=settings.IMPORT_MAX_BYTES,
        max_rows=settings.IMPORT_MAX_ROWS,
        max_columns=settings.IMPORT_MAX_COLUMNS,
        max_cell_chars=settings.IMPORT_MAX_CELL_CHARS,
        max_xlsx_uncompressed_bytes=settings.IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES,
        max_xlsx_ratio=settings.IMPORT_MAX_XLSX_RATIO,
        max_decoded_chars=settings.IMPORT_MAX_DECODED_CHARS,
    )


def drift_thresholds(settings: Settings) -> DriftThresholds | None:
    """BE-12.3: the PROVISIONAL thresholds, or None while DRIFT_DETECTION_ENABLED is off."""
    if not settings.DRIFT_DETECTION_ENABLED:
        return None
    return DriftThresholds(settings.DRIFT_NULL_RATE_THRESHOLD, settings.DRIFT_RANGE_THRESHOLD)


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
    if spec.retry_of_import_id is not None:  # only when set: earlier fingerprints still hold
        payload["retry_of_import_id"] = str(spec.retry_of_import_id)
    return sha256_for(HashKind.IMPORT_REQUEST, payload)


def row_sha256(row: SourceRow) -> str:
    return sha256_for(HashKind.SOURCE_ROW, {"values": row.values, "extras": row.extras})


def _flags(row: SourceRow) -> list[dict[str, Any]]:
    return [{"code": f.code.value, "column": f.column} for f in row.flags]


@dataclass(frozen=True)
class ImportLabels:
    """Names for the ids an import pins, resolved for the caller's tenant."""

    source_codes: dict[uuid.UUID, str]
    contract_versions: dict[uuid.UUID, int]
    schema_versions: dict[uuid.UUID, tuple[str, int]]  # version id -> (schema code, version)


class ImportReads:
    """The base of `ImportService`: its collaborators, reads and refusal."""

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
        schema_code: str | None = None,
        projected_ids: Sequence[uuid.UUID] | None = None,
        projected: bool | None = None,
    ) -> tuple[list[ImportJob], int]:
        """Newest first. `schema_code` keeps imports pinned to a version of that schema (in
        the caller's tenant). `projected` keeps the imports in (True) or not in (False)
        `projected_ids`: the tenant's projected imports, which the caller reads from the
        domain module (Phase 15, for the new-run panel)."""
        versions = None
        if schema_code is not None:
            versions = [
                v.id
                for s in await self.schemas.list_schemas()
                if s.code == schema_code
                for v in s.versions
            ]
        ids = None
        if projected is not None:
            ids = list(projected_ids or [])
        return await self.repo.page_filtered(
            limit, offset, source_id, status, versions, ids, exclude_ids=projected is False
        )

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

    async def source_codes(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Source id -> code, within the caller's tenant (for the WS3 domain catalogue)."""
        return await self.repo.source_codes(ids)

    # --- for the WS3 projection (Phase 14, D-29): read only ---------------------------------

    async def locked(self, import_id: uuid.UUID) -> ImportJob | None:
        """The import locked for the caller's transaction, or None when it is absent or in
        another tenant (the caller refuses both alike, D-04)."""
        return await self.repo.lock(import_id)

    async def accepted_records(self, job: ImportJob) -> list[CanonicalRecord]:
        """The import's accepted rows (canonical records) in source order."""
        return await self.repo.canonical_records(job.id)

    # --- for WS3 dataset snapshots (Phase 15, D-30): read only -----------------------------

    async def find(self, import_id: uuid.UUID) -> ImportJob | None:
        """The import, or None when it is absent or in another tenant (the caller refuses
        both alike, D-04)."""
        return await self.repo.fresh(import_id)

    # --- writes -------------------------------------------------------------------------

    async def refuse_upload(self, error: ApiError) -> NoReturn:
        """A refused upload the router detects (e.g. over the byte limit), audited DENIED."""
        await self._refuse(error)

    async def _refuse(
        self,
        error: ApiError,
        resource_id: uuid.UUID | None = None,
        action: str = CREATE_IMPORT,
    ) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        if action == CREATE_IMPORT:  # BE-34.4: a refused upload, by its refusal code
            metrics.count_import_failure("upload", [error.code])
        await refuse(
            self.session,
            self.principal,
            error,
            action=action,
            resource_type="import_job",
            resource_id=resource_id,
        )

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

    async def drift_baseline(self, job: ImportJob) -> tuple[ImportJob, DataProfileSnapshot] | None:
        """BE-12.3: the latest COMPLETED or COMPLETED_WITH_WARNINGS import of the job's source
        (one profile per source), with its profile; None for a first import."""
        return await self.repo.drift_baseline(job)

    # --- validation (BE-11.3, BE-11.5, D-20, D-21, D-22) ----------------------------------------

    async def manifest(self, job: ImportJob) -> ImportManifest | None:
        """The stored manifest of an import already reached through this scope."""
        return await self.repo.manifest(job.id)

    async def issues(
        self,
        import_id: uuid.UUID,
        severity: ValidationSeverity | None,
        code: ValidationCode | None,
        field: str | None,
        limit: int,
        offset: int,
    ) -> tuple[ImportJob, list[ValidationIssue], int, dict[str, int]]:
        """An import's issues in the order validation raised them (file-level first), with
        the per-severity counts of all of them. 404 alike for absent and foreign (D-04)."""
        job = await self.get(import_id)
        items, total = await self.repo.issue_page(job.id, severity, code, field, limit, offset)
        return job, items, total, await self.repo.severity_counts(job.id)
