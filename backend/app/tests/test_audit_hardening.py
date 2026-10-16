"""Phase 3 verification follow-ups: defects found by the independent review of BE-3.1…3.5."""

import asyncio
import json
import logging
import uuid
from typing import Annotated, Any

import pytest
from alembic import command
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.classification import Classification, MaskedValue, mask_mapping, mask_model
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.core.principal import Principal
from app.db.session import Database, get_session
from app.governance.constants import DEFAULT_TENANT_ID, AuditOutcome
from app.governance.schemas import UserSummary
from app.governance.service import audit
from app.main import create_app
from app.tests.conftest import alembic_config, dev_token, insert_tenant, sql

CLEAR_INTERNAL = frozenset({"public", "internal"})


def cid() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


def events(url: str, correlation_id: str) -> list[Any]:
    return sql(url, "SELECT * FROM audit_event WHERE correlation_id = $1", correlation_id)


# --- Defect 1: unclassified audit details fail closed ---------------------------------------


def _audit(**kw: Any) -> None:
    class _Session:
        def add(self, _: object) -> None:
            pass

    audit(
        _Session(),  # type: ignore[arg-type]
        tenant_id=DEFAULT_TENANT_ID,
        actor="test",
        action="test.x",
        resource_type="test",
        outcome=AuditOutcome.SUCCESS,
        **kw,
    )


def test_audit_rejects_details_without_a_classification() -> None:
    with pytest.raises(ValueError, match="old_value"):
        _audit(details={"old_value": "x"})
    with pytest.raises(ValueError, match="made-up"):
        _audit(details={"k": "x"}, details_classification={"k": "made-up"})
    _audit(details={"k": "x"}, details_classification={"k": "public"})


def test_unclassified_stored_details_are_masked() -> None:
    out = mask_mapping({"old_value": "secret", "n": 1}, {"n": "public"}, CLEAR_INTERNAL)
    assert out["n"] == 1
    assert out["old_value"] == MaskedValue(classification="unclassified")


# --- Defect 2: a downgrade must not silently destroy the audit trail ------------------------


def test_downgrade_refuses_while_audit_rows_exist(db: str) -> None:
    sql(
        db,
        "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type, outcome,"
        " details, details_classification) VALUES (gen_random_uuid(), $1, 'test', 'test.keep',"
        " 'test', 'SUCCESS', '{}', '{}')",
        DEFAULT_TENANT_ID,
    )
    cfg = alembic_config(db)
    cfg.attributes.pop("allow_audit_loss", None)
    try:
        with pytest.raises(Exception, match="refusing to drop the audit trail"):
            command.downgrade(cfg, "0003")
    finally:
        command.upgrade(alembic_config(db), "head")
    assert sql(db, "SELECT count(*) AS n FROM audit_event WHERE action = 'test.keep'")[0]["n"] >= 1


def test_downgrade_guard_works_offline() -> None:
    """`alembic downgrade --sql` (a script for a DBA) carries the guard instead of failing."""
    import io

    from alembic.config import Config

    buf = io.StringIO()
    cfg = Config("alembic.ini", output_buffer=buf)
    cfg.set_main_option("sqlalchemy.url", "postgresql+asyncpg://x:y@localhost/none")
    cfg.attributes["skip_logging"] = True
    command.downgrade(cfg, "0004:0003", sql=True)
    script = buf.getvalue()
    assert "LOCK TABLE audit_event IN ACCESS EXCLUSIVE MODE" in script
    assert "refusing to drop the audit trail" in script
    assert script.index("LOCK TABLE audit_event") < script.index("DROP TABLE audit_event")


# --- Defect 3: database errors do not echo bound parameters ---------------------------------


def test_database_errors_do_not_log_bound_values(db: str) -> None:
    """Neither SQLAlchemy's parameter echo nor the driver's own message reaches the log."""

    value = "-".join(["pat", uuid.uuid4().hex])  # not in this file's source (tracebacks)

    async def scenario() -> BaseException:
        database = Database(db)
        try:
            async with database.sessionmaker() as s:
                await s.execute(text("SELECT CAST(:v AS integer)"), {"v": value})
        except Exception as exc:  # noqa: BLE001
            return exc
        finally:
            await database.dispose()
        raise AssertionError("expected a database error")

    exc = asyncio.run(scenario())
    assert "SQL parameters hidden" in str(exc)  # hide_parameters
    record = logging.LogRecord("t", logging.ERROR, "f", 1, "boom", None, (type(exc), exc, None))
    out = json.loads(JsonFormatter().format(record))
    assert value not in out["exc"]
    assert "DataError" in out["exc"] and "SELECT CAST" in out["exc"]


# --- Defect 4 and BE-3.3: stable paging, resource filter, bounded filters --------------------


def _read(api: TestClient, headers: dict[str, str], **params: Any) -> Any:
    return api.get("/api/v1/audit-events", params=params, headers=headers)


def test_keyset_paging_does_not_repeat_rows_despite_self_audit(api: TestClient) -> None:
    admin = dev_token(api, "platform_admin")
    for _ in range(4):
        api.get("/api/v1/users", headers=admin)
    auditor = dev_token(api, "auditor")
    first = _read(api, auditor, limit=3).json()["items"]
    second = _read(api, auditor, limit=3, before=first[-1]["id"]).json()["items"]
    assert len(first) == 3 and len(second) == 3
    assert {i["id"] for i in first}.isdisjoint({i["id"] for i in second})
    newest_second = (second[0]["occurred_at"], second[0]["id"])
    assert newest_second < (first[-1]["occurred_at"], first[-1]["id"])


def test_unknown_or_foreign_before_cursor_is_rejected(api: TestClient, db: str) -> None:
    auditor = dev_token(api, "auditor")
    assert _read(api, auditor, before=str(uuid.uuid4())).status_code == 422
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    foreign = sql(
        db,
        "INSERT INTO audit_event (id, tenant_id, actor, action, resource_type, outcome,"
        " details, details_classification) VALUES (gen_random_uuid(), $1, 'x', 'foreign',"
        " 'test', 'SUCCESS', '{}', '{}') RETURNING id",
        other,
    )[0]["id"]
    assert _read(api, auditor, before=str(foreign)).status_code == 422


def test_resource_id_filter(api: TestClient) -> None:
    admin = dev_token(api, "platform_admin")
    users = api.get("/api/v1/users", headers=admin).json()["items"]
    target = users[0]["id"]
    api.get(f"/api/v1/users/{target}", headers=admin)
    body = _read(api, dev_token(api, "auditor"), resource_type="user", resource_id=target).json()
    assert body["items"] and all(i["resource_id"] == target for i in body["items"])
    assert any(i["action"] == "user.read" for i in body["items"])


def test_filters_are_length_bounded(api: TestClient) -> None:
    r = _read(api, dev_token(api, "auditor"), actor="x" * 1000)
    assert r.status_code == 422


# --- Defect 5: account-state denials are audited --------------------------------------------


def test_disabled_account_denial_is_audited(api: TestClient, db: str) -> None:
    headers = dev_token(api, "auditor")
    sql(db, "UPDATE user_account SET status = 'DISABLED' WHERE idp_subject = 'dev:auditor'")
    c = cid()
    r = api.get("/api/v1/me", headers={**headers, "X-Correlation-ID": c})
    assert r.status_code == 403 and r.json()["code"] == "account_disabled"
    [e] = events(db, c)
    assert (e["action"], e["outcome"], e["resource_type"]) == (
        "auth.access",
        "DENIED",
        "user",
    )
    assert json.loads(e["details"]) == {"reason": "account_disabled"}


# --- Defect 6: a denial commits only its own audit row --------------------------------------


def test_denied_audit_does_not_commit_pending_request_work(db: str) -> None:
    marker = f"pending-{uuid.uuid4().hex[:8]}"
    app = create_app(Settings(ENV="test", DATABASE_URL=db))

    async def pending(session: Annotated[AsyncSession, Depends(get_session)]) -> None:
        audit(
            session,
            tenant_id=DEFAULT_TENANT_ID,
            actor="test",
            action=marker,
            resource_type="test",
            outcome=AuditOutcome.SUCCESS,
        )

    @app.get("/api/v1/_test/pending")
    async def route(
        _: Annotated[None, Depends(pending)],
        principal: Annotated[Principal, Depends(require_permission("admin:read"))],
    ) -> dict[str, str]:
        return {}

    with TestClient(app) as c:
        r = c.get("/api/v1/_test/pending", headers=dev_token(c, "risk_analyst"))
    assert r.status_code == 403
    assert sql(db, "SELECT count(*) AS n FROM audit_event WHERE action = $1", marker)[0]["n"] == 0


# --- Defect 7 and 8: mask_model covers containers, None and the user directory --------------


class Inner(BaseModel):
    secret: Annotated[str | None | MaskedValue, Classification("restricted")]


class Outer(BaseModel):
    items: list[Inner | str]
    by_key: dict[str, Inner]
    tags: list[Annotated[str | MaskedValue, Classification("restricted")]]
    maybe: Annotated[str | None | MaskedValue, Classification("restricted")] = None


def test_mask_model_walks_containers_and_masks_none() -> None:
    model = Outer(
        items=["plain", Inner(secret="a")],
        by_key={"k": Inner(secret="b")},
        tags=["t1", "t2"],
        maybe=None,
    )
    out = mask_model(model, CLEAR_INTERNAL)
    hidden = MaskedValue(classification="restricted")
    assert out.items[0] == "plain"
    assert isinstance(out.items[1], Inner) and out.items[1].secret == hidden
    assert out.by_key["k"].secret == hidden
    assert out.tags == [hidden, hidden]
    assert out.maybe == hidden
    assert '"a"' not in out.model_dump_json() and "t1" not in out.model_dump_json()


def test_user_directory_email_is_confidential() -> None:
    from datetime import UTC, datetime

    user = UserSummary(
        id=uuid.uuid4(),
        display_name="Pat",
        email="pat@example.test",
        status="ACTIVE",
        roles=[],
        created_at=datetime.now(UTC),
    )
    assert mask_model(user, CLEAR_INTERNAL).email == MaskedValue(classification="confidential")
    assert mask_model(user, CLEAR_INTERNAL | {"confidential"}).email == "pat@example.test"


# --- Re-verification: N1 denial audit must not need a second pooled connection ------------


def test_denials_do_not_exhaust_a_one_connection_pool(db: str) -> None:
    """The request session releases its connection before the denial is audited, so a
    refusal never waits on the pool (with one connection it would otherwise time out)."""
    with TestClient(create_app(Settings(ENV="test", DATABASE_URL=db))) as c:
        headers = dev_token(c, "risk_analyst")
        c.app.state.db = Database(db, pool_size=1, max_overflow=0, pool_timeout=2)  # type: ignore[attr-defined]
        try:
            r = c.get("/api/v1/users", headers=headers)
        finally:
            asyncio.run(c.app.state.db.dispose())  # type: ignore[attr-defined]
    assert r.status_code == 403, r.text


# --- Re-verification: N3 masking finds markers in every container and optional shape ------


class Shapes(BaseModel):
    opt: Annotated[str, Classification("restricted")] | None
    opt_list: list[Annotated[str | MaskedValue, Classification("restricted")]] | None
    tup: tuple[Annotated[str | MaskedValue, Classification("restricted")], ...]
    by_key: dict[str, Annotated[str | MaskedValue, Classification("restricted")]]
    nested_opt: list[Inner] | None


def test_mask_model_handles_optional_tuple_and_dict_shapes() -> None:
    model = Shapes(
        opt="o",
        opt_list=["l"],
        tup=("t",),
        by_key={"k": "d"},
        nested_opt=[Inner(secret="n")],
    )
    out = mask_model(model, CLEAR_INTERNAL)
    hidden = MaskedValue(classification="restricted")
    assert out.opt == hidden  # type: ignore[comparison-overlap]
    assert out.opt_list == [hidden]
    assert out.tup == (hidden,)
    assert out.by_key == {"k": hidden}
    assert out.nested_opt is not None and out.nested_opt[0].secret == hidden
    dumped = out.model_dump_json()
    for raw in ('"o"', '"l"', '"t"', '"d"', '"n"'):
        assert raw not in dumped
    assert mask_model(model, CLEAR_INTERNAL | {"confidential", "restricted"}) == model


def test_mask_model_keeps_namedtuples_working() -> None:
    from typing import NamedTuple

    class Pair(NamedTuple):
        a: str
        b: str

    class WithPair(BaseModel):
        pair: Pair

    model = WithPair(pair=Pair("x", "y"))
    assert mask_model(model, CLEAR_INTERNAL).pair == Pair("x", "y")


def test_keyset_read_records_its_cursor(api: TestClient, db: str) -> None:
    auditor = dev_token(api, "auditor")
    first = api.get("/api/v1/audit-events", params={"limit": 1}, headers=auditor).json()
    before = first["items"][0]["id"]
    c = cid()
    api.get(
        "/api/v1/audit-events",
        params={"limit": 1, "before": before},
        headers={**auditor, "X-Correlation-ID": c},
    )
    [e] = events(db, c)
    assert json.loads(e["details"])["before"] == before


def test_log_formatter_keeps_grouped_exceptions_without_values() -> None:
    from app.core.logging import format_exception

    marker = uuid.uuid4().hex  # not in the source line the traceback prints
    try:
        raise ExceptionGroup("tasks", [ValueError(marker), LookupError("second")])
    except ExceptionGroup as group:
        text = format_exception(group)
    assert marker in text and "LookupError" in text
