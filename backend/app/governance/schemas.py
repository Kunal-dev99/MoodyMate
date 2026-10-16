"""governance.schemas

Request/response models for identity endpoints.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.classification import MaskedValue
from app.governance.constants import Permission, Role


class OidcClientConfig(BaseModel):
    issuer: str
    client_id: str
    scopes: str


class AuthConfig(BaseModel):
    mode: Literal["oidc", "dev"]
    dev_sessions: bool = Field(description="True when the dev sign-in endpoints are available.")
    oidc: OidcClientConfig | None = None


class Me(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str
    email: str | None
    roles: list[Role]


class MyPermissions(BaseModel):
    permissions: list[Permission]


class UserSummary(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str | None
    status: str
    roles: list[Role]
    created_at: datetime


class UserPage(BaseModel):
    items: list[UserSummary]
    total: int
    limit: int
    offset: int


class DevPersona(BaseModel):
    role: Role
    name: str
    description: str | None
    permissions: list[Permission]


class DevSessionRequest(BaseModel):
    role: str = Field(min_length=1, max_length=100)  # validated against the DB, not the enum


class DevSessionResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - token type, not a secret
    expires_in: int


class AuditEventOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor: str
    actor_user_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    correlation_id: str | None
    approval_ref: str | None
    details: dict[str, MaskedValue | Any] = Field(
        description="Safe metadata. Values the caller may not see are MaskedValue (BE-3.5)."
    )


class AuditEventPage(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int
