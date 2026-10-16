"""mapping.models

Versioned field mappings (BE-10.1, DI-03, D-18): `field_mapping` → `mapping_version` →
`mapping_rule`, plus the stored dry-run results, `mapping_validation`. Only
`field_mapping` carries `tenant_id`; versions, rules and validations are reached through a
scoped mapping (D-04), as schema versions are through their definition.

Database guards (migration 0014), none of them unlocked by any session setting:
- a mapping is never deleted and its content never changes; it stays in its tenant, with a
  contract of its own source and that contract's target schema;
- a version is never deleted; once it leaves DRAFT its content is frozen, its decision is
  written once, and its status moves only along MAPPING_VERSION_TRANSITIONS; at most one
  version per mapping is ACTIVE; it pins a version of the mapping's schema;
- a rule may be inserted, changed or deleted only while its version is DRAFT;
- a validation is append-only;
- TRUNCATE is refused on all four tables.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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
from app.mapping.constants import (
    ColumnPolicy,
    MappingVersionStatus,
    NullPolicy,
    TransformCode,
    ValidationOutcome,
)

SHA256 = "~ '^[0-9a-f]{64}$'"


def _in(column: str, values: type[StrEnum]) -> str:
    """CHECK expression limiting a column to an enum's values."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class FieldMapping(ScopedModel, Base):
    """How one source contract feeds its target canonical schema. One mapping per contract
    (unique), so an import's mapping is never ambiguous (D-18 §1)."""

    __tablename__ = "field_mapping"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    # The contract's source, kept here so lists filter without reaching into ingestion.
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), index=True, nullable=False
    )
    source_contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_contract.id"), unique=True, nullable=False
    )
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_definition.id"), index=True, nullable=False
    )

    versions: Mapped[list["MappingVersion"]] = relationship(
        lazy="selectin", order_by="MappingVersion.version"
    )


class MappingVersion(AuditColumns, Base):
    """One immutable-once-submitted version of a mapping, pinned to one schema version
    (D-18 §1-2). `content_sha256` hashes the pinned schema version and the rules."""

    __tablename__ = "mapping_version"
    __table_args__ = (
        UniqueConstraint("field_mapping_id", "version"),
        Index(
            "uq_mapping_version_active",
            "field_mapping_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        CheckConstraint(_in("status", MappingVersionStatus), name="status"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(f"content_sha256 {SHA256}", name="content_sha256"),
        # A decision is all or nothing.
        CheckConstraint(
            "(decided_at IS NULL) = (decided_by IS NULL)"
            " AND (decided_at IS NULL) = (decided_by_user_id IS NULL)",
            name="decision",
        ),
    )

    field_mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field_mapping.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(1000))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(String(2000))
    # The latest dry run of this version while it was DRAFT (frozen with the version).
    last_validation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_validation.id", use_alter=True)
    )

    rules: Mapped[list["MappingRule"]] = relationship(
        lazy="selectin", order_by="MappingRule.position"
    )
    last_validation: Mapped["MappingValidation | None"] = relationship(
        foreign_keys=[last_validation_id], lazy="selectin", viewonly=True
    )


class MappingRule(AuditColumns, Base):
    """Source column → target field through one allowlisted transform (D-19), with a null
    policy, or a source column PRESERVEd unmapped (never dropped)."""

    __tablename__ = "mapping_rule"
    __table_args__ = (
        UniqueConstraint("mapping_version_id", "position"),
        Index(
            "uq_mapping_rule_target",
            "mapping_version_id",
            "target_field",
            unique=True,
            postgresql_where=text("target_field IS NOT NULL"),
        ),
        CheckConstraint("position >= 1", name="position"),
        CheckConstraint(_in("transform_code", TransformCode), name="transform_code"),
        CheckConstraint(_in("null_policy", NullPolicy), name="null_policy"),
        CheckConstraint(_in("column_policy", ColumnPolicy), name="column_policy"),
        CheckConstraint("jsonb_typeof(transform_params) = 'object'", name="transform_params"),
        # MAP names a target; PRESERVE keeps a source column as it is, with no target.
        CheckConstraint(
            "(column_policy = 'MAP') = (target_field IS NOT NULL)", name="policy_target"
        ),
        CheckConstraint(
            "column_policy = 'MAP' OR (source_column IS NOT NULL AND transform_code = 'COPY')",
            name="preserve",
        ),
        # Only CONSTANT reads no column.
        CheckConstraint(
            "(source_column IS NULL) = (transform_code = 'CONSTANT')", name="source_column"
        ),
        CheckConstraint(
            "(null_policy = 'USE_DEFAULT') = (default_value IS NOT NULL)", name="default_value"
        ),
    )

    mapping_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_version.id"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_column: Mapped[str | None] = mapped_column(String(200))
    target_field: Mapped[str | None] = mapped_column(String(100))
    transform_code: Mapped[str] = mapped_column(String(30), nullable=False)
    transform_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    null_policy: Mapped[str] = mapped_column(String(20), nullable=False)
    default_value: Mapped[str | None] = mapped_column(String(200))
    column_policy: Mapped[str] = mapped_column(String(10), nullable=False)


class MappingValidation(AuditColumns, Base):
    """One dry run of a mapping version against one import (BE-10.4). Append-only. It holds
    counts, codes and names only, never a source value."""

    __tablename__ = "mapping_validation"
    __table_args__ = (
        CheckConstraint(_in("outcome", ValidationOutcome), name="outcome"),
        CheckConstraint(
            "row_count >= 0 AND blocker_count >= 0 AND error_count >= 0 AND warning_count >= 0",
            name="counts",
        ),
        CheckConstraint(
            "(outcome = 'FAILED') = (blocker_count + error_count > 0)", name="outcome_counts"
        ),
        CheckConstraint(f"mapping_sha256 {SHA256}", name="mapping_sha256"),
        CheckConstraint("jsonb_typeof(issues) = 'array'", name="issues"),
        CheckConstraint("jsonb_typeof(rule_results) = 'array'", name="rule_results"),
    )

    mapping_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mapping_version.id"), index=True, nullable=False
    )
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), index=True, nullable=False
    )
    # The version's content hash when validated: a validation proves only that content.
    mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blocker_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
