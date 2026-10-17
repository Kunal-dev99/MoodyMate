"""reconciliation.repository

The only code that touches the DB session for this module; every query applies the
principal's scope (D-04): runs carry `tenant_id`, items are reached only through a scoped run.
"""

import uuid
from typing import Any

from sqlalchemy import insert, select

from app.db.repository import ScopedRepository
from app.reconciliation.models import ReconciliationItem, ReconciliationRun


class ReconciliationRepository(ScopedRepository[ReconciliationRun]):
    model = ReconciliationRun

    async def for_import(self, import_id: uuid.UUID) -> ReconciliationRun | None:
        result = await self.session.execute(
            self.scoped().where(ReconciliationRun.import_job_id == import_id)
        )
        return result.scalar_one_or_none()

    async def items(self, run_id: uuid.UUID) -> list[ReconciliationItem]:
        """A run's items in check order, through a run of the caller's tenant."""
        result = await self.session.execute(
            select(ReconciliationItem)
            .join(
                ReconciliationRun,
                ReconciliationRun.id == ReconciliationItem.reconciliation_run_id,
            )
            .where(
                ReconciliationItem.reconciliation_run_id == run_id,
                ReconciliationRun.tenant_id == self.scope.tenant_id,
            )
            .order_by(ReconciliationItem.sequence)
        )
        return list(result.scalars())

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def insert_items(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(insert(ReconciliationItem), rows)

    async def flush(self) -> None:
        await self.session.flush()
