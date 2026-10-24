"""core.locks

`advisory_xact_lock` (BE-29.3): the one transaction-scoped PostgreSQL advisory lock. Writers
of one content-addressed or keyed row serialise on it, so a second writer finds the first
one's row instead of racing it. The lock is released at commit or rollback.

The lock key is `"<key>:<tenant id>"`: the tenant comes from the principal's scope (D-04),
never from the request, so two tenants never wait on each other. Only repositories call it
(they are the only layer that holds the session).
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


def lock_key(key: str, tenant_id: uuid.UUID) -> str:
    return f"{key}:{tenant_id}"


async def advisory_xact_lock(session: AsyncSession, key: str, tenant_id: uuid.UUID) -> None:
    """Take the transaction-scoped advisory lock of `key` within the tenant."""
    await session.execute(_LOCK, {"key": lock_key(key, tenant_id)})
