"""Bearer-token verification for both authentication modes (BE-2.3, D-09).

* `OidcTokens`: asymmetric JWTs from the enterprise identity provider, keys from its
  JWKS. Symmetric algorithms are never accepted (prevents algorithm-confusion attacks).
* `DevTokens`: HS256 JWTs signed with a local secret, for local/test only. Production
  startup refuses this mode (Settings validator).
"""

import http.client
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import jwt

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
JsonFetcher = Callable[[str], dict[str, Any]]


class ProviderUnavailable(Exception):
    """The identity provider's discovery document or signing keys cannot be obtained."""


def _fetch_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - scheme checked by caller
            data = json.load(resp)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # HTTPException covers truncated bodies (IncompleteRead) and malformed responses.
        raise ProviderUnavailable(f"fetch failed: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise ProviderUnavailable("unexpected response shape")
    return data


class JwksKeys:
    """Signing keys from the provider's JWKS, fetched lazily and refreshed sparingly (BE-2.10).

    * Network access happens at most once per `min_refresh_seconds`, whether the trigger is
      an unknown `kid` (a forged token cannot force a fetch per request) or a failed fetch
      (a provider outage is not hammered).
    * Cached keys are read without locking; the lock only guards bookkeeping, never network
      I/O (BE-2.13). A slow provider therefore cannot stall requests whose key is cached.
    * One request performs a fetch. Concurrent misses wait for it (bounded) only while no
      keys are cached (cold start); otherwise an unknown kid is rejected at once, so forged
      kids cannot park worker threads behind a slow provider.
    * URLs must be https unless `allow_http` (local/test only, D-13). A discovered
      `jwks_uri` is checked before it is remembered.
    """

    def __init__(
        self,
        issuer: str,
        jwks_url: str | None = None,
        *,
        allow_http: bool = False,
        min_refresh_seconds: float = 60.0,
        wait_seconds: float = 10.0,
        fetch: JsonFetcher = _fetch_json,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._jwks_url = jwks_url
        self._allow_http = allow_http
        self._min_refresh = min_refresh_seconds
        self._wait = wait_seconds
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str | None, Any] = {}  # replaced wholesale, never mutated
        self._last_attempt: float | None = None
        self._inflight: threading.Event | None = None
        self.fetches = 0  # network round trips, for tests and diagnostics

    def _checked(self, url: str) -> str:
        if url.startswith("https://") or (self._allow_http and url.startswith("http://")):
            return url
        raise ProviderUnavailable("identity provider URL must use https")

    def _download(self, jwks_url: str | None) -> tuple[str, dict[str | None, Any]]:
        """Network I/O only; called without the lock held."""
        if jwks_url is None:
            self.fetches += 1
            doc = self._fetch(
                self._checked(self._issuer.rstrip("/") + "/.well-known/openid-configuration")
            )
            uri = doc.get("jwks_uri")
            if not isinstance(uri, str):
                raise ProviderUnavailable("discovery document has no jwks_uri")
            jwks_url = self._checked(uri)  # checked BEFORE it can be remembered
        self.fetches += 1
        data = self._fetch(self._checked(jwks_url))
        try:
            jwk_set = jwt.PyJWKSet.from_dict(data)
        except jwt.PyJWTError as exc:
            raise ProviderUnavailable(f"unusable JWKS: {type(exc).__name__}") from exc
        return jwks_url, {k.key_id: k.key for k in jwk_set.keys}

    def _lookup(self, kid: str | None) -> Any:
        keys = self._keys
        if kid is None:
            # A token without kid is acceptable only when the provider has exactly one key.
            return next(iter(keys.values())) if len(keys) == 1 else None
        return keys.get(kid)

    def __call__(self, token: str) -> Any:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise InvalidToken(type(exc).__name__) from exc

        key = self._lookup(kid)  # fast path: no lock
        if key is not None:
            return key

        with self._lock:
            # Re-check under the lock: another request's fetch may have stored this key
            # between the fast-path miss and here.
            key = self._lookup(kid)
            if key is not None:
                return key
            inflight = self._inflight
            fetcher = False
            if inflight is not None and self._keys:
                # Keys are cached and a refresh is already running: this kid is unknown
                # now. Don't park a worker thread waiting on a possibly slow provider.
                raise InvalidToken("unknown signing key")
            if inflight is None:
                may_fetch = (
                    self._last_attempt is None
                    or self._clock() - self._last_attempt >= self._min_refresh
                )
                if may_fetch:
                    self._inflight = inflight = threading.Event()
                    self._last_attempt = self._clock()
                    fetcher = True
                    jwks_url = self._jwks_url

        if inflight is None:  # not allowed to fetch now
            if not self._keys:
                raise ProviderUnavailable("identity provider unavailable (backing off)")
            raise InvalidToken("unknown signing key")

        if fetcher:
            try:
                url, keys = self._download(jwks_url)
                with self._lock:
                    self._jwks_url = url
                    self._keys = keys
            finally:
                with self._lock:
                    self._inflight = None
                inflight.set()
        elif not inflight.wait(self._wait):
            raise ProviderUnavailable("identity provider is slow to respond")

        key = self._lookup(kid)
        if key is not None:
            return key
        if not self._keys:
            raise ProviderUnavailable("identity provider keys unavailable")
        raise InvalidToken("unknown signing key")


class OidcTokens:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        key_resolver: KeyResolver | None = None,
        allow_http: bool = False,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self._resolver: KeyResolver = key_resolver or JwksKeys(
            issuer, jwks_url, allow_http=allow_http
        )

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidToken(type(exc).__name__) from exc
        if header.get("alg") not in ASYMMETRIC_ALGORITHMS:
            raise InvalidToken("algorithm not allowed")
        key = self._resolver(token)  # InvalidToken / ProviderUnavailable propagate
        return _decode(token, key, ASYMMETRIC_ALGORITHMS, self.issuer, self.audience)


def build_verifier(settings: Settings) -> TokenVerifier:
    if settings.AUTH_MODE == "oidc":
        assert settings.OIDC_ISSUER and settings.OIDC_AUDIENCE  # enforced by Settings
        return OidcTokens(
            settings.OIDC_ISSUER,
            settings.OIDC_AUDIENCE,
            settings.OIDC_JWKS_URL,
            allow_http=settings.allow_insecure_oidc,
        )
    return DevTokens(settings.AUTH_DEV_SECRET, settings.AUTH_DEV_TOKEN_TTL_MINUTES)
