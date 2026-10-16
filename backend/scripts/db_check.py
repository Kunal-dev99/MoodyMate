"""Can the backend sign in to its database? Used by the local start scripts.

Reads DATABASE_URL the way the app does (environment, then backend/.env) and tries one
connection, so a wrong password or a different PostgreSQL on the same port is named
before the migrations fail with a long traceback. Exit codes, acted on by the scripts:

  0  signed in                      2  user name or password rejected
  1  nothing accepts connections    3  server is up but the database does not exist
                                    4  any other failure (message printed)

Usage (from backend/):
  python -m scripts.db_check               check and print a one-line diagnosis
  python -m scripts.db_check --port        print the port from DATABASE_URL
  python -m scripts.db_check --move-port   point backend/.env at the first free port
                                           from 5433 and print it
"""

import asyncio
import socket
import sys
from pathlib import Path

import asyncpg
from sqlalchemy.engine import URL, make_url

from app.core.config import Settings

OK, UNREACHABLE, BAD_LOGIN, NO_DATABASE, OTHER = 0, 1, 2, 3, 4

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _describe(url: URL) -> str:
    return f"database {url.database} at {url.host}:{url.port or 5432} as {url.username}"


async def check(database_url: str, connect_timeout: float = 5) -> tuple[int, str]:
    url = make_url(database_url)
    where = _describe(url)
    try:
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
            timeout=connect_timeout,
        )
    except asyncpg.InvalidAuthorizationSpecificationError:
        # Wrong password, unknown user or no pg_hba entry: all are a refused sign-in.
        return BAD_LOGIN, f"The server refused the user name or password for {where}."
    except asyncpg.InvalidCatalogNameError:
        return NO_DATABASE, f"The server is up but has no {where}."
    except (OSError, TimeoutError):
        return UNREACHABLE, f"Nothing accepts connections for {where}."
    except Exception as exc:  # report anything else as-is
        return OTHER, f"Could not connect to {where}: {type(exc).__name__}: {exc}"
    await conn.close()
    return OK, f"Signed in to {where}."


def _in_use(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return True
    with socket.socket() as probe:
        try:
            probe.bind(("0.0.0.0", port))  # noqa: S104 - same bind Docker will need
        except OSError:
            return True
    return False


def free_port(start: int = 5433) -> int:
    for port in range(start, start + 100):
        if not _in_use(port):
            return port
    raise RuntimeError(f"no free port between {start} and {start + 99}")


def set_env_port(env_file: Path, port: int) -> None:
    """Change only the port of DATABASE_URL in env_file, adding the default URL if absent."""
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    for i, line in enumerate(lines):
        if line.startswith("DATABASE_URL="):
            url = make_url(line.split("=", 1)[1].strip())
            lines[i] = "DATABASE_URL=" + url.set(port=port).render_as_string(hide_password=False)
            break
    else:
        default = make_url(Settings.model_fields["DATABASE_URL"].default)
        lines.append("DATABASE_URL=" + default.set(port=port).render_as_string(hide_password=False))
    env_file.write_text("\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    url = Settings().DATABASE_URL  # ENV comes from the environment or backend/.env
    if "--port" in argv:
        print(make_url(url).port or 5432)
        return OK
    if "--move-port" in argv:
        port = free_port()
        set_env_port(ENV_FILE, port)
        print(port)
        return OK
    code, message = asyncio.run(check(url))
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
