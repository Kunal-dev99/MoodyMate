"""Declarative base and shared columns (BE-1.6).

* `Base` carries a constraint naming convention so Alembic autogenerate produces stable,
  reversible names (D-02).
* `AuditColumns` gives every table its id and created/updated metadata.
* `TenantScoped` adds `tenant_id` for row scoping (D-04, D-11).
* `ScopedModel` is the combination that `ScopedRepository` works with.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AuditColumns:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Principal identifier (user or service). Not a FK: service identities and
    # pre-identity bootstrap rows must still be attributable.
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class TenantScoped:
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=False)


class ScopedModel(AuditColumns, TenantScoped):
    """Mixin for tenant-scoped entities read through `ScopedRepository`."""
