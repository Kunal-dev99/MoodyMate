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
    RetentionState,
    SourceEncoding,
    SourceFormat,
    SourceKind,
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
