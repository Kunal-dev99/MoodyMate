"""Local start-script helper: diagnose why the backend cannot sign in to its database."""

import asyncio
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
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
    setup,
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


def _admin(database_url: str) -> tuple[str, str]:
    url = make_url(database_url)
    return url.username or "", url.password or ""


async def _drop(database_url: str, role: str, database: str) -> None:
    user, password = _admin(database_url)
    url = make_url(database_url)
    conn = await asyncpg.connect(
        host=url.host, port=url.port, user=user, password=password, database=url.database
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await conn.execute(f'DROP ROLE IF EXISTS "{role}"')
    finally:
        await conn.close()


@pytest.fixture
def new_app_url(database_url: str) -> Iterator[str]:
    role, database = "mm_setup_role_test", "mm_setup_db_test"
    asyncio.run(_drop(database_url, role, database))
    yield _with(database_url, username=role, password="s3t'up pw", database=database)
    asyncio.run(_drop(database_url, role, database))


def test_setup_creates_the_app_role_and_database(database_url: str, new_app_url: str) -> None:
    assert asyncio.run(check(new_app_url))[0] == BAD_LOGIN
    code, _ = asyncio.run(setup(new_app_url, *_admin(database_url)))
    assert code == OK
    assert asyncio.run(check(new_app_url))[0] == OK


def test_setup_is_repeatable_and_resets_the_role_password(
    database_url: str, new_app_url: str
) -> None:
    assert asyncio.run(setup(new_app_url, *_admin(database_url)))[0] == OK
    changed = _with(new_app_url, password="another password")
    assert asyncio.run(setup(changed, *_admin(database_url)))[0] == OK
    assert asyncio.run(check(changed))[0] == OK
    assert asyncio.run(check(new_app_url))[0] == BAD_LOGIN


def test_setup_with_a_wrong_admin_password_is_a_rejected_login(
    database_url: str, new_app_url: str
) -> None:
    user, _ = _admin(database_url)
    code, message = asyncio.run(setup(new_app_url, user, "wrong-admin-password"))
    assert code == BAD_LOGIN
    assert "wrong-admin-password" not in message


def test_new_password_is_safe_for_the_windows_installer_command_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--new-password"]) == OK
    password = capsys.readouterr().out.strip()
    assert len(password) >= 20
    assert password.isalnum()
