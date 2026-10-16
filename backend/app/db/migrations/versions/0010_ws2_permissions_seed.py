"""Seed the nine WS2 permissions and their role grants (BE-7.2, D-11).

PROVISIONAL until the client approves the role model (Arch §10): docs/DELIVERY_PLAN.md
Feature 02 "Role → permission matrix additions". Every inserted row is tagged
`seeded_by = '0010_ws2_permissions'`; downgrade deletes only tagged rows. Roles are the
ones 0003 seeded, found by their deterministic IDs.

The values below are a frozen copy; app.governance.constants.WS2_ROLE_PERMISSIONS must
stay in step (enforced by test_identity_seed).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-16
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0010_ws2_permissions"
ACTOR = "system:migration"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs

PERMISSIONS = {
    "source:read": "Browse registered sources, profiles and contracts.",
    "source:manage": "Register sources and add source contract versions.",
    "import:read": "Browse import jobs, profiles, issues, reconciliation and drift.",
    "import:write": "Upload and validate imports.",
    "mapping:read": "Browse source-to-canonical mappings and their versions.",
    "mapping:propose": "Draft, dry-run and submit mapping versions.",
    "mapping:approve": "Approve or reject mapping versions (maker-checker).",
    "quarantine:read": "Read quarantined rows (values masked by classification).",
    "quality:review": "Disposition quarantined rows and review drift events.",
}

MATRIX = {
    "platform_admin": ["source:read", "import:read", "mapping:read"],
    "data_steward": [
        "source:read",
        "source:manage",
        "import:read",
        "import:write",
        "mapping:read",
        "mapping:propose",
        "quarantine:read",
        "quality:review",
    ],
    "risk_analyst": ["source:read", "import:read", "mapping:read"],
    "approver": ["source:read", "import:read", "mapping:read", "mapping:approve", "quarantine:read"],
    "auditor": ["source:read", "import:read", "mapping:read", "quarantine:read"],
}


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{code}")


permission = sa.table(
    "permission",
    sa.column("id", sa.UUID()),
    sa.column("code", sa.String()),
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
        permission,
        [
            {"id": _id("permission", c), "code": c, "description": d, "created_by": ACTOR,
             "seeded_by": TAG}
            for c, d in PERMISSIONS.items()
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
    # Only rows this migration inserted (D-11). Order respects foreign keys: a grant of a
    # WS2 permission made by hand (untagged) blocks the permission's removal, by design.
    for table in ("role_permission", "permission"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
