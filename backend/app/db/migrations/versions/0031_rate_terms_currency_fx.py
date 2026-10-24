"""Rate terms, term vocabularies, currency reference and FX observations (BE-32.1, FC-01,
FC-08; D-61).

(a) `instrument_term_version` (D-61 §1-2, §4-5): the typed rate terms, business-effective date
    and lifecycle state of one `instrument_version`, written by projector v3 for the
    INSTRUMENT_RATE_TERMS contract. A side table, so the `instrument_version` shape stays
    frozen: `UNIQUE (instrument_version_id)`, at most one row per version. Written only when
    the terms are consistent, which three CHECKs repeat (FIXED has no reference index,
    FLOATING has a reset frequency, floor <= cap). Vocabulary values are kept as given text;
    `unknown_fields` names (never values) those outside the seeded vocabulary. Rates are exact
    NUMERIC, dates DATE. Insert check (`instrument_term_version_checks`): the version is of the
    row's tenant and of the row's projection.
(b) `instrument_term_vocabulary` (D-61 §3): seeded PROVISIONAL for the default tenant with the
    rate types FIXED and FLOATING, the day counts ACT_360, ACT_365F, 30_360 and ACT_ACT, the
    frequencies P1M, P3M, P6M and P1Y, and the lifecycle states ACTIVE, MATURED, PREPAID,
    RESTRUCTURED and DEFAULTED. No reference index is seeded.
(c) `currency` (D-61 §6): seeded PROVISIONAL for the default tenant with the ISO 4217 codes the
    synthetic samples use (CHF, EUR, GBP, USD). Reference only.
(d) `fx_rate_observation` (D-61 §6): an observed rate between two currencies of the tenant's
    reference (composite FKs, so same-tenant), `rate > 0`. There is no write route and no
    conversion code: FX consolidation is BLOCKED. No seed.

All four tables are append-only: UPDATE, DELETE and TRUNCATE are refused (0017's
`domain_append_only`). Every seed row carries its tag: `seeded_by = '0031_term_vocabulary'`
or `'0031_currencies'` (D-11).

D-01: additive only. No column is added to any existing table, and no existing table, CHECK
or trigger is touched.

The downgrade refuses while any term row or FX observation exists, or any vocabulary or
currency row that is not this migration's seed (D-02: domain rows are kept), then removes the
triggers, the function, the indexes and the four tables (and with them the tagged seed).

Revision ID: 0031
Revises: 0030 (pre-numbered for Phase 32; Phases 33 and 34, built in parallel, hold no
migration. The coordinator re-chains `down_revision` to the merged head at merge time,
CLAUDE.md step 6)
Create Date: 2026-10-24

D-01: additive only. D-02: the downgrade restores the prior schema.
"""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR = "system:migration"
TENANT = "00000000-0000-4000-8000-000000000001"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs
VOCABULARY_TAG = "0031_term_vocabulary"
CURRENCY_TAG = "0031_currencies"
ILLUSTRATIVE = (
    "PROVISIONAL / ILLUSTRATIVE seed (D-11, D-61); not client-approved. Unknown values are "
    "kept as given and counted UNKNOWN_VALUE."
)

# Frozen copies of the code's enums and seeds (app.domain.constants; kept equal by the
# Phase 32 folds).
VOCABULARY_KINDS = ("RATE_TYPE", "DAY_COUNT", "FREQUENCY", "LIFECYCLE_STATE")
VOCABULARY = (
    ("RATE_TYPE", "FIXED", "Fixed rate"),
    ("RATE_TYPE", "FLOATING", "Floating rate"),
    ("DAY_COUNT", "ACT_360", "Actual/360"),
    ("DAY_COUNT", "ACT_365F", "Actual/365 Fixed"),
    ("DAY_COUNT", "30_360", "30/360"),
    ("DAY_COUNT", "ACT_ACT", "Actual/Actual"),
    ("FREQUENCY", "P1M", "Monthly"),
    ("FREQUENCY", "P3M", "Quarterly"),
    ("FREQUENCY", "P6M", "Semi-annual"),
    ("FREQUENCY", "P1Y", "Annual"),
    ("LIFECYCLE_STATE", "ACTIVE", "Active"),
    ("LIFECYCLE_STATE", "MATURED", "Matured"),
    ("LIFECYCLE_STATE", "PREPAID", "Prepaid"),
    ("LIFECYCLE_STATE", "RESTRUCTURED", "Restructured"),
    ("LIFECYCLE_STATE", "DEFAULTED", "Defaulted"),
)
CURRENCIES = (
    ("CHF", "Swiss franc", 2),
    ("EUR", "Euro", 2),
    ("GBP", "Pound sterling", 2),
    ("USD", "US dollar", 2),
)
FIXED, FLOATING = "FIXED", "FLOATING"

TERMS, VOCAB, CURRENCY, FX = (
    "instrument_term_version",
    "instrument_term_vocabulary",
    "currency",
    "fx_rate_observation",
)
TABLES = (VOCAB, CURRENCY, FX, TERMS)  # creation order (FX names currency)

TERM_CHECKS = """
CREATE FUNCTION instrument_term_version_checks() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM instrument_version v
        WHERE v.id = NEW.instrument_version_id AND v.tenant_id = NEW.tenant_id
            AND v.domain_projection_id = NEW.domain_projection_id
    ) THEN
        RAISE EXCEPTION 'instrument_term_version must type an instrument version of its tenant'
            ' and projection (D-61 §2, D-04)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (TERM_CHECKS,)
# (trigger, timing and events, table, level, function); domain_append_only is 0017's.
_GUARD = "domain_append_only"
TRIGGERS: tuple[tuple[str, str, str, str, str], ...] = (
    *((f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", _GUARD) for t in TABLES),
    *((f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", _GUARD) for t in TABLES),
    (f"{TERMS}_checks", "BEFORE INSERT", TERMS, "ROW", "instrument_term_version_checks"),
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _common(table: str) -> list[Any]:
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f(f"fk_{table}_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _ck(table: str, name: str, expression: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(expression, name=op.f(f"ck_{table}_{name}"))


def _create_tables() -> None:
    t = VOCAB
    op.create_table(
        t,
        sa.Column("vocabulary", sa.String(length=30), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("seeded_by", sa.String(length=100), nullable=True),
        *_common(t),
        sa.UniqueConstraint("tenant_id", "vocabulary", "code", name=op.f(f"uq_{t}_tenant_id")),
        _ck(t, "vocabulary", _in("vocabulary", VOCABULARY_KINDS)),
        _ck(t, "code", "code ~ '^[A-Z0-9][A-Z0-9_]*$'"),
    )
    t = CURRENCY
    op.create_table(
        t,
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        sa.Column("seeded_by", sa.String(length=100), nullable=True),
        *_common(t),
        sa.UniqueConstraint("tenant_id", "code", name=op.f(f"uq_{t}_tenant_id")),
        _ck(t, "code", "code ~ '^[A-Z]{3}$'"),
        _ck(t, "minor_units", "minor_units IS NULL OR minor_units BETWEEN 0 AND 4"),
    )
    t = FX
    op.create_table(
        t,
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("source_label", sa.String(length=200), nullable=True),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        *_common(t),
        sa.ForeignKeyConstraint(
            ["tenant_id", "base_currency"],
            ["currency.tenant_id", "currency.code"],
            name="fk_fx_rate_observation_base_currency_currency",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "quote_currency"],
            ["currency.tenant_id", "currency.code"],
            name="fk_fx_rate_observation_quote_currency_currency",
        ),
        _ck(t, "pair", "base_currency <> quote_currency"),
        _ck(t, "rate", "rate > 0"),
    )
    t = TERMS
    op.create_table(
        t,
        sa.Column("instrument_version_id", sa.UUID(), nullable=False),
        sa.Column("domain_projection_id", sa.UUID(), nullable=False),
        sa.Column("rate_type", sa.String(), nullable=True),
        sa.Column("reference_index", sa.String(), nullable=True),
        sa.Column("spread", sa.Numeric(), nullable=True),
        sa.Column("reset_frequency", sa.String(), nullable=True),
        sa.Column("next_reset_date", sa.Date(), nullable=True),
        sa.Column("cap_rate", sa.Numeric(), nullable=True),
        sa.Column("floor_rate", sa.Numeric(), nullable=True),
        sa.Column("day_count", sa.String(), nullable=True),
        sa.Column("payment_frequency", sa.String(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("lifecycle_state", sa.String(), nullable=True),
        sa.Column("unknown_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_common(t),
        sa.ForeignKeyConstraint(
            ["instrument_version_id"],
            ["instrument_version.id"],
            name=op.f(f"fk_{t}_instrument_version_id_instrument_version"),
        ),
        sa.ForeignKeyConstraint(
            ["domain_projection_id"],
            ["domain_projection.id"],
            name=op.f(f"fk_{t}_domain_projection_id_domain_projection"),
        ),
        sa.UniqueConstraint("instrument_version_id", name=op.f(f"uq_{t}_instrument_version_id")),
        _ck(
            t,
            "fixed_without_index",
            f"rate_type IS DISTINCT FROM '{FIXED}' OR reference_index IS NULL",
        ),
        _ck(
            t,
            "floating_with_reset",
            f"rate_type IS DISTINCT FROM '{FLOATING}' OR reset_frequency IS NOT NULL",
        ),
        _ck(t, "floor_cap", "floor_rate IS NULL OR cap_rate IS NULL OR floor_rate <= cap_rate"),
        _ck(t, "unknown_fields", "jsonb_typeof(unknown_fields) = 'array'"),
    )


# (index, table, columns), as the models declare them.
INDEXES = tuple((f"ix_{t}_tenant_id", t, ["tenant_id"]) for t in TABLES)


def _seed() -> None:
    vocabulary = sa.table(
        VOCAB,
        *(
            sa.column(c, sa.UUID() if c in ("id", "tenant_id") else None)
            for c in (
                "id",
                "tenant_id",
                "vocabulary",
                "code",
                "name",
                "description",
                "provisional",
                "created_by",
                "seeded_by",
            )
        ),
    )
    op.bulk_insert(
        vocabulary,
        [
            {
                "id": uuid.uuid5(NS, f"{VOCAB}:{TENANT}:{kind}:{code}"),
                "tenant_id": uuid.UUID(TENANT),
                "vocabulary": kind,
                "code": code,
                "name": name,
                "description": ILLUSTRATIVE,
                "provisional": True,
                "created_by": ACTOR,
                "seeded_by": VOCABULARY_TAG,
            }
            for kind, code, name in VOCABULARY
        ],
    )
    currency = sa.table(
        CURRENCY,
        *(
            sa.column(c, sa.UUID() if c in ("id", "tenant_id") else None)
            for c in (
                "id",
                "tenant_id",
                "code",
                "name",
                "minor_units",
                "provisional",
                "created_by",
                "seeded_by",
            )
        ),
    )
    op.bulk_insert(
        currency,
        [
            {
                "id": uuid.uuid5(NS, f"{CURRENCY}:{TENANT}:{code}"),
                "tenant_id": uuid.UUID(TENANT),
                "code": code,
                "name": name,
                "minor_units": minor_units,
                "provisional": True,
                "created_by": ACTOR,
                "seeded_by": CURRENCY_TAG,
            }
            for code, name, minor_units in CURRENCIES
        ],
    )


def upgrade() -> None:
    _create_tables()
    for name, table, columns in INDEXES:
        op.create_index(op.f(name), table, columns, unique=False)
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, call in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {call}()"
        )
    _seed()


def downgrade() -> None:
    # Term rows and observations are kept (D-02, D-61), and so is any vocabulary or currency
    # row not seeded here: refuse rather than drop them.
    checks = " OR ".join(
        [
            f"EXISTS (SELECT 1 FROM {TERMS})",  # noqa: S608
            f"EXISTS (SELECT 1 FROM {FX})",  # noqa: S608
            *(
                f"EXISTS (SELECT 1 FROM {table} WHERE seeded_by IS DISTINCT FROM '{tag}')"  # noqa: S608
                for table, tag in ((VOCAB, VOCABULARY_TAG), (CURRENCY, CURRENCY_TAG))
            ),
        ]
    )
    op.execute(
        f"DO $$ BEGIN IF {checks} THEN RAISE EXCEPTION 'instrument term rows, FX observations,"
        " or term vocabulary or currency rows that are not the 0031 seed exist; they are kept"
        " (D-02, D-61), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS instrument_term_version_checks()")
    for name, table, _ in reversed(INDEXES):
        op.drop_index(op.f(name), table_name=table)
    # Only the tagged seed is in the two reference tables now (refused above otherwise).
    for table in reversed(TABLES):
        op.drop_table(table)
