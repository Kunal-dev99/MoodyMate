"""governance.constants

Permission and role codes, statuses, and the seed role → permission matrix (BE-2.2).
The seed is PROVISIONAL (D-11) until the client approves the role model (Arch §10).
"""

import uuid
from enum import StrEnum


class Permission(StrEnum):
    SCHEMA_READ = "schema:read"
    SCHEMA_PROPOSE = "schema:propose"
    SCHEMA_APPROVE = "schema:approve"
    AUDIT_READ = "audit:read"
    ADMIN_READ = "admin:read"


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    DATA_STEWARD = "data_steward"
    RISK_ANALYST = "risk_analyst"
    APPROVER = "approver"
    AUDITOR = "auditor"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


# docs/DELIVERY_PLAN.md "Role → permission matrix (seed, BE-2.2)". platform_admin
# deliberately lacks schema:approve (segregation of duties). The seed migration holds a
# frozen copy; test_identity_seed asserts the database matches this table.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(
        {Permission.SCHEMA_READ, Permission.AUDIT_READ, Permission.ADMIN_READ}
    ),
    Role.DATA_STEWARD: frozenset({Permission.SCHEMA_READ, Permission.SCHEMA_PROPOSE}),
    Role.RISK_ANALYST: frozenset({Permission.SCHEMA_READ}),
    Role.APPROVER: frozenset({Permission.SCHEMA_READ, Permission.SCHEMA_APPROVE}),
    Role.AUDITOR: frozenset({Permission.SCHEMA_READ, Permission.AUDIT_READ}),
}

DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
SEED_TAG = "0003_identity_seed"

DEV_ISSUER = "moodymate-dev"
DEV_AUDIENCE = "moodymate-api"
