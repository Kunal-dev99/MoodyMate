"""The authenticated caller, as resolved server-side for one request (D-04)."""

import uuid
from dataclasses import dataclass

from app.db.repository import DataScope


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    issuer: str
    subject: str
    display_name: str
    email: str | None
    roles: frozenset[str]
    permissions: frozenset[str]
    # Classification codes this principal may see unmasked (BE-3.5).
    clear_classifications: frozenset[str] = frozenset({"public"})

    @property
    def scope(self) -> DataScope:
        """Row scope for repositories. Derived from identity, never from request input."""
        return DataScope(tenant_id=self.tenant_id)

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}"
