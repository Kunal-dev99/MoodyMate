"""Readiness checks (BE-34.5, D-62 §1).

`check_readiness` answers four fixed, value-free checks, each `ok` or `fail`:

* `database`: a connection and `SELECT 1` within `READINESS_TIMEOUT_SECONDS` (2 s);
* `migrations`: the revisions `alembic_version` records equal this code's migration heads
  (so a database behind or ahead of the code is not ready). `fail` when the database is;
* `artifact_store`: the store can take a write (`ArtifactStore.ready`, which writes nothing);
* `workers`: the calculation workers can take a run. Inline execution (no worker pool on
  `app.state.workers`) runs in the API process, so it is `ok` whenever the process answers;
  a pool (D-59) is asked through its `ready()`.

Nothing here returns or logs a DSN, host, revision or version string: a failure is only the
check name and `fail`.
"""

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from alembic.script import ScriptDirectory

from app.core.artifacts import ArtifactStore
from app.db.session import Database

logger = logging.getLogger("app.readiness")

READINESS_TIMEOUT_SECONDS = 2.0  # D-62 §1: the DB check answers within 2 s
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"

CheckResult = Literal["ok", "fail"]
CHECK_NAMES = ("database", "migrations", "artifact_store", "workers")


class Workers(Protocol):
    def ready(self) -> bool: ...


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    """This code's migration heads (read once from the migration scripts)."""
    return frozenset(ScriptDirectory(str(MIGRATIONS_DIR)).get_heads())


def _result(ok: bool) -> CheckResult:
    return "ok" if ok else "fail"


async def _database(db: Database) -> tuple[bool, frozenset[str] | None]:
    """(reachable, recorded revisions or None) within the 2 s bound."""
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            if not await db.ping():
                return False, None
            return True, await db.migration_revisions()
    except Exception:  # noqa: BLE001 - any failure (timeout included) is `fail`
        return False, None


async def _artifact_store(store: ArtifactStore | None) -> bool:
    if store is None:
        return False
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            return bool(await asyncio.to_thread(store.ready))
    except Exception:  # noqa: BLE001
        return False


def _workers(workers: Workers | None) -> bool:
    if workers is None:
        return True  # inline execution: the API process is the worker, and it is answering
    try:
        return bool(workers.ready())
    except Exception:  # noqa: BLE001
        return False


async def check_readiness(state: Any) -> dict[str, CheckResult]:
    """The four checks for an app's `state` (its `db`, `artifact_store` and, with a worker
    pool, `workers`)."""
    (db_ok, revisions), store_ok = await asyncio.gather(
        _database(state.db), _artifact_store(getattr(state, "artifact_store", None))
    )
    try:
        heads = expected_heads()
    except Exception:  # noqa: BLE001 - unreadable scripts: not ready
        heads = frozenset()
    migrations_ok = db_ok and bool(heads) and revisions == heads
    checks: dict[str, CheckResult] = {
        "database": _result(db_ok),
        "migrations": _result(migrations_ok),
        "artifact_store": _result(store_ok),
        "workers": _result(_workers(getattr(state, "workers", None))),
    }
    failed = [name for name in CHECK_NAMES if checks[name] == "fail"]
    if failed:
        logger.warning("readiness_failed", extra={"checks": failed})
    return checks
