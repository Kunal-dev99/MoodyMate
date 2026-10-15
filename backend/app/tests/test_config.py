import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app


def test_production_with_dev_auth_is_refused() -> None:
    with pytest.raises(ValidationError, match="AUTH_MODE=dev is not allowed"):
        Settings(ENV="production", AUTH_MODE="dev")


def test_app_refuses_to_start_with_dev_auth_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "dev")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            create_app()
    finally:
        get_settings.cache_clear()


def test_production_with_oidc_is_allowed() -> None:
    s = Settings(ENV="production", AUTH_MODE="oidc")
    assert s.AUTH_MODE == "oidc"


def test_safe_defaults() -> None:
    s = Settings()
    assert s.AI_ENABLED is False
    assert s.DATA_CLASS == "synthetic"
