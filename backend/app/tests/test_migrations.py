import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.mark.db
def test_round_trip_upgrade_downgrade_upgrade(db: str) -> None:
    # `db` removes non-seed rows first; the seed downgrade must not delete user data.
    cfg = _config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def test_every_revision_has_a_downgrade() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for rev in script.walk_revisions():
        assert hasattr(rev.module, "downgrade"), rev.revision


def test_no_drop_in_forward_migrations() -> None:
    # D-01: upgrade() must be additive. Exceptions need a named decision and an
    # allowlist entry here.
    import inspect

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for rev in script.walk_revisions():
        src = inspect.getsource(rev.module.upgrade)
        for forbidden in ("drop_table", "drop_column", "drop_constraint", "DROP "):
            assert forbidden not in src, f"{rev.revision}: {forbidden} in upgrade()"
