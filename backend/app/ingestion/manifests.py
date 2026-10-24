"""ingestion.manifests (BE-35.2: split from ingestion.service, which re-exports it)

`POST imports/{import_id}/validate` (BE-11.3): the engine (`validation`) runs over the
import's records in keyset pages, its results are written once (issues, quarantine items,
canonical records and their lineage), the reconciliation runs (D-28), and the immutable
import manifest is written (`manifest_sha256`). Refusals are audited DENIED under
`import.validate`.
"""

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core import metrics
from app.core.canonical import HashKind, sha256_for, sha256_hex
from app.core.classification import most_sensitive
from app.core.errors import ApiError
from app.governance.service import audit_by
from app.ingestion.constants import (
    DEFAULT_VALIDATION_PROFILE_CODE,
    VALIDATOR_VERSION,
    ImportStatus,
    QuarantineStatus,
    RowOutcome,
)
from app.ingestion.contracts import _public, profile_of
from app.ingestion.imports import move
from app.ingestion.models import (
    CanonicalRecord,
    ImportArtifact,
    ImportJob,
    ImportManifest,
    QuarantineItem,
    RecordLineage,
    SourceContract,
    ValidationIssue,
)
from app.ingestion.profiling import ImportProfiling
from app.ingestion.quarantine import QuarantineService
from app.ingestion.validation import (
    Finding,
    MappedRule,
    RowResult,
    Validator,
    payload_sha256,
    profile_entries,
    target_fields_of,
)

# Audit action of validation (BE-11.3); a refusal is recorded under the same action, DENIED.
VALIDATE_IMPORT = "import.validate"
VALIDATE_PAGE_ROWS = 1_000  # records per keyset page of a validation
# Statuses only validation reaches (D-21): an import in one was validated already.
VALIDATED_STATUSES = frozenset(
    {
        ImportStatus.COMPLETED,
        ImportStatus.COMPLETED_WITH_WARNINGS,
        ImportStatus.QUARANTINED,
        ImportStatus.FAILED,
    }
)


class _ResultWriter:
    """Turns the engine's row results into the rows validation inserts, and keeps what the
    manifest and a retry need (payload hashes in file order, business key hashes)."""

    def __init__(
        self,
        job: ImportJob,
        mapping_version_id: uuid.UUID,
        profile_id: uuid.UUID,
        actor: str,
    ) -> None:
        self.job_id = job.id
        self.schema_version_id = job.schema_version_id
        self.mapping_version_id = mapping_version_id
        self.profile_id = profile_id
        self.actor = actor
        self.owner = ""
        self.hashes: list[str] = []
        self.present_keys: set[str] = set()
        self.accepted_keys: set[str] = set()

    def issue(self, f: "Finding", record_id: uuid.UUID | None = None) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "import_job_id": self.job_id,
            "sequence": f.sequence,
            "source_record_id": record_id,
            "ordinal": f.ordinal if record_id is not None else None,
            "code": f.code.value,
            "severity": f.severity.value,
            "disposition": f.disposition.value,
            "field": f.field,
            "source_column": f.source_column,
            "rule_position": f.rule_position,
            "validation_profile_id": self.profile_id,
            "message": f.message,
            "created_by": self.actor,
        }

    def add(
        self, page: Sequence[Any], results: Sequence["RowResult"]
    ) -> list[tuple[type[Any], list[dict[str, Any]]]]:
        canonical: list[dict[str, Any]] = []
        lineage: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        for record, result in zip(page, results, strict=True):
            record_id, batch_id = record[0], record[1]
            issues.extend(self.issue(f, record_id) for f in result.findings)
            if result.key_sha256 is not None:
                self.present_keys.add(result.key_sha256)
            if result.outcome == RowOutcome.ACCEPTED:
                assert result.payload is not None
                canonical_id = uuid.uuid4()
                digest = payload_sha256(result.payload)
                self.hashes.append(digest)
                if result.key_sha256 is not None:
                    self.accepted_keys.add(result.key_sha256)
                canonical.append(
                    {
                        "id": canonical_id,
                        "import_job_id": self.job_id,
                        "schema_version_id": self.schema_version_id,
                        "mapping_version_id": self.mapping_version_id,
                        "source_record_id": record_id,
                        "ordinal": result.ordinal,
                        "payload": result.payload,
                        "payload_sha256": digest,
                        "created_by": self.actor,
                    }
                )
                lineage.append(
                    {
                        "id": uuid.uuid4(),
                        "canonical_record_id": canonical_id,
                        "source_record_id": record_id,
                        "mapping_version_id": self.mapping_version_id,
                        "transform_trace": result.trace,
                        "created_by": self.actor,
                    }
                )
                continue
            items.append(
                {
                    "id": uuid.uuid4(),
                    "import_job_id": self.job_id,
                    "batch_id": batch_id,
                    "source_record_id": record_id,
                    "ordinal": result.ordinal,
                    "reason_codes": result.reason_codes,
                    "business_key_sha256": result.key_sha256,
                    "owner": self.owner,
                    "status": (
                        QuarantineStatus.OPEN
                        if result.outcome == RowOutcome.QUARANTINED
                        else QuarantineStatus.REJECTED
                    ).value,
                    "created_by": self.actor,
                }
            )
        return [
            (CanonicalRecord, canonical),
            (RecordLineage, lineage),
            (ValidationIssue, issues),
            (QuarantineItem, items),
        ]

    def dataset_sha256(self) -> str:
        """The accepted payloads' hashes in file order, hashed: the dataset's identity (the
        manifest's `canonical_sha256`)."""
        return sha256_hex("\n".join(self.hashes).encode())


class ImportValidation(ImportProfiling):
    """Validating an import and writing its manifest (BE-11.3)."""

    async def validate(self, import_id: uuid.UUID, tolerance: Decimal = Decimal(0)) -> ImportJob:
        """Validate an import once (D-21), in one transaction, audited `import.validate`:
        pin the usable ACTIVE mapping version (D-18 §5), move it VALIDATING, give every row
        exactly one outcome (canonical record, OPEN or REJECTED quarantine item, D-20 §1),
        store the issues and the manifest, move it to its outcome status, reconcile it
        (BE-12.2, `tolerance` = RECONCILIATION_TOLERANCE), and resolve the quarantine items
        of the import it retries (D-20 §2). Refusals are audited DENIED."""
        job = await self.repo.lock(import_id)
        if job is None:
            await self._refuse(
                ApiError(404, "not_found", "Import not found."), import_id, VALIDATE_IMPORT
            )
        status = ImportStatus(job.status)
        if status in VALIDATED_STATUSES or await self.repo.manifest(job.id) is not None:
            await self._refuse(
                ApiError(
                    409,
                    "already_validated",
                    "This import was validated already; a re-run is a new import (D-21).",
                    {"status": status.value},
                ),
                job.id,
                VALIDATE_IMPORT,
            )
        if status not in (ImportStatus.RECEIVED, ImportStatus.MAPPING_REQUIRED):
            await self._refuse(
                ApiError(
                    409,
                    "invalid_status",
                    f"Only a RECEIVED or MAPPING_REQUIRED import can be validated; this one is "
                    f"{status}.",
                    {"status": status.value},
                ),
                job.id,
                VALIDATE_IMPORT,
            )
        from app.mapping.service import MappingService  # mapping depends on ingestion

        usable = await MappingService(self.session, self.principal).usable_mapping(
            job.source_contract_id, job.schema_version_id
        )
        if usable is None:
            await self._refuse(
                ApiError(
                    409,
                    "mapping_required",
                    "No ACTIVE mapping version maps this import's contract and schema version; "
                    "one must be approved first (D-18 §4).",
                    {"status": status.value},
                ),
                job.id,
                VALIDATE_IMPORT,
            )
        profile = await self.repo.default_profile(DEFAULT_VALIDATION_PROFILE_CODE)
        if profile is None:
            await self._refuse(
                ApiError(409, "validation_profile_missing", "No validation profile exists."),
                job.id,
                VALIDATE_IMPORT,
            )
        mapping, mapping_version = usable
        source = await self.sources.get(job.source_system_id)
        contract = next(c for c in profile_of(source).contracts if c.id == job.source_contract_id)
        schema = await self.schemas.get_schema(contract.schema_id)
        schema_version = next(v for v in schema.versions if v.id == job.schema_version_id)
        fields, units = target_fields_of(schema_version)
        validator = Validator(
            [MappedRule.of(r) for r in mapping_version.rules],
            fields,
            units,
            list(dict.fromkeys(c for batch in job.batches for c in batch.columns)),
            contract.business_key_columns,
            profile_entries(profile.rules),
        )

        move(job, ImportStatus.VALIDATING)
        job.mapping_version_id = mapping_version.id  # written once (DB trigger, D-18 §5)
        await self.repo.flush()
        writer = _ResultWriter(job, mapping_version.id, profile.id, self.principal.actor)
        writer.owner = profile_of(source).owner  # the source's data owner holds its items
        await self.repo.insert_rows(ValidationIssue, [writer.issue(f) for f in validator.findings])
        after: tuple[int, int] | None = None
        while page := await self.repo.record_page(job.id, after, VALIDATE_PAGE_ROWS):
            after = (page[-1][2], page[-1][3])
            results = await run_in_threadpool(
                validator.add_all, [(r[3], r[4], r[5], r[6]) for r in page]
            )
            rows = writer.add(page, results)
            for model, values in rows:
                await self.repo.insert_rows(model, values)
        final = validator.status()
        move(job, final)
        if final == ImportStatus.FAILED:  # BE-34.4: by each file-level blocker code
            metrics.count_import_failure("validation", validator.blocked)
        await self.repo.flush()

        artifact = job.artifact
        manifest: dict[str, Any] = {
            "manifest_version": 1,
            "import_id": str(job.id),
            "status": final.value,
            "source_id": str(source.id),
            "source_code": source.code,
            "source_profile_id": str(profile_of(source).id),
            "source_contract_id": str(contract.id),
            "source_contract_version": contract.version,
            "source_contract_sha256": contract.content_sha256,
            "schema_id": str(schema.id),
            "schema_code": schema.code,
            "schema_version_id": str(schema_version.id),
            "schema_version": schema_version.version,
            "schema_sha256": schema_version.content_sha256,
            "mapping_id": str(mapping.id),
            "mapping_code": mapping.code,
            "mapping_version_id": str(mapping_version.id),
            "mapping_version": mapping_version.version,
            "mapping_sha256": mapping_version.content_sha256,
            "validation_profile": {
                "id": str(profile.id),
                "code": profile.code,
                "version": profile.version,
                "content_sha256": profile.content_sha256,
                "provisional": profile.provisional,
            },
            "validator_version": VALIDATOR_VERSION,
            "artifact": {
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "classification": artifact.classification,
            },
            "quality": {
                "row_count": job.row_count,
                "accepted_records": validator.accepted,
                "quarantined_records": validator.quarantined,
                "rejected_records": validator.rejected,
                "warnings": validator.warnings,
            },
            "canonical_sha256": writer.dataset_sha256(),
        }
        manifest_sha = sha256_for(HashKind.IMPORT_MANIFEST, manifest)
        self.repo.add(
            ImportManifest(
                import_job_id=job.id,
                validation_profile_id=profile.id,
                mapping_version_id=mapping_version.id,
                schema_version_id=schema_version.id,
                row_count=job.row_count,
                accepted_count=validator.accepted,
                quarantined_count=validator.quarantined,
                rejected_count=validator.rejected,
                warning_count=validator.warnings,
                manifest=manifest,
                manifest_sha256=manifest_sha,
                created_by=self.principal.actor,
            )
        )
        await self.repo.flush()
        details: dict[str, Any] = {
            "status": final.value,
            "mapping_version_id": str(mapping_version.id),
            "validation_profile_id": str(profile.id),
            "row_count": job.row_count,
            "accepted": validator.accepted,
            "quarantined": validator.quarantined,
            "rejected": validator.rejected,
            "warnings": validator.warnings,
            "manifest_sha256": manifest_sha,
            "retry_of_import_id": str(job.retry_of_import_id) if job.retry_of_import_id else None,
        }
        await self._reconcile(job, contract, mapping_version, units, artifact, tolerance)
        audit_by(
            self.session,
            self.principal,
            VALIDATE_IMPORT,
            "import_job",
            resource_id=str(job.id),
            details=details,
            details_classification=_public(*details),
        )
        if job.retry_of_import_id is not None:
            await QuarantineService(self.session, self.principal).resolve_retry(
                job.id, job.retry_of_import_id, writer.present_keys, writer.accepted_keys
            )
        await self.session.commit()
        return await self.get(job.id)

    async def _reconcile(
        self,
        job: ImportJob,
        contract: SourceContract,
        mapping_version: Any,
        units: Mapping[str, str | None],
        artifact: ImportArtifact,
        tolerance: Decimal,
    ) -> None:
        """BE-12.2: reconcile what this validation stored, read back from the database
        (source rows against canonical records and quarantine items), in its transaction.
        A control total is labelled with its mapped field's unit and masked by the more
        sensitive of the column's classification (BE-9.3) and the artifact's (as the
        declared totals are on the import)."""
        from app.reconciliation.service import (  # reconciliation depends on ingestion
            ColumnFigures,
            ColumnMeta,
            Declared,
            ImportFigures,
            ReconciliationService,
        )

        columns = list(contract.control_total_columns)
        rows, accepted, held, totals = await self.repo.reconciliation_figures(job.id, columns)
        mapped = await self.mapped_classifications(job)
        source = await self.sources.get(job.source_system_id)
        default = profile_of(source).default_classification
        targets = {
            r.source_column: r.target_field
            for r in mapping_version.rules
            if r.column_policy == "MAP" and r.source_column is not None
        }
        await ReconciliationService(self.session, self.principal).record(
            job,
            ImportFigures(rows, accepted, held, {c: ColumnFigures(*totals[c]) for c in columns}),
            Declared(job.expected_row_count, dict(job.control_totals or {})),
            {
                c: ColumnMeta(
                    units.get(targets.get(c) or ""),
                    most_sensitive([mapped.get(c, default), artifact.classification]),
                )
                for c in columns
            },
            tolerance,
        )
