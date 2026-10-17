"""WS3 extension points: PLANNED alm_irrbb, collateral and capital (BE-18.1).

The tagged seed (FC-05, FC-06; D-31 §1, D-35) of the PLANNED module versions `alm_irrbb`,
`collateral` and `capital`, v1, for the default tenant. Each is a `calculation_module` row
plus a `module_version` row with `implementation_status = 'PLANNED'` and its declared input
and output contracts (the same shape as an AVAILABLE module's, see
docs/CALCULATION_MODULES.md). They are declared
contracts only: no code implements them, no rule definition or rule version exists for them,
and `POST /calculation-runs` refuses them with 409 `module_not_available` (reason `planned`,
audited DENIED). They are PROVISIONAL and ILLUSTRATIVE and carry the D-35 statement.

Rows are tagged `seeded_by = '0021_planned_modules'` (D-11). The downgrade removes only these
rows, and refuses while any calculation run, rule definition or untagged module version
references them (D-02: calculation evidence is kept). Both tables are append-only, so the
downgrade lifts their append-only triggers for its own transaction only.

Revision ID: 0021
Revises: 0018 (re-chained at merge time after Phases 16 and 17)
Create Date: 2026-10-17

D-01: additive only (inserts, no DDL). D-02: downgrade restores the prior state.
"""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0021_planned_modules"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs

STATEMENT = (
    "Illustrative prototype. Not a regulatory calculation; no parity with any vendor system "
    "is claimed."
)
PLANNED = (
    "PLANNED extension point: a declared contract only. Not implemented and never runnable "
    "until a specification, reference cases and a domain-expert sign-off exist. "
)

# --- frozen seed: code -> (name, module description, version description, contracts) ---------
MODULES: dict[str, dict[str, Any]] = {
    "alm_irrbb": {
        "name": "ALM / IRRBB (planned)",
        "description": "Interest-rate risk in the banking book: repricing gaps and rate-shock "
        "sensitivities. " + PLANNED + STATEMENT,
        "version_description": "Planned v1 contract (FC-05): repricing gaps by bucket and "
        "per-scenario sensitivity of the banking book, per currency. " + PLANNED + STATEMENT,
        "input_contract": {
            "schemas": [
                {
                    "schema_code": "instrument",
                    "required": True,
                    "fields": ["instrument_id", "instrument_type"],
                },
                {
                    "schema_code": "position",
                    "required": True,
                    "fields": ["instrument_id", "balance", "currency"],
                },
                {
                    "schema_code": "cashflow_event",
                    "required": True,
                    "fields": [
                        "cashflow_id",
                        "instrument_id",
                        "due_date",
                        "amount",
                        "currency",
                        "origin",
                    ],
                },  # fmt: skip
                {
                    "schema_code": "market_observation",
                    "required": True,
                    "fields": ["curve_id", "tenor", "rate", "currency", "observed_at"],
                },
            ],
            "rules": ["repricing_buckets", "rate_shock_scenarios"],
            "assumption_keys": ["behavioural_repricing", "prepayment_rate"],
            "parameters": ["reporting_date"],
        },
        "output_contract": {
            "results": ["irrbb_repricing_gap_result", "irrbb_sensitivity_result"],
            "intermediates": ["REPRICING_CONTRIBUTION", "SCENARIO_DISCOUNT_FACTOR"],
            "warning_codes": [],
        },
    },
    "collateral": {
        "name": "Collateral (planned)",
        "description": "Collateral valuation, haircuts, eligibility, allocation to exposures "
        "and encumbrance. " + PLANNED + STATEMENT,
        "version_description": "Planned v1 contract (FC-06): haircut-adjusted collateral value "
        "allocated to secured exposures, with eligibility and encumbrance treatment. "
        + PLANNED
        + STATEMENT,
        "input_contract": {
            "schemas": [
                {
                    "schema_code": "collateral_asset",
                    "required": True,
                    "fields": [
                        "collateral_id",
                        "collateral_type",
                        "valuation",
                        "currency",
                        "eligible",
                        "encumbered",
                    ],
                },  # fmt: skip
                {
                    "schema_code": "collateral_link",
                    "required": True,
                    "fields": ["collateral_id", "instrument_id", "allocated_amount", "currency"],
                },
                {
                    "schema_code": "position",
                    "required": True,
                    "fields": ["instrument_id", "balance", "currency"],
                },
            ],
            "rules": ["collateral_haircuts", "collateral_eligibility"],
            "assumption_keys": [],
            "parameters": ["reporting_date"],
        },
        "output_contract": {
            "results": ["collateral_result"],
            "intermediates": ["COLLATERAL_ALLOCATION"],
            "warning_codes": [],
        },
    },
    "capital": {
        "name": "Capital (planned)",
        "description": "Capital components, exposure and risk-weighted asset references, and "
        "capital ratios; the scope needs regulatory confirmation. " + PLANNED + STATEMENT,
        "version_description": "Planned v1 contract (FC-06): capital components and ratios over "
        "exposures by capital category. " + PLANNED + STATEMENT,
        "input_contract": {
            "schemas": [
                {
                    "schema_code": "instrument",
                    "required": True,
                    "fields": ["instrument_id", "instrument_type"],
                },
                {
                    "schema_code": "position",
                    "required": True,
                    "fields": ["instrument_id", "balance", "currency"],
                },
                {
                    "schema_code": "collateral_link",
                    "required": False,
                    "fields": ["collateral_id", "instrument_id", "allocated_amount", "currency"],
                },
            ],
            "rules": ["capital_categories", "risk_weights"],
            "assumption_keys": [],
            "parameters": ["reporting_date"],
        },
        "output_contract": {
            "results": ["capital_result"],
            "intermediates": ["RISK_WEIGHTED_EXPOSURE"],
            "warning_codes": [],
        },
    },
}
VERSION = 1
ENGINE_VERSION = "1"
TABLES = ("calculation_module", "module_version")  # parents first
TRIGGERS = tuple(f"{t}_append_only" for t in TABLES)


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{TENANT}:{code}")


def upgrade() -> None:
    bind = op.get_bind()
    common = {
        "tenant_id": uuid.UUID(TENANT),
        "created_by": ACTOR,
        "seeded_by": TAG,
        "provisional": True,
        "illustrative": True,
    }
    for code, m in MODULES.items():
        module_id = _id("calculation_module", code)
        bind.execute(
            sa.text(
                "INSERT INTO calculation_module (id, code, name, description, provisional,"
                " illustrative, seeded_by, tenant_id, created_by) VALUES (:id, :code, :name,"
                " :description, :provisional, :illustrative, :seeded_by, :tenant_id, :created_by)"
            ),
            {"id": module_id, "code": code, "name": m["name"], "description": m["description"]}
            | common,
        )
        bind.execute(
            sa.text(
                "INSERT INTO module_version (id, module_id, version, implementation_status,"
                " input_contract, output_contract, engine_version, description, provisional,"
                " illustrative, seeded_by, tenant_id, created_by) VALUES (:id, :module_id,"
                " :version, 'PLANNED', CAST(:input_contract AS jsonb),"
                " CAST(:output_contract AS jsonb), :engine_version, :description, :provisional,"
                " :illustrative, :seeded_by, :tenant_id, :created_by)"
            ),
            {
                "id": _id("module_version", f"{code}:{VERSION}"),
                "module_id": module_id,
                "version": VERSION,
                "input_contract": json.dumps(m["input_contract"]),
                "output_contract": json.dumps(m["output_contract"]),
                "engine_version": ENGINE_VERSION,
                "description": m["version_description"],
            }
            | common,
        )


def downgrade() -> None:
    # Only this seed's rows go, and only while nothing references them (D-02, D-31 §5).
    tagged = f"SELECT id FROM calculation_module WHERE seeded_by = '{TAG}'"  # noqa: S608
    tagged_versions = f"SELECT id FROM module_version WHERE seeded_by = '{TAG}'"  # noqa: S608
    op.execute(
        "DO $$ BEGIN IF"  # noqa: S608 (constants only)
        f" EXISTS (SELECT 1 FROM calculation_run WHERE module_version_id IN ({tagged_versions}))"
        f" OR EXISTS (SELECT 1 FROM rule_definition WHERE target_module_id IN ({tagged}))"
        f" OR EXISTS (SELECT 1 FROM module_version WHERE module_id IN ({tagged})"
        f" AND seeded_by IS DISTINCT FROM '{TAG}')"
        " THEN RAISE EXCEPTION 'a calculation run, rule definition or module version references"
        " the PLANNED modules; they are kept (D-02, D-31), so this downgrade is refused';"
        " END IF; END $$"
    )
    # Both tables are append-only: lift that for this transaction only (the table owner,
    # which the migration role is, may), as 0007 does for its seed.
    for table, trigger in zip(TABLES, TRIGGERS, strict=True):
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    for table in reversed(TABLES):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
    for table, trigger in zip(TABLES, TRIGGERS, strict=True):
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
