"""Authentication and authorization (BE-2.3, BE-2.4, BE-3.2, D-04): dev sign-in, OIDC token
validation, fail-closed route declarations and audited 403s."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core.auth import PUBLIC_PATHS, undeclared_routes
from app.core.config import Settings
from app.core.tokens import OidcTokens
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
