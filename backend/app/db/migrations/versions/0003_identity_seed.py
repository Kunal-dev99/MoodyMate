"""Seed identity data (BE-2.2, D-11): default tenant, roles, permissions, matrix.

PROVISIONAL until the client approves the role model (Arch §10). Every inserted row is
tagged `seeded_by = '0003_identity_seed'`; downgrade deletes only tagged rows. If user
data references a seeded row (e.g. a user holds a seeded role), downgrade fails on the
foreign key instead of deleting user data. That is intended.

The values below are a frozen copy; app.governance.constants must stay in step
(enforced by test_identity_seed).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0003_identity_seed"
ACTOR = "system:migration"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # namespace for seed IDs

TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")

PERMISSIONS = {
    "schema:read": "Browse schemas, versions and diffs.",
    "schema:propose": "Create schema change proposals and run impact analysis.",
    "schema:approve": "Approve or reject schema change proposals (maker-checker).",
    "audit:read": "Read the audit trail.",
    "admin:read": "Read users, roles and platform settings.",
}

ROLES = {
    "platform_admin": ("Platform administrator", "Operates the platform; cannot approve changes."),
    "data_steward": ("Data steward", "Owns schemas and proposes changes."),
    "risk_analyst": ("Risk analyst", "Uses data and results; read-only on governance."),
    "approver": ("Approver", "Checker in maker-checker approvals."),
    "auditor": ("Auditor", "Reads schemas and the audit trail."),
}

MATRIX = {
    "platform_admin": ["schema:read", "audit:read", "admin:read"],
    "data_steward": ["schema:read", "schema:propose"],
    "risk_analyst": ["schema:read"],
    "approver": ["schema:read", "schema:approve"],
    "auditor": ["schema:read", "audit:read"],
}


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{code}")


tenant = sa.table(
    "tenant",
    sa.column("id", sa.UUID()),
    sa.column("name", sa.String()),
    sa.column("slug", sa.String()),
    sa.column("status", sa.String()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)
permission = sa.table(
    "permission",
    sa.column("id", sa.UUID()),
    sa.column("code", sa.String()),
    sa.column("description", sa.String()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)
role = sa.table(
    "role",
    sa.column("id", sa.UUID()),
    sa.column("code", sa.String()),
    sa.column("name", sa.String()),
    sa.column("description", sa.String()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)
role_permission = sa.table(
    "role_permission",
    sa.column("role_id", sa.UUID()),
    sa.column("permission_id", sa.UUID()),
    sa.column("created_by", sa.String()),
    sa.column("seeded_by", sa.String()),
)


def upgrade() -> None:
    op.bulk_insert(
        tenant,
        [
            {
                "id": TENANT_ID,
                "name": "Default tenant",
                "slug": "default",
                "status": "ACTIVE",
                "created_by": ACTOR,
                "seeded_by": TAG,
            }
        ],
    )
    op.bulk_insert(
        permission,
        [
            {"id": _id("permission", c), "code": c, "description": d, "created_by": ACTOR,
             "seeded_by": TAG}
            for c, d in PERMISSIONS.items()
        ],
    )
    op.bulk_insert(
        role,
        [
            {"id": _id("role", c), "code": c, "name": n, "description": d, "created_by": ACTOR,
             "seeded_by": TAG}
            for c, (n, d) in ROLES.items()
        ],
    )
    op.bulk_insert(
        role_permission,
        [
            {"role_id": _id("role", r), "permission_id": _id("permission", p),
             "created_by": ACTOR, "seeded_by": TAG}
            for r, perms in MATRIX.items()
            for p in perms
        ],
    )


def downgrade() -> None:
    # Only rows this migration inserted (D-11). Order respects foreign keys.
    for table in ("role_permission", "role", "permission", "tenant"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
