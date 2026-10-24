"""Application settings (BE-1.2, D-09, D-13).

All behaviour switches live here so that new behaviour can ship behind a setting (D-02).
Values come from environment variables or `backend/.env`; never commit real values.

Fail-safe (D-13): `ENV` has no default, dev authentication exists only in local/test, and
OIDC outside local/test requires HTTPS and an API audience distinct from the client ID.
Raw upload artifacts (D-15) live under `ARTIFACT_STORE_DIR`, required outside local/test and
never inside the repository; upload bounds (D-16) may be lowered, never raised.
"""

import tempfile
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
AuthMode = Literal["oidc", "dev"]
DataClass = Literal["synthetic", "client"]

LOCAL_ENVS = ("local", "test")
# D-53: the per-route bucket settings are RATE_LIMIT_<ROUTE>_BURST and _PER_MINUTE.
RATE_LIMIT_SUFFIXES = ("_BURST", "_PER_MINUTE")

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
    # D-25 §1: decoded cell characters (header included) of one upload, every format. One
    # XLSX shared string can be referenced by any number of cells, so the file size alone
    # does not bound what a workbook decodes to.
    "IMPORT_MAX_DECODED_CHARS": 50 * _MIB,
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
    # Phase 10 (DI-03, D-02): mapping writes (create, version, validate, submit, approve).
    # Off freezes mappings at their ACTIVE versions: writes answer 409
    # mapping_change_disabled (audited), reads and imports using ACTIVE versions still work.
    MAPPING_CHANGE_ENABLED: bool = True
    # Phase 12 (DI-05): the absolute tolerance of control-total reconciliation items,
    # PROVISIONAL until the client sets one (Decimal, never float). Row counts always have
    # zero tolerance. Reconciliation is a control: it has no off switch.
    RECONCILIATION_TOLERANCE: Decimal = Decimal("0")
    # Phase 12 (DI-06, D-02): compare each new profile with the source's baseline and raise
    # drift events. Off stops new events; existing ones stay readable and reviewable.
    DRIFT_DETECTION_ENABLED: bool = True
    # PROVISIONAL thresholds: a null rate moving by more than this (0..1) is a
    # NULL_RATE_SHIFT; a numeric min or max beyond the baseline's by more than this share of
    # the baseline's range is a RANGE_SHIFT.
    DRIFT_NULL_RATE_THRESHOLD: Decimal = Decimal("0.10")
    DRIFT_RANGE_THRESHOLD: Decimal = Decimal("0.25")
    # Phase 14 (FC-01, D-29, D-02): project validated imports into the WS3 domain tables.
    # Off stops new projections (409 domain_projection_disabled, audited); existing domain
    # rows stay readable.
    DOMAIN_PROJECTION_ENABLED: bool = True
    # Phase 15 (FC-04, D-30, D-31, D-02): new dataset snapshots and calculation runs. Off
    # refuses both (409 calculations_disabled, audited); existing snapshots and runs stay
    # readable.
    CALCULATIONS_ENABLED: bool = True
    # Phase 19 (CR-01, D-37 §7, D-02): project the credit schemas (counterparty,
    # credit_exposure). Off answers 409 projection_unsupported for them (audited), exactly the
    # pre-Feature-04 behaviour; projected credit rows stay readable. Validated at startup
    # (D-13): a value that is not a boolean stops the app from starting.
    CREDIT_PROJECTION_ENABLED: bool = True
    # D-33 minimum aggregation (PROVISIONAL): a maturity-ladder figure built from fewer than
    # this many contributions (but at least one) takes its contributions' classification, so
    # a role not cleared for them sees it masked. 1 turns the rule off.
    CALC_MIN_AGGREGATE_COUNT: int = 3
    # A run still RUNNING this many seconds after it started is presumed stopped: a replay of
    # its Idempotency-Key ends it FAILED (ENGINE_ERROR) and returns it.
    CALC_RUN_STALE_SECONDS: int = 900
    # Phase 16 (FC-07, D-32 §3, D-02): governed rule changes (propose, submit, approve or
    # reject a rule version). Off freezes the rules at their ACTIVE versions: the writes
    # answer 409 rule_change_disabled (audited); reads and runs keep working.
    RULE_CHANGE_ENABLED: bool = True
    # Phase 20 (CR-02, D-39 §6, D-02): the obligor rating workflow (propose, approve or
    # reject). Off freezes ratings at their ACTIVE versions: the writes answer 409
    # rating_change_disabled (audited); reads and runs keep working and pin the ACTIVE
    # ratings as they stand. Validated at startup (D-13): a non-boolean stops the app.
    RATING_CHANGE_ENABLED: bool = True
    # Phase 24 (WS4 RO-02, D-44 §9, D-02): scenario writes (create, version, edit, confirm,
    # submit, decide, close). Off answers 409 scenarios_disabled to every write (audited
    # DENIED, nothing written); reads keep working. Validated at startup (D-13): a
    # non-boolean stops the app.
    SCENARIOS_ENABLED: bool = True
    # Phase 31 (FC-01, D-57 §2, D-02): projector v2 for the typed-maturity instrument contract
    # (matched by contract hash). Off answers 409 projection_unsupported for typed imports
    # (audited DENIED); v1 imports keep projector 1. Validated at startup (D-13).
    INSTRUMENT_PROJECTOR_V2_ENABLED: bool = True
    # Phase 31 (FC-02, D-58 §4, D-02): identity links, written by the projection step and by
    # POST /identity-links/reconcile. Off: no new links, and the reconcile answers 409
    # identity_links_disabled (audited DENIED); existing links stay readable.
    IDENTITY_LINKS_ENABLED: bool = True
    # Phase 32 (FC-01, D-61 §1-2, D-02): projector v3 for the rate-terms instrument contract
    # (matched by contract hash), which also writes the `instrument_term_version` rows. Off
    # answers 409 projection_unsupported for rate-terms imports (audited DENIED); v1 and
    # typed-maturity imports are unaffected, and existing term rows stay readable.
    INSTRUMENT_TERMS_PROJECTION_ENABLED: bool = True
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Phase 28 (BE-28.3, D-53): in-process token buckets per (tenant, principal, route) on the
    # expensive endpoints. Off is allowed only in local/test (D-13). Each route has a burst
    # (bucket capacity) and a refill rate per minute; the defaults are PROVISIONAL. The limit
    # is per process: N API processes allow N times these values (ARCHITECTURE.md).
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_CREATE_IMPORT_BURST: int = 5
    RATE_LIMIT_CREATE_IMPORT_PER_MINUTE: int = 10
    RATE_LIMIT_VALIDATE_IMPORT_BURST: int = 5
    RATE_LIMIT_VALIDATE_IMPORT_PER_MINUTE: int = 10
    RATE_LIMIT_SCENARIO_VALIDATION_BURST: int = 5
    RATE_LIMIT_SCENARIO_VALIDATION_PER_MINUTE: int = 10
    RATE_LIMIT_CALCULATION_RUN_BURST: int = 5
    RATE_LIMIT_CALCULATION_RUN_PER_MINUTE: int = 10
    RATE_LIMIT_SCENARIO_RUN_BURST: int = 3
    RATE_LIMIT_SCENARIO_RUN_PER_MINUTE: int = 5

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
    IMPORT_MAX_DECODED_CHARS: int = IMPORT_HARD_LIMITS["IMPORT_MAX_DECODED_CHARS"]

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
        if not self.RATE_LIMIT_ENABLED and not local:  # D-53 §4
            raise ValueError(f"RATE_LIMIT_ENABLED=false is not allowed when ENV={self.ENV}")
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
        self._guard_quality()
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

    def _guard_quality(self) -> None:
        """Phase 12 thresholds: finite, not negative; a null-rate threshold is a rate. Phase 15
        counts: at least 1."""
        for name in (
            "RECONCILIATION_TOLERANCE",
            "DRIFT_NULL_RATE_THRESHOLD",
            "DRIFT_RANGE_THRESHOLD",
        ):
            value: Decimal = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite decimal of at least 0")
        if self.DRIFT_NULL_RATE_THRESHOLD > 1:
            raise ValueError("DRIFT_NULL_RATE_THRESHOLD must be at most 1")
        # D-53 §1: every bucket holds at least one request and refills.
        rate_limits = [n for n in type(self).model_fields if n.endswith(RATE_LIMIT_SUFFIXES)]
        for name in ("CALC_MIN_AGGREGATE_COUNT", "CALC_RUN_STALE_SECONDS", *rate_limits):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")

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
