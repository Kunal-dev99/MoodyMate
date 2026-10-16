"""governance.models

Identity and authorization tables (BE-2.1, Data Model §3.1). No passwords are stored:
users are identity-provider subjects (Arch §10, D-09).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditColumns, Base, ScopedModel
from app.governance.constants import (
    Compatibility,
    DependentType,
    FieldType,
    ProposalStatus,
    SchemaVersionStatus,
    TenantStatus,
    UserStatus,
)


class Tenant(AuditColumns, Base):
    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TenantStatus.ACTIVE)
    # D-11: set on rows inserted by a seed migration so its downgrade removes only those.
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class PermissionModel(AuditColumns, Base):
    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class RoleModel(AuditColumns, Base):
    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    seeded_by: Mapped[str | None] = mapped_column(String(100))

    permissions: Mapped[list[PermissionModel]] = relationship(
        secondary="role_permission", lazy="selectin", order_by=PermissionModel.code
    )


class UserAccount(ScopedModel, Base):
    __tablename__ = "user_account"
    __table_args__ = (UniqueConstraint("idp_issuer", "idp_subject"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    idp_issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    idp_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=UserStatus.ACTIVE)

    roles: Mapped[list[RoleModel]] = relationship(
        secondary="user_role", lazy="selectin", order_by=RoleModel.code
    )


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_account.id"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("role.id"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("role.id"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permission.id"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class AuditEvent(Base):
    """Append-only record of a privileged action (BE-3.1, D-10).

    A DB trigger rejects UPDATE, DELETE and TRUNCATE. `tenant_id` and `actor_user_id` are
    deliberately not foreign keys: the audit trail must outlive and never block changes
    to the rows it describes.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_audit_event_tenant_resource", "tenant_id", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    approval_ref: Mapped[str | None] = mapped_column(String(255))
    # Safe metadata, plus the classification of each key (masked on read, BE-3.5).
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    details_classification: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class DataClassification(AuditColumns, Base):
    """PROVISIONAL classification levels (BE-3.4, D-11). Higher rank = more sensitive."""

    __tablename__ = "data_classification"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class AccessPolicy(AuditColumns, Base):
    """Per role: the most sensitive classification its holders see unmasked (BE-3.4)."""

    __tablename__ = "access_policy"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("role.id"), nullable=False, unique=True
    )
    max_clear_classification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_classification.id"), nullable=False
    )
    seeded_by: Mapped[str | None] = mapped_column(String(100))


def _in(column: str, values: type[StrEnum]) -> str:
    """CHECK expression limiting a column to an enum's values."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class SchemaDefinition(ScopedModel, Base):
    """A logical contract such as instrument or position (BE-4.1, Data Model §2)."""

    __tablename__ = "schema_definition"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))

    versions: Mapped[list["SchemaVersion"]] = relationship(
        back_populates="schema", lazy="selectin", order_by="SchemaVersion.version"
    )
    dependents: Mapped[list["SchemaDependency"]] = relationship(
        foreign_keys="SchemaDependency.schema_id",
        lazy="selectin",
        order_by="(SchemaDependency.dependent_type, SchemaDependency.dependent_ref)",
    )


class SchemaVersion(AuditColumns, Base):
    """Immutable once it leaves DRAFT: the contract (its fields and content hash) can no
    longer change; only the status moves (enforced by DB triggers, migration 0006)."""

    __tablename__ = "schema_version"
    __table_args__ = (
        UniqueConstraint("schema_id", "version"),
        CheckConstraint(_in("status", SchemaVersionStatus), name="status"),
        CheckConstraint(
            f"compatibility IS NULL OR {_in('compatibility', Compatibility)}", name="compatibility"
        ),
    )

    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_definition.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    # Severity of the change from the previous version (null for version 1).
    compatibility: Mapped[str | None] = mapped_column(String(20))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(1000))
    seeded_by: Mapped[str | None] = mapped_column(String(100))

    schema: Mapped[SchemaDefinition] = relationship(back_populates="versions")
    fields: Mapped[list["SchemaField"]] = relationship(
        lazy="selectin", order_by="SchemaField.position"
    )


class SchemaField(AuditColumns, Base):
    __tablename__ = "schema_field"
    __table_args__ = (
        UniqueConstraint("schema_version_id", "name"),
        CheckConstraint(_in("data_type", FieldType), name="data_type"),
    )

    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50))
    precision: Mapped[int | None] = mapped_column(Integer)
    scale: Mapped[int | None] = mapped_column(Integer)
    # A data_classification code (BE-3.4): the sensitivity of values in this field.
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    extension_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class SchemaDependency(AuditColumns, Base):
    """Something that consumes a schema (Data Model §2: schema_id + dependent ref). Until
    WS2–WS4 exist, dependents are seeded illustrations used by impact analysis."""

    __tablename__ = "schema_dependency"
    __table_args__ = (
        UniqueConstraint("schema_id", "dependent_type", "dependent_ref"),
        CheckConstraint(_in("dependent_type", DependentType), name="dependent_type"),
    )

    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_definition.id"), index=True, nullable=False
    )
    dependent_type: Mapped[str] = mapped_column(String(20), nullable=False)
    dependent_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    # Set when the dependent is itself a registered schema.
    dependent_schema_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_definition.id")
    )
    description: Mapped[str | None] = mapped_column(String(500))
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class SchemaChangeProposal(ScopedModel, Base):
    """A proposed new version of a schema (BE-5.1, Data Model §2). The proposed contract
    lives in a DRAFT schema_version created with the proposal; approval activates it."""

    __tablename__ = "schema_change_proposal"
    __table_args__ = (CheckConstraint(_in("status", ProposalStatus), name="status"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_definition.id"), index=True, nullable=False
    )
    # The version in effect when proposed; approval requires it still to be ACTIVE.
    base_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    proposed_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[str | None] = mapped_column(String(2000))
    proposer_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Impact analysis result (diff, compatibility, affected dependents), when recorded.
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    impact_analysed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    regression_evidence_ref: Mapped[str | None] = mapped_column(String(500))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(String(2000))
    # Rollback reference: the applied proposal this one reverts (BE-5.4).
    reverts_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_change_proposal.id")
    )

    schema: Mapped[SchemaDefinition] = relationship(lazy="joined")
    base_version: Mapped[SchemaVersion] = relationship(
        foreign_keys=[base_version_id], lazy="joined"
    )
    proposed_version: Mapped[SchemaVersion] = relationship(
        foreign_keys=[proposed_version_id], lazy="joined"
    )
