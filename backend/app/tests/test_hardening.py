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
    s = Settings(
        ENV="production",
        DATA_CLASS="client",
        OIDC_ISSUER="https://idp.example/realm",
        ARTIFACT_STORE_DIR="/var/lib/moodymate/artifacts",  # required outside local/test (D-15)
        **OIDC,
    )
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
        with pytest.raises(ProviderUnavailable):  # BE-2.15: retryable, never a fetch
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


# --- BE-2.13 JWKS: no network I/O under the lock; discovered URL checked before caching ----


def test_cached_keys_served_while_a_refresh_is_in_flight() -> None:
    import threading

    clock = Clock()
    release = threading.Event()
    started = threading.Event()
    calls = {"n": 0}

    def fetch(_url: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] > 1:  # the refresh triggered by the forged kid hangs
            started.set()
            release.wait(5)
        return _jwks("k1")

    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=fetch, clock=clock)
    keys(_signed("k1"))  # warm the cache
    clock.now += 61

    errors: list[Exception] = []

    def forged() -> None:
        try:
            keys(_signed("forged"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=forged)
    t.start()
    assert started.wait(5)
    # While that fetch hangs, a request with a cached kid completes immediately.
    done = threading.Event()

    def cached() -> None:
        keys(_signed("k1"))
        done.set()

    threading.Thread(target=cached).start()
    assert done.wait(1), "cached-key lookup blocked behind the in-flight refresh"
    release.set()
    t.join(5)
    assert isinstance(errors[0], InvalidToken)


def test_concurrent_first_requests_share_one_fetch() -> None:
    import threading

    gate = threading.Event()

    def slow(_url: str) -> dict[str, Any]:
        gate.wait(5)
        return _jwks("k1")

    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=slow)
    results: list[Any] = []
    threads = [
        threading.Thread(target=lambda: results.append(keys(_signed("k1")))) for _ in range(6)
    ]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert len(results) == 6 and all(r is not None for r in results)
    assert keys.fetches == 1


def test_unsafe_discovered_jwks_uri_is_not_remembered() -> None:
    clock = Clock()
    published = {"jwks_uri": "http://idp.example/jwks"}

    def fetch(url: str) -> dict[str, Any]:
        if url.endswith("openid-configuration"):
            return dict(published)
        return _jwks("k1")

    keys = JwksKeys("https://idp.example", fetch=fetch, clock=clock)
    with pytest.raises(ProviderUnavailable):
        keys(_signed("k1"))
    published["jwks_uri"] = "https://idp.example/jwks"  # provider fixes its config
    clock.now += 61
    assert keys(_signed("k1")) is not None  # recovers without a restart


# --- BE-2.14 registry: parent-path mounts ----------------------------------------------------


@pytest.mark.parametrize("prefix", ["/api", ""])
def test_registry_reports_parent_path_mounts(prefix: str, tmp_path: Any) -> None:
    from fastapi import FastAPI

    app = create_app(Settings(ENV="test"))
    app.mount(prefix, FastAPI(), name="legacy")
    assert f"Mount {prefix or '/'}" in undeclared_routes(app)


def test_registry_ignores_unrelated_mounts(tmp_path: Any) -> None:
    app = create_app(Settings(ENV="test"))
    app.mount("/static", StaticFiles(directory=tmp_path), name="static")
    assert undeclared_routes(app) == []


# --- restored coverage ------------------------------------------------------------------------


def test_short_dev_secret_refused() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(ENV="local", AUTH_MODE="dev", AUTH_DEV_SECRET="too-short")


# --- verifier findings on the BE-2.13 rewrite ---------------------------------------------


def test_key_stored_between_fast_path_and_lock_is_found() -> None:
    """Race: the fast-path lookup misses, another request's fetch then stores the key and
    leaves the refresh interval running. The key must still be found, not 401."""

    class RacyKeys(JwksKeys):
        missed = False

        def _lookup(self, kid: str | None) -> Any:
            if not self.missed:  # simulate: fast path ran just before the other fetch landed
                self.missed = True
                return None
            return super()._lookup(kid)

    clock = Clock()
    keys = RacyKeys(ISSUER, "https://idp.example/jwks", fetch=lambda _u: _jwks("k1"), clock=clock)
    keys._keys = {"k1": KEY.public_key()}  # the other request's fetch has completed
    keys._last_attempt = clock.now  # ...and the refresh interval is running
    assert keys(_signed("k1")) is not None


def test_unknown_kid_does_not_wait_when_keys_are_cached() -> None:
    import threading
    import time as _time

    clock = Clock()
    release = threading.Event()
    started = threading.Event()
    calls = {"n": 0}

    def fetch(_url: str) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] > 1:
            started.set()
            release.wait(5)
        return _jwks("k1")

    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=fetch, clock=clock)
    keys(_signed("k1"))
    clock.now += 61
    t = threading.Thread(target=lambda: pytest.raises(InvalidToken, keys, _signed("trigger")))
    t.start()
    assert started.wait(5)
    t0 = _time.monotonic()
    with pytest.raises(ProviderUnavailable):  # BE-2.15: retryable, returned at once
        keys(_signed("flood"))  # must not park a worker thread behind the slow fetch
    assert _time.monotonic() - t0 < 1.0
    release.set()
    t.join(5)


def test_truncated_provider_response_is_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client
    import urllib.request

    from app.core.tokens import _fetch_json

    def broken(*_a: Any, **_k: Any) -> Any:
        raise http.client.IncompleteRead(b"{")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    with pytest.raises(ProviderUnavailable):
        _fetch_json("https://idp.example/jwks")


def test_registry_segment_aware_and_catches_param_mounts_and_hosts(tmp_path: Any) -> None:
    from fastapi import FastAPI
    from starlette.routing import Host

    app = create_app(Settings(ENV="test"))
    app.mount("/ap", StaticFiles(directory=tmp_path), name="not-a-parent")
    app.mount("/{tenant}", FastAPI(), name="param")
    app.router.routes.append(Host("api.example", app=FastAPI()))
    found = undeclared_routes(app)
    assert "Mount /ap" not in found  # "/ap" is not a parent path segment of /api/v1
    assert "Mount /{tenant}" in found
    assert any(f.startswith("Host ") for f in found)


# --- BE-2.15 key rotation must not sign users out ---------------------------------------------


def test_rotated_key_during_inflight_refresh_is_retryable_not_401() -> None:
    import threading

    clock = Clock()
    release = threading.Event()
    started = threading.Event()
    published = {"kids": ["k1"]}

    def fetch(_url: str) -> dict[str, Any]:
        if published["kids"] == ["k1", "k2"]:
            started.set()
            release.wait(5)
        return {"keys": [_jwks(k)["keys"][0] for k in published["kids"]]}

    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=fetch, clock=clock)
    keys(_signed("k1"))  # cache holds the old key
    published["kids"] = ["k1", "k2"]  # provider rotates in k2
    clock.now += 61

    first: list[Any] = []
    t = threading.Thread(target=lambda: first.append(keys(_signed("k2"))))  # starts the refresh
    t.start()
    assert started.wait(5)
    # A parallel request (the page's second API call) while the refresh is in flight:
    with pytest.raises(ProviderUnavailable):  # retryable 503 — the session survives
        keys(_signed("k2"))
    release.set()
    t.join(5)
    assert first and first[0] is not None
    assert keys(_signed("k2")) is not None  # and the retry succeeds


def test_unknown_kid_within_refresh_interval_is_retryable() -> None:
    clock = Clock()
    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=lambda _u: _jwks("k1"), clock=clock)
    keys(_signed("k1"))
    with pytest.raises(ProviderUnavailable):  # can't check yet: don't end the session
        keys(_signed("new-kid"))


def test_unknown_kid_after_a_completed_refresh_is_401() -> None:
    clock = Clock()
    keys = JwksKeys(ISSUER, "https://idp.example/jwks", fetch=lambda _u: _jwks("k1"), clock=clock)
    keys(_signed("k1"))
    clock.now += 61
    with pytest.raises(InvalidToken, match="unknown signing key"):
        keys(_signed("forged"))  # refresh ran, kid still unknown: definitive


# --- BE-2.16 catch-all routes -------------------------------------------------------------------


def test_registry_reports_unguarded_catch_all_route() -> None:
    app = create_app(Settings(ENV="test"))

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> dict[str, str]:
        return {"path": full_path}

    assert "GET /{full_path:path}" in undeclared_routes(app)


def test_catch_all_route_cannot_serve_api_paths_unguarded() -> None:
    app = create_app(Settings(ENV="test"))

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> dict[str, str]:
        return {"secret": full_path}

    c = TestClient(app)
    r = c.get("/api/v1/does-not-exist-anywhere")
    assert r.status_code == 403
    assert r.json()["code"] == "permission_not_declared"
    assert c.get("/some/frontend/page").status_code == 200  # non-API paths unaffected
