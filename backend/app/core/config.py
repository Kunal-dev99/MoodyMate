"""Application settings (BE-1.2, D-09, D-13).

All behaviour switches live here so that new behaviour can ship behind a setting (D-02).
Values come from environment variables or `backend/.env`; never commit real values.

Fail-safe (D-13): `ENV` has no default, dev authentication exists only in local/test, and
OIDC outside local/test requires HTTPS and an API audience distinct from the client ID.
Raw upload artifacts (D-15) live under `ARTIFACT_STORE_DIR`, required outside local/test and
never inside the repository; upload bounds (D-16) may be lowered, never raised.
"""

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
AuthMode = Literal["oidc", "dev"]
DataClass = Literal["synthetic", "client"]

LOCAL_ENVS = ("local", "test")

# Signing secret for AUTH_MODE=dev, which itself only runs in local/test (D-13).
DEV_SECRET_DEFAULT = "local-dev-only-secret-change-me-0123456789"  # noqa: S105

_MIB = 1024 * 1024
# D-16 §1: hard upload ceilings. The IMPORT_MAX_* settings may lower them, never raise them.
IMPORT_HARD_LIMITS: dict[str, int] = {
    "IMPORT_MAX_BYTES": 10 * _MIB,
    "IMPORT_MAX_ROWS": 50_000,
    "IMPORT_MAX_COLUMNS": 200,
    "IMPORT_MAX_CELL_CHARS": 4_000,
    "IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES": 50 * _MIB,
    "IMPORT_MAX_XLSX_RATIO": 100,
}


def _repo_root() -> Path:
    """The checkout this code runs from (the parent of backend/), else the backend dir."""
    backend = Path(__file__).resolve().parents[2]
    parent = backend.parent
    return parent if (parent / ".git").exists() or (parent / "docs").is_dir() else backend


# D-15: artifacts never live inside the source tree (they would be one `git add` away).
REPO_ROOT = _repo_root()
# local/test only, when ARTIFACT_STORE_DIR is unset: outside any checkout.
LOCAL_ARTIFACT_DIR = Path(tempfile.gettempdir()) / "moodymate-artifacts"


class Settings(BaseSettings):
    # hide_input_in_errors: a startup failure must not print secrets from the settings.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    ENV: Environment  # required: a deployment that forgets it must not start (D-13)
    DATABASE_URL: str = "postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_dev"
    AUTH_MODE: AuthMode = "dev"
    AI_ENABLED: bool = False
    # BE-6.1: required in staging/production (fail closed, D-13); local/test are synthetic.
    DATA_CLASS: DataClass | None = None
    LOG_LEVEL: str = "INFO"
    # BE-3.5: mask classified values the caller may not see. Off only in ENV=local.
    MASKING_ENABLED: bool = True
    # Phase 5 (DG-05): schema change proposals. Off makes the registry read-only, which
    # is the Phase 4 behaviour; reads of existing proposals still work.
    SCHEMA_CHANGE_ENABLED: bool = True
    # Feature 02 (WS2): ingestion writes (sources, imports, mappings, dispositions). Off
    # makes WS2 read-only: writes answer 409 ingestion_disabled (audited), reads still work.
    INGESTION_ENABLED: bool = True
    # Phase 9 (DI-06, D-02): profile each upload on receipt. Off gives the Phase 8
    # behaviour back: uploads end RECEIVED with no profile.
    IMPORT_PROFILING_ENABLED: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # D-15: root of the write-once raw artifact store. Required outside local/test (D-13);
    # must be absolute and outside the repository. Pair one directory with each DB copy.
    ARTIFACT_STORE_DIR: Path | None = None
    # D-16 §1: upload bounds; each may only lower its IMPORT_HARD_LIMITS ceiling.
    IMPORT_MAX_BYTES: int = IMPORT_HARD_LIMITS["IMPORT_MAX_BYTES"]
    IMPORT_MAX_ROWS: int = IMPORT_HARD_LIMITS["IMPORT_MAX_ROWS"]
    IMPORT_MAX_COLUMNS: int = IMPORT_HARD_LIMITS["IMPORT_MAX_COLUMNS"]
    IMPORT_MAX_CELL_CHARS: int = IMPORT_HARD_LIMITS["IMPORT_MAX_CELL_CHARS"]
    IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES: int = IMPORT_HARD_LIMITS[
        "IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES"
    ]
    IMPORT_MAX_XLSX_RATIO: int = IMPORT_HARD_LIMITS["IMPORT_MAX_XLSX_RATIO"]

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
        # BE-6.1 (DG-06), D-14: client data only in production; every other environment
        # holds synthetic data only. Checked last, so more specific errors surface first.
        if self.DATA_CLASS is None and self.ENV in ("staging", "production"):
            raise ValueError(f"DATA_CLASS is required when ENV={self.ENV}")
        if self.DATA_CLASS == "client" and self.ENV != "production":  # D-14
            raise ValueError(f"DATA_CLASS=client is not allowed when ENV={self.ENV}")
        self._guard_imports()
        return self

    def _guard_imports(self) -> None:
        """D-15 artifact root and D-16 bounds (Feature 02, Phase 8)."""
        root = self.ARTIFACT_STORE_DIR
        if root is None and self.ENV not in LOCAL_ENVS:
            raise ValueError(f"ARTIFACT_STORE_DIR is required when ENV={self.ENV}")
        if root is not None:
            if not root.is_absolute():
                raise ValueError("ARTIFACT_STORE_DIR must be an absolute path")
            if root.resolve().is_relative_to(REPO_ROOT):
                raise ValueError("ARTIFACT_STORE_DIR must be outside the repository")
        for name, ceiling in IMPORT_HARD_LIMITS.items():
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
            if value > ceiling:
                raise ValueError(f"{name} may only lower the hard ceiling ({ceiling})")

    @property
    def artifact_store_dir(self) -> Path:
        """The configured root, else (local/test only, D-13) a directory outside the repo."""
        return self.ARTIFACT_STORE_DIR or LOCAL_ARTIFACT_DIR

    @property
    def data_class(self) -> DataClass:
        """The effective data class: as configured, else synthetic (local/test only)."""
        return self.DATA_CLASS or "synthetic"

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
