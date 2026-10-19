"""The obligor rating workflow (BE-20.1, CR-02; D-39, D-40).

(a) `obligor_rating` (D-39 §1-2): one immutable version per rating proposal, for one
    counterparty identity of the tenant: a grade of the tenant's `rating_grade` vocabulary,
    `effective_from` (DATE), a rationale of 1 to 2,000 characters, the proposer and the time.
    The decision fields (decider, time, comment) are set once.
    * Lifecycle trigger: a row is written IN_REVIEW, with no decision; its content
      (counterparty, grade, date, rationale, proposer) never changes; the only moves are
      IN_REVIEW -> ACTIVE | REJECTED and ACTIVE -> SUPERSEDED | RETIRED (RETIRED reserved),
      each once; the decision fields are set only on the move out of IN_REVIEW, and the
      superseded fields only on the move to SUPERSEDED, naming another version of the same
      counterparty, date and tenant. DELETE and TRUNCATE are refused.
    * Insert checks (D-04): the counterparty and the proposer are in the row's tenant, and
      the grade is a `rating_grade` code of the tenant's credit vocabulary.
    * A partial unique index allows one ACTIVE version per (counterparty, `effective_from`).
(b) The tagged seed `0023_credit_permissions` (D-40, PROVISIONAL D-11; permission change
    approved by the user 2026-10-19): `rating:propose` -> risk_analyst and `rating:approve`
    -> approver. platform_admin, data_steward and auditor gain nothing. Roles are 0003's,
    found by their deterministic IDs. The values below are a frozen copy of
    app.governance.constants.CREDIT_ROLE_PERMISSIONS.

The downgrade refuses while any rating exists (D-02: ratings are kept), then drops the
triggers, functions, index and table, and removes only the tagged seed (a hand-made grant of
a new permission blocks the permission's removal, by design, as 0010).

Revision ID: 0023
Revises: 0022 (pre-numbered for Phase 20; Phase 21 takes 0024)
Create Date: 2026-10-19

D-01: additive only. D-02: the downgrade restores the prior schema.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAG = "0023_credit_permissions"
ACTOR = "system:migration"
NS = uuid.UUID("6f1c2a52-6d0e-4f0b-9a35-3c1a8b5e7d10")  # 0003's namespace for seed IDs
DENY = "USING ERRCODE = 'insufficient_privilege'"

# Frozen copies of the code's enums and seeds (kept equal by the Phase 20 tests).
STATUSES = ("IN_REVIEW", "ACTIVE", "REJECTED", "SUPERSEDED", "RETIRED")
TRANSITIONS = {
    "IN_REVIEW": ("ACTIVE", "REJECTED"),
    "ACTIVE": ("SUPERSEDED", "RETIRED"),
}
RATIONALE_MAX = 2000
COMMENT_MAX = 1000
PERMISSIONS = {
    "rating:propose": "Propose obligor ratings (grade, effective date, rationale).",
    "rating:approve": "Approve or reject obligor ratings (maker-checker).",
}
MATRIX = {
    "risk_analyst": ["rating:propose"],
    "approver": ["rating:approve"],
}

TABLE = "obligor_rating"
# Columns that move: the status, the decision (once, out of IN_REVIEW) and the superseded
# fields (once, to SUPERSEDED). Everything else is the frozen content.
MUTABLE = (
    "status",
    "decided_by",
    "decided_at",
    "decision_comment",
    "superseded_at",
    "superseded_by_rating_id",
    "updated_at",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


_MOVES = ", ".join(f"'{a}>{b}'" for a, bs in TRANSITIONS.items() for b in bs)
_MUTABLE = ", ".join(f"'{c}'" for c in MUTABLE)

GUARD = f"""
CREATE FUNCTION obligor_rating_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutable text[] := ARRAY[{_MUTABLE}];
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'obligor ratings are never deleted (D-39 §2): % rejected', TG_OP {DENY};
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'IN_REVIEW' OR NEW.decided_at IS NOT NULL
                OR NEW.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'an obligor_rating is written IN_REVIEW, undecided (D-39 §2)' {DENY};
        END IF;
        IF NOT EXISTS (SELECT 1 FROM counterparty c WHERE c.id = NEW.counterparty_id
                AND c.tenant_id = NEW.tenant_id) THEN
            RAISE EXCEPTION 'an obligor_rating rates a counterparty of its tenant (D-04)' {DENY};
        END IF;
        IF NOT EXISTS (SELECT 1 FROM user_account u WHERE u.id = NEW.proposer_user_id
                AND u.tenant_id = NEW.tenant_id) THEN
            RAISE EXCEPTION 'an obligor_rating proposer is a user of its tenant (D-04)' {DENY};
        END IF;
        IF NOT EXISTS (SELECT 1 FROM credit_vocabulary v WHERE v.tenant_id = NEW.tenant_id
                AND v.vocabulary = 'rating_grade' AND v.code = NEW.grade) THEN
            RAISE EXCEPTION 'an obligor_rating grade is a rating_grade code of its tenant'
                ' (D-39 §1)' {DENY};
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION 'an obligor_rating''s content never changes (D-39 §2)' {DENY};
    END IF;
    IF NEW.status IS NOT DISTINCT FROM OLD.status
            OR (OLD.status || '>' || NEW.status) NOT IN ({_MOVES}) THEN
        RAISE EXCEPTION 'obligor_rating status move % -> % is not allowed (D-39 §2)',
            OLD.status, NEW.status {DENY};
    END IF;
    IF OLD.status <> 'IN_REVIEW' AND (NEW.decided_by, NEW.decided_at, NEW.decision_comment)
            IS DISTINCT FROM (OLD.decided_by, OLD.decided_at, OLD.decision_comment) THEN
        RAISE EXCEPTION 'an obligor_rating decision is recorded once, on the move out of'
            ' IN_REVIEW (D-39 §1)' {DENY};
    END IF;
    IF NEW.status <> 'SUPERSEDED' AND (NEW.superseded_at, NEW.superseded_by_rating_id)
            IS DISTINCT FROM (OLD.superseded_at, OLD.superseded_by_rating_id) THEN
        RAISE EXCEPTION 'an obligor_rating is marked superseded only on the move to'
            ' SUPERSEDED (D-39 §2)' {DENY};
    END IF;
    IF NEW.status = 'SUPERSEDED' AND NOT EXISTS (
        SELECT 1 FROM obligor_rating r WHERE r.id = NEW.superseded_by_rating_id
            AND r.id <> NEW.id AND r.tenant_id = NEW.tenant_id
            AND r.counterparty_id = NEW.counterparty_id
            AND r.effective_from = NEW.effective_from
    ) THEN
        RAISE EXCEPTION 'an obligor_rating is superseded by another version of the same'
            ' counterparty and effective date (D-39 §2)' {DENY};
    END IF;
    RETURN NEW;
END;
$$
"""

FUNCTIONS = (GUARD,)
# (trigger, timing and events, table, level, function)
TRIGGERS = (
    (
        f"{TABLE}_lifecycle",
        "BEFORE INSERT OR UPDATE OR DELETE",
        TABLE,
        "ROW",
        "obligor_rating_guard",
    ),
    (f"{TABLE}_no_truncate", "BEFORE TRUNCATE", TABLE, "STATEMENT", "obligor_rating_guard"),
)


def _indexes() -> list[tuple[str, list[str], bool, str | None]]:
    """(index, columns, unique, partial WHERE), as the model declares them."""
    return [
        (f"ix_{TABLE}_tenant_id", ["tenant_id"], False, None),
        (f"ix_{TABLE}_counterparty_id", ["counterparty_id"], False, None),
        ("uq_obligor_rating_one_active", ["counterparty_id", "effective_from"], True,
         "status = 'ACTIVE'"),  # fmt: skip
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


def _fk(column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=op.f(f"fk_{TABLE}_{column}_{target}")
    )


def _ck(name: str, condition: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(condition, name=op.f(f"ck_{TABLE}_{name}"))


def upgrade() -> None:
    # (a) the rating versions.
    op.create_table(
        TABLE,
        sa.Column("counterparty_id", sa.UUID(), nullable=False),
        sa.Column("grade", sa.String(length=50), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("rationale", sa.String(length=RATIONALE_MAX), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("proposed_by", sa.String(length=255), nullable=False),
        sa.Column("proposer_user_id", sa.UUID(), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.String(length=COMMENT_MAX), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_rating_id", sa.UUID(), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        _fk("tenant_id", "tenant"),
        _fk("counterparty_id", "counterparty"),
        _fk("proposer_user_id", "user_account"),
        _fk("superseded_by_rating_id", TABLE),
        _ck("status", _in("status", STATUSES)),
        _ck("rationale", f"char_length(rationale) BETWEEN 1 AND {RATIONALE_MAX}"),
        _ck(
            "decision_comment",
            f"decision_comment IS NULL OR char_length(decision_comment) BETWEEN 1 AND {COMMENT_MAX}",
        ),
        _ck(
            "decision",
            "(decided_by IS NULL) = (decided_at IS NULL)"
            " AND (decided_by IS NULL) = (decision_comment IS NULL)"
            " AND (status = 'IN_REVIEW') = (decided_at IS NULL)",
        ),
        _ck(
            "superseded",
            "(superseded_at IS NULL) = (superseded_by_rating_id IS NULL)"
            " AND (status = 'SUPERSEDED') = (superseded_at IS NOT NULL)",
        ),
        _ck("superseded_by", "superseded_by_rating_id IS NULL OR superseded_by_rating_id <> id"),
    )
    for name, columns, unique, where in _indexes():
        op.create_index(
            op.f(name),
            TABLE,
            columns,
            unique=unique,
            postgresql_where=sa.text(where) if where else None,
        )
    # One statement per op.execute: asyncpg cannot run several in one call.
    for function in FUNCTIONS:
        op.execute(function)
    for name, when, table, level, function in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} {when} ON {table} FOR EACH {level} EXECUTE FUNCTION {function}()"
        )
    # (b) the tagged permission seed (D-40).
    op.bulk_insert(
        permission,
        [
            {"id": _id("permission", c), "code": c, "description": d, "created_by": ACTOR,
             "seeded_by": TAG}
            for c, d in PERMISSIONS.items()
        ],  # fmt: skip
    )
    op.bulk_insert(
        role_permission,
        [
            {"role_id": _id("role", r), "permission_id": _id("permission", p),
             "created_by": ACTOR, "seeded_by": TAG}
            for r, perms in MATRIX.items()
            for p in perms
        ],  # fmt: skip
    )


def downgrade() -> None:
    # Ratings are kept (D-02, D-39): refuse rather than drop them.
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {TABLE}) THEN RAISE EXCEPTION 'obligor ratings"  # noqa: S608
        " exist; they are kept (D-02, D-39), so this downgrade is refused'; END IF; END $$"
    )
    for name, _, table, _, _ in reversed(TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in reversed(FUNCTIONS):
        name = function.split("FUNCTION ", 1)[1].split("(", 1)[0]
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    for name, _, _, _ in reversed(_indexes()):
        op.drop_index(op.f(name), table_name=TABLE)
    op.drop_table(TABLE)
    # Only the rows this migration seeded (D-11), grants first.
    for table in ("role_permission", "permission"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE seeded_by = :tag").bindparams(tag=TAG))  # noqa: S608
