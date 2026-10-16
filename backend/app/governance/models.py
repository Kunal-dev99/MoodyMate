"""governance.models

Identity and authorization tables (BE-2.1, Data Model §3.1). No passwords are stored:
users are identity-provider subjects (Arch §10, D-09).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditColumns, Base, ScopedModel
from app.governance.constants import TenantStatus, UserStatus


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
