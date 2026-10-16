"""governance.schemas

Request/response models for identity endpoints.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.core.classification import Classification, MaskedValue
from app.governance.constants import (
    ClassificationCode,
    Compatibility,
    DependentType,
    FieldType,
    Permission,
    Role,
    SchemaVersionStatus,
)


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
    email: Annotated[str | None | MaskedValue, Classification(ClassificationCode.CONFIDENTIAL)]
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


# --- Schema registry (Phase 4) -----------------------------------------------------------


class SchemaVersionRef(BaseModel):
    id: uuid.UUID
    version: int
    status: SchemaVersionStatus
    effective_from: date | None


class SchemaDependent(BaseModel):
    dependent_type: DependentType
    dependent_ref: str
    dependent_schema_id: uuid.UUID | None
    description: str | None
    provisional: bool


class SchemaSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    owner: str
    provisional: bool = Field(description="True for seeded, not yet client-approved schemas.")
    status: SchemaVersionStatus = Field(
        description="Status of the effective version, or of the latest one if none is in effect."
    )
    effective_version: SchemaVersionRef | None
    latest_version: SchemaVersionRef
    version_count: int
    dependents: list[SchemaDependent]


class SchemaList(BaseModel):
    items: list[SchemaSummary]
    total: int


class SchemaFieldOut(BaseModel):
    position: int
    name: str
    data_type: FieldType
    required: bool
    unit: str | None
    precision: int | None
    scale: int | None
    classification: str = Field(description="Sensitivity of values in this field (BE-3.4).")
    extension_eligible: bool
    description: str | None


class SchemaVersionOut(SchemaVersionRef):
    compatibility: Compatibility | None = Field(
        description="Severity of the change from the previous version (null for version 1)."
    )
    content_sha256: str
    change_summary: str | None
    created_at: datetime
    fields: list[SchemaFieldOut]


class SchemaVersionList(BaseModel):
    schema_: SchemaSummary = Field(alias="schema")
    items: list[SchemaVersionOut]

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class SchemaFieldChange(BaseModel):
    field: str
    change: Literal["ADDED", "REMOVED", "MODIFIED"]
    attribute: str | None = Field(description="The changed attribute, for MODIFIED.")
    from_value: Any
    to_value: Any
    compatibility: Compatibility
    reason: str


class SchemaDiffOut(BaseModel):
    schema_id: uuid.UUID
    from_version: SchemaVersionRef
    to_version: SchemaVersionRef
    overall: Compatibility | None = Field(description="Most severe change; null if identical.")
    changes: list[SchemaFieldChange]
