"""BE-2.2: the seed matches the plan's role → permission matrix; downgrade removes it."""

import pytest
from alembic import command

from app.governance.constants import (
    DEFAULT_TENANT_ID,
    ROLE_PERMISSIONS,
    SEED_TAG,
    WS1_ROLE_PERMISSIONS,
    WS2_ROLE_PERMISSIONS,
    Permission,
    Role,
)
from app.ingestion.constants import WS2_PERMISSION_SEED_TAG
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
    matrix = _matrix(db)
    assert matrix == expected
    assert "schema:approve" not in matrix["platform_admin"]  # segregation of duties
    # Feature 02 matrix (BE-7.2): only data_steward writes WS2, only approver activates
    # mappings, and platform_admin reads WS2 without seeing quarantined values.
    holders = {p: {r for r, perms in matrix.items() if p in perms} for p in Permission}
    for write in ("source:manage", "import:write", "mapping:propose", "quality:review"):
        assert holders[Permission(write)] == {"data_steward"}, write
    assert holders[Permission.MAPPING_APPROVE] == {"approver"}
    assert holders[Permission.QUARANTINE_READ] == {"data_steward", "approver", "auditor"}
    for read in ("source:read", "import:read", "mapping:read"):
        assert holders[Permission(read)] == set(expected), read


def test_seed_rows_are_tagged(db: str) -> None:
    tenant = sql(db, "SELECT seeded_by FROM tenant WHERE id = $1", DEFAULT_TENANT_ID)
    assert tenant[0]["seeded_by"] == SEED_TAG
    untagged = sql(
        db, "SELECT count(*) AS n FROM role WHERE seeded_by IS DISTINCT FROM $1", SEED_TAG
    )
    assert untagged[0]["n"] == 0
    # Each permission and grant carries the tag of the migration that seeded it.
    ws2 = {p.value for perms in WS2_ROLE_PERMISSIONS.values() for p in perms}
    for row in sql(db, "SELECT code, seeded_by FROM permission"):
        assert row["seeded_by"] == (WS2_PERMISSION_SEED_TAG if row["code"] in ws2 else SEED_TAG)
    grants = sql(
        db,
        "SELECT r.code AS role, p.code AS perm, rp.seeded_by FROM role_permission rp"
        " JOIN role r ON r.id = rp.role_id JOIN permission p ON p.id = rp.permission_id",
    )
    for g in grants:
        tag = WS2_PERMISSION_SEED_TAG if g["perm"] in ws2 else SEED_TAG
        assert g["seeded_by"] == tag, (g["role"], g["perm"])


def test_ws2_permission_downgrade_removes_only_its_rows(db: str) -> None:
    """BE-7.2: downgrading 0010 removes exactly the rows it tagged; the WS1 seed and
    rows added by hand (untagged) stay."""
    cfg = alembic_config(db)
    sql(
        db,
        "INSERT INTO permission (id, code, description, created_by)"
        " VALUES (gen_random_uuid(), 'custom:read', 'Added by hand', 'test')",
    )
    sql(
        db,
        "INSERT INTO role_permission (role_id, permission_id, created_by)"
        " SELECT r.id, p.id, 'test' FROM role r, permission p"
        " WHERE r.code = 'auditor' AND p.code = 'custom:read'",
    )
    try:
        command.downgrade(cfg, "0009")
        matrix = _matrix(db)
        ws1 = {role.value: {p.value for p in perms} for role, perms in WS1_ROLE_PERMISSIONS.items()}
        ws1["auditor"] = ws1["auditor"] | {"custom:read"}
        assert matrix == ws1
        tagged = sql(
            db,
            "SELECT (SELECT count(*) FROM permission WHERE seeded_by = $1)"
            " + (SELECT count(*) FROM role_permission WHERE seeded_by = $1) AS n",
            WS2_PERMISSION_SEED_TAG,
        )
        assert tagged[0]["n"] == 0
    finally:
        command.upgrade(cfg, "head")
    assert _matrix(db)["auditor"] == {p.value for p in ROLE_PERMISSIONS[Role.AUDITOR]} | {
        "custom:read"
    }


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
