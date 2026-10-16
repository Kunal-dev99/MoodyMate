"""Phase 3: audit trail (BE-3.1…3.3), classification (BE-3.4) and masking (BE-3.5)."""

import asyncio
import io
import json
import logging
import uuid
from typing import Annotated, Any

import asyncpg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import require_permission
from app.core.classification import Classification, Classified, MaskedValue, mask_model
from app.core.config import Settings
from app.core.logging import REDACTED, JsonFormatter
from app.core.principal import Principal
from app.governance.constants import (
    DEFAULT_TENANT_ID,
    ROLE_CLEARANCE,
    AuditOutcome,
    ClassificationCode,
)
from app.governance.service import audit
from app.main import create_app
from app.tests.conftest import dev_token, insert_tenant, sql

pytestmark = pytest.mark.db


def events(url: str, correlation_id: str) -> list[Any]:
    return sql(url, "SELECT * FROM audit_event WHERE correlation_id = $1", correlation_id)


def cid() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


# --- BE-3.1 append-only ------------------------------------------------------------------


def _insert_event(url: str) -> Any:
    return sql(
        url,
        "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type, outcome,"
        " details, details_classification) VALUES (gen_random_uuid(), $1, 'test', 'test.x',"
        " 'test', 'SUCCESS', '{}', '{}') RETURNING id",
        DEFAULT_TENANT_ID,
    )[0]["id"]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_event SET actor = 'tampered' WHERE id = $1",
        "DELETE FROM audit_event WHERE id = $1",
    ],
)
def test_audit_rows_cannot_be_updated_or_deleted(db: str, statement: str) -> None:
    event_id = _insert_event(db)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        sql(db, statement, event_id)
    assert sql(db, "SELECT actor FROM audit_event WHERE id = $1", event_id)[0]["actor"] == "test"


def test_audit_table_cannot_be_truncated(db: str) -> None:
    _insert_event(db)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        sql(db, "TRUNCATE audit_event")


# --- BE-3.2 recording ----------------------------------------------------------------------


def test_successful_action_produces_exactly_one_event(api: TestClient, db: str) -> None:
    c = cid()
    r = api.get(
        "/api/v1/users", headers={**dev_token(api, "platform_admin"), "X-Correlation-ID": c}
    )
    assert r.status_code == 200
    rows = events(db, c)
    assert len(rows) == 1
    e = rows[0]
    assert (e["action"], e["outcome"], e["resource_type"]) == ("user.list", "SUCCESS", "user")
    assert e["actor"].startswith("user:") and e["actor_user_id"] is not None
    assert e["tenant_id"] == DEFAULT_TENANT_ID


def test_denied_action_produces_exactly_one_event(api: TestClient, db: str) -> None:
    c = cid()
    r = api.get("/api/v1/users", headers={**dev_token(api, "risk_analyst"), "X-Correlation-ID": c})
    assert r.status_code == 403
    rows = events(db, c)
    assert len(rows) == 1
    e = rows[0]
    assert (e["outcome"], e["action"]) == ("DENIED", "GET /api/v1/users")
    assert json.loads(e["details"]) == {"required_permission": "admin:read"}


def test_audit_joins_the_callers_transaction(db: str) -> None:
    """Rolled back with the action it describes: written atomically or not at all."""
    marker = f"rollback-{uuid.uuid4().hex[:8]}"

    async def scenario() -> None:
        engine = create_async_engine(db)
        async with async_sessionmaker(engine)() as s:
            audit(
                s,
                tenant_id=DEFAULT_TENANT_ID,
                actor="test",
                action=marker,
                resource_type="test",
                outcome=AuditOutcome.SUCCESS,
            )
            await s.flush()
            await s.rollback()
        await engine.dispose()

    asyncio.run(scenario())
    assert sql(db, "SELECT count(*) AS n FROM audit_event WHERE action = $1", marker)[0]["n"] == 0


def test_dev_session_creation_is_audited(api: TestClient, db: str) -> None:
    c = cid()
    r = api.post("/api/v1/dev/sessions", json={"role": "auditor"}, headers={"X-Correlation-ID": c})
    assert r.status_code == 201
    [e] = events(db, c)
    assert e["action"] == "auth.dev_session.create"
    assert json.loads(e["details"]) == {"role": "auditor"}


# --- BE-3.3 reading --------------------------------------------------------------------------


def _read(api: TestClient, role: str, **params: Any) -> Any:
    return api.get("/api/v1/audit-events", params=params, headers=dev_token(api, role))


def test_audit_read_requires_audit_read(api: TestClient) -> None:
    r = _read(api, "risk_analyst")
    assert r.status_code == 403
    assert r.json()["details"] == {"required_permission": "audit:read"}


def test_filters_and_pagination(api: TestClient, db: str) -> None:
    admin = dev_token(api, "platform_admin")
    for _ in range(3):
        api.get("/api/v1/users", headers=admin)
    api.get("/api/v1/users", headers=dev_token(api, "risk_analyst"))  # one DENIED

    lists = _read(api, "auditor", action="user.list", limit=2)
    body = lists.json()
    assert lists.status_code == 200
    assert body["total"] >= 3 and len(body["items"]) == 2 and body["limit"] == 2
    assert all(i["action"] == "user.list" for i in body["items"])
    second = _read(api, "auditor", action="user.list", limit=2, offset=2).json()
    assert {i["id"] for i in second["items"]}.isdisjoint({i["id"] for i in body["items"]})

    denied = _read(api, "auditor", outcome="DENIED", resource_type="endpoint").json()
    assert denied["items"] and all(i["outcome"] == "DENIED" for i in denied["items"])

    actor = body["items"][0]["actor"]
    by_actor = _read(api, "auditor", actor=actor).json()
    assert by_actor["items"] and all(i["actor"] == actor for i in by_actor["items"])

    future = _read(api, "auditor", **{"from": "2999-01-01T00:00:00Z"}).json()
    assert future["total"] == 0
    past = _read(api, "auditor", to="2000-01-01T00:00:00Z").json()
    assert past["total"] == 0


def test_reading_audit_is_itself_audited(api: TestClient, db: str) -> None:
    c = cid()
    r = api.get(
        "/api/v1/audit-events",
        params={"resource_type": "user"},
        headers={**dev_token(api, "auditor"), "X-Correlation-ID": c},
    )
    assert r.status_code == 200
    [e] = events(db, c)
    assert (e["action"], e["resource_type"], e["outcome"]) == ("audit.read", "audit", "SUCCESS")
    assert json.loads(e["details"])["filters"] == {"resource_type": "user"}
    # ...and the next read can see that read.
    seen = _read(api, "auditor", action="audit.read").json()["items"]
    assert any(i["correlation_id"] == c for i in seen)


def test_audit_reads_are_tenant_scoped(api: TestClient, db: str) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    sql(
        db,
        "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type, outcome,"
        " details, details_classification) VALUES (gen_random_uuid(), $1, 'x', 'foreign.secret',"
        " 'test', 'SUCCESS', '{}', '{}')",
        other,
    )
    assert _read(api, "auditor", action="foreign.secret").json()["total"] == 0


# --- BE-3.4 classification seed -----------------------------------------------------------------


def test_seed_classifications_exist_and_are_provisional(db: str) -> None:
    rows = sql(
        db, "SELECT code, rank, provisional, seeded_by FROM data_classification ORDER BY rank"
    )
    assert [(r["code"], r["rank"]) for r in rows] == [
        ("public", 0),
        ("internal", 1),
        ("confidential", 2),
        ("restricted", 3),
    ]
    assert all(r["provisional"] and r["seeded_by"] == "0005_classification" for r in rows)
    assert [c.value for c in ClassificationCode] == [r["code"] for r in rows]


def test_seed_clearances_match_constants(db: str) -> None:
    rows = sql(
        db,
        "SELECT r.code AS role, c.code AS level FROM access_policy p"
        " JOIN role r ON r.id = p.role_id"
        " JOIN data_classification c ON c.id = p.max_clear_classification_id",
    )
    assert {r["role"]: r["level"] for r in rows} == {
        k.value: v.value for k, v in ROLE_CLEARANCE.items()
    }


# --- BE-3.5 masking -------------------------------------------------------------------------------


class Record(BaseModel):
    name: str
    secret: Annotated[str | MaskedValue, Classification("restricted")]
    note: Annotated[str | MaskedValue, Classification("internal")]


def _app_with_record(db: str, **settings: Any) -> TestClient:
    app = create_app(Settings(ENV=settings.pop("ENV", "test"), DATABASE_URL=db, **settings))

    @app.get("/api/v1/_test/record", response_model=Record)
    async def record(
        principal: Annotated[Principal, Depends(require_permission("schema:read"))],
    ) -> Record:
        return mask_model(
            Record(name="r-1", secret="account 12345", note="ok"), principal.clear_classifications
        )

    return TestClient(app)


def test_same_record_masked_for_risk_analyst_clear_for_data_steward(db: str) -> None:
    with _app_with_record(db) as c:
        analyst = c.get("/api/v1/_test/record", headers=dev_token(c, "risk_analyst")).json()
        steward = c.get("/api/v1/_test/record", headers=dev_token(c, "data_steward")).json()
    assert analyst["secret"] == {"masked": True, "classification": "restricted"}
    assert analyst["note"] == "ok"  # internal is within risk_analyst's clearance
    assert "12345" not in json.dumps(analyst)
    assert steward["secret"] == "account 12345"


def test_audit_details_masked_by_clearance(db: str) -> None:
    sql(
        db,
        "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type, outcome, details,"
        " details_classification) VALUES (gen_random_uuid(), $1, 'system:test', 'user.provision',"
        " 'user', 'SUCCESS', $2, $3)",
        DEFAULT_TENANT_ID,
        json.dumps({"email": "pat@example.test", "issuer": "https://idp.test"}),
        json.dumps({"email": "confidential", "issuer": "internal"}),
    )
    with _app_with_record(db) as c:
        auditor = c.get(
            "/api/v1/audit-events",
            params={"action": "user.provision"},
            headers=dev_token(c, "auditor"),
        ).json()["items"][0]["details"]
        admin = c.get(
            "/api/v1/audit-events",
            params={"action": "user.provision"},
            headers=dev_token(c, "platform_admin"),
        ).json()["items"][0]["details"]
    assert auditor["email"] == {"masked": True, "classification": "confidential"}
    assert auditor["issuer"] == "https://idp.test"
    assert admin["email"] == "pat@example.test"


def test_masking_cannot_be_disabled_outside_local() -> None:
    with pytest.raises(ValidationError, match="MASKING_ENABLED=false is not allowed"):
        Settings(ENV="test", MASKING_ENABLED=False)


def test_masking_disabled_in_local_shows_everything(db: str) -> None:
    with _app_with_record(db, ENV="local", MASKING_ENABLED=False) as c:
        analyst = c.get("/api/v1/_test/record", headers=dev_token(c, "risk_analyst")).json()
    assert analyst["secret"] == "account 12345"


def test_log_filter_uses_the_same_classifications() -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.classified")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.info(
        "values",
        extra={
            "secret": Classified("account 12345", "restricted"),
            "contact": Classified("pat@example.test", "confidential"),
            "unknown": Classified("odd", "made-up-level"),
            "note": Classified("visible note", "internal"),
        },
    )
    out = json.loads(buf.getvalue())["extra"]
    assert out["secret"] == REDACTED and out["contact"] == REDACTED and out["unknown"] == REDACTED
    assert out["note"] == "visible note"
