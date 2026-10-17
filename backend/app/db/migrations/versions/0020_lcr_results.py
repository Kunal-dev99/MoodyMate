"""WS3 LCR/HQLA prototype outputs (BE-17.1; FC-03; D-31, D-33, D-35): `lcr_result` and
`hqla_asset_result`, with the tagged ILLUSTRATIVE seed of module `lcr_hqla` v1 (D-11).

* `lcr_result`: one row per lcr_hqla run and currency: HQLA by level and in total, outflows,
  inflows before and after the cap, the denominator and the ratio (NULL with
  LCR_DENOMINATOR_ZERO when the denominator is not positive). The identities between the
  figures are CHECK-constrained (exact NUMERIC arithmetic).
* `hqla_asset_result`: one row per asset position of a run, included at its level (value =
  balance x (1 - haircut)) or excluded with its reason (value 0), under its currency's
  `lcr_result` row, with its position version's lineage.
* Both are run outputs with 0018's guards: inserted only while their run is RUNNING, in its
  tenant (`calc_output_guard`, which reads the run row FOR SHARE, so a run cannot finish
  under an open insert), then append-only (UPDATE, DELETE and TRUNCATE raise). Insert checks
  (D-04, D-31 §6): an asset row repeats its position version's lineage, reads a member import
  of its run's snapshot, adds to an LCR row of its own run and currency, and names an
  instrument version of its tenant.
* Seed, tagged `seeded_by = '0020_lcr_hqla'` (D-11), PROVISIONAL / ILLUSTRATIVE (D-35 §2),
  for the default tenant: module `lcr_hqla` v1 AVAILABLE with its declared contracts, and
  the rules `hqla_level`, `hqla_haircut`, `lcr_outflow_run_off`, `lcr_inflow_rate`,
  `lcr_inflow_horizon_days` and `lcr_inflow_cap`, each with an ACTIVE v1 (inserted DRAFT and
  moved through IN_REVIEW, approved_by the tag). Decimal parameters are JSON strings of exact
  decimal text. Frozen copies of the code's enums and seed (kept equal by test_calculations).

D-01: additive only. `calculation_warning.code` (0018) is a closed CHECK that a forward
migration may not widen, so the LCR's own warnings live in `lcr_result.warning_codes`.

The downgrade refuses while any LCR output row, any run or rule set pinning the lcr_hqla
seed, or any untagged row under it exists (D-02: calculation evidence is kept); then it
drops the triggers, function and tables, and removes only its tagged seed rows (D-11), with
their append-only triggers lifted for that one transaction.

Revision ID: 0020
Revises: 0018 (re-chained after Phase 16's 0019 at merge time)
Create Date: 2026-10-17

D-01: additive only (no DROP in upgrade). D-02: downgrade must restore the prior schema.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0020_lcr_hqla"
ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs

# --- frozen copies of the code's enums (test_calculations keeps them equal) ------------------
HQLA_LEVELS = ("L1", "L2A", "L2B")
EXCLUSION_REASONS = ("NOT_HQLA_ELIGIBLE",)
LCR_WARNING_CODES = ("LCR_DENOMINATOR_ZERO", "LCR_LEVEL_CAPS_NOT_APPLIED")

# --- frozen copies of the seed (D-35 §2) -----------------------------------------------------
STATEMENT = (
    "Illustrative prototype. Not a regulatory calculation; no parity with any vendor system "
    "is claimed."
)
MODULE = {
    "code": "lcr_hqla",
    "name": "LCR/HQLA (illustrative prototype)",
    "description": "Per-currency liquidity coverage ratio and HQLA. " + STATEMENT,
}
MODULE_VERSION = {
    "version": 1,
    "implementation_status": "AVAILABLE",
    "engine_version": "1",
    "description": (
        "Per-currency liquidity coverage ratio with no FX: HQLA by level from position "
        "balances less haircuts, outflows from run-off rates, inflows within the horizon "
        "capped as a share of outflows; ratio to 4 dp. No L2A/L2B caps. Illustrative "
        "prototype; not a regulatory calculation (D-35)."
    ),
    "input_contract": {
        "schemas": [
            {
                "schema_code": "position",
                "required": True,
                "fields": ["position_id", "instrument_id", "balance", "currency"],
            },
            {
                "schema_code": "instrument",
                "required": True,
                "fields": ["instrument_id", "instrument_type"],
            },
            {
                "schema_code": "cashflow_event",
                "required": True,
                "fields": ["cashflow_id", "instrument_id", "due_date", "amount", "currency"],
            },
        ],
        "rules": [
            "hqla_level", "hqla_haircut", "lcr_outflow_run_off", "lcr_inflow_rate",
            "lcr_inflow_horizon_days", "lcr_inflow_cap",
        ],  # fmt: skip
        "assumption_keys": [],
        "parameters": ["reporting_date"],
    },
    "output_contract": {
        "results": ["lcr_result", "hqla_asset_result"],
        "intermediates": [],
        "warning_codes": [
            "AMBIGUOUS_INSTRUMENT_KEY", "INSTRUMENT_NOT_IN_SNAPSHOT", "LCR_DENOMINATOR_ZERO",
            "LCR_LEVEL_CAPS_NOT_APPLIED", "MISSING_VALUE", "NEGATIVE_AMOUNT",
            "PARAMETERS_INVALID", "UNMAPPED_INSTRUMENT_TYPE",
        ],  # fmt: skip
    },
}


def _factor(keyed: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "kind": "DECIMAL_MAP" if keyed else "DECIMAL",
        "min": "0",
        "max": "1",
        "scale": 4,
    }
    if keyed:
        schema["key_vocabulary"] = "instrument_type"
    return schema


# code -> (name, category, parameter_schema, parameters, description)
RULES: dict[str, tuple[str, str, dict[str, Any], dict[str, Any], str]] = {
    "hqla_level": (
        "HQLA level by instrument type (illustrative)",
        "CLASSIFICATION",
        {"kind": "ENUM_MAP", "key_vocabulary": "instrument_type", "values": ["L1", "L2A", "L2B"]},
        {"map": {"BOND": "L1"}},
        "BOND is Level 1 HQLA; an instrument type that is not a key of the map is not HQLA "
        "(D-35 §2).",
    ),
    "hqla_haircut": (
        "HQLA haircut by instrument type (illustrative)",
        "FACTOR",
        _factor(keyed=True),
        {"map": {"BOND": "0"}},
        "BOND has a 0 haircut: its HQLA value is its position balance x (1 - haircut) "
        "(D-35 §2).",
    ),
    "lcr_outflow_run_off": (
        "Outflow run-off rate by instrument type (illustrative)",
        "FACTOR",
        _factor(keyed=True),
        {"map": {"DEPOSIT": "0.10"}},
        "DEPOSIT balances run off at 0.10: outflows are balance x run-off (D-35 §2).",
    ),
    "lcr_inflow_rate": (
        "Inflow rate by instrument type (illustrative)",
        "FACTOR",
        _factor(keyed=True),
        {"map": {"LOAN": "0.50"}},
        "LOAN cash flows due within the horizon flow in at 0.50 (D-35 §2).",
    ),
    "lcr_inflow_horizon_days": (
        "Inflow horizon in days (illustrative)",
        "BUCKETING",
        {"kind": "INTEGER", "min": 1, "max": 366, "unit": "calendar days after reporting date"},
        {"value": 30},
        "A cash flow counts as an inflow when it is due (0, 30] calendar days after the "
        "reporting date (D-35 §2).",
    ),
    "lcr_inflow_cap": (
        "Inflow cap as a share of outflows (illustrative)",
        "FACTOR",
        _factor(keyed=False),
        {"value": "0.75"},
        "Inflows count up to 0.75 x outflows: inflows = min(inflows x rate, cap x outflows) "
        "(D-35 §2).",
    ),
}
RULE_OWNER = "Methodology owner (risk_analyst), provisional"
SEED_SUMMARY = "ILLUSTRATIVE seed (D-35); PROVISIONAL until the client's financial owner approves."

OUTPUT_TABLES = ("lcr_result", "hqla_asset_result")  # creation order
# The seeded registry tables, children first, with the trigger that refuses their DELETE.
SEEDED = (
    ("rule_version", "rule_version_lifecycle"),
    ("rule_definition", "rule_definition_append_only"),
    ("module_version", "module_version_append_only"),
    ("calculation_module", "calculation_module_append_only"),
)


def canonical_sha256(value: object) -> str:
    """Frozen copy of app.rules.service.canonical_sha256 (a test keeps them equal)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id(kind: str, code: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"{kind}:{TENANT}:{code}")


MODULE_ID = _id("calculation_module", MODULE["code"])
VERSION_ID = _id("module_version", f"{MODULE['code']}:1")

# --- trigger function ------------------------------------------------------------------------

DENY = "USING ERRCODE = 'insufficient_privilege'"

ASSET_CHECKS = f"""
CREATE FUNCTION hqla_asset_result_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM position_version p WHERE p.id = NEW.position_version_id
            AND p.tenant_id = NEW.tenant_id AND p.canonical_record_id = NEW.canonical_record_id
            AND p.import_job_id = NEW.import_job_id AND p.source_record_id = NEW.source_record_id
            AND p.source_ordinal = NEW.source_ordinal) THEN
        RAISE EXCEPTION 'hqla_asset_result must repeat its position version''s lineage, in its'
            ' tenant (D-31 §6, D-04)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM calculation_run r JOIN dataset_snapshot_member m
            ON m.dataset_snapshot_id = r.dataset_snapshot_id
            WHERE r.id = NEW.calculation_run_id AND m.import_job_id = NEW.import_job_id) THEN
        RAISE EXCEPTION 'hqla_asset_result must read a member import of its run''s snapshot'
            ' (D-30, D-31 §6)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM lcr_result l WHERE l.id = NEW.lcr_result_id
            AND l.calculation_run_id = NEW.calculation_run_id AND l.currency = NEW.currency) THEN
        RAISE EXCEPTION 'hqla_asset_result must add to an LCR row of its own run and currency'
            ' (D-31)' {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM instrument_version v WHERE v.id = NEW.instrument_version_id
            AND v.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'hqla_asset_result must name an instrument version of its tenant'
            ' (D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

_ROW_EVENTS = "BEFORE INSERT OR UPDATE OR DELETE"
# (trigger, timing and events, table, level, function); calc_output_guard() is 0018's.
TRIGGERS: tuple[tuple[str, str, str, str, str], ...] = (
    *((f"{t}_guard", _ROW_EVENTS, t, "ROW", "calc_output_guard()") for t in OUTPUT_TABLES),
    *(
        (f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", "calc_output_guard()")
        for t in OUTPUT_TABLES
    ),
    (
        "hqla_asset_result_checks",
        "BEFORE INSERT",
        "hqla_asset_result",
        "ROW",
        "hqla_asset_result_checks()",
    ),
)

# --- columns -------------------------------------------------------------------------------

CURRENCY = "~ '^[A-Z]{3}$'"


def _common(table: str) -> list[Any]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _fk(table: str, column: str, target: str, nullable: bool = False) -> list[Any]:
    return [
        sa.Column(column, sa.UUID(), nullable=nullable),
        sa.ForeignKeyConstraint(
            [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}")
        ),
    ]


def _ck(table: str, name: str, expression: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=op.f(f"ck_{table}_{name}"))


def _uq(table: str, *columns: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint(*columns, name=op.f(f"uq_{table}_{columns[0]}"))


def _classification(table: str, column: str) -> list[Any]:
    return [
        sa.Column(column, sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            [column],
            ["data_classification.code"],
            name=op.f(f"fk_{table}_{column}_data_classification"),
        ),
    ]


def _output(table: str) -> list[Any]:
    return [
        *_fk(table, "calculation_run_id", "calculation_run"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        _uq(table, "calculation_run_id", "sequence"),
        _ck(table, "sequence", "sequence >= 1"),
    ]


def _numeric(*names: str, nullable: bool = False) -> list[sa.Column]:  # type: ignore[type-arg]
    return [sa.Column(n, sa.Numeric(), nullable=nullable) for n in names]


def _integer(*names: str) -> list[sa.Column]:  # type: ignore[type-arg]
    return [sa.Column(n, sa.Integer(), nullable=False) for n in names]


LCR_WARNINGS_JSON = "[" + ", ".join(f'"{c}"' for c in LCR_WARNING_CODES) + "]"


def _create_tables() -> None:
    t = "lcr_result"
    op.create_table(
        t,
        *_output(t),
        sa.Column("currency", sa.String(length=3), nullable=False),
        *_numeric("hqla_level1", "hqla_level2a", "hqla_level2b", "hqla_total"),
        *_integer("level1_count", "level2a_count", "level2b_count", "excluded_asset_count"),
        *_numeric("outflow_balance", "outflows"),
        *_integer("outflow_count"),
        *_numeric(
            "inflows_gross",
            "inflows_before_cap",
            "inflow_cap_rate",
            "inflow_cap_amount",
            "inflows_after_cap",
        ),
        sa.Column("inflow_cap_applied", sa.Boolean(), nullable=False),
        *_integer("inflow_count"),
        *_numeric("net_outflows"),
        *_numeric("ratio", nullable=True),
        sa.Column("warning_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule_version_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_classification(t, "classification"),
        *_classification(t, "contribution_classification"),
        *_common(t),
        _uq(t, "currency", "calculation_run_id"),
        _ck(t, "currency", f"currency {CURRENCY}"),
        _ck(t, "hqla", "hqla_total = hqla_level1 + hqla_level2a + hqla_level2b"),
        _ck(
            t,
            "non_negative",
            "hqla_level1 >= 0 AND hqla_level2a >= 0 AND hqla_level2b >= 0 AND outflows >= 0"
            " AND outflow_balance >= 0 AND inflows_gross >= 0 AND inflows_before_cap >= 0",
        ),
        _ck(
            t,
            "counts",
            "level1_count >= 0 AND level2a_count >= 0 AND level2b_count >= 0"
            " AND excluded_asset_count >= 0 AND outflow_count >= 0 AND inflow_count >= 0",
        ),
        _ck(t, "inflow_cap_rate", "inflow_cap_rate >= 0 AND inflow_cap_rate <= 1"),
        _ck(t, "inflow_cap", "inflow_cap_amount = inflow_cap_rate * outflows"),
        _ck(
            t,
            "inflows",
            "inflows_after_cap = LEAST(inflows_before_cap, inflow_cap_amount)"
            " AND inflow_cap_applied = (inflows_before_cap > inflow_cap_amount)",
        ),
        _ck(t, "net_outflows", "net_outflows = outflows - inflows_after_cap"),
        _ck(t, "ratio", "(ratio IS NULL) = (net_outflows <= 0)"),
        _ck(
            t,
            "warning_codes",
            "jsonb_typeof(warning_codes) = 'array'"
            f" AND warning_codes <@ '{LCR_WARNINGS_JSON}'::jsonb"
            " AND (warning_codes @> '[\"LCR_DENOMINATOR_ZERO\"]') = (net_outflows <= 0)",
        ),
        _ck(t, "rule_version_ids", "jsonb_typeof(rule_version_ids) = 'array'"),
    )
    t = "hqla_asset_result"
    op.create_table(
        t,
        *_output(t),
        *_fk(t, "lcr_result_id", "lcr_result"),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("level", sa.String(length=3), nullable=True),
        sa.Column("exclusion_reason", sa.String(length=30), nullable=True),
        sa.Column("instrument_type", sa.String(length=50), nullable=False),
        *_numeric("balance"),
        *_numeric("haircut", nullable=True),
        *_numeric("hqla_value"),
        sa.Column("rule_version_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_classification(t, "classification"),
        *_fk(t, "position_version_id", "position_version"),
        *_fk(t, "instrument_version_id", "instrument_version"),
        *_fk(t, "canonical_record_id", "canonical_record"),
        *_fk(t, "import_job_id", "import_job"),
        *_fk(t, "source_record_id", "source_record"),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        *_common(t),
        _uq(t, "position_version_id", "calculation_run_id"),
        _ck(t, "currency", f"currency {CURRENCY}"),
        _ck(t, "level", _in("level", HQLA_LEVELS)),
        _ck(t, "exclusion_reason", _in("exclusion_reason", EXCLUSION_REASONS)),
        _ck(
            t,
            "included",
            "included = (level IS NOT NULL) AND included = (exclusion_reason IS NULL)"
            " AND included = (haircut IS NOT NULL)",
        ),
        _ck(t, "balance", "balance >= 0 AND (haircut IS NULL OR (haircut >= 0 AND haircut <= 1))"),
        _ck(
            t,
            "hqla_value",
            "hqla_value = CASE WHEN included THEN balance * (1 - haircut) ELSE 0 END",
        ),
        _ck(t, "rule_version_ids", "jsonb_typeof(rule_version_ids) = 'array'"),
        _ck(t, "source_ordinal", "source_ordinal >= 1"),
    )


def _indexes() -> list[tuple[str, str, list[str]]]:
    """(index, table, columns), as the models declare them."""
    found = [(f"ix_{t}_tenant_id", t, ["tenant_id"]) for t in OUTPUT_TABLES]
    found += [(f"ix_{t}_calculation_run_id", t, ["calculation_run_id"]) for t in OUTPUT_TABLES]
    found.append(("ix_hqla_asset_result_lcr_result_id", "hqla_asset_result", ["lcr_result_id"]))
    return found


# --- seed (D-11, D-35) -----------------------------------------------------------------------


def _seed() -> None:
    bind = op.get_bind()
    tenant = uuid.UUID(TENANT)
    common = {"tenant_id": tenant, "created_by": ACTOR, "seeded_by": TAG}
    labels = {"provisional": True, "illustrative": True}
    rows: list[tuple[str, dict[str, Any]]] = [
        ("calculation_module", {"id": MODULE_ID, **MODULE, **labels, **common}),
        (
            "module_version",
            {
                "id": VERSION_ID,
                "module_id": MODULE_ID,
                **{k: v for k, v in MODULE_VERSION.items() if not k.endswith("_contract")},
                "input_contract": json.dumps(MODULE_VERSION["input_contract"]),
                "output_contract": json.dumps(MODULE_VERSION["output_contract"]),
                **labels,
                **common,
            },
        ),
    ]
    for code, (name, category, schema, _, description) in RULES.items():
        rows.append(
            (
                "rule_definition",
                {
                    "id": _id("rule_definition", code),
                    "code": code,
                    "name": name,
                    "category": category,
                    "target_module_id": MODULE_ID,
                    "owner": RULE_OWNER,
                    "parameter_schema": json.dumps(schema),
                    "description": description + " " + STATEMENT,
                    **labels,
                    **common,
                },
            )
        )
    for code, (_, _, _, parameters, _) in RULES.items():
        rows.append(
            (
                "rule_version",
                {
                    "id": _id("rule_version", f"{code}:1"),
                    "rule_definition_id": _id("rule_definition", code),
                    "version": 1,
                    "status": "DRAFT",
                    "parameters": json.dumps(parameters),
                    "parameters_sha256": canonical_sha256(parameters),
                    "authored_by": TAG,
                    "change_summary": SEED_SUMMARY,
                    **labels,
                    **common,
                },
            )
        )
    jsonb = {"input_contract", "output_contract", "parameter_schema", "parameters"}
    for table, row in rows:
        columns = ", ".join(row)
        values = ", ".join(f"CAST(:{k} AS jsonb)" if k in jsonb else f":{k}" for k in row)
        bind.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), row)  # noqa: S608
    # Seeded versions move as the application's would (D-32 §7): submitted, then approved by
    # the seed tag. Inserted DRAFT because the lifecycle trigger accepts nothing else.
    bind.execute(
        sa.text("UPDATE rule_version SET status = 'IN_REVIEW' WHERE seeded_by = :tag"),
        {"tag": TAG},
    )
    bind.execute(
        sa.text(
            "UPDATE rule_version SET status = 'ACTIVE', approved_by = :tag, approved_at = now(),"
            " decision_comment = :comment WHERE seeded_by = :tag"
        ),
        {"tag": TAG, "comment": "Seeded ACTIVE (D-32 §7). " + STATEMENT},
    )


def upgrade() -> None:
    _create_tables()
    for name, table, columns in _indexes():
        op.create_index(op.f(name), table, columns, unique=False)
    # One statement per op.execute: asyncpg cannot run several in one call.
    op.execute(ASSET_CHECKS)
    for name, when, table, level, call in TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {call}")
    _seed()


def downgrade() -> None:
    # Calculation evidence is kept (D-02, D-31 §5): refuse rather than drop it. The tagged
    # seed does not count; anything that pins it or was added under it does.
    rules = ", ".join(f"'{_id('rule_definition', code)}'" for code in RULES)
    checks = [f"EXISTS (SELECT 1 FROM {t})" for t in OUTPUT_TABLES]
    checks += [
        f"EXISTS (SELECT 1 FROM calculation_run WHERE module_version_id IN (SELECT id FROM"
        f" module_version WHERE module_id = '{MODULE_ID}'))",
        f"EXISTS (SELECT 1 FROM rule_set_member WHERE rule_definition_id IN ({rules}))",
        f"EXISTS (SELECT 1 FROM rule_version WHERE rule_definition_id IN ({rules})"
        f" AND seeded_by IS DISTINCT FROM '{TAG}')",
        f"EXISTS (SELECT 1 FROM rule_definition WHERE target_module_id = '{MODULE_ID}'"
        f" AND seeded_by IS DISTINCT FROM '{TAG}')",
        f"EXISTS (SELECT 1 FROM module_version WHERE module_id = '{MODULE_ID}'"
        f" AND seeded_by IS DISTINCT FROM '{TAG}')",
    ]
    op.execute(
        "DO $$ BEGIN IF " + " OR ".join(checks) + " THEN RAISE EXCEPTION 'LCR results, runs or"
        " rule sets pinning the lcr_hqla seed, or untagged rows under it exist; they are kept"
        " (D-02, D-31), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS hqla_asset_result_checks()")
    for name, table, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=table)
    for table in reversed(OUTPUT_TABLES):
        op.drop_table(table)
    # Only the seed rows this migration inserted (D-11). Their tables are append-only (and
    # rule versions never deleted), so each guard is lifted for this one transaction.
    for table, trigger in SEEDED:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
