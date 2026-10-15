"""Bearer-token verification for both authentication modes (BE-2.3, D-09).

* `OidcTokens`: asymmetric JWTs from the enterprise identity provider, keys from its
  JWKS. Symmetric algorithms are never accepted (prevents algorithm-confusion attacks).
* `DevTokens`: HS256 JWTs signed with a local secret, for local/test only. Production
  startup refuses this mode (Settings validator).
"""

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from app.core.config import Settings
from app.governance.constants import DEV_AUDIENCE, DEV_ISSUER

ASYMMETRIC_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384"]


class InvalidToken(Exception):
    """The bearer token is malformed, unsigned, mis-signed or has invalid claims."""


class TokenExpired(InvalidToken):
    pass


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    email: str | None
    name: str


class TokenVerifier(Protocol):
    def verify(self, token: str) -> VerifiedIdentity: ...


def _identity(claims: dict[str, Any]) -> VerifiedIdentity:
    email = claims.get("email")
    name = claims.get("name") or claims.get("preferred_username") or email or claims["sub"]
    return VerifiedIdentity(
        issuer=str(claims["iss"]),
        subject=str(claims["sub"]),
        email=str(email) if email else None,
        name=str(name),
    )


def _decode(
    token: str, key: Any, algorithms: list[str], issuer: str, audience: str
) -> VerifiedIdentity:
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iss", "sub", "aud"]},
            leeway=30,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("token expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken(type(exc).__name__) from exc
    return _identity(claims)


class DevTokens:
    algorithm = "HS256"

    def __init__(self, secret: str, ttl_minutes: int) -> None:
        self._secret = secret
        self.ttl = timedelta(minutes=ttl_minutes)

    def issue(self, subject: str, email: str | None, name: str) -> str:
        now = datetime.now(UTC)
        claims = {
            "iss": DEV_ISSUER,
            "aud": DEV_AUDIENCE,
            "sub": subject,
            "email": email,
            "name": name,
            "iat": now,
            "exp": now + self.ttl,
        }
        return jwt.encode(claims, self._secret, algorithm=self.algorithm)

    def verify(self, token: str) -> VerifiedIdentity:
        return _decode(token, self._secret, [self.algorithm], DEV_ISSUER, DEV_AUDIENCE)


KeyResolver = Callable[[str], Any]


class OidcTokens:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_resolver: KeyResolver | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self._jwks_url = jwks_url
        self._resolver = key_resolver

    def _discover_jwks_url(self) -> str:
        url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
        if not url.startswith(("https://", "http://")):
            raise InvalidToken("issuer is not an http(s) URL")
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - scheme checked
            jwks_uri = json.load(resp)["jwks_uri"]
        return str(jwks_uri)

    def _key_for(self, token: str) -> Any:
        if self._resolver is None:
            client = PyJWKClient(self._jwks_url or self._discover_jwks_url(), cache_keys=True)
            self._resolver = lambda t: client.get_signing_key_from_jwt(t).key
        try:
            return self._resolver(token)
        except jwt.PyJWTError as exc:
            raise InvalidToken(type(exc).__name__) from exc

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidToken(type(exc).__name__) from exc
        if header.get("alg") not in ASYMMETRIC_ALGORITHMS:
            raise InvalidToken("algorithm not allowed")
        return _decode(
            token, self._key_for(token), ASYMMETRIC_ALGORITHMS, self.issuer, self.audience
        )


def build_verifier(settings: Settings) -> TokenVerifier:
    if settings.AUTH_MODE == "oidc":
        assert settings.OIDC_ISSUER and settings.OIDC_AUDIENCE  # enforced by Settings
        return OidcTokens(settings.OIDC_ISSUER, settings.OIDC_AUDIENCE, settings.OIDC_JWKS_URL)
    return DevTokens(settings.AUTH_DEV_SECRET, settings.AUTH_DEV_TOKEN_TTL_MINUTES)
