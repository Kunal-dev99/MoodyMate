"""Seed one PROVISIONAL illustrative source: core_banking (illustrative) -> instrument (BE-7.5).

ILLUSTRATIVE and PROVISIONAL (D-11): a FILE_DROP source whose CSV profile and contract v1
match backend/samples/synthetic/instrument.csv (test_sources keeps them equal). It is not a
client system. Every row is tagged `seeded_by = '0011_ws2_source_seed'`; downgrade removes
only tagged rows, and fails on the foreign key rather than delete user data that refers to
them (e.g. a later contract version someone added).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0011_ws2_source_seed"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
ILLUSTRATIVE = "ILLUSTRATIVE seed for synthetic samples; not a client system."

EXPECTED_COLUMNS = [
    {"name": "instrument_id", "required": True},
    {"name": "instrument_type", "required": True},
    {"name": "currency", "required": True},
    {"name": "counterparty_id", "required": False},
    {"name": "product_family", "required": False},
    {"name": "notional", "required": True},
    {"name": "interest_rate", "required": False},
    {"name": "maturity_date", "required": False},
    {"name": "book_code", "required": False},
]
BUSINESS_KEY = ["instrument_id"]
CONTROL_TOTALS = ["notional"]

# Row triggers that refuse the deletes below (0009); TRUNCATE triggers are untouched.
TRIGGERS = [
    ("source_contract", "source_contract_immutable"),
    ("source_profile", "source_profile_no_delete"),
    ("source_system", "source_system_no_delete"),
]


def contract_sha256(
    schema_id: str,
    expected_columns: list[dict[str, Any]],
    business_key_columns: list[str],
    control_total_columns: list[str],
) -> str:
    """Frozen copy of app.ingestion.service.contract_sha256 (a test keeps them equal)."""
    payload = {
        "schema_id": str(schema_id),
        "expected_columns": sorted(
            ({"name": c["name"], "required": bool(c["required"])} for c in expected_columns),
            key=lambda c: c["name"],
        ),
        "business_key_columns": sorted(business_key_columns),
        "control_total_columns": sorted(control_total_columns),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    schema_id = bind.execute(
        sa.text(
            "SELECT id FROM schema_definition WHERE tenant_id = CAST(:tenant AS uuid)"
            " AND code = 'instrument'"
        ).bindparams(tenant=TENANT)
    ).scalar_one()
    system_id = bind.execute(
        sa.text(
            "INSERT INTO source_system (id, tenant_id, code, name, kind, description,"
            " created_by, seeded_by) VALUES (gen_random_uuid(), CAST(:tenant AS uuid),"
            " 'core_banking', 'core_banking (illustrative)', 'FILE_DROP', :description,"
            " :actor, :tag) RETURNING id"
        ).bindparams(
            tenant=TENANT,
            description=f"Core banking instrument extract (file drop). {ILLUSTRATIVE}",
            actor=ACTOR,
            tag=TAG,
        )
    ).scalar_one()
    profile_id = bind.execute(
        sa.text(
            "INSERT INTO source_profile (id, source_system_id, format, encoding, delimiter,"
            " cadence, owner, default_classification, provisional, created_by, seeded_by)"
            " VALUES (gen_random_uuid(), :system_id, 'CSV', 'UTF-8', ',', 'daily (illustrative)',"
            " :owner, 'confidential', true, :actor, :tag) RETURNING id"
        ).bindparams(
            system_id=system_id,
            owner="Data governance (data_steward), provisional",
            actor=ACTOR,
            tag=TAG,
        )
    ).scalar_one()
    bind.execute(
        sa.text(
            "INSERT INTO source_contract (id, source_profile_id, version, status, schema_id,"
            " expected_columns, business_key_columns, control_total_columns, content_sha256,"
            " change_summary, created_by, seeded_by) VALUES (gen_random_uuid(), :profile_id, 1,"
            " 'PROVISIONAL', :schema_id, CAST(:expected AS jsonb), CAST(:keys AS jsonb),"
            " CAST(:totals AS jsonb), :sha, :summary, :actor, :tag)"
        ).bindparams(
            profile_id=profile_id,
            schema_id=schema_id,
            expected=json.dumps(EXPECTED_COLUMNS),
            keys=json.dumps(BUSINESS_KEY),
            totals=json.dumps(CONTROL_TOTALS),
            sha=contract_sha256(str(schema_id), EXPECTED_COLUMNS, BUSINESS_KEY, CONTROL_TOTALS),
            summary=f"Provisional contract for the synthetic instrument sample. {ILLUSTRATIVE}",
            actor=ACTOR,
            tag=TAG,
        )
    )


def downgrade() -> None:
    # Only rows this migration inserted (D-11), children first. The no-delete triggers
    # would refuse: disable them for this transaction only (needs the table owner, which
    # the migration role is; no session setting can do this).
    for table, trigger in TRIGGERS:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    for table in ("source_contract", "source_profile", "source_system"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
    for table, trigger in TRIGGERS:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
