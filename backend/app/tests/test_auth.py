"""Authentication and authorization (BE-2.3, BE-2.4, BE-3.2, D-04): dev sign-in, OIDC token
validation, fail-closed route declarations and audited 403s."""

import base64
import hashlib
import hmac
import io
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.core.auth import PUBLIC_PATHS, undeclared_routes
from app.core.config import Settings
from app.core.tokens import (
    InvalidToken,
    JwksKeys,
    OidcTokens,
    ProviderUnavailable,
    TokenExpired,
    _fetch_json,
)
from app.main import create_app
from app.tests.conftest import dev_token, insert_user, sql
from app.tests.helpers import (
    IMPORTS,
    SOURCES,
    cid,
    events,
    seeded_source_id,
    source_body,
    token_for,
    upload,
)

ISSUER = "https://idp.test/realms/moodymate"
AUDIENCE = "moodymate-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_client(db: str, env: str = "test") -> TestClient:
    settings = Settings(
        ENV=env,
        DATABASE_URL=db,
        AUTH_MODE="oidc",
        OIDC_ISSUER=ISSUER,
        OIDC_AUDIENCE=AUDIENCE,
        OIDC_CLIENT_ID="moodymate-web",
    )
    app = create_app(settings)
    # Local issuer: the verifier resolves the signing key from our test key pair instead
    # of fetching a JWKS over the network.
    app.state.token_verifier = OidcTokens(
        ISSUER, AUDIENCE, key_resolver=lambda _t: KEY.public_key()
    )
    return TestClient(app)


def token(sub: str = "alice", key: Any = KEY, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "email": f"{sub}@example.test",
        "name": sub.title(),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _hs256(payload: dict[str, Any], secret: bytes) -> str:
    """Hand-built HS256 JWT. PyJWT refuses to HMAC-sign with a PEM key, an attacker won't."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = (
        f"{b64(b'{\"alg\":\"HS256\",\"typ\":\"JWT\"}')}.{b64(json.dumps(payload).encode())}"
    )
    sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64(sig)}"


@pytest.mark.db
def test_dev_sign_in(api: TestClient, db: str) -> None:
    assert api.get("/api/v1/auth/config").json() == {
        "mode": "dev",
        "environment": {"env": "test", "data_class": "synthetic"},
        "dev_sessions": True,
        "oidc": None,
    }
    correlation = cid()
    r = api.post(
        "/api/v1/dev/sessions", json={"role": "auditor"}, headers={"X-Correlation-ID": correlation}
    )
    assert r.status_code == 201, r.text
    [event] = events(db, correlation)
    assert event["action"] == "auth.dev_session.create"
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = api.get("/api/v1/me", headers=headers)
    assert me.status_code == 200 and me.json()["roles"] == ["auditor"]

    r = api.post("/api/v1/dev/sessions", json={"role": "superuser"})
    assert (r.status_code, r.json()["code"]) == (422, "unknown_role")
    good = headers["Authorization"].removeprefix("Bearer ")
    forged = jwt.encode(jwt.decode(good, options={"verify_signature": False}), "x" * 40, "HS256")
    assert api.get("/api/v1/me", headers=bearer(forged)).status_code == 401
    # A dev token means nothing to an OIDC deployment, which has no dev endpoints.
    with oidc_client(db) as oidc:
        assert oidc.get("/api/v1/me", headers=headers).status_code == 401
        assert oidc.post("/api/v1/dev/sessions", json={"role": "auditor"}).status_code == 404
        assert oidc.get("/api/v1/auth/config").json()["dev_sessions"] is False


@pytest.mark.db
def test_oidc_tokens_are_validated(db: str) -> None:
    insert_user(db, ISSUER, "alice", roles=("risk_analyst",))
    insert_user(db, ISSUER, "mallory", roles=("platform_admin",), status="DISABLED")
    with oidc_client(db) as oidc:
        r = oidc.get("/api/v1/me", headers=bearer(token()))
        assert r.status_code == 200, r.text
        assert r.json()["roles"] == ["risk_analyst"]
        assert r.json()["display_name"] == "User alice"  # from the user record, not the token

        r = oidc.get("/api/v1/me")
        assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
        assert r.headers["WWW-Authenticate"] == "Bearer"
        past = datetime.now(UTC) - timedelta(hours=1)
        r = oidc.get("/api/v1/me", headers=bearer(token(exp=past, iat=past)))
        assert (r.status_code, r.json()["code"]) == (401, "token_expired")

        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "alice", "exp": 9999999999}
        pem = KEY.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        bad_tokens = {
            "wrong issuer": token(iss="https://evil.test"),
            "wrong audience": token(aud="some-other-api"),
            "wrong key": token(key=OTHER_KEY),
            "alg confusion": _hs256(claims, pem),  # public key used as an HMAC secret
            "alg none": jwt.encode(claims, "", algorithm="none"),
            "garbage": "not-a-jwt",
        }
        for name, bad in bad_tokens.items():
            assert oidc.get("/api/v1/me", headers=bearer(bad)).status_code == 401, name

        r = oidc.get("/api/v1/me", headers=bearer(token(sub="stranger")))
        assert (r.status_code, r.json()["code"]) == (403, "account_not_provisioned")
        r = oidc.get("/api/v1/me", headers=bearer(token(sub="mallory")))
        assert (r.status_code, r.json()["code"]) == (403, "account_disabled")


# BE-28.3: no default bucket is anywhere near this; a bucket that never empties fails the test.
RATE_LIMIT_CEILING = 100


def _replays_until_refused(client: TestClient, steward: dict[str, str], source: str) -> int:
    """Replays one idempotent upload until the POST /imports bucket refuses: the count."""
    for n in range(1, RATE_LIMIT_CEILING + 1):
        r = upload(client, steward, source, b"a\n1\n", key="rate-limit-1")
        if r.status_code == 429:
            return n
        assert r.status_code in (200, 201), (n, r.text)
    raise AssertionError(f"POST {IMPORTS} was never rate limited in {RATE_LIMIT_CEILING} calls")


def _rate_limits_refuse_audit_and_switch_off(api: TestClient, db: str, artifact_dir: Any) -> None:
    """BE-28.3: an exhausted per-principal, per-route bucket is a 429 `rate_limited` envelope
    with Retry-After, audited DENIED, writes nothing; other principals and routes are unaffected;
    RATE_LIMIT_ENABLED=false (test only) never refuses."""
    source = seeded_source_id(db)
    steward = dev_token(api, "data_steward")
    allowed = _replays_until_refused(api, steward, source) - 1
    assert allowed >= 1
    jobs = sql(db, "SELECT count(*) AS n FROM import_job")[0]["n"]
    correlation = cid()
    r = upload(api, steward, source, b"a\n1\n", key="rate-limit-2", cid=correlation)
    assert r.status_code == 429, r.text
    body = r.json()
    assert set(body) == {"code", "message", "details", "correlation_id", "retryable"}
    assert (body["code"], body["retryable"], body["correlation_id"]) == (
        "rate_limited",
        True,
        correlation,
    )
    assert int(r.headers["Retry-After"]) >= 1
    assert sql(db, "SELECT count(*) AS n FROM import_job")[0]["n"] == jobs  # nothing written
    [event] = events(db, correlation)
    assert event["outcome"] == "DENIED", dict(event)
    assert "rate_limited" in json.dumps(json.loads(event["details"])), dict(event)
    # Per principal: another steward has a full bucket. Per route: other routes still answer.
    other = token_for(db, "rate-limit-other", ("data_steward",))
    assert upload(api, other, source, b"a\n1\n", key="rate-limit-3").status_code in (200, 201)
    assert api.get("/api/v1/me", headers=steward).status_code == 200
    assert api.get(IMPORTS, headers=steward).status_code == 200

    # Switched off (local/test only): the same number of calls plus margin are all served.
    off = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, RATE_LIMIT_ENABLED=False
    )
    with TestClient(create_app(off)) as client:
        steward = dev_token(client, "data_steward")
        for n in range(allowed + 5):
            r = upload(client, steward, source, b"a\n1\n", key="rate-limit-1")
            assert r.status_code == 200, (n, r.text)


@pytest.mark.db
def test_missing_permission_is_403_with_a_denied_audit_event(
    api: TestClient, db: str, artifact_dir: Any
) -> None:
    def denied(role: str, method: str, path: str, permission: str, **kw: Any) -> None:
        correlation = cid()
        headers = {**dev_token(api, role), "X-Correlation-ID": correlation}
        r = api.request(method, path, headers=headers, **kw)
        assert r.status_code == 403, (role, path, r.text)
        assert r.json()["code"] == "forbidden"
        assert r.json()["details"] == {"required_permission": permission}
        [event] = events(db, correlation)
        assert (event["outcome"], event["action"]) == ("DENIED", f"{method} {path}")
        assert json.loads(event["details"]) == {"required_permission": permission}

    denied("risk_analyst", "GET", "/api/v1/users", "admin:read")
    denied("risk_analyst", "GET", "/api/v1/audit-events", "audit:read")
    denied("auditor", "POST", SOURCES, "source:manage", json=source_body(db))
    correlation = cid()
    r = upload(api, dev_token(api, "approver"), seeded_source_id(db), b"a\n1\n", cid=correlation)
    assert r.status_code == 403 and r.json()["details"] == {"required_permission": "import:write"}
    [event] = events(db, correlation)
    assert (event["outcome"], event["action"]) == ("DENIED", f"POST {IMPORTS}")
    _rate_limits_refuse_audit_and_switch_off(api, db, artifact_dir)


def test_every_route_declares_a_permission_and_undeclared_routes_fail_closed() -> None:
    app = create_app(Settings(ENV="test"))
    assert undeclared_routes(app) == []
    # Changing this set is a security decision: update the test deliberately.
    assert PUBLIC_PATHS == {
        "/api/v1/health",
        "/api/v1/health/live",  # D-62 §1 (BE-34.5)
        "/api/v1/health/ready",  # D-62 §1 (BE-34.5)
        "/api/v1/auth/config",
        "/api/v1/dev/personas",
        "/api/v1/dev/sessions",
    }

    @app.get("/api/v1/_unguarded")
    async def unguarded() -> dict[str, str]:
        return {"secret": "data"}

    assert undeclared_routes(app) == ["GET /api/v1/_unguarded"]
    r = TestClient(app).get("/api/v1/_unguarded")
    assert (r.status_code, r.json()["code"]) == (403, "permission_not_declared")
    assert "secret" not in r.text


# --- BE-35.6: OIDC / JWKS behaviour against a mock identity provider (no network) -----------

JWKS_PATH = "/realms/moodymate/protocol/openid-connect/certs"
DISCOVERY_PATH = "/realms/moodymate/.well-known/openid-configuration"
ROTATED_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
REFRESH = 60.0  # JwksKeys' min_refresh_seconds in these tests


class MockIdp:
    """Discovery and JWKS behind `httpx.MockTransport`: every round trip is recorded, the key
    set can rotate, and the provider can go down. Nothing leaves the process."""

    def __init__(self, **keys: Any) -> None:
        self.keys: dict[str, Any] = keys
        self.down = False
        self.requests: list[str] = []
        self.now = 0.0
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.down:
            return httpx.Response(503, text="maintenance")
        if request.url.path == DISCOVERY_PATH:
            return httpx.Response(
                200, json={"issuer": ISSUER, "jwks_uri": ISSUER_ORIGIN + JWKS_PATH}
            )
        if request.url.path == JWKS_PATH:
            jwks = []
            for kid, key in self.keys.items():
                jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
                jwks.append({**jwk, "kid": kid, "alg": "RS256", "use": "sig"})
            return httpx.Response(200, json={"keys": jwks})
        return httpx.Response(404)

    def fetch(self, url: str) -> dict[str, Any]:
        """`JwksKeys`' fetch seam, with `_fetch_json`'s contract: JSON object or
        `ProviderUnavailable`."""
        try:
            r = self.client.get(url)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable(f"fetch failed: {type(exc).__name__}") from exc
        assert isinstance(data, dict)
        return data

    def resolver(self, *, jwks_url: str | None = None) -> JwksKeys:
        return JwksKeys(
            ISSUER,
            jwks_url,
            min_refresh_seconds=REFRESH,
            wait_seconds=0.1,
            fetch=self.fetch,
            clock=lambda: self.now,
        )

    def verifier(self) -> tuple[OidcTokens, JwksKeys]:
        keys = self.resolver()
        return OidcTokens(ISSUER, AUDIENCE, key_resolver=keys), keys


ISSUER_ORIGIN = "https://idp.test"
# alg "none": PyJWT needs the key None at runtime but types it as a real key.
NO_KEY = cast(str, None)


def signed(kid: str | None, key: Any = KEY, **overrides: Any) -> str:
    """An RS256 token for alice with this `kid` header (none when None)."""
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    headers = {"kid": kid} if kid is not None else None
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


def test_jwks_key_rotation_refetches_once_and_refuses_a_removed_key() -> None:
    idp = MockIdp(k1=KEY)
    verifier, keys = idp.verifier()
    assert verifier.verify(signed("k1")).subject == "alice"
    assert idp.requests == [DISCOVERY_PATH, JWKS_PATH]  # discovery once, then the key set
    assert verifier.verify(signed("k1")).subject == "alice"
    assert keys.fetches == 2  # a cached key is never refetched

    # The provider rotates: k1 out, k2 in. Inside the refresh interval an unknown kid cannot
    # be told from a forged one: retryable, no fetch (BE-2.15), never a 401.
    idp.keys = {"k2": ROTATED_KEY}
    with pytest.raises(ProviderUnavailable):
        verifier.verify(signed("k2", ROTATED_KEY))
    assert keys.fetches == 2
    # After the interval the unknown kid refetches the key set exactly once (the discovery
    # document's jwks_uri is remembered), and the rotated key is served from cache after.
    idp.now += REFRESH
    assert verifier.verify(signed("k2", ROTATED_KEY)).subject == "alice"
    assert idp.requests[2:] == [JWKS_PATH]
    for _ in range(3):
        assert verifier.verify(signed("k2", ROTATED_KEY)).subject == "alice"
    assert keys.fetches == 3

    # The removed key: refused (retryable) inside the interval without a fetch, then refused
    # as invalid after one refetch shows it is gone; forged kids then force no more fetches.
    with pytest.raises(ProviderUnavailable):
        verifier.verify(signed("k1"))
    assert keys.fetches == 3
    idp.now += REFRESH
    with pytest.raises(InvalidToken) as refused:
        verifier.verify(signed("k1"))
    assert not isinstance(refused.value, TokenExpired)
    assert keys.fetches == 4 and idp.requests[3:] == [JWKS_PATH]
    for forged in ("k1", "k3", "../../etc/passwd"):
        with pytest.raises(ProviderUnavailable):
            verifier.verify(signed(forged, OTHER_KEY))
    assert keys.fetches == 4
    # A token claiming the rotated kid but signed by another key is refused outright.
    with pytest.raises(InvalidToken):
        verifier.verify(signed("k2", OTHER_KEY))
    assert keys.fetches == 4


def test_oidc_refuses_alg_none_and_hs256_signed_with_the_public_key() -> None:
    idp = MockIdp(k1=KEY)
    verifier, keys = idp.verifier()
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "alice", "exp": 9999999999}
    pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    jwk_n = RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)["n"].encode()
    refused = {
        "alg none": jwt.encode(claims, NO_KEY, algorithm="none", headers={"kid": "k1"}),
        "alg none, no kid": jwt.encode(claims, NO_KEY, algorithm="none"),
        "alg NONE": _with_header(signed("k1"), {"alg": "NONE", "kid": "k1"}),
        "HS256 with the PEM public key": _hs256(claims, pem),
        "HS256 with the JWK modulus": _hs256(claims, jwk_n),
        "HS512 header": _with_header(signed("k1"), {"alg": "HS512", "kid": "k1"}),
        "no alg": _with_header(signed("k1"), {"kid": "k1"}),
    }
    for name, bad in refused.items():
        with pytest.raises(InvalidToken):
            verifier.verify(bad)
        assert idp.requests == [], name  # refused on the header, before any key fetch
    assert keys.fetches == 0
    # An RS256 header over an HS256 signature made with the public key is refused as well.
    hs = _hs256(claims, pem)
    forged = _with_header(hs, {"alg": "RS256", "kid": "k1"})
    with pytest.raises(InvalidToken):
        verifier.verify(forged)


def test_oidc_exp_nbf_leeway_issuer_and_audience() -> None:
    idp = MockIdp(k1=KEY)
    verifier, _ = idp.verifier()
    now = datetime.now(UTC)
    # 30 s leeway either way (tokens._decode): inside it accepted, beyond it refused.
    assert verifier.verify(signed("k1", exp=now - timedelta(seconds=10))).subject == "alice"
    with pytest.raises(TokenExpired):
        verifier.verify(
            signed("k1", exp=now - timedelta(seconds=60), iat=now - timedelta(minutes=5))
        )
    assert verifier.verify(signed("k1", nbf=now + timedelta(seconds=10))).subject == "alice"
    with pytest.raises(InvalidToken) as early:
        verifier.verify(signed("k1", nbf=now + timedelta(seconds=60)))
    assert not isinstance(early.value, TokenExpired)  # not yet valid is not "expired"
    # Wrong issuer, wrong audience (also in a list), and required claims missing.
    assert verifier.verify(signed("k1", aud=["other-api", AUDIENCE])).subject == "alice"
    for name, bad in {
        "wrong issuer": signed("k1", iss="https://evil.test/realms/moodymate"),
        "issuer with a trailing slash": signed("k1", iss=ISSUER + "/"),
        "wrong audience": signed("k1", aud="some-other-api"),
        "audience list without ours": signed("k1", aud=["a", "b"]),
    }.items():
        with pytest.raises(InvalidToken) as refused:
            verifier.verify(bad)
        assert not isinstance(refused.value, TokenExpired), name
    for claim in ("exp", "iss", "sub", "aud"):
        claims = jwt.decode(signed("k1"), options={"verify_signature": False})
        del claims[claim]
        with pytest.raises(InvalidToken):
            verifier.verify(jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "k1"}))


def test_jwks_token_without_kid() -> None:
    # One key at the provider: a token without kid uses it.
    single = MockIdp(k1=KEY)
    verifier, _ = single.verifier()
    assert verifier.verify(signed(None)).subject == "alice"
    with pytest.raises(InvalidToken):
        verifier.verify(signed(None, OTHER_KEY))  # the one key does not verify it
    # Two keys: a missing kid is ambiguous and refused after one fetch, then not refetched.
    double = MockIdp(k1=KEY, k2=ROTATED_KEY)
    verifier, keys = double.verifier()
    with pytest.raises(InvalidToken):
        verifier.verify(signed(None))
    assert keys.fetches == 2
    with pytest.raises(ProviderUnavailable):
        verifier.verify(signed(None))
    assert keys.fetches == 2
    assert verifier.verify(signed("k1")).subject == "alice"  # a named kid still works


@pytest.mark.db
def test_jwks_down_is_provider_unavailable(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Cold start with the provider down: ProviderUnavailable, and the outage is not hammered.
    idp = MockIdp(k1=KEY)
    idp.down = True
    verifier, keys = idp.verifier()
    for _ in range(3):
        with pytest.raises(ProviderUnavailable):
            verifier.verify(signed("k1"))
    assert idp.requests == [DISCOVERY_PATH]
    # The API answers 503 auth_provider_unavailable, retryable (never a 401 sign-out).
    insert_user(db, ISSUER, "alice", roles=("risk_analyst",))
    with oidc_client(db) as oidc:
        cast(FastAPI, oidc.app).state.token_verifier = verifier
        r = oidc.get("/api/v1/me", headers=bearer(signed("k1")))
        assert r.status_code == 503, r.text
        assert (r.json()["code"], r.json()["retryable"]) == ("auth_provider_unavailable", True)
        # Back up after the interval: signed in; down again: cached keys still verify, a
        # new kid is retryable, not invalid.
        idp.down, idp.now = False, idp.now + REFRESH
        assert oidc.get("/api/v1/me", headers=bearer(signed("k1"))).status_code == 200
        idp.down, idp.now = True, idp.now + REFRESH
        assert oidc.get("/api/v1/me", headers=bearer(signed("k1"))).status_code == 200
        r = oidc.get("/api/v1/me", headers=bearer(signed("k9", ROTATED_KEY)))
        assert (r.status_code, r.json()["code"]) == (503, "auth_provider_unavailable")
    assert keys.fetches == 4  # discovery (down), discovery + JWKS (up), JWKS (down)

    # A configured JWKS URL skips discovery; a broken key set or a plain-http jwks_uri is
    # ProviderUnavailable too.
    direct = MockIdp(k1=KEY)
    assert (
        OidcTokens(
            ISSUER, AUDIENCE, key_resolver=direct.resolver(jwks_url=ISSUER_ORIGIN + JWKS_PATH)
        )
        .verify(signed("k1"))
        .subject
        == "alice"
    )
    assert direct.requests == [JWKS_PATH]
    documents: tuple[dict[str, Any], ...] = ({"keys": [{"kty": "bogus"}]}, {"keys": "nope"}, {})
    for document in documents:

        def serve(_url: str, served: dict[str, Any] = document) -> dict[str, Any]:
            return served

        broken = JwksKeys(ISSUER, ISSUER_ORIGIN + JWKS_PATH, fetch=serve)
        with pytest.raises(ProviderUnavailable):
            broken(signed("k1"))
    http_jwks = JwksKeys(ISSUER, fetch=lambda _u: {"jwks_uri": "http://idp.test/certs"})
    with pytest.raises(ProviderUnavailable):
        http_jwks(signed("k1"))

    # The production fetcher maps transport failures and non-object bodies the same way
    # (urlopen replaced: no socket is opened).
    def refused_connection(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refused_connection)
    with pytest.raises(ProviderUnavailable):
        _fetch_json("https://idp.test/certs")
    for body in (b"[1, 2]", b"<html>down</html>", b'{"keys": ['):
        monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, b=body, **_k: io.BytesIO(b))
        with pytest.raises(ProviderUnavailable):
            _fetch_json("https://idp.test/certs")


def _with_header(token: str, header: dict[str, Any]) -> str:
    """The same payload and signature under another (unsigned-over) header."""
    _, payload, signature = token.split(".")
    raw = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    return f"{raw}.{payload}.{signature}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_oidc_malformed_tokens_are_invalid() -> None:
    idp = MockIdp(k1=KEY)
    verifier, keys = idp.verifier()
    good = signed("k1")
    header, payload, signature = good.split(".")
    malformed = {
        "empty": "",
        "garbage": "not-a-jwt",
        "two segments": f"{header}.{payload}",
        "four segments": f"{good}.{signature}",
        "dots only": "..",
        "header not base64": f"!!!.{payload}.{signature}",
        "header not JSON": f"{_b64(b'not json')}.{payload}.{signature}",
        "header a JSON list": f"{_b64(b'[1]')}.{payload}.{signature}",
        "unicode": "éé.é.é",
    }
    for name, bad in malformed.items():
        with pytest.raises(InvalidToken):
            verifier.verify(bad)
        assert idp.requests == [], name  # an unparseable header never triggers a fetch
    # A well-formed header with a known kid, but a broken payload or signature.
    signed_but_broken = {
        "payload not base64": f"{header}.!!!.{signature}",
        "payload not JSON": f"{header}.{_b64(b'not json')}.{signature}",
        "payload tampered": f"{header}.{_b64(json.dumps({'sub': 'mallory'}).encode())}.{signature}",
        "signature missing": f"{header}.{payload}.",
        "signature garbage": f"{header}.{payload}.{_b64(b'x' * 256)}",
        "kid not a string": _with_header(good, {"alg": "RS256", "kid": {"k": 1}}),
    }
    for name, bad in signed_but_broken.items():
        with pytest.raises(InvalidToken) as refused:
            verifier.verify(bad)
        assert not isinstance(refused.value, TokenExpired), name
    assert keys.fetches == 2  # the one cold-start fetch (discovery and key set), no more
    assert verifier.verify(good).subject == "alice"
