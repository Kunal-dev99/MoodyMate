"""BE-2.3: principal resolution for AUTH_MODE=oidc and AUTH_MODE=dev."""

import base64
import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.tokens import OidcTokens
from app.main import create_app
from app.tests.conftest import dev_token, insert_user

pytestmark = pytest.mark.db

ISSUER = "https://idp.test/realms/moodymate"
AUDIENCE = "moodymate-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_settings(db: str, env: str = "test") -> Settings:
    return Settings(
        ENV=env,
        DATABASE_URL=db,
        AUTH_MODE="oidc",
        OIDC_ISSUER=ISSUER,
        OIDC_AUDIENCE=AUDIENCE,
        OIDC_CLIENT_ID="moodymate-web",
    )


def oidc_client(settings: Settings) -> TestClient:
    app = create_app(settings)
    # Local issuer: the verifier resolves the signing key from our test key pair instead
    # of fetching a JWKS over the network.
    app.state.token_verifier = OidcTokens(
        ISSUER, AUDIENCE, key_resolver=lambda _t: KEY.public_key()
    )
    return TestClient(app)


def token(sub: str = "alice", key: Any = KEY, alg: str = "RS256", **overrides: Any) -> str:
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
    return jwt.encode(claims, key, algorithm=alg)


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture
def oidc(db: str) -> Iterator[TestClient]:
    insert_user(db, ISSUER, "alice", roles=("risk_analyst",))
    with oidc_client(oidc_settings(db)) as c:
        yield c


def test_valid_oidc_token_resolves_principal(oidc: TestClient) -> None:
    r = oidc.get("/api/v1/me", headers=bearer(token()))
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == ["risk_analyst"]
    assert r.json()["display_name"] == "User alice"  # from the user record, not the token


def test_no_token_is_401_with_challenge(oidc: TestClient) -> None:
    r = oidc.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token_is_401(oidc: TestClient) -> None:
    past = datetime.now(UTC) - timedelta(hours=1)
    r = oidc.get("/api/v1/me", headers=bearer(token(exp=past, iat=past - timedelta(minutes=5))))
    assert r.status_code == 401
    assert r.json()["code"] == "token_expired"
    assert 'error="invalid_token"' in r.headers["WWW-Authenticate"]


def test_wrong_issuer_is_401(oidc: TestClient) -> None:
    r = oidc.get("/api/v1/me", headers=bearer(token(iss="https://evil.test")))
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_token"


def test_wrong_audience_is_401(oidc: TestClient) -> None:
    r = oidc.get("/api/v1/me", headers=bearer(token(aud="some-other-api")))
    assert r.status_code == 401


def test_wrong_signing_key_is_401(oidc: TestClient) -> None:
    r = oidc.get("/api/v1/me", headers=bearer(token(key=OTHER_KEY)))
    assert r.status_code == 401


def _hs256(payload: dict[str, Any], secret: bytes) -> str:
    """Hand-built HS256 JWT. PyJWT refuses to HMAC-sign with a PEM key, an attacker won't."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = (
        f"{b64(b'{\"alg\":\"HS256\",\"typ\":\"JWT\"}')}.{b64(json.dumps(payload).encode())}"
    )
    sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64(sig)}"


def test_symmetric_and_unsigned_tokens_are_rejected_in_oidc_mode(oidc: TestClient) -> None:
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "alice", "exp": 9999999999}
    pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    confused = _hs256(claims, pem)  # algorithm confusion: public key used as HMAC secret
    unsigned = jwt.encode(claims, "", algorithm="none")
    for bad in (confused, unsigned, "not-a-jwt"):
        r = oidc.get("/api/v1/me", headers=bearer(bad))
        assert r.status_code == 401, bad


def test_unknown_user_is_not_provisioned_outside_local(oidc: TestClient) -> None:
    r = oidc.get("/api/v1/me", headers=bearer(token(sub="stranger")))
    assert r.status_code == 403
    assert r.json()["code"] == "account_not_provisioned"


def test_unknown_user_is_provisioned_without_roles_in_local(db: str) -> None:
    with oidc_client(oidc_settings(db, env="local")) as c:
        h = bearer(token(sub="newcomer"))
        me = c.get("/api/v1/me", headers=h)
        assert me.status_code == 200, me.text
        assert me.json()["roles"] == []
        assert c.get("/api/v1/me/permissions", headers=h).json() == {"permissions": []}
        assert c.get("/api/v1/users", headers=h).status_code == 403  # default deny


def test_disabled_user_is_403(db: str) -> None:
    insert_user(db, ISSUER, "mallory", roles=("platform_admin",), status="DISABLED")
    with oidc_client(oidc_settings(db)) as c:
        r = c.get("/api/v1/me", headers=bearer(token(sub="mallory")))
    assert r.status_code == 403
    assert r.json()["code"] == "account_disabled"


def test_dev_endpoints_absent_in_oidc_mode(oidc: TestClient) -> None:
    assert oidc.post("/api/v1/dev/sessions", json={"role": "auditor"}).status_code == 404
    assert oidc.get("/api/v1/auth/config").json() == {
        "mode": "oidc",
        "environment": {"env": "test", "data_class": "synthetic"},
        "dev_sessions": False,
        "oidc": {"issuer": ISSUER, "client_id": "moodymate-web", "scopes": "openid profile email"},
    }


# --- dev mode --------------------------------------------------------------------------


def test_dev_token_round_trip(api: TestClient) -> None:
    assert api.get("/api/v1/auth/config").json() == {
        "mode": "dev",
        "environment": {"env": "test", "data_class": "synthetic"},
        "dev_sessions": True,
        "oidc": None,
    }
    r = api.get("/api/v1/me", headers=dev_token(api, "auditor"))
    assert r.status_code == 200
    assert r.json()["roles"] == ["auditor"]


def test_tampered_dev_token_is_401(api: TestClient) -> None:
    good = dev_token(api, "auditor")["Authorization"].removeprefix("Bearer ")
    forged = jwt.encode(jwt.decode(good, options={"verify_signature": False}), "x" * 40, "HS256")
    assert api.get("/api/v1/me", headers=bearer(forged)).status_code == 401


def test_dev_token_is_rejected_by_oidc_mode(api: TestClient, oidc: TestClient) -> None:
    assert oidc.get("/api/v1/me", headers=dev_token(api, "auditor")).status_code == 401


def test_unknown_dev_role_is_422(api: TestClient) -> None:
    r = api.post("/api/v1/dev/sessions", json={"role": "superuser"})
    assert r.status_code == 422
    assert r.json()["code"] == "unknown_role"


def test_dev_mode_refused_outside_local_and_test() -> None:
    # D-13 supersedes D-09's staging allowance: dev auth exists only in local/test.
    for env in ("staging", "production"):
        with pytest.raises(ValueError, match=f"AUTH_MODE=dev is not allowed when ENV={env}"):
            Settings(ENV=env, AUTH_MODE="dev", AUTH_DEV_SECRET="s" * 40)


def test_dev_mode_refused_in_production() -> None:
    with pytest.raises(ValueError, match="AUTH_MODE=dev is not allowed"):
        Settings(ENV="production", AUTH_MODE="dev")


def test_oidc_mode_requires_issuer_audience_client() -> None:
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        Settings(ENV="production", AUTH_MODE="oidc")
