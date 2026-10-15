"""BE-2.2: the seed matches the plan's role → permission matrix; downgrade removes it."""

import pytest
from alembic import command

from app.governance.constants import DEFAULT_TENANT_ID, ROLE_PERMISSIONS, SEED_TAG
from app.tests.conftest import alembic_config, sql

pytestmark = pytest.mark.db

IDENTITY_TABLES = ("tenant", "user_account", "role", "permission", "user_role", "role_permission")


def _matrix(url: str) -> dict[str, set[str]]:
    rows = sql(
        url,
        "SELECT r.code AS role, p.code AS perm FROM role r"
        " JOIN role_permission rp ON rp.role_id = r.id"
        " JOIN permission p ON p.id = rp.permission_id",
    )
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["role"], set()).add(row["perm"])
    return out


def test_seeded_matrix_matches_plan(db: str) -> None:
    expected = {role.value: {p.value for p in perms} for role, perms in ROLE_PERMISSIONS.items()}
    assert _matrix(db) == expected
    assert "schema:approve" not in _matrix(db)["platform_admin"]  # segregation of duties


def test_seed_rows_are_tagged(db: str) -> None:
    tenant = sql(db, "SELECT seeded_by FROM tenant WHERE id = $1", DEFAULT_TENANT_ID)
    assert tenant[0]["seeded_by"] == SEED_TAG
    for table in ("role", "permission", "role_permission"):
        untagged = sql(
            db, f"SELECT count(*) AS n FROM {table} WHERE seeded_by IS DISTINCT FROM $1", SEED_TAG
        )  # noqa: S608
        assert untagged[0]["n"] == 0, table


def test_downgrade_removes_seed_and_upgrade_restores_it(db: str) -> None:
    cfg = alembic_config(db)
    command.downgrade(cfg, "0002")
    for table in IDENTITY_TABLES:
        assert sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"] == 0, table  # noqa: S608
    command.upgrade(cfg, "head")
    assert len(_matrix(db)) == len(ROLE_PERMISSIONS)


def test_seed_downgrade_refuses_to_delete_user_data(db: str) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.tests.conftest import insert_user

    insert_user(db, "https://idp.test", "holder", roles=("auditor",))
    with pytest.raises(IntegrityError, match="ForeignKeyViolation"):
        command.downgrade(alembic_config(db), "0002")
    # Nothing was removed.
    assert len(_matrix(db)) == len(ROLE_PERMISSIONS)
