"""ingestion.models

Source registration tables (BE-7.1, DI-01, Data Model §2): `source_system` →
`source_profile` → `source_contract`. Only `source_system` carries `tenant_id`; profiles and
contracts are reached through it (D-04), as schema versions are through their definition.

Nothing here is ever deleted (D-02): DB triggers reject DELETE and TRUNCATE on all three
tables, and a contract's content is frozen once it is not PROVISIONAL (migration 0009).
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
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

    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), index=True, nullable=False
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
    (target schema, expected/business-key/control-total columns, hash, lineage) is frozen
    once the contract is not PROVISIONAL, and SUPERSEDED is final (DB trigger, 0009). At
    most one contract per profile is not SUPERSEDED (partial unique index).
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
