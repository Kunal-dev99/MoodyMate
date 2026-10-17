"""ingestion.models

Source registration tables (BE-7.1, DI-01, Data Model §2): `source_system` →
`source_profile` → `source_contract`. Import tables (BE-8.1, DI-02, Data Model §3):
`import_job` → `import_artifact`, `import_batch` → `source_record`. Only `source_system`
and `import_job` carry `tenant_id`; profiles and contracts are reached through a source,
artifacts, batches and records through an import (D-04), as schema versions are through
their definition.

Nothing here is ever deleted (D-02): DB triggers reject DELETE and TRUNCATE on all three
tables, a contract's content is frozen from creation in every status (D-24 §3), a source
never moves tenant and a profile never moves source (migration 0009).

Imports (migration 0012): `import_artifact`, `import_batch` and `source_record` are
append-only; an `import_job` is never deleted, its content is frozen and only its status
moves, along IMPORT_TRANSITIONS. TRUNCATE is refused on all four.

Profiles (migration 0013): `data_profile_snapshot`, one per import, append-only and reached
only through a scoped import.

Validation (migration 0015, BE-11.1): `validation_profile` (tenant-scoped, versioned),
and per import `validation_issue`, `quarantine_item`, `canonical_record`, `record_lineage`
and `import_manifest`, all reached through a scoped import. All are append-only except
`quarantine_item`, whose status alone moves (along QUARANTINE_TRANSITIONS) and whose
`resolved_by_import_id` is written once, with RESOLVED. TRUNCATE is refused on all six.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditColumns, Base, ScopedModel
from app.ingestion.constants import (
    ContractStatus,
    DataClass,
    ImportStatus,
    IssueDisposition,
    QuarantineStatus,
    RetentionState,
    SourceEncoding,
    SourceFormat,
    SourceKind,
    ValidationSeverity,
)


def _in(column: str, values: type[StrEnum]) -> str:
    """CHECK expression limiting a column to an enum's values."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class SourceSystem(ScopedModel, Base):
    """Where data comes from: a file drop, warehouse, application or feed (D-23)."""

    __tablename__ = "source_system"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(_in("kind", SourceKind), name="kind"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    seeded_by: Mapped[str | None] = mapped_column(String(100))

    profiles: Mapped[list["SourceProfile"]] = relationship(
        lazy="selectin", order_by="(SourceProfile.created_at, SourceProfile.id)"
    )


class SourceProfile(AuditColumns, Base):
    """How the source delivers: format, encoding, delimiter, sheet, cadence, owner and the
    classification of values that no mapping has classified (D-20)."""

    __tablename__ = "source_profile"
    __table_args__ = (
        CheckConstraint(_in("format", SourceFormat), name="format"),
        CheckConstraint(_in("encoding", SourceEncoding), name="encoding"),
        # chr(9): a tab. A '\t' literal would be backslash-t in standard SQL strings.
        CheckConstraint(
            "delimiter IS NULL OR delimiter IN (',', ';', '|', chr(9))", name="delimiter"
        ),
        # A delimiter belongs to CSV and is required there; a sheet name only to XLSX.
        CheckConstraint("(format = 'CSV') = (delimiter IS NOT NULL)", name="delimiter_format"),
        CheckConstraint("format = 'XLSX' OR sheet_name IS NULL", name="sheet_name_format"),
    )

    # One profile per source (Phase 7 review, deviation 4): unique.
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), index=True, unique=True, nullable=False
    )
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    encoding: Mapped[str] = mapped_column(String(20), nullable=False)
    delimiter: Mapped[str | None] = mapped_column(String(1))
    sheet_name: Mapped[str | None] = mapped_column(String(31))
    cadence: Mapped[str | None] = mapped_column(String(100))
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    default_classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))

    contracts: Mapped[list["SourceContract"]] = relationship(
        lazy="selectin", order_by="SourceContract.version"
    )


class SourceContract(AuditColumns, Base):
    """The expected shape of a source's files, targeting one canonical schema.

    Versioned: a new version supersedes the previous one, which is never edited. Content
    (target schema, expected/business-key/control-total columns, hash, lineage, creator) is
    frozen from creation in every status (D-24 §3); only `status` and `updated_at` change,
    along CONTRACT_TRANSITIONS, and SUPERSEDED is final (DB trigger, 0009). At most one
    contract per profile is not SUPERSEDED (partial unique index).
    """

    __tablename__ = "source_contract"
    __table_args__ = (
        UniqueConstraint("source_profile_id", "version"),
        Index(
            "uq_source_contract_current",
            "source_profile_id",
            unique=True,
            postgresql_where=text("status <> 'SUPERSEDED'"),
        ),
        CheckConstraint(_in("status", ContractStatus), name="status"),
        CheckConstraint("version >= 1", name="version"),
    )

    source_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_profile.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_definition.id"), index=True, nullable=False
    )
    # [{"name": str, "required": bool}], in file order.
    expected_columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    business_key_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    control_total_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_contract.id")
    )
    change_summary: Mapped[str | None] = mapped_column(String(1000))
    seeded_by: Mapped[str | None] = mapped_column(String(100))


# --- Phase 8: imports (BE-8.1) ----------------------------------------------------------------


class ImportJob(ScopedModel, Base):
    """One upload of one file for one source (D-21: processed synchronously).

    Pins the source contract and the ACTIVE schema version at creation. `idempotency_key`
    is unique per tenant and actor (D-21); `request_sha256` covers what a replay must
    repeat (source, file bytes and declared figures). `data_class` and
    `synthetic_attested` record D-16 §5; `synthetic_marker` records whether the file
    carried the ILLUSTRATIVE / SYNTHETIC marker (D-24 §2).
    """

    __tablename__ = "import_job"
    __table_args__ = (
        UniqueConstraint("tenant_id", "created_by", "idempotency_key"),
        CheckConstraint(_in("status", ImportStatus), name="status"),
        CheckConstraint(_in("data_class", DataClass), name="data_class"),
        CheckConstraint(_in("format", SourceFormat), name="format"),
        # D-16 §5: a synthetic upload is always attested.
        CheckConstraint(
            "data_class <> 'synthetic' OR synthetic_attested", name="synthetic_attested"
        ),
        CheckConstraint("row_count >= 0", name="row_count"),
        CheckConstraint(
            "expected_row_count IS NULL OR expected_row_count >= 0", name="expected_row_count"
        ),
        Index("ix_import_job_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), index=True, nullable=False
    )
    source_contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_contract.id"), nullable=False
    )
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    data_class: Mapped[str] = mapped_column(String(20), nullable=False)
    synthetic_attested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synthetic_marker: Mapped[bool] = mapped_column(Boolean, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    retry_of_import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id")
    )
    expected_row_count: Mapped[int | None] = mapped_column(Integer)
    # {column: decimal string}, declared by the uploader for reconciliation (Phase 12).
    control_totals: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # D-18 §5 (migration 0014, a nullable column, D-01): the mapping version the import is
    # validated with. Written once (NULL -> a version, then frozen) by a DB trigger, which
    # also checks that the version maps this import's contract and schema version.
    mapping_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_version.id")
    )

    artifact: Mapped["ImportArtifact"] = relationship(lazy="selectin", uselist=False)
    batches: Mapped[list["ImportBatch"]] = relationship(
        lazy="selectin", order_by="ImportBatch.batch_number"
    )


class ImportArtifact(AuditColumns, Base):
    """The raw uploaded file (D-15): its content-addressed ref in the artifact store, hash,
    size, content-detected MIME type, bounded original name, classification (the source
    profile's default) and retention state (D-20). Append-only."""

    __tablename__ = "import_artifact"
    __table_args__ = (
        CheckConstraint(_in("retention_state", RetentionState), name="retention_state"),
        CheckConstraint("size_bytes >= 0", name="size_bytes"),
        CheckConstraint(
            "storage_ref = 'sha256/' || substr(sha256, 1, 2) || '/' || sha256", name="storage_ref"
        ),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), unique=True, index=True, nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_mime: Mapped[str] = mapped_column(String(100), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    retention_state: Mapped[str] = mapped_column(String(20), nullable=False)


class ImportBatch(AuditColumns, Base):
    """A logical part of an import with its row count. Phase 8 writes one per import (the
    file, or the one XLSX sheet read). `columns` is the header, in file order."""

    __tablename__ = "import_batch"
    __table_args__ = (
        UniqueConstraint("import_job_id", "batch_number"),
        CheckConstraint("batch_number >= 1", name="batch_number"),
        CheckConstraint("row_count >= 0", name="row_count"),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), nullable=False
    )
    batch_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(31))
    columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceRecord(AuditColumns, Base):
    """One source row, as read: never edited (D-20 §3), never deleted. `raw_values` maps
    column -> cell text (strings only, never floats); cells beyond the header are kept in
    `extra_values`; `flags` are the adapter's row facts ([{code, column}]); `row_sha256`
    hashes the values and extras."""

    __tablename__ = "source_record"
    __table_args__ = (
        UniqueConstraint("batch_id", "ordinal"),
        CheckConstraint("ordinal >= 1", name="ordinal"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_batch.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # {"line": n} (CSV) | {"sheet": name, "row": n} (XLSX) | {"index": i} (JSON)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_values: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    extra_values: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    flags: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    row_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


# --- Phase 9: field profiling (BE-9.1) ------------------------------------------------------


class DataProfileSnapshot(AuditColumns, Base):
    """The profile of one import (DI-06): deterministic per-column observations of its
    source records, as the profiler (`PROFILER_VERSION`) computed them. Append-only (DB
    trigger, TRUNCATE included, migration 0013); reached only through a scoped import.

    `summary` holds row-level facts (blank rows, rows with missing or extra cells, flag
    counts); `columns` one observation object per header column, in file order. Counts,
    rates, lengths, precision and scale are integers or decimal text, never floats.
    `content_sha256` hashes the profiler version, summary and columns (not ids or times),
    so the same records always give the same hash. Profiling never changes a schema or a
    contract: suggestions are computed on read (BE-9.3)."""

    __tablename__ = "data_profile_snapshot"
    __table_args__ = (
        CheckConstraint("profiler_version >= 1", name="profiler_version"),
        CheckConstraint("row_count >= 0", name="row_count"),
        # One observation per column. CASE: jsonb_array_length raises on a non-array, which
        # the `columns` CHECK refuses on its own.
        CheckConstraint(
            "CASE WHEN jsonb_typeof(columns) = 'array'"
            " THEN column_count = jsonb_array_length(columns) ELSE true END",
            name="column_count",
        ),
        CheckConstraint("jsonb_typeof(columns) = 'array'", name="columns"),
        CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), unique=True, index=True, nullable=False
    )
    profiler_version: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


# --- Phase 11: validation, quarantine and canonical records (BE-11.1) ----------------------

SHA256_CHECK = "~ '^[0-9a-f]{64}$'"


class ValidationProfile(ScopedModel, Base):
    """A versioned, immutable set of checks: issue code -> {severity, disposition}
    (append-only, DB trigger). The default one is seeded PROVISIONAL (D-11)."""

    __tablename__ = "validation_profile"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("jsonb_typeof(rules) = 'object'", name="rules"),
        CheckConstraint(f"content_sha256 {SHA256_CHECK}", name="content_sha256"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    # {code: {"severity": ValidationSeverity, "disposition": IssueDisposition}}
    rules: Mapped[dict[str, dict[str, str]]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class ValidationIssue(AuditColumns, Base):
    """One finding of an import's validation. Names a row (`source_record_id`, `ordinal`;
    none for a file-level issue), a schema field and/or a source column and the rule (mapping
    rule position, profile), never a value. Append-only."""

    __tablename__ = "validation_issue"
    __table_args__ = (
        UniqueConstraint("import_job_id", "sequence"),
        CheckConstraint(_in("severity", ValidationSeverity), name="severity"),
        CheckConstraint(_in("disposition", IssueDisposition), name="disposition"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("(source_record_id IS NULL) = (ordinal IS NULL)", name="row_ref"),
        # A file-level issue blocks the file or warns; it never quarantines one row.
        CheckConstraint(
            "source_record_id IS NOT NULL OR disposition IN ('FILE_BLOCKED',"
            " 'ACCEPTED_WITH_WARNING')",
            name="file_level",
        ),
        Index("ix_validation_issue_import_code", "import_job_id", "code"),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_record.id"), index=True
    )
    ordinal: Mapped[int | None] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    disposition: Mapped[str] = mapped_column(String(30), nullable=False)
    field: Mapped[str | None] = mapped_column(String(100))
    source_column: Mapped[str | None] = mapped_column(String(200))
    rule_position: Mapped[int | None] = mapped_column(Integer)
    validation_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("validation_profile.id"), nullable=False
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)


class QuarantineItem(AuditColumns, Base):
    """A held (OPEN) or rejected (REJECTED) source row (D-20). Its reference, reasons and
    owner are frozen; only `status` moves, along QUARANTINE_TRANSITIONS, and
    `resolved_by_import_id` is written once with RESOLVED (DB trigger). `audit_event` is its
    disposition history. `business_key_sha256` matches it to a retry's rows without keeping
    the key's values here."""

    __tablename__ = "quarantine_item"
    __table_args__ = (
        CheckConstraint(_in("status", QuarantineStatus), name="status"),
        CheckConstraint("jsonb_typeof(reason_codes) = 'array'", name="reason_codes"),
        CheckConstraint(
            f"business_key_sha256 IS NULL OR business_key_sha256 {SHA256_CHECK}",
            name="business_key_sha256",
        ),
        CheckConstraint(
            "(resolved_by_import_id IS NULL) = (status <> 'RESOLVED')", name="resolution"
        ),
        Index("ix_quarantine_item_batch_status", "batch_id", "status"),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), index=True, nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_batch.id"), nullable=False
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_record.id"), unique=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    business_key_sha256: Mapped[str | None] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    resolved_by_import_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id")
    )


class CanonicalRecord(AuditColumns, Base):
    """An accepted row under the import's pinned schema and mapping versions (D-22).
    `payload` is keyed by schema field name, typed strings only (decimals at the field's
    scale, dates ISO-8601, never JSON numbers); `payload_sha256` hashes it. Append-only."""

    __tablename__ = "canonical_record"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload"),
        CheckConstraint(f"payload_sha256 {SHA256_CHECK}", name="payload_sha256"),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), index=True, nullable=False
    )
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    mapping_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_version.id"), nullable=False
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_record.id"), unique=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class RecordLineage(AuditColumns, Base):
    """Canonical record -> its source record, the mapping version and the transforms that
    made each field ([{field, source_column, rule_position, transform}], no values).
    Append-only."""

    __tablename__ = "record_lineage"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(transform_trace) = 'array'", name="transform_trace"),
    )

    canonical_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_record.id"), unique=True, nullable=False
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_record.id"), index=True, nullable=False
    )
    mapping_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_version.id"), nullable=False
    )
    transform_trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)


class ImportManifest(AuditColumns, Base):
    """What one validation pinned and produced (BE-11.5, Data Model §10): written once, with
    the final status, and append-only, so it is what a calculation run pins (D-22). The
    counts always add up to the import's rows (CHECK)."""

    __tablename__ = "import_manifest"
    __table_args__ = (
        CheckConstraint(
            "row_count = accepted_count + quarantined_count + rejected_count", name="conservation"
        ),
        CheckConstraint(
            "accepted_count >= 0 AND quarantined_count >= 0 AND rejected_count >= 0"
            " AND warning_count >= 0",
            name="counts",
        ),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest"),
        CheckConstraint(f"manifest_sha256 {SHA256_CHECK}", name="manifest_sha256"),
    )

    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), unique=True, index=True, nullable=False
    )
    validation_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("validation_profile.id"), nullable=False
    )
    mapping_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_version.id"), nullable=False
    )
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
