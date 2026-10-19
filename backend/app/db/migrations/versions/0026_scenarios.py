"""Stress scenarios: versions, exploration and promotion (BE-24.1, WS4 RO-02; D-44, D-47).

(a) `scenario`: a named scenario, its code unique per tenant, its author a user of its
    tenant. Append-only: UPDATE, DELETE and TRUNCATE are refused.
(b) `scenario_version` (D-44 §5-7): one version of a scenario's shocks (1 to 5, JSONB, with
    their sha256), its provenance (`origin` MANUAL or AI_PROPOSED, `parent_version_id`,
    `parameter_sources`) and its lifecycle fields (author, `confirmed_at`, submit, decision,
    close, `ai_acknowledged`, `acknowledged_defaults`).
    * Lifecycle trigger: a row is written DRAFT, without lifecycle fields, in the tenant of
      its scenario and author (and of its parent version, which is a version of the same
      scenario). The shocks, their sha256 and the change summary change only while DRAFT.
      The status moves only along D-44 §5 (each move once: the moves form no cycle), and each
      move sets only its own fields. The author never decides (maker-checker, D-44 §6).
      DELETE and TRUNCATE are refused.
(c) `scenario_change`: one row per shock, written when the version leaves DRAFT; each row
    repeats its version's shock at that index and template. Append-only (UPDATE, DELETE and
    TRUNCATE refused).
(d) The tagged seed `0026_scenario_permissions` (D-47, approved by the user 2026-10-19,
    PROVISIONAL D-11): `scenario:propose` -> risk_analyst and `scenario:approve` -> approver.
    platform_admin, data_steward and auditor gain nothing. Roles are 0003's, found by their
    deterministic IDs. The values below are a frozen copy of
    app.governance.constants.SCENARIO_ROLE_PERMISSIONS.

The downgrade refuses while any scenario row exists (D-02: scenarios are kept), then removes
the triggers, functions and tables, and only the tagged seed (a hand-made grant of a new
permission blocks the permission's removal, by design, as 0010).

Revision ID: 0026
Revises: 0023 (pre-numbered for Phase 24; Phases 21-22 hold 0024-0025, and the coordinator
re-chains `down_revision` to the merged head at merge time, CLAUDE.md step 6)
Create Date: 2026-10-19

D-01: additive only. D-02: the downgrade restores the prior schema.
"""

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0026_scenario_permissions"
ACTOR = "system:migration"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs
DENY = "USING ERRCODE = 'insufficient_privilege'"

# Frozen copies of the code's enums, bounds and seed (kept equal by the Phase 24 tests).
STATUSES = (
    "DRAFT",
    "CONFIRMED_FOR_EXPLORATION",
    "IN_REVIEW",
    "APPROVED_FOR_TEST",
    "RUNNING",
    "TESTED",
    "REJECTED",
    "CLOSED",
)
TRANSITIONS = {
    "DRAFT": ("CONFIRMED_FOR_EXPLORATION", "IN_REVIEW"),
    "CONFIRMED_FOR_EXPLORATION": ("IN_REVIEW", "CLOSED"),
    "IN_REVIEW": ("APPROVED_FOR_TEST", "REJECTED", "CLOSED"),
    "APPROVED_FOR_TEST": ("CLOSED",),
    "REJECTED": ("CLOSED",),
}
ORIGINS = ("MANUAL", "AI_PROPOSED")
TEMPLATES = ("DELAYED_INFLOWS", "DEPOSIT_WITHDRAWAL", "FUNDING_UNAVAILABLE")
MIN_SHOCKS, MAX_SHOCKS = 1, 5
CODE_MAX, NAME_MAX, DESCRIPTION_MAX, SUMMARY_MAX, COMMENT_MAX = 64, 200, 2000, 1000, 1000
PERMISSIONS = {
    "scenario:propose": "Author stress scenarios, confirm own versions for exploration and "
    "submit them for promotion.",
    "scenario:approve": "Approve or reject stress scenario versions for promotion "
    "(maker-checker), and close approved ones.",
}
MATRIX = {
    "risk_analyst": ["scenario:propose"],
    "approver": ["scenario:approve"],
}

SCENARIO, VERSION, CHANGE = "scenario", "scenario_version", "scenario_change"
TABLES = (SCENARIO, VERSION, CHANGE)

# Per target status: the columns a move to it may set (D-44 §5). A DRAFT edit may change
# only EDITABLE.
MOVE_COLUMNS = {
    "CONFIRMED_FOR_EXPLORATION": ("confirmed_at", "ai_acknowledged", "acknowledged_defaults"),
    "IN_REVIEW": (
        "submitted_by",
        "submitted_at",
        "submit_comment",
        "ai_acknowledged",
        "acknowledged_defaults",
    ),
    "APPROVED_FOR_TEST": ("decided_by", "decided_at", "decision_comment"),
    "REJECTED": ("decided_by", "decided_at", "decision_comment"),
    "CLOSED": ("closed_by", "closed_at", "close_comment"),
}
EDITABLE = ("shocks", "shocks_sha256", "change_summary", "updated_at")
LIFECYCLE_FIELDS = (
    "confirmed_at",
    "submitted_by",
    "submitted_at",
    "submit_comment",
    "decided_by",
    "decided_at",
    "decision_comment",
    "closed_by",
    "closed_at",
    "close_comment",
)
LIFECYCLE_PAIRS = (
    "(submitted_by IS NULL) = (submitted_at IS NULL)"
    " AND (decided_by IS NULL) = (decided_at IS NULL)"
    " AND (decided_by IS NULL) = (decision_comment IS NULL)"
    " AND (closed_by IS NULL) = (closed_at IS NULL)"
)
LIFECYCLE = (
    "(status <> 'DRAFT' OR (confirmed_at IS NULL AND submitted_at IS NULL"
    " AND decided_at IS NULL AND closed_at IS NULL))"
    " AND (status <> 'CONFIRMED_FOR_EXPLORATION' OR (confirmed_at IS NOT NULL"
    " AND submitted_at IS NULL))"
    " AND (status <> 'IN_REVIEW' OR (submitted_at IS NOT NULL AND decided_at IS NULL))"
    " AND (status NOT IN ('APPROVED_FOR_TEST', 'REJECTED') OR decided_at IS NOT NULL)"
    " AND ((status = 'CLOSED') = (closed_at IS NOT NULL))"
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _array(values: Sequence[str]) -> str:
    return "ARRAY[" + ", ".join(f"'{v}'" for v in values) + "]::text[]"


_MOVES = ", ".join(f"'{a}>{b}'" for a, bs in TRANSITIONS.items() for b in bs)
_MOVE_ALLOWED = "\n".join(
    f"        {'IF' if i == 0 else 'ELSIF'} NEW.status = '{status}' THEN"
    f" allowed := {_array(('status', 'updated_at', *columns))};"
    for i, (status, columns) in enumerate(MOVE_COLUMNS.items())
)
_UNSET = " OR ".join(f"NEW.{c} IS NOT NULL" for c in LIFECYCLE_FIELDS)

SCENARIO_GUARD = f"""
CREATE FUNCTION scenario_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'a scenario is append-only (D-44 §5): % rejected', TG_OP {DENY};
    END IF;
    IF NOT EXISTS (SELECT 1 FROM user_account u WHERE u.id = NEW.author_user_id
            AND u.tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'a scenario author is a user of its tenant (D-04)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

VERSION_GUARD = f"""
CREATE FUNCTION scenario_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    allowed text[];
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'scenario versions are never deleted (D-44 §5): % rejected', TG_OP
            {DENY};
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' OR {_UNSET} OR NEW.ai_acknowledged
                OR NEW.acknowledged_defaults <> '[]'::jsonb THEN
            RAISE EXCEPTION 'a scenario_version is written DRAFT, without lifecycle fields'
                ' (D-44 §5)' {DENY};
        END IF;
        IF NOT EXISTS (SELECT 1 FROM scenario s WHERE s.id = NEW.scenario_id
                AND s.tenant_id = NEW.tenant_id) THEN
            RAISE EXCEPTION 'a scenario_version belongs to a scenario of its tenant (D-04)'
                {DENY};
        END IF;
        IF NOT EXISTS (SELECT 1 FROM user_account u WHERE u.id = NEW.author_user_id
                AND u.tenant_id = NEW.tenant_id) THEN
            RAISE EXCEPTION 'a scenario_version author is a user of its tenant (D-04)' {DENY};
        END IF;
        IF NEW.parent_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM scenario_version p WHERE p.id = NEW.parent_version_id
                AND p.scenario_id = NEW.scenario_id AND p.tenant_id = NEW.tenant_id
        ) THEN
            RAISE EXCEPTION 'a scenario_version adapts a version of the same scenario'
                ' (D-44 §7)' {DENY};
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        IF OLD.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'a scenario_version changes only while DRAFT; it is %'
                ' (D-44 §5)', OLD.status {DENY};
        END IF;
        allowed := {_array(EDITABLE)};
    ELSE
        IF (OLD.status || '>' || NEW.status) NOT IN ({_MOVES}) THEN
            RAISE EXCEPTION 'scenario_version status move % -> % is not allowed (D-44 §5)',
                OLD.status, NEW.status {DENY};
        END IF;
{_MOVE_ALLOWED}
        END IF;
        IF NEW.status IN ('APPROVED_FOR_TEST', 'REJECTED')
                AND NEW.decided_by IS NOT DISTINCT FROM OLD.authored_by THEN
            RAISE EXCEPTION 'the author never decides on a scenario_version (D-44 §6)' {DENY};
        END IF;
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'a scenario_version move % -> % changes only its own fields'
            ' (D-44 §5)', OLD.status, NEW.status {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

CHANGE_GUARD = f"""
CREATE FUNCTION scenario_change_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'scenario_change is append-only (D-44 §5): % rejected', TG_OP {DENY};
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM scenario_version v WHERE v.id = NEW.scenario_version_id
            AND v.tenant_id = NEW.tenant_id AND v.status <> 'DRAFT'
            AND v.shocks -> NEW.shock_index = NEW.shock
            AND NEW.shock ->> 'template' = NEW.template
    ) THEN
        RAISE EXCEPTION 'a scenario_change repeats the shock of a version of its tenant that'
            ' has left DRAFT (D-44 §5)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (SCENARIO_GUARD, VERSION_GUARD, CHANGE_GUARD)
# (trigger, timing and events, table, level, function)
TRIGGERS = tuple(
    trigger
    for table in TABLES
    for trigger in (
        (f"{table}_guard", "BEFORE INSERT OR UPDATE OR DELETE", table, "ROW", f"{table}_guard"),
        (f"{table}_no_truncate", "BEFORE TRUNCATE", table, "STATEMENT", f"{table}_guard"),
    )
)


def _indexes() -> list[tuple[str, str, list[str]]]:
    """(index, table, columns), as the models declare them."""
    return [
        (f"ix_{SCENARIO}_tenant_id", SCENARIO, ["tenant_id"]),
        (f"ix_{VERSION}_tenant_id", VERSION, ["tenant_id"]),
        (f"ix_{VERSION}_scenario_id", VERSION, ["scenario_id"]),
        (f"ix_{VERSION}_author_user_id", VERSION, ["author_user_id"]),
        (f"ix_{CHANGE}_tenant_id", CHANGE, ["tenant_id"]),
        (f"ix_{CHANGE}_scenario_version_id", CHANGE, ["scenario_version_id"]),
    ]


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


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{table}_{column}_{target}")
    )


def _ck(table: str, name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{table}_{name}"))


def _common(table: str) -> list[Any]:
    return [
        sa.Column("tenant_id", sa.UUID(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
        _fk(table, "tenant_id", "tenant"),
    ]


def _comment(column: str) -> str:
    return f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {COMMENT_MAX}"


def upgrade() -> None:
    # (a) scenarios.
    op.create_table(
        SCENARIO,
        sa.Column("code", sa.String(length=CODE_MAX), nullable=False),
        sa.Column("name", sa.String(length=NAME_MAX), nullable=False),
        sa.Column("description", sa.String(length=DESCRIPTION_MAX), nullable=True),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        *_common(SCENARIO),
        _fk(SCENARIO, "author_user_id", "user_account"),
        sa.UniqueConstraint("tenant_id", "code", name=op.f("uq_scenario_tenant_code")),
        _ck(SCENARIO, "name", f"char_length(name) BETWEEN 1 AND {NAME_MAX}"),
    )
    # (b) their versions.
    op.create_table(
        VERSION,
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("parent_version_id", sa.UUID(), nullable=True),
        sa.Column("parameter_sources", postgresql.JSONB(), nullable=True),
        sa.Column("shocks", postgresql.JSONB(), nullable=False),
        sa.Column("shocks_sha256", sa.String(length=64), nullable=False),
        sa.Column("change_summary", sa.String(length=SUMMARY_MAX), nullable=True),
        sa.Column("authored_by", sa.String(length=255), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by", sa.String(length=255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_comment", sa.String(length=COMMENT_MAX), nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(length=COMMENT_MAX), nullable=True),
        sa.Column("closed_by", sa.String(length=255), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_comment", sa.String(length=COMMENT_MAX), nullable=True),
        sa.Column("ai_acknowledged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "acknowledged_defaults",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        *_common(VERSION),
        _fk(VERSION, "scenario_id", SCENARIO),
        _fk(VERSION, "parent_version_id", VERSION),
        _fk(VERSION, "author_user_id", "user_account"),
        sa.UniqueConstraint(
            "scenario_id", "version", name=op.f("uq_scenario_version_scenario_version")
        ),
        _ck(VERSION, "status", _in("status", STATUSES)),
        _ck(VERSION, "origin", _in("origin", ORIGINS)),
        _ck(VERSION, "version", "version >= 1"),
        _ck(
            VERSION,
            "shocks",
            "jsonb_typeof(shocks) = 'array' AND jsonb_array_length(shocks) BETWEEN "
            f"{MIN_SHOCKS} AND {MAX_SHOCKS}",
        ),
        _ck(VERSION, "shocks_sha256", "shocks_sha256 ~ '^[0-9a-f]{64}$'"),
        _ck(VERSION, "parameter_sources", "origin <> 'MANUAL' OR parameter_sources IS NULL"),
        _ck(VERSION, "acknowledged_defaults", "jsonb_typeof(acknowledged_defaults) = 'array'"),
        _ck(VERSION, "parent", "parent_version_id IS NULL OR parent_version_id <> id"),
        _ck(VERSION, "submit_comment", _comment("submit_comment")),
        _ck(VERSION, "decision_comment", _comment("decision_comment")),
        _ck(VERSION, "close_comment", _comment("close_comment")),
        _ck(VERSION, "lifecycle_pairs", LIFECYCLE_PAIRS),
        _ck(VERSION, "lifecycle", LIFECYCLE),
    )
    # (c) the materialised shocks.
    op.create_table(
        CHANGE,
        sa.Column("scenario_version_id", sa.UUID(), nullable=False),
        sa.Column("shock_index", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=30), nullable=False),
        sa.Column("shock", postgresql.JSONB(), nullable=False),
        *_common(CHANGE),
        _fk(CHANGE, "scenario_version_id", VERSION),
        sa.UniqueConstraint(
            "scenario_version_id", "shock_index", name=op.f("uq_scenario_change_version_index")
        ),
        _ck(CHANGE, "template", _in("template", TEMPLATES)),
        _ck(CHANGE, "shock_index", f"shock_index BETWEEN 0 AND {MAX_SHOCKS - 1}"),
    )
    for name, table, columns in _indexes():
        op.create_index(op.f(name), table, columns)
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )
    # (d) the tagged permission seed (D-47).
    op.bulk_insert(
        permission,
        [
            {
                "id": _id("permission", c),
                "code": c,
                "description": d,
                "created_by": ACTOR,
                "seeded_by": TAG,
            }
            for c, d in PERMISSIONS.items()
        ],  # fmt: skip
    )
    op.bulk_insert(
        role_permission,
        [
            {
                "role_id": _id("role", r),
                "permission_id": _id("permission", p),
                "created_by": ACTOR,
                "seeded_by": TAG,
            }
            for r, perms in MATRIX.items()
            for p in perms
        ],  # fmt: skip
    )


def downgrade() -> None:
    # Scenarios are kept (D-02, D-44): refuse rather than drop them.
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {SCENARIO}) THEN RAISE EXCEPTION 'scenarios"  # noqa: S608
        " exist; they are kept (D-02, D-44), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    for name, table, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
    # Only the rows this migration seeded (D-11), grants first.
    for table in ("role_permission", "permission"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
