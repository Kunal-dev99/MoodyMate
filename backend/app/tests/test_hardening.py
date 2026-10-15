"""Phase 2 hardening (BE-2.7…BE-2.12, D-13): regressions for the security review of PR #3."""

from typing import Any

import jwt
import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.staticfiles import StaticFiles

from app.core.auth import undeclared_routes
from app.core.config import Settings
from app.core.tokens import InvalidToken, JwksKeys, OidcTokens, ProviderUnavailable
from app.main import create_app
from app.tests.conftest import insert_user, sql
from app.tests.test_auth import AUDIENCE, ISSUER, KEY, bearer, oidc_client, oidc_settings, token

OIDC: dict[str, Any] = {
    "AUTH_MODE": "oidc",
    "OIDC_AUDIENCE": "moodymate-api",
    "OIDC_CLIENT_ID": "moodymate-web",
}


# --- BE-2.7 fail-safe configuration ------------------------------------------------------


def test_startup_refused_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(ValidationError, match="ENV"):
        Settings(_env_file=None)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_dev_auth_refused_outside_local_and_test(env: str) -> None:
    with pytest.raises(ValidationError, match=f"AUTH_MODE=dev is not allowed when ENV={env}"):
        Settings(ENV=env, AUTH_MODE="dev", AUTH_DEV_SECRET="s" * 40)


def test_settings_errors_do_not_echo_secrets() -> None:
    secret = "do-not-print-this-secret-0123456789abcdef"
    with pytest.raises(ValidationError) as excinfo:
        Settings(ENV="staging", AUTH_MODE="dev", AUTH_DEV_SECRET=secret)
    assert secret[:12] not in str(excinfo.value)


def test_dev_auth_allowed_in_local_and_test() -> None:
    for env in ("local", "test"):
        assert Settings(ENV=env, AUTH_MODE="dev").dev_sessions_enabled


# --- BE-2.8 OIDC transport -----------------------------------------------------------------


def test_http_issuer_refused_in_staging() -> None:
    with pytest.raises(ValidationError, match="OIDC_ISSUER must use https"):
        Settings(ENV="staging", OIDC_ISSUER="http://idp.example/realm", **OIDC)


def test_http_jwks_url_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="OIDC_JWKS_URL must use https"):
        Settings(
            ENV="production",
            OIDC_ISSUER="https://idp.example/realm",
            OIDC_JWKS_URL="http://idp.example/jwks",
            **OIDC,
        )


def test_https_oidc_accepted_in_production() -> None:
    s = Settings(ENV="production", OIDC_ISSUER="https://idp.example/realm", **OIDC)
    assert not s.allow_insecure_oidc


def test_http_jwks_uri_from_discovery_rejected_when_insecure_not_allowed() -> None:
    calls: list[str] = []

    def fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        return {"jwks_uri": "http://attacker.example/jwks"}

    keys = JwksKeys("https://idp.example/realm", allow_http=False, fetch=fetch)
    with pytest.raises(ProviderUnavailable, match="https"):
        keys(token())
    assert calls == ["https://idp.example/realm/.well-known/openid-configuration"]


# --- BE-2.9 token-type confusion -----------------------------------------------------------


def test_audience_equal_to_client_id_refused() -> None:
    with pytest.raises(ValidationError, match="OIDC_AUDIENCE must be the API audience"):
        Settings(
            ENV="local",
            AUTH_MODE="oidc",
            OIDC_ISSUER="https://idp.example/realm",
            OIDC_AUDIENCE="moodymate-web",
            OIDC_CLIENT_ID="moodymate-web",
        )


# --- BE-2.10 JWKS resilience ---------------------------------------------------------------


def _jwks(kid: str) -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "use": "sig", "alg": "RS256"}]}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _signed(kid: str) -> str:
    return jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "alice", "exp": 9999999999},
        KEY,
        algorithm="RS256",
        headers={"kid": kid},
    )


def test_unknown_kids_trigger_at_most_one_fetch_per_interval() -> None:
    clock = Clock()
    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=lambda _u: _jwks("k1"), clock=clock)
    assert keys(_signed("k1")) is not None
    assert keys.fetches == 1
    for i in range(50):  # forged tokens with random kids
        with pytest.raises(InvalidToken, match="unknown signing key"):
            keys(_signed(f"forged-{i}"))
    assert keys.fetches == 1  # none of the 50 reached the network
    clock.now += 61
    with pytest.raises(InvalidToken):
        keys(_signed("forged-after-interval"))
    assert keys.fetches == 2  # one refresh after the interval, not one per token


def test_key_rotation_is_picked_up_after_the_interval() -> None:
    clock = Clock()
    current = {"kid": "old"}
    keys = JwksKeys(
        ISSUER, "https://idp.example/jwks", fetch=lambda _u: _jwks(current["kid"]), clock=clock
    )
    keys(_signed("old"))
    current["kid"] = "new"
    clock.now += 61
    assert keys(_signed("new")) is not None


def test_provider_outage_backs_off() -> None:
    clock = Clock()
    attempts: list[str] = []

    def down(url: str) -> dict[str, Any]:
        attempts.append(url)
        raise ProviderUnavailable("connection refused")

    keys = JwksKeys(ISSUER, allow_http=True, fetch=down, clock=clock)
    for _ in range(20):
        with pytest.raises(ProviderUnavailable):
            keys(_signed("k1"))
    assert len(attempts) == 1  # backed off instead of hammering the provider


@pytest.mark.db
def test_provider_unavailable_is_503_envelope(db: str) -> None:
    def down(_url: str) -> dict[str, Any]:
        raise ProviderUnavailable("connection refused")

    app = create_app(oidc_settings(db))
    app.state.token_verifier = OidcTokens(
        ISSUER, AUDIENCE, key_resolver=JwksKeys(ISSUER, allow_http=True, fetch=down)
    )
    r = TestClient(app).get("/api/v1/me", headers=bearer(token()))
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "auth_provider_unavailable"
    assert body["retryable"] is True
    assert body["correlation_id"] == r.headers["X-Correlation-ID"]


# --- BE-2.11 suspended tenant --------------------------------------------------------------


@pytest.mark.db
def test_user_of_suspended_tenant_is_denied(db: str) -> None:
    from app.tests.conftest import insert_tenant

    tenant = insert_tenant(db, "suspended-bank")
    insert_user(db, ISSUER, "sam", ("platform_admin",), tenant)
    sql(db, "UPDATE tenant SET status = 'SUSPENDED' WHERE id = $1", tenant)
    with oidc_client(oidc_settings(db)) as c:
        r = c.get("/api/v1/me", headers=bearer(token(sub="sam")))
    assert r.status_code == 403
    assert r.json()["code"] == "tenant_suspended"


# --- BE-2.12 guard coverage ----------------------------------------------------------------


def test_registry_reports_mounts_and_websockets(tmp_path: Any) -> None:
    app = create_app(Settings(ENV="test"))
    app.mount("/api/v1/files", StaticFiles(directory=tmp_path), name="files")

    @app.websocket("/api/v1/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.close()

    assert undeclared_routes(app) == ["APIWebSocketRoute /api/v1/ws", "Mount /api/v1/files"]


# --- found during hardening E2E: concurrent first sign-in ------------------------------------


@pytest.mark.db
def test_concurrent_first_sign_in_provisions_once(db: str) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.tokens import VerifiedIdentity
    from app.governance.service import IdentityService

    identity = VerifiedIdentity(issuer=ISSUER, subject="racer", email=None, name="Racer")

    async def scenario() -> list[Any]:
        engine = create_async_engine(db)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def resolve() -> Any:
            async with sessions() as s:
                return await IdentityService(s).resolve_principal(identity, can_self_provision=True)

        try:
            return await asyncio.gather(*(resolve() for _ in range(8)))
        finally:
            await engine.dispose()

    principals = asyncio.run(scenario())
    assert len({p.user_id for p in principals}) == 1
    count = sql(db, "SELECT count(*) AS n FROM user_account WHERE idp_subject = 'racer'")
    assert count[0]["n"] == 1
