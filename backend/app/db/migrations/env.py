"""Alembic environment (async). Standing rules: D-01 additive forward migrations only;
D-02 every revision ships a tested downgrade (CI runs upgrade → downgrade → upgrade)."""

import asyncio
import importlib
import pkgutil
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app
from app.core.config import get_settings
from app.db.base import Base

if context.config.config_file_name and not context.config.attributes.get("skip_logging"):
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

# Import every module's models so autogenerate sees the full metadata.
for mod in pkgutil.iter_modules(app.__path__):
    if mod.ispkg:
        try:
            importlib.import_module(f"app.{mod.name}.models")
        except ModuleNotFoundError:
            pass

target_metadata = Base.metadata


def _url() -> str:
    return context.config.get_main_option("sqlalchemy.url") or get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
