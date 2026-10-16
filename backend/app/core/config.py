"""Application settings (BE-1.2, D-09, D-13).

All behaviour switches live here so that new behaviour can ship behind a setting (D-02).
Values come from environment variables or `backend/.env`; never commit real values.

Fail-safe (D-13): `ENV` has no default, dev authentication exists only in local/test, and
OIDC outside local/test requires HTTPS and an API audience distinct from the client ID.
"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
AuthMode = Literal["oidc", "dev"]
DataClass = Literal["synthetic", "client"]

LOCAL_ENVS = ("local", "test")

# Signing secret for AUTH_MODE=dev, which itself only runs in local/test (D-13).
DEV_SECRET_DEFAULT = "local-dev-only-secret-change-me-0123456789"  # noqa: S105


class Settings(BaseSettings):
    # hide_input_in_errors: a startup failure must not print secrets from the settings.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    ENV: Environment  # required: a deployment that forgets it must not start (D-13)
    DATABASE_URL: str = "postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_dev"
    AUTH_MODE: AuthMode = "dev"
    AI_ENABLED: bool = False
    DATA_CLASS: DataClass = "synthetic"
    LOG_LEVEL: str = "INFO"
    # BE-3.5: mask classified values the caller may not see. Off only in ENV=local.
    MASKING_ENABLED: bool = True
    # Phase 5 (DG-05): schema change proposals. Off makes the registry read-only, which
    # is the Phase 4 behaviour; reads of existing proposals still work.
    SCHEMA_CHANGE_ENABLED: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # D-09 dev mode: HS256 tokens signed with this local secret (local/test only).
    AUTH_DEV_SECRET: str = DEV_SECRET_DEFAULT
    AUTH_DEV_TOKEN_TTL_MINUTES: int = 480

    # D-09 oidc mode: tokens from the enterprise identity provider.
    OIDC_ISSUER: str | None = None
    OIDC_AUDIENCE: str | None = None
    OIDC_CLIENT_ID: str | None = None  # public client used by the frontend (PKCE)
    OIDC_JWKS_URL: str | None = None  # defaults to the issuer's discovery document
    OIDC_SCOPES: str = "openid profile email"

    @model_validator(mode="after")
    def _guard_auth(self) -> "Settings":
        local = self.ENV in LOCAL_ENVS
        # BE-6.1 (DG-06): client data only where it may live; local and test hold
        # synthetic data only.
        if self.DATA_CLASS == "client" and self.ENV not in ("staging", "production"):
            raise ValueError(f"DATA_CLASS=client is not allowed when ENV={self.ENV}")
        if not self.MASKING_ENABLED and self.ENV != "local":
            raise ValueError(f"MASKING_ENABLED=false is not allowed when ENV={self.ENV}")
        if self.AUTH_MODE == "dev":
            # D-09/D-13: the dev token issuer exists only on developer machines and CI.
            if not local:
                raise ValueError(f"AUTH_MODE=dev is not allowed when ENV={self.ENV}")
            if len(self.AUTH_DEV_SECRET) < 32:
                raise ValueError("AUTH_DEV_SECRET must be at least 32 characters")
        if self.AUTH_MODE == "oidc":
            missing = [
                name
                for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_CLIENT_ID")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"AUTH_MODE=oidc requires {', '.join(missing)}")
            if self.OIDC_AUDIENCE == self.OIDC_CLIENT_ID:
                # Otherwise ID tokens issued to the frontend pass as API access tokens.
                raise ValueError("OIDC_AUDIENCE must be the API audience, not OIDC_CLIENT_ID")
            if not local:
                for name in ("OIDC_ISSUER", "OIDC_JWKS_URL"):
                    value = getattr(self, name)
                    if value and not value.startswith("https://"):
                        raise ValueError(f"{name} must use https:// when ENV={self.ENV}")
        return self

    @property
    def allow_insecure_oidc(self) -> bool:
        """Plain-http identity providers are accepted only in local/test (D-13)."""
        return self.ENV in LOCAL_ENVS

    @property
    def can_self_provision(self) -> bool:
        """Unknown identities are created on first sign-in only in local development."""
        return self.ENV == "local"

    @property
    def dev_sessions_enabled(self) -> bool:
        """The dev sign-in endpoints exist only in dev mode (itself local/test only)."""
        return self.AUTH_MODE == "dev" and self.ENV in LOCAL_ENVS


@lru_cache
def get_settings() -> Settings:
    return Settings()  # ENV comes from the environment or backend/.env
