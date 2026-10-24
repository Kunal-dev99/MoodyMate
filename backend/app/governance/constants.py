"""governance.constants

Permission and role codes, statuses, and the seed role → permission matrix (BE-2.2).
The seed is PROVISIONAL (D-11) until the client approves the role model (Arch §10).
"""

import uuid
from enum import StrEnum

from app.core.classification import SENSITIVITY


class Permission(StrEnum):
    SCHEMA_READ = "schema:read"
    SCHEMA_PROPOSE = "schema:propose"
    SCHEMA_APPROVE = "schema:approve"
    AUDIT_READ = "audit:read"
    ADMIN_READ = "admin:read"
    # WS2 ingestion (BE-7.2, Feature 02 matrix; seeded by migration 0010).
    SOURCE_READ = "source:read"
    SOURCE_MANAGE = "source:manage"
    IMPORT_READ = "import:read"
    IMPORT_WRITE = "import:write"
    MAPPING_READ = "mapping:read"
    MAPPING_PROPOSE = "mapping:propose"
    MAPPING_APPROVE = "mapping:approve"
    QUARANTINE_READ = "quarantine:read"
    QUALITY_REVIEW = "quality:review"
    # WS3 domain, rules and calculations (BE-14.2, D-34, Feature 03 matrix; seeded by 0017).
    DOMAIN_READ = "domain:read"
    DOMAIN_PROJECT = "domain:project"
    RULE_READ = "rule:read"
    RULE_PROPOSE = "rule:propose"
    RULE_APPROVE = "rule:approve"
    CALC_READ = "calc:read"
    CALC_RUN = "calc:run"
    # Feature 04 credit (BE-20.1, D-40; seeded by the tagged migration 0023_credit_permissions).
    RATING_PROPOSE = "rating:propose"
    RATING_APPROVE = "rating:approve"
    # Feature 05 stress scenarios (BE-24.1, D-47, user-approved 2026-10-19; seeded by the
    # tagged migration 0026_scenario_permissions).
    SCENARIO_PROPOSE = "scenario:propose"
    SCENARIO_APPROVE = "scenario:approve"


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
WS1_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(
        {Permission.SCHEMA_READ, Permission.AUDIT_READ, Permission.ADMIN_READ}
    ),
    Role.DATA_STEWARD: frozenset({Permission.SCHEMA_READ, Permission.SCHEMA_PROPOSE}),
    Role.RISK_ANALYST: frozenset({Permission.SCHEMA_READ}),
    Role.APPROVER: frozenset({Permission.SCHEMA_READ, Permission.SCHEMA_APPROVE}),
    Role.AUDITOR: frozenset({Permission.SCHEMA_READ, Permission.AUDIT_READ}),
}

# docs/DELIVERY_PLAN.md Feature 02 "Role → permission matrix additions (seed, BE-7.2;
# PROVISIONAL, D-11)", seeded by migration 0010 (frozen copy there). platform_admin reads
# WS2 but sees no quarantine; only data_steward writes; only approver activates mappings.
WS2_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(
        {Permission.SOURCE_READ, Permission.IMPORT_READ, Permission.MAPPING_READ}
    ),
    Role.DATA_STEWARD: frozenset(
        {
            Permission.SOURCE_READ,
            Permission.SOURCE_MANAGE,
            Permission.IMPORT_READ,
            Permission.IMPORT_WRITE,
            Permission.MAPPING_READ,
            Permission.MAPPING_PROPOSE,
            Permission.QUARANTINE_READ,
            Permission.QUALITY_REVIEW,
        }
    ),
    Role.RISK_ANALYST: frozenset(
        {Permission.SOURCE_READ, Permission.IMPORT_READ, Permission.MAPPING_READ}
    ),
    Role.APPROVER: frozenset(
        {
            Permission.SOURCE_READ,
            Permission.IMPORT_READ,
            Permission.MAPPING_READ,
            Permission.MAPPING_APPROVE,
            Permission.QUARANTINE_READ,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            Permission.SOURCE_READ,
            Permission.IMPORT_READ,
            Permission.MAPPING_READ,
            Permission.QUARANTINE_READ,
        }
    ),
}

# docs/DELIVERY_PLAN.md Feature 03 "Role → permission matrix additions (seed, BE-14.2;
# PROVISIONAL, D-11, D-34)", seeded by migration 0017 (frozen copy there). Every role reads
# the domain, the rules and the runs; only data_steward projects; only risk_analyst
# proposes rule versions and runs calculations; only approver activates rule versions.
_WS3_READ = frozenset({Permission.DOMAIN_READ, Permission.RULE_READ, Permission.CALC_READ})
WS3_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: _WS3_READ,
    Role.DATA_STEWARD: _WS3_READ | {Permission.DOMAIN_PROJECT},
    Role.RISK_ANALYST: _WS3_READ | {Permission.RULE_PROPOSE, Permission.CALC_RUN},
    Role.APPROVER: _WS3_READ | {Permission.RULE_APPROVE},
    Role.AUDITOR: _WS3_READ,
}

# docs/DELIVERY_PLAN.md Feature 04 (D-40, PROVISIONAL D-11; permission change approved by the
# user 2026-10-19), seeded by migration 0023 (frozen copy there). Analysts propose obligor
# ratings and approvers decide them; every other credit read or write reuses an existing
# permission, and platform_admin, data_steward and auditor gain nothing.
CREDIT_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(),
    Role.DATA_STEWARD: frozenset(),
    Role.RISK_ANALYST: frozenset({Permission.RATING_PROPOSE}),
    Role.APPROVER: frozenset({Permission.RATING_APPROVE}),
    Role.AUDITOR: frozenset(),
}

# docs/DELIVERY_PLAN.md Feature 05 "Role -> permission matrix additions" (D-47, PROVISIONAL
# D-11; hard stop approved by the user 2026-10-19), seeded by migration 0026 (frozen copy
# there). Analysts author scenarios and confirm their own for exploration; approvers promote
# them; platform_admin, data_steward and auditor gain nothing. Reads reuse calc:read.
SCENARIO_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(),
    Role.DATA_STEWARD: frozenset(),
    Role.RISK_ANALYST: frozenset({Permission.SCENARIO_PROPOSE}),
    Role.APPROVER: frozenset({Permission.SCENARIO_APPROVE}),
    Role.AUDITOR: frozenset(),
}

# The full seeded matrix (0003 + 0010 + 0017 + 0023 + 0026). Authorization reads the
# database, not this table.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    role: WS1_ROLE_PERMISSIONS[role]
    | WS2_ROLE_PERMISSIONS[role]
    | WS3_ROLE_PERMISSIONS[role]
    | CREDIT_ROLE_PERMISSIONS[role]
    | SCENARIO_ROLE_PERMISSIONS[role]
    for role in Role
}

DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
SEED_TAG = "0003_identity_seed"

DEV_ISSUER = "moodymate-dev"
DEV_AUDIENCE = "moodymate-api"


class AuditOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    FAILURE = "FAILURE"


class ClassificationCode(StrEnum):
    """PROVISIONAL (D-11): seeded by migration 0005; ranks 0..3 in this order."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# core.classification ranks the same codes (most_sensitive, LOG_CLEAR): one order (BE-29.3).
if tuple(ClassificationCode) != SENSITIVITY:
    raise RuntimeError("ClassificationCode and core.classification.SENSITIVITY disagree")


# Highest classification each role sees in clear (frozen copy in migration 0005).
ROLE_CLEARANCE: dict[Role, ClassificationCode] = {
    Role.PLATFORM_ADMIN: ClassificationCode.CONFIDENTIAL,
    Role.DATA_STEWARD: ClassificationCode.RESTRICTED,
    Role.RISK_ANALYST: ClassificationCode.INTERNAL,
    Role.APPROVER: ClassificationCode.CONFIDENTIAL,
    Role.AUDITOR: ClassificationCode.INTERNAL,
}

CLASSIFICATION_SEED_TAG = "0005_classification"


# --- Schema registry (Phase 4, DG-01) ---------------------------------------------------


class SchemaVersionStatus(StrEnum):
    """Data Model §9 plus REJECTED (D-06). Transitions arrive with Phase 5."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class FieldType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"


class Compatibility(StrEnum):
    """How a version change affects consumers, least to most severe."""

    ADDITIVE = "ADDITIVE"  # only new optional fields: existing producers/consumers unaffected
    COMPATIBLE = "COMPATIBLE"  # existing data still valid (e.g. a widened decimal)
    BREAKING = "BREAKING"  # existing data or consumers may fail


COMPATIBILITY_RANK = {
    Compatibility.ADDITIVE: 0,
    Compatibility.COMPATIBLE: 1,
    Compatibility.BREAKING: 2,
}


class DependentType(StrEnum):
    SCHEMA = "SCHEMA"
    MAPPING = "MAPPING"
    RULE = "RULE"
    MODULE = "MODULE"
    REPORT = "REPORT"
    INTEGRATION = "INTEGRATION"


SCHEMA_SEED_TAG = "0007_schema_seed"


# --- Controlled schema change (Phase 5, DG-05) ------------------------------------------


class ProposalStatus(StrEnum):
    """schema_change_proposal lifecycle (D-06). APPROVED and IN_REVIEW are reserved: an
    approval applies the change in the same transaction, so it lands in APPLIED."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    SUPERSEDED = "SUPERSEDED"


class ProposalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


# D-06 transition table: status -> statuses it may move to. Enforced by the service.
PROPOSAL_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFT: frozenset({ProposalStatus.SUBMITTED, ProposalStatus.SUPERSEDED}),
    ProposalStatus.SUBMITTED: frozenset(
        {ProposalStatus.APPLIED, ProposalStatus.REJECTED, ProposalStatus.SUPERSEDED}
    ),
    ProposalStatus.IN_REVIEW: frozenset(),
    ProposalStatus.APPROVED: frozenset(),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.APPLIED: frozenset(),
    ProposalStatus.SUPERSEDED: frozenset(),
}
