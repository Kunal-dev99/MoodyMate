"""ingestion.quarantine (BE-35.2: split from ingestion.service, which re-exports it)

Quarantine review and retry (BE-11.4, D-20): read a batch's held rows and disposition them
along the D-20 table. Nothing is deleted and no source record is edited.
"""

import uuid
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.refusals import refuse
from app.governance.constants import ClassificationCode
from app.governance.service import audit_by
from app.ingestion.constants import (
    MANUAL_QUARANTINE_MOVES,
    QUARANTINE_TRANSITIONS,
    QuarantineStatus,
)
from app.ingestion.contracts import _public, profile_of
from app.ingestion.imports import ImportReads
from app.ingestion.models import ImportBatch, ImportJob, QuarantineItem, SourceRecord
from app.ingestion.repository import ImportRepository

# --- Phase 11: quarantine review and retry (BE-11.4, D-20) ---------------------------------------

DISPOSITION = "quarantine.disposition"  # a reviewer's move; refusals under the same action
# Moves made when a retry import is validated (D-20 §2), audited under these actions.
AUTOMATIC_MOVES = {
    QuarantineStatus.CORRECTED: "quarantine.correct",
    QuarantineStatus.RESOLVED: "quarantine.resolve",
    QuarantineStatus.RETRY_READY: "quarantine.retry_ready",
}


def manual_moves(status: str) -> list[QuarantineStatus]:
    """The dispositions a reviewer may give an item in `status`."""
    return sorted(to for frm, to in MANUAL_QUARANTINE_MOVES if frm == status)


@dataclass(frozen=True)
class QuarantineView:
    """One batch's quarantine items as stored, with what masking their values needs: each
    column's classification (the mapped field's, else the source profile's default, D-20)."""

    batch: ImportBatch
    job: ImportJob
    items: list[tuple[QuarantineItem, SourceRecord]]
    total: int
    status_counts: dict[str, int]
    classifications: dict[str, str]
    default_classification: str


class QuarantineService:
    """Read and disposition held rows (D-20). A disposition only moves an item's status
    along the D-20 table; nothing is deleted and no source record is edited."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = ImportRepository(session, principal.scope)
        # BE-35.2: the import reads it needs (sources, mapped classifications), not the
        # whole ImportService, so that validation (manifests) can use this service.
        self.imports = ImportReads(session, principal)

    async def _refuse(self, resource_id: uuid.UUID | None, error: ApiError) -> NoReturn:
        await refuse(
            self.session,
            self.principal,
            error,
            action=DISPOSITION,
            resource_type="quarantine_item",
            resource_id=resource_id,
        )

    async def batch_view(
        self, batch_id: uuid.UUID, status: QuarantineStatus | None, limit: int, offset: int
    ) -> QuarantineView:
        found = await self.repo.batch(batch_id)
        if found is None:
            # Same response whether the batch is absent or in another tenant (D-04).
            raise ApiError(404, "not_found", "Batch not found.")
        batch, job = found
        items, total = await self.repo.quarantine_page(batch.id, status, limit, offset)
        source = await self.imports.sources.get(job.source_system_id)
        default = profile_of(source).default_classification
        mapped = await self.imports.mapped_classifications(job)
        return QuarantineView(
            batch=batch,
            job=job,
            items=items,
            total=total,
            status_counts=await self.repo.status_counts(batch.id),
            classifications={c: mapped.get(c, default) for c in batch.columns},
            default_classification=default,
        )

    async def item_view(self, batch_id: uuid.UUID, item_id: uuid.UUID) -> QuarantineView:
        """One item, as `batch_view` shows it."""
        view = await self.batch_view(batch_id, None, 1, 0)
        found = await self.repo.batch_item(batch_id, item_id)
        assert found is not None
        return QuarantineView(
            view.batch,
            view.job,
            [found],
            1,
            view.status_counts,
            view.classifications,
            view.default_classification,
        )

    async def disposition(
        self,
        batch_id: uuid.UUID,
        item_id: uuid.UUID,
        to: QuarantineStatus,
        comment: str | None,
    ) -> QuarantineView:
        """A reviewer's move (D-20 §2: OPEN->UNDER_REVIEW, UNDER_REVIEW->REJECTED |
        RETRY_READY | OPEN), audited. Any other move is 409 `invalid_transition` (audited):
        CORRECTED and RESOLVED are reached only by a retry import."""
        item = await self.repo.lock_item(batch_id, item_id)
        if item is None:
            await self._refuse(item_id, ApiError(404, "not_found", "Quarantine item not found."))
        current = QuarantineStatus(item.status)
        if (current, to) not in MANUAL_QUARANTINE_MOVES:
            automatic = to in QUARANTINE_TRANSITIONS[current]
            await self._refuse(
                item.id,
                ApiError(
                    409,
                    "invalid_transition",
                    f"A quarantine item cannot be moved from {current} to {to}"
                    + (" by hand; a retry import does that (D-20)." if automatic else "."),
                    {"from": current.value, "to": to.value, "allowed": manual_moves(current)},
                ),
            )
        item.status = to
        await self.repo.flush()
        details: dict[str, Any] = {
            "batch_id": str(item.batch_id),
            "import_id": str(item.import_job_id),
            "from": current.value,
            "to": to.value,
            "comment": comment,
        }
        audit_by(
            self.session,
            self.principal,
            DISPOSITION,
            "quarantine_item",
            resource_id=str(item.id),
            details=details,
            details_classification={
                **_public("batch_id", "import_id", "from", "to"),
                "comment": ClassificationCode.INTERNAL,
            },
        )
        await self.session.commit()
        return await self.item_view(batch_id, item_id)

    async def resolve_retry(
        self,
        retry_id: uuid.UUID,
        original_id: uuid.UUID,
        present: set[str],
        accepted: set[str],
    ) -> None:
        """D-20 §2, automatic and audited, in the retry's validation transaction: each
        RETRY_READY item of the original whose business key the retry contains moves to
        CORRECTED, then to RESOLVED (resolved_by the retry) when the retry accepted that key,
        else back to RETRY_READY. Each move is its own UPDATE, so the DB trigger checks it."""
        for item in await self.repo.retry_ready_items(original_id):
            key = item.business_key_sha256
            if key is None or key not in present:
                continue
            await self._auto(item, QuarantineStatus.CORRECTED, retry_id)
            if key in accepted:
                item.resolved_by_import_id = retry_id
                await self._auto(item, QuarantineStatus.RESOLVED, retry_id)
            else:
                await self._auto(item, QuarantineStatus.RETRY_READY, retry_id)

    async def _auto(self, item: QuarantineItem, to: QuarantineStatus, retry_id: uuid.UUID) -> None:
        current = QuarantineStatus(item.status)
        assert to in QUARANTINE_TRANSITIONS[current]
        item.status = to
        await self.repo.flush()
        details: dict[str, Any] = {
            "batch_id": str(item.batch_id),
            "import_id": str(item.import_job_id),
            "from": current.value,
            "to": to.value,
            "retry_import_id": str(retry_id),
            "automatic": True,
        }
        audit_by(
            self.session,
            self.principal,
            AUTOMATIC_MOVES[to],
            "quarantine_item",
            resource_id=str(item.id),
            details=details,
            details_classification=_public(*details),
        )
