"""ingestion.uploads (BE-35.2: split from ingestion.service, which re-exports it)

`ImportService`: `POST imports` (BE-8.4, DI-02). One upload becomes an import job with a
write-once raw artifact and one immutable `source_record` per row, synchronously (D-21);
an Idempotency-Key replay returns the original import, and a retry of an earlier import is
checked (D-20 §3). Refusals are audited DENIED under `import.create`.
"""

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.canonical import sha256_hex
from app.core.correlation import get_correlation_id
from app.core.errors import ApiError
from app.governance.service import audit_by, effective_version
from app.ingestion.adapters import AdapterError, AdapterOptions, ParsedUpload, parse_upload
from app.ingestion.constants import (
    CREATE_IMPORT,
    RETRYABLE_IMPORT_STATUSES,
    CsvDelimiter,
    ImportStatus,
    RetentionState,
    SourceFormat,
    UploadRefusal,
)
from app.ingestion.contracts import _public, current_contract, profile_of
from app.ingestion.imports import ImportPolicy, UploadSpec, _flags, move, request_sha256, row_sha256
from app.ingestion.manifests import ImportValidation
from app.ingestion.models import ImportArtifact, ImportBatch, ImportJob


class ImportService(ImportValidation):
    async def create(self, spec: UploadSpec, policy: ImportPolicy) -> tuple[ImportJob, bool]:
        """The import and whether it was created now (False: an idempotent replay)."""
        if policy.data_class == "synthetic" and not spec.synthetic_attested:
            await self._refuse(
                ApiError(
                    422,
                    UploadRefusal.SYNTHETIC_ATTESTATION_REQUIRED,
                    "This environment holds synthetic data only: confirm the file is "
                    "synthetic (synthetic_attested=true).",
                )
            )
        file_sha = sha256_hex(spec.data)
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
                await self.profile(replay_id, policy.drift)
                return await self.get(replay_id), False
            return replay, False

        try:
            source = await self.sources.get(spec.source_id)
        except ApiError as exc:
            await self._refuse(exc)
        profile = profile_of(source)
        contract = current_contract(profile.contracts)
        if contract is None:
            await self._refuse(
                ApiError(409, UploadRefusal.NO_CURRENT_CONTRACT, "The source has no contract.")
            )
        schema = await self.schemas.get_schema(contract.schema_id)
        version = effective_version(schema)
        if version is None:
            await self._refuse(
                ApiError(
                    409,
                    UploadRefusal.NO_ACTIVE_VERSION,
                    "The contract's target schema has no ACTIVE version.",
                    {"schema_id": str(schema.id)},
                )
            )
        if spec.retry_of_import_id is not None:
            await self._check_retry(spec.retry_of_import_id, source.id)
        unknown = sorted(set(spec.control_totals or {}) - set(contract.control_total_columns))
        if unknown:
            await self._refuse(
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
            await self._refuse(ApiError(exc.status_code, exc.code, exc.message, exc.details))
        try:
            stored = await run_in_threadpool(policy.store.put, spec.data)
        except ApiError as exc:  # artifact_integrity_error: fail closed
            await self._refuse(exc)

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
            retry_of_import_id=spec.retry_of_import_id,
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
            "retry_of_import_id": str(spec.retry_of_import_id) if spec.retry_of_import_id else None,
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
            await self.profile(job.id, policy.drift)
        return await self.get(job.id), True

    async def _check_retry(self, original_id: uuid.UUID, source_id: uuid.UUID) -> None:
        """D-20 §3: a retry corrects an earlier validated import of the same source that held
        rows (QUARANTINED or FAILED). Absent and foreign imports look alike (D-04)."""
        original = await self.repo.fresh(original_id)
        if original is None:
            await self._refuse(ApiError(404, "not_found", "The import to retry was not found."))
        if original.source_system_id != source_id:
            await self._refuse(
                ApiError(
                    409,
                    UploadRefusal.RETRY_SOURCE_MISMATCH,
                    "A retry must be uploaded for the same source as the import it corrects.",
                    {"retry_of_import_id": str(original.id)},
                )
            )
        if original.status not in RETRYABLE_IMPORT_STATUSES:
            await self._refuse(
                ApiError(
                    409,
                    UploadRefusal.RETRY_NOT_APPLICABLE,
                    "Only a QUARANTINED or FAILED import can be retried.",
                    {"retry_of_import_id": str(original.id), "status": original.status},
                )
            )

    async def _replay(self, key: str, fingerprint: str) -> ImportJob | None:
        """D-21: the same key with the same request is the original import; with anything
        else it is a 409 idempotency_conflict (audited)."""
        existing = await self.repo.by_idempotency_key(self.principal.actor, key)
        if existing is None:
            return None
        if existing.request_sha256 != fingerprint:
            await self._refuse(
                ApiError(
                    409,
                    UploadRefusal.IDEMPOTENCY_CONFLICT,
                    "This Idempotency-Key was already used for a different upload.",
                    {"import_id": str(existing.id)},
                ),
                existing.id,
            )
        return existing
