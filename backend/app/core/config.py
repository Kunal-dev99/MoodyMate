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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: Environment = "local"
    DATABASE_URL: str = "postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_dev"
    AUTH_MODE: AuthMode = "dev"
    AI_ENABLED: bool = False
    DATA_CLASS: DataClass = "synthetic"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    @model_validator(mode="after")
    def _guard_production(self) -> "Settings":
        # D-09: the dev token issuer must never be reachable in production.
        if self.ENV == "production" and self.AUTH_MODE == "dev":
            raise ValueError("AUTH_MODE=dev is not allowed when ENV=production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
