"""reconciliation.router

GET /imports/{import_id}/reconciliation (BE-12.2), under `import:read` like the other import
reads (the Feature 02 endpoint table). Tenant scope comes from the principal (D-04).
Reconciliation is written by validation; this module has no write route.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.classification import mask_mapping
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.ingestion.constants import ImportStatus
from app.reconciliation.constants import (
    ReconciliationCheck,
    ReconciliationItemStatus,
    ReconciliationOutcome,
)
from app.reconciliation.models import ReconciliationItem
from app.reconciliation.schemas import ReconciliationItemOut, ReconciliationOut
from app.reconciliation.service import ReconciliationService, decimal_text

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ImportReader = Annotated[Principal, Depends(require_permission(Permission.IMPORT_READ))]

router = APIRouter(tags=["reconciliation"])


def _item(item: ReconciliationItem, clear: frozenset[str]) -> ReconciliationItemOut:
    """Figures as exact text, masked by the item's classification (BE-3.5)."""
    figures = {
        "source": decimal_text(item.source_value),
        "target": decimal_text(item.target_value),
        "held": decimal_text(item.held_value) if item.held_value is not None else None,
        "variance": decimal_text(item.variance),
    }
    shown = mask_mapping(figures, dict.fromkeys(figures, item.classification), clear)
    return ReconciliationItemOut(
        sequence=item.sequence,
        check=ReconciliationCheck(item.check_code),
        column=item.column_name,
        unit=item.unit,
        classification=item.classification,
        source=shown["source"],
        target=shown["target"],
        held=shown["held"],
        variance=shown["variance"],
        tolerance=decimal_text(item.tolerance),
        status=ReconciliationItemStatus(item.status),
        details=item.details,
    )


@router.get(
    "/imports/{import_id}/reconciliation",
    response_model=ReconciliationOut,
    operation_id="getImportReconciliation",
)
async def get_import_reconciliation(
    import_id: uuid.UUID, principal: ImportReader, session: SessionDep
) -> ReconciliationOut:
    """The import's reconciliation (DI-05): row-count conservation, the declared row count
    and each control total, with exact decimal figures. 404 reconciliation_not_found until
    the import is validated."""
    job, run, items = await ReconciliationService(session, principal).view(import_id)
    clear = principal.clear_classifications
    return ReconciliationOut(
        reconciliation_id=run.id,
        import_id=job.id,
        import_status=ImportStatus(job.status),
        outcome=ReconciliationOutcome(run.outcome),
        tolerance_policy=run.tolerance_policy,
        reconciler_version=run.reconciler_version,
        reconciled_at=run.created_at,
        reconciled_by=run.created_by,
        items=[_item(i, clear) for i in items],
    )
