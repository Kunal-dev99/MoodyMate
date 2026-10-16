"""Seed classification levels and role clearances (BE-3.4, D-11).

PROVISIONAL until the client approves its classification scheme. Every inserted row is
tagged `seeded_by = '0005_classification'`; downgrade deletes only tagged rows. Values are
a frozen copy; app.governance.constants must stay in step (test_classification checks).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0005_classification"
ACTOR = "system:migration"

LEVELS = [
    ("public", "Public", 0, "May be shared outside the organisation."),
    ("internal", "Internal", 1, "Staff only; low impact if disclosed."),
    ("confidential", "Confidential", 2, "Business-sensitive or personal data."),
    ("restricted", "Restricted", 3, "Highest sensitivity; strictly need-to-know."),
]

CLEARANCE = {
    "platform_admin": "confidential",
    "data_steward": "restricted",
    "risk_analyst": "internal",
    "approver": "confidential",
    "auditor": "internal",
}


def upgrade() -> None:
    for code, name, rank, description in LEVELS:
        op.execute(
            sa.text(
                "INSERT INTO data_classification "
                "(id, code, name, rank, description, provisional, created_by, seeded_by) "
                "VALUES (gen_random_uuid(), :code, :name, :rank, :description, true, :actor, :tag)"
            ).bindparams(
                code=code, name=name, rank=rank, description=description, actor=ACTOR, tag=TAG
            )
        )
    for role, level in CLEARANCE.items():
        op.execute(
            sa.text(
                "INSERT INTO access_policy "
                "(id, role_id, max_clear_classification_id, created_by, seeded_by) "
                "SELECT gen_random_uuid(), r.id, c.id, :actor, :tag "
                "FROM role r, data_classification c WHERE r.code = :role AND c.code = :level"
            ).bindparams(role=role, level=level, actor=ACTOR, tag=TAG)
        )


def downgrade() -> None:
    # Only rows this migration inserted (D-11); policies first (they reference levels).
    for table in ("access_policy", "data_classification"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
