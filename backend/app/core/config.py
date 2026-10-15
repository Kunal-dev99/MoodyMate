"""Application settings (BE-1.2).

All behaviour switches live here so that new behaviour can ship behind a setting (D-02).
Values come from environment variables or `backend/.env`; never commit real values.
"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
AuthMode = Literal["oidc", "dev"]
DataClass = Literal["synthetic", "client"]

# Local-only signing secret for AUTH_MODE=dev. Refused outside local/test.
DEV_SECRET_DEFAULT = "local-dev-only-secret-change-me-0123456789"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: Environment = "local"
    DATABASE_URL: str = "postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_dev"
    AUTH_MODE: AuthMode = "dev"
    AI_ENABLED: bool = False
    DATA_CLASS: DataClass = "synthetic"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # D-09 dev mode: HS256 tokens signed with this local secret. The default is only
    # accepted in local/test environments.
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
        # D-09: the dev token issuer must never be reachable in production.
        if self.ENV == "production" and self.AUTH_MODE == "dev":
            raise ValueError("AUTH_MODE=dev is not allowed when ENV=production")
        if self.AUTH_MODE == "dev":
            if self.ENV not in ("local", "test") and self.AUTH_DEV_SECRET == DEV_SECRET_DEFAULT:
                raise ValueError("AUTH_DEV_SECRET must be set outside local/test")
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
        return self

    @property
    def can_self_provision(self) -> bool:
        """Unknown identities are created on first sign-in only in local development."""
        return self.ENV == "local"

    @property
    def dev_sessions_enabled(self) -> bool:
        """The dev sign-in endpoints exist only in dev mode on local/test."""
        return self.AUTH_MODE == "dev" and self.ENV in ("local", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
