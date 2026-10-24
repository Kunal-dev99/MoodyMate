"""Legal entities and identity links (BE-31.1, WS3 FC-02, DG-05; D-58).

(a) `legal_entity` (D-58 §2): a stable identity per (tenant, source system, code), the code
    being a position's `legal_entity_ref`. `provisional` (always true for now): its attributes
    and the source-of-truth rule are deferred to the client (Data Model §11). Insert check (0017's
    `domain_identity_checks`): the source is of the row's tenant.
(b) `domain_identity_link` (D-58 §2-3): one link from a position version, cash-flow event or
    credit-exposure version (`source_kind`, `source_row_id`) to the instrument, counterparty
    or legal entity (`target_kind`, `target_id`) its text key names, with the `method` and
    `resolver_version` that resolved it and its creator and time. `UNIQUE (source_kind,
    source_row_id, target_kind)`: a link is written once and never changed. Insert check
    (`domain_identity_link_checks`): the pair is one D-58 allows, both ends are rows of the
    link's tenant (same-tenant on both ends, D-04), and the target's key is the source row's
    own key.

Both tables are append-only: UPDATE, DELETE and TRUNCATE are refused (0017's
`domain_append_only`).

D-01: additive only. No column is added to `instrument_version`, `position_version`,
`cashflow_event` or the credit tables (D-58 §1), and no existing table, CHECK or trigger is
touched. There is no seed: identities and links are written at runtime by the projection step
and by POST /identity-links/reconcile.

The downgrade refuses while either table has a row (D-02: identities and links are kept),
then removes the triggers, the function, the indexes and the two tables.

Revision ID: 0030
Revises: 0027 (pre-numbered for Phase 31; Phases 21-22 hold 0024-0025 and Phases 26-27 hold
0028-0029, none built yet; Phases 28-30 add no migration, and Phase 32 holds 0031. The
coordinator re-chains `down_revision` to the merged head at merge time, CLAUDE.md step 6)
Create Date: 2026-10-24

D-01: additive only. D-02: the downgrade restores the prior schema.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DENY = "USING ERRCODE = 'insufficient_privilege'"

# Frozen copies of the code's enums (app.domain.constants; kept equal by the Phase 31 tests).
SOURCE_KINDS = ("POSITION_VERSION", "CASHFLOW_EVENT", "CREDIT_EXPOSURE")
TARGET_KINDS = ("INSTRUMENT", "COUNTERPARTY", "LEGAL_ENTITY")
METHODS = ("BUSINESS_KEY_UNIQUE",)
# (source kind, source table, its key column, target kind) for every pair a link may join.
# CREDIT_EXPOSURE -> INSTRUMENT is allowed here (the exposure carries an `instrument_key`)
# although resolver 1 does not write it, so linking it later needs no CHECK or trigger change.
PAIRS = (
    ("POSITION_VERSION", "position_version", "instrument_key", "INSTRUMENT"),
    ("POSITION_VERSION", "position_version", "legal_entity_ref", "LEGAL_ENTITY"),
    ("CASHFLOW_EVENT", "cashflow_event", "instrument_key", "INSTRUMENT"),
    ("CREDIT_EXPOSURE", "credit_exposure_version", "counterparty_key", "COUNTERPARTY"),
    ("CREDIT_EXPOSURE", "credit_exposure_version", "instrument_key", "INSTRUMENT"),
)
# target kind -> (table, key column)
TARGETS = {
    "INSTRUMENT": ("instrument", "business_key"),
    "COUNTERPARTY": ("counterparty", "business_key"),
    "LEGAL_ENTITY": ("legal_entity", "code"),
}

ENTITY, LINK = "legal_entity", "domain_identity_link"
TABLES = (ENTITY, LINK)  # creation order


def _source_branches() -> str:
    branches = []
    for i, (kind, table, column, target) in enumerate(PAIRS):
        keyword = "IF" if i == 0 else "ELSIF"
        branches.append(
            f"    {keyword} NEW.source_kind = '{kind}' AND NEW.target_kind = '{target}' THEN\n"
            f"        SELECT s.{column} INTO source_key FROM {table} s\n"
            "            WHERE s.id = NEW.source_row_id AND s.tenant_id = NEW.tenant_id;"
        )
    branches.append(
        "    ELSE\n"
        "        RAISE EXCEPTION 'domain_identity_link: % -> % is not a link D-58 allows',\n"
        f"            NEW.source_kind, NEW.target_kind {DENY};\n"
        "    END IF;"
    )
    return "\n".join(branches)


def _target_branches() -> str:
    branches = []
    for i, (kind, (table, column)) in enumerate(TARGETS.items()):
        keyword = "IF" if i == 0 else "ELSIF"
        branches.append(
            f"    {keyword} NEW.target_kind = '{kind}' THEN\n"
            f"        SELECT t.{column} INTO target_key FROM {table} t\n"
            "            WHERE t.id = NEW.target_id AND t.tenant_id = NEW.tenant_id;"
        )
    branches.append("    END IF;")
    return "\n".join(branches)


LINK_CHECKS = f"""
CREATE FUNCTION domain_identity_link_checks() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_key text;
    target_key text;
BEGIN
{_source_branches()}
    IF source_key IS NULL OR source_key = '' THEN
        RAISE EXCEPTION 'domain_identity_link must start from a % row of its tenant that has'
            ' a key (D-58 §2, D-04)', NEW.source_kind {DENY};
    END IF;
{_target_branches()}
    IF target_key IS NULL THEN
        RAISE EXCEPTION 'domain_identity_link must name a % of its tenant (D-58 §2, D-04)',
            NEW.target_kind {DENY};
    END IF;
    IF target_key <> source_key THEN
        RAISE EXCEPTION 'domain_identity_link must resolve its source row''s own key'
            ' (D-58 §3)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (LINK_CHECKS,)
# (trigger, timing and events, table, level, function); domain_append_only and
# domain_identity_checks are 0017's functions.
_GUARD = "domain_append_only"
TRIGGERS: tuple[tuple[str, str, str, str, str], ...] = (
    *((f"{t}_append_only", "BEFORE UPDATE OR DELETE", t, "ROW", _GUARD) for t in TABLES),
    *((f"{t}_no_truncate", "BEFORE TRUNCATE", t, "STATEMENT", _GUARD) for t in TABLES),
    (f"{ENTITY}_checks", "BEFORE INSERT", ENTITY, "ROW", "domain_identity_checks"),
    (f"{LINK}_checks", "BEFORE INSERT", LINK, "ROW", "domain_identity_link_checks"),
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
    t = ENTITY
    op.create_table(
        t,
        sa.Column("source_system_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("provisional", sa.Boolean(), nullable=False),
        *_common(t),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
            name=op.f("fk_legal_entity_source_system_id_source_system"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_system_id", "code", name=op.f("uq_legal_entity_tenant_id")
        ),
        _ck(t, "code", "char_length(code) >= 1"),
    )
    t = LINK
    op.create_table(
        t,
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_row_id", sa.UUID(), nullable=False),
        sa.Column("target_kind", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("resolver_version", sa.Integer(), nullable=False),
        *_common(t),
        sa.UniqueConstraint(
            "source_kind",
            "source_row_id",
            "target_kind",
            name=op.f("uq_domain_identity_link_source_kind"),
        ),
        _ck(t, "source_kind", _in("source_kind", SOURCE_KINDS)),
        _ck(t, "target_kind", _in("target_kind", TARGET_KINDS)),
        _ck(t, "method", _in("method", METHODS)),
        _ck(t, "resolver_version", "resolver_version >= 1"),
    )


# (index, table, columns), as the models declare them.
INDEXES = (
    (f"ix_{ENTITY}_tenant_id", ENTITY, ["tenant_id"]),
    (f"ix_{ENTITY}_source_system_id", ENTITY, ["source_system_id"]),
    (f"ix_{LINK}_tenant_id", LINK, ["tenant_id"]),
    (f"ix_{LINK}_target", LINK, ["target_kind", "target_id"]),
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


def downgrade() -> None:
    # Identities and links are kept (D-02, D-58): refuse rather than drop them.
    checks = " OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in TABLES)
    op.execute(
        f"DO $$ BEGIN IF {checks} THEN RAISE EXCEPTION 'legal entities or identity links exist;"
        " they are kept (D-02, D-58), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS domain_identity_link_checks()")
    for name, table, _ in reversed(INDEXES):
        op.drop_index(op.f(name), table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
