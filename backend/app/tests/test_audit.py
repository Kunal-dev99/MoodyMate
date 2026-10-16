"""Audit trail (BE-3.1..3.3): rows are append-only in the database; reading them needs
audit:read, is tenant-scoped and is itself audited. (Every mutating route emitting its
event is proven in test_ws1_verification.py.)"""

import json
import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.governance.constants import DEFAULT_TENANT_ID
from app.tests.conftest import dev_token, insert_tenant, sql
from app.tests.helpers import cid, events

pytestmark = pytest.mark.db

INSERT = (
    "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type, outcome,"
    " details, details_classification) VALUES (gen_random_uuid(), $1, 'test', $2,"
    " 'test', 'SUCCESS', '{}', '{}') RETURNING id"
)


def test_audit_rows_cannot_be_updated_deleted_or_truncated(db: str) -> None:
    event_id = sql(db, INSERT, DEFAULT_TENANT_ID, "test.keep")[0]["id"]
    for statement in (
        "UPDATE audit_event SET actor = 'tampered' WHERE id = $1",
        "DELETE FROM audit_event WHERE id = $1",
    ):
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            sql(db, statement, event_id)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        sql(db, "TRUNCATE audit_event")
    assert sql(db, "SELECT actor FROM audit_event WHERE id = $1", event_id)[0]["actor"] == "test"


def test_audit_reads_are_permissioned_scoped_and_audited(api: TestClient, db: str) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    sql(db, INSERT, other, "foreign.secret")
    admin = dev_token(api, "platform_admin")
    for _ in range(3):
        assert api.get("/api/v1/users", headers=admin).status_code == 200

    def read(**params: Any) -> Any:
        r = api.get("/api/v1/audit-events", params=params, headers=dev_token(api, "auditor"))
        assert r.status_code == 200, r.text
        return r.json()

    page = read(action="user.list", limit=2)
    assert page["total"] >= 3 and len(page["items"]) == 2
    assert all(i["action"] == "user.list" and i["outcome"] == "SUCCESS" for i in page["items"])
    assert read(action="foreign.secret")["total"] == 0  # another tenant's rows are invisible
    assert read(**{"from": "2999-01-01T00:00:00Z"})["total"] == 0

    correlation = cid()
    r = api.get(
        "/api/v1/audit-events",
        params={"resource_type": "user"},
        headers={**dev_token(api, "auditor"), "X-Correlation-ID": correlation},
    )
    assert r.status_code == 200
    [event] = events(db, correlation)
    assert (event["action"], event["outcome"]) == ("audit.read", "SUCCESS")
    assert json.loads(event["details"])["filters"] == {"resource_type": "user"}
