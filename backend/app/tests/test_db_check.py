"""Local start-script helper (scripts/db_check.py): diagnose why the backend cannot sign in
to its database, without echoing the password."""

import asyncio
from typing import Any

from sqlalchemy.engine import make_url

from scripts.db_check import BAD_LOGIN, NO_DATABASE, OK, UNREACHABLE, check


def _with(url: str, **parts: Any) -> str:
    return make_url(url).set(**parts).render_as_string(hide_password=False)


def test_db_check_reports_each_failure(database_url: str) -> None:
    assert asyncio.run(check(database_url))[0] == OK
    code, message = asyncio.run(check(_with(database_url, password="not-the-password")))
    assert code == BAD_LOGIN and "not-the-password" not in message
    assert asyncio.run(check(_with(database_url, username="no_such_role_xyz")))[0] == BAD_LOGIN
    assert (
        asyncio.run(check(_with(database_url, database="no_such_database_xyz")))[0] == NO_DATABASE
    )
    unreachable = "postgresql+asyncpg://u:p@127.0.0.1:1/x"
    assert asyncio.run(check(unreachable, connect_timeout=2))[0] == UNREACHABLE
