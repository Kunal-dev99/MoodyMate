"""Async engine and session wiring (BE-1.6).

Only repositories use sessions (Router → Service → Repository → Database).
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, url: str) -> None:
        # Creating the engine does not connect; the app starts without a DB.
        # hide_parameters: bound values (possibly classified, BE-3.5) never appear in
        # error messages, and so never reach the logs.
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, hide_parameters=True, connect_args={"timeout": 2}
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - any failure means unreachable
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.sessionmaker() as session:
        yield session
