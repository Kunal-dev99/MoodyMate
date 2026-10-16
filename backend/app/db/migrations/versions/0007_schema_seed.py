"""Seed illustrative schemas instrument, position, cashflow_event (BE-4.2, D-11).

ILLUSTRATIVE and PROVISIONAL: drawn from Data Model §4 to exercise the registry, not a
client-approved canonical model. `instrument` has two versions: v1 ACTIVE and v2 DRAFT,
whose differences are the diff fixture for BE-4.3/FE-4.2 (one change of each kind).
Every row is tagged `seeded_by = '0007_schema_seed'`; downgrade removes only tagged rows.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0007_schema_seed"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
OWNER = "Data governance (data_steward), provisional"
ILLUSTRATIVE = "ILLUSTRATIVE seed from Data Model §4; not client-approved."


def f(
    name: str,
    data_type: str,
    required: bool,
    classification: str,
    *,
    unit: str | None = None,
    precision: int | None = None,
    scale: int | None = None,
    extension_eligible: bool = False,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "data_type": data_type,
        "required": required,
        "unit": unit,
        "precision": precision,
        "scale": scale,
        "classification": classification,
        "extension_eligible": extension_eligible,
        "description": description,
    }


INSTRUMENT_V1 = [
    f("instrument_id", "STRING", True, "internal", description="Stable canonical identity."),
    f("instrument_type", "STRING", True, "internal", description="Deposit, loan, bond, ..."),
    f("currency", "STRING", True, "public", unit="ISO 4217"),
    f("counterparty_id", "STRING", False, "confidential"),
    f("product_family", "STRING", False, "internal", extension_eligible=True),
    f("notional", "DECIMAL", True, "confidential", unit="currency", precision=20, scale=2),
    f("interest_rate", "DECIMAL", False, "internal", unit="percent", precision=9, scale=4),
    f("maturity_date", "STRING", False, "internal", description="Free text in v1."),
    f("legacy_book_code", "STRING", False, "internal", description="Removed in v2."),
]

# v2 (DRAFT): one change of each kind, the BE-4.3 / FE-4.2 diff fixture.
#   rating_category added, optional     -> ADDITIVE
#   interest_rate scale 4 -> 6 (9 -> 11) -> COMPATIBLE (widened)
#   legacy_book_code removed            -> BREAKING
#   product_family becomes required     -> BREAKING
#   maturity_date STRING -> DATE        -> BREAKING
INSTRUMENT_V2 = [
    f("instrument_id", "STRING", True, "internal", description="Stable canonical identity."),
    f("instrument_type", "STRING", True, "internal", description="Deposit, loan, bond, ..."),
    f("currency", "STRING", True, "public", unit="ISO 4217"),
    f("counterparty_id", "STRING", False, "confidential"),
    f("product_family", "STRING", True, "internal", extension_eligible=True),
    f("notional", "DECIMAL", True, "confidential", unit="currency", precision=20, scale=2),
    f("interest_rate", "DECIMAL", False, "internal", unit="percent", precision=11, scale=6),
    f("maturity_date", "DATE", False, "internal", description="Typed date from v2."),
    f("rating_category", "STRING", False, "internal", description="New in v2."),
]

POSITION_V1 = [
    f("position_id", "STRING", True, "internal"),
    f("instrument_id", "STRING", True, "internal"),
    f("account_id", "STRING", True, "confidential"),
    f("legal_entity_id", "STRING", True, "internal"),
    f("as_of_date", "DATE", True, "internal", description="Business date of the holding."),
    f("quantity", "DECIMAL", False, "confidential", precision=24, scale=6),
    f("balance", "DECIMAL", True, "confidential", unit="currency", precision=20, scale=2),
    f("currency", "STRING", True, "public", unit="ISO 4217"),
    f("valuation_date", "DATE", False, "internal"),
]

CASHFLOW_EVENT_V1 = [
    f("cashflow_id", "STRING", True, "internal"),
    f("instrument_id", "STRING", True, "internal"),
    f("due_date", "DATE", True, "internal"),
    f("amount", "DECIMAL", True, "confidential", unit="currency", precision=20, scale=2),
    f("currency", "STRING", True, "public", unit="ISO 4217"),
    f("cashflow_type", "STRING", True, "internal", description="PRINCIPAL, INTEREST, FEE."),
    f("origin", "STRING", True, "internal", description="CONTRACTUAL or BEHAVIOURAL."),
]

SCHEMAS = [
    (
        "instrument",
        "Instrument",
        "Canonical financial instrument: identity, type, currency and terms.",
        [
            (1, "ACTIVE", "2026-01-01", None, INSTRUMENT_V1, "Initial version."),
            (2, "DRAFT", None, "BREAKING", INSTRUMENT_V2, "Typed maturity date, rating."),
        ],
    ),
    (
        "position",
        "Position",
        "Holding or exposure at a point in time.",
        [(1, "ACTIVE", "2026-01-01", None, POSITION_V1, "Initial version.")],
    ),
    (
        "cashflow_event",
        "Cash-flow event",
        "Contractual or behavioural cash-flow with due date and amount.",
        [(1, "ACTIVE", "2026-01-01", None, CASHFLOW_EVENT_V1, "Initial version.")],
    ),
]

# (schema, dependent type, dependent ref, dependent schema code, description)
DEPENDENCIES = [
    ("instrument", "SCHEMA", "position", "position", "Positions reference instruments."),
    ("instrument", "SCHEMA", "cashflow_event", "cashflow_event", "Cash flows reference instruments."),
    ("instrument", "MAPPING", "core_banking_instrument (illustrative)", None, "WS2 mapping."),
    ("position", "MODULE", "liquidity_gap (illustrative)", None, "WS3 calculation module."),
    ("cashflow_event", "MODULE", "liquidity_gap (illustrative)", None, "WS3 calculation module."),
    ("cashflow_event", "REPORT", "maturity_ladder (illustrative)", None, "WS4 report."),
]

TRIGGERS = [
    ("schema_field", "schema_field_immutable"),
    ("schema_version", "schema_version_immutable"),
]

CONTRACT_KEYS = (
    "name",
    "data_type",
    "required",
    "unit",
    "precision",
    "scale",
    "classification",
    "extension_eligible",
)


def contract_sha256(fields: list[dict[str, Any]]) -> str:
    """Frozen copy of app.governance.service.contract_sha256 (a test keeps them equal)."""
    ordered = sorted(fields, key=lambda fld: fld["name"])  # field order is not contract
    canonical = [{k: fld[k] for k in CONTRACT_KEYS} for fld in ordered]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    for code, name, description, versions in SCHEMAS:
        schema_id = bind.execute(
            sa.text(
                "INSERT INTO schema_definition (id, tenant_id, code, name, description, owner,"
                " provisional, created_by, seeded_by) VALUES (gen_random_uuid(), CAST(:tenant AS uuid),"
                " :code, :name, :description, :owner, true, :actor, :tag) RETURNING id"
            ).bindparams(
                tenant=TENANT,
                code=code,
                name=name,
                description=f"{description} {ILLUSTRATIVE}",
                owner=OWNER,
                actor=ACTOR,
                tag=TAG,
            )
        ).scalar_one()
        for number, status, effective, compatibility, fields, summary in versions:
            # Inserted as DRAFT so its fields can be added, then moved to its status:
            # the same path the triggers allow the application (BE-4.1).
            version_id = bind.execute(
                sa.text(
                    "INSERT INTO schema_version (id, schema_id, version, status, effective_from,"
                    " compatibility, content_sha256, change_summary, created_by, seeded_by)"
                    " VALUES (gen_random_uuid(), :schema_id, :version, 'DRAFT',"
                    " CAST(:effective AS date), :compatibility, :sha, :summary, :actor, :tag)"
                    " RETURNING id"
                ).bindparams(
                    schema_id=schema_id,
                    version=number,
                    effective=effective,
                    compatibility=compatibility,
                    sha=contract_sha256(fields),
                    summary=summary,
                    actor=ACTOR,
                    tag=TAG,
                )
            ).scalar_one()
            for position, fld in enumerate(fields, start=1):
                bind.execute(
                    sa.text(
                        "INSERT INTO schema_field (id, schema_version_id, position, name,"
                        " data_type, required, unit, precision, scale, classification,"
                        " extension_eligible, description, created_by, seeded_by) VALUES"
                        " (gen_random_uuid(), :version_id, :position, :name, :data_type,"
                        " :required, :unit, :precision, :scale, :classification,"
                        " :extension_eligible, :description, :actor, :tag)"
                    ).bindparams(version_id=version_id, position=position, actor=ACTOR, tag=TAG, **fld)
                )
            if status != "DRAFT":
                bind.execute(
                    sa.text("UPDATE schema_version SET status = :status WHERE id = :id").bindparams(
                        status=status, id=version_id
                    )
                )
    for code, dep_type, ref, dep_code, description in DEPENDENCIES:
        bind.execute(
            sa.text(
                "INSERT INTO schema_dependency (id, schema_id, dependent_type, dependent_ref,"
                " dependent_schema_id, description, provisional, created_by, seeded_by)"
                " SELECT gen_random_uuid(), s.id, :dep_type, :ref,"
                " (SELECT d.id FROM schema_definition d WHERE d.tenant_id = s.tenant_id"
                "  AND d.code = :dep_code), :description, true, :actor, :tag"
                " FROM schema_definition s WHERE s.tenant_id = CAST(:tenant AS uuid) AND s.code = :code"
            ).bindparams(
                dep_type=dep_type,
                ref=ref,
                dep_code=dep_code,
                description=description,
                actor=ACTOR,
                tag=TAG,
                tenant=TENANT,
                code=code,
            )
        )


def downgrade() -> None:
    # Only rows this migration inserted (D-11), children first. Seeded versions are not
    # DRAFT, so the immutability triggers would refuse: disable them for this transaction
    # only (needs the table owner, which the migration role is).
    for table, trigger in TRIGGERS:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    for table in ("schema_dependency", "schema_field", "schema_version", "schema_definition"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
    for table, trigger in TRIGGERS:
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
