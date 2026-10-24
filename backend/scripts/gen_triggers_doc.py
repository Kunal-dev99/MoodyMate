"""Draft docs/ops/TRIGGERS.md from the migrations (BE-34.7). Run from backend/:

    uv run python -m scripts.gen_triggers_doc ../docs/ops/TRIGGERS.md

Re-run it after any migration that creates or drops a trigger; the committed-artifacts fold
fails until docs/ops/TRIGGERS.md matches the migrations.

The set comes from app.tests.observability_kit.migration_triggers() (the same parser the
committed-artifacts fold uses); this script adds table, timing and function per trigger by
recording each upgrade()'s statements the same way. No database is used.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from unittest import mock

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.tests.observability_kit import documented_triggers, migration_triggers

DETAIL = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(CONSTRAINT\s+)?TRIGGER\s+([a-z0-9_]+)\s+"
    r"(BEFORE|AFTER|INSTEAD\s+OF)\s+(.+?)\s+ON\s+([a-z0-9_.]+)"
    r".*?EXECUTE\s+(?:FUNCTION|PROCEDURE)\s+([a-z0-9_.]+)",
    re.I | re.S,
)
NAME = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+([a-z0-9_]+)", re.I)


def recorded() -> list[tuple[str, str, list[str]]]:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    out = []
    for rev in reversed(list(script.walk_revisions())):
        statements: list[str] = []

        def record(statement: Any, *_: Any, _into: list[str] = statements, **__: Any) -> Any:
            _into.append(str(statement))
            return mock.MagicMock()

        op = mock.MagicMock()
        op.execute.side_effect = record
        op.get_bind.return_value.execute.side_effect = record
        with mock.patch.object(rev.module, "op", op, create=True):
            rev.module.upgrade()
        out.append((rev.revision, Path(rev.path).stem, statements))
    return out


def main(target: Path) -> None:
    first: dict[str, tuple[str, str]] = {}
    details: dict[str, tuple[str, str, str, str]] = {}
    again: dict[str, list[str]] = defaultdict(list)
    for revision, stem, statements in recorded():
        for statement in statements:
            for m in DETAIL.finditer(statement):
                constraint, name, timing, events, table, fn = m.groups()
                if name not in first:
                    first[name] = (revision, stem)
                    kind = "constraint, " if constraint else ""
                    fires = f"{kind}{' '.join(timing.split()).upper()} {' '.join(events.split())}"
                    details[name] = (table, fires, fn, revision)
                elif first[name][0] != revision:
                    again[name].append(revision)
            for name in NAME.findall(statement):  # names DETAIL could not parse in full
                first.setdefault(name, (revision, stem))

    parsed = migration_triggers()
    mine = {(rev, name) for name, (rev, _) in first.items()}
    assert mine == parsed, (sorted(parsed - mine), sorted(mine - parsed))

    by_rev: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, key in first.items():
        by_rev[key].append(name)

    lines = [
        "# Database triggers, by migration",
        "",
        "Every trigger a migration's `upgrade()` creates, by the migration that first creates it",
        "(BE-34.7). The backend suite checks this list against the parsed `CREATE TRIGGER` set",
        "(`check_triggers_doc` in `backend/app/tests/observability_kit.py`, run by",
        "`test_committed_openapi_and_samples_are_current`). A migration that adds a trigger",
        "adds its row here in the same change.",
        "",
        "Operational notes:",
        "",
        "- The triggers enforce append-only and immutability rules in the database itself (D-01,",
        "  D-02: lifecycle actions set status or validity fields, never delete rows). The app",
        "  never disables them. Only the table owner can, inside one transaction, as the seed",
        "  downgrades and the test cleanup do.",
        "- A restore (see `BACKUP_RESTORE.md`) restores the triggers with the schema. Check that",
        "  they are enabled afterwards:",
        "  `SELECT tgrelid::regclass, tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'D';`",  # noqa: E501
        "  should return no rows.",
        "- Retention enforcement is described in `../RETENTION.md` (PROVISIONAL).",
        "",
        f"{len(parsed)} triggers in {len(by_rev)} migrations.",
        "",
        "| Migration | Trigger | Table | Fires | Function |",
        "| --- | --- | --- | --- | --- |",
    ]
    for (revision, stem), names in sorted(by_rev.items()):
        for name in sorted(names):
            table, fires, fn, _ = details.get(name, ("?", "?", "?", revision))
            note = f" (re-created in {', '.join(again[name])})" if again.get(name) else ""
            lines.append(f"| `{stem}` | `{name}` | {table} | {fires}{note} | {fn}() |")
    target.write_text("\n".join(lines) + "\n")

    assert documented_triggers(target) == parsed
    missing = sorted(n for n in first if n not in details)
    print(f"wrote {target}: {len(parsed)} triggers, {len(by_rev)} migrations")
    print("documented_triggers(draft) == migration_triggers(): True")
    print(f"triggers without parsed details: {missing}")
    print(f"triggers re-created by a later migration: {dict(again)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
