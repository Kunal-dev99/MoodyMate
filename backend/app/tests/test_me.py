"""BE-2.6: /me and /me/permissions per seeded role."""

import pytest
from fastapi.testclient import TestClient

from app.governance.constants import DEFAULT_TENANT_ID, ROLE_PERMISSIONS, Permission, Role
from app.tests.conftest import dev_token

pytestmark = pytest.mark.db


@pytest.mark.parametrize("role", list(Role))
def test_me_and_permissions_per_role(api: TestClient, role: Role) -> None:
    headers = dev_token(api, role.value)
    me = api.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["roles"] == [role.value]
    assert me.json()["tenant_id"] == str(DEFAULT_TENANT_ID)
    perms = api.get("/api/v1/me/permissions", headers=headers).json()["permissions"]
    assert perms == sorted(p.value for p in ROLE_PERMISSIONS[role])


@pytest.mark.parametrize(
    "role", [r for r in Role if Permission.ADMIN_READ not in ROLE_PERMISSIONS[r]]
)
def test_admin_endpoints_forbidden_without_admin_read(api: TestClient, role: Role) -> None:
    r = api.get("/api/v1/users", headers=dev_token(api, role.value))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert r.json()["details"] == {"required_permission": "admin:read"}


def test_me_requires_authentication(api: TestClient) -> None:
    assert api.get("/api/v1/me").status_code == 401
    assert api.get("/api/v1/me/permissions").status_code == 401


@pytest.mark.parametrize(
    ("role", "ws2"),
    [
        ("platform_admin", {"source:read", "import:read", "mapping:read"}),
        (
            "data_steward",
            {
                "source:read",
                "source:manage",
                "import:read",
                "import:write",
                "mapping:read",
                "mapping:propose",
                "quarantine:read",
                "quality:review",
            },
        ),
        ("risk_analyst", {"source:read", "import:read", "mapping:read"}),
        (
            "approver",
            {"source:read", "import:read", "mapping:read", "mapping:approve", "quarantine:read"},
        ),
        ("auditor", {"source:read", "import:read", "mapping:read", "quarantine:read"}),
    ],
)
def test_ws2_permissions_per_role(api: TestClient, role: str, ws2: set[str]) -> None:
    """BE-7.2: the Feature 02 matrix, written out from the plan (not from constants)."""
    perms = set(
        api.get("/api/v1/me/permissions", headers=dev_token(api, role)).json()["permissions"]
    )
    ws2_codes = {"source:", "import:", "mapping:", "quarantine:", "quality:"}
    assert {p for p in perms if any(p.startswith(c) for c in ws2_codes)} == ws2
