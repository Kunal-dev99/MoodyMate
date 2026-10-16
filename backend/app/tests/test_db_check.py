"""Local start-script helper: diagnose why the backend cannot sign in to its database."""

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

from scripts.db_check import (
    BAD_LOGIN,
    NO_DATABASE,
    OK,
    UNREACHABLE,
    check,
    free_port,
    main,
    set_env_port,
)


def _with(url: str, **parts: Any) -> str:
    return make_url(url).set(**parts).render_as_string(hide_password=False)


def test_connects_with_the_right_credentials(database_url: str) -> None:
    code, _ = asyncio.run(check(database_url))
    assert code == OK


def test_wrong_password_is_reported_as_a_rejected_login(database_url: str) -> None:
    code, message = asyncio.run(check(_with(database_url, password="not-the-password")))
    assert code == BAD_LOGIN
    assert "not-the-password" not in message


def test_unknown_user_is_reported_as_a_rejected_login(database_url: str) -> None:
    code, _ = asyncio.run(check(_with(database_url, username="no_such_role_xyz")))
    assert code == BAD_LOGIN


def test_missing_database_is_reported(database_url: str) -> None:
    code, _ = asyncio.run(check(_with(database_url, database="no_such_database_xyz")))
    assert code == NO_DATABASE


def test_nothing_listening_is_reported_as_unreachable() -> None:
    code, _ = asyncio.run(check("postgresql+asyncpg://u:p@127.0.0.1:1/x", connect_timeout=2))
    assert code == UNREACHABLE


def test_free_port_skips_a_port_in_use() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        busy = held.getsockname()[1]
        port = free_port(busy)
    assert port > busy


def test_set_env_port_changes_only_the_database_port(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ENV=local\n"
        "DATABASE_URL=postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_dev\n"
        "AUTH_MODE=dev\n"
    )
    set_env_port(env, 5433)
    assert env.read_text() == (
        "ENV=local\n"
        "DATABASE_URL=postgresql+asyncpg://moodymate:moodymate@localhost:5433/moodymate_dev\n"
        "AUTH_MODE=dev\n"
    )


def test_set_env_port_adds_the_default_url_when_missing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ENV=local\n")
    set_env_port(env, 5434)
    assert env.read_text() == (
        "ENV=local\n"
        "DATABASE_URL=postgresql+asyncpg://moodymate:moodymate@localhost:5434/moodymate_dev\n"
    )


def test_port_option_prints_the_port_the_app_will_use(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:6543/x")
    assert main(["--port"]) == OK
    assert capsys.readouterr().out.strip() == "6543"
