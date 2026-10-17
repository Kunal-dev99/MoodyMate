"""reconciliation.service

Import reconciliation (BE-12.2, DI-05). It runs once, at the end of an import's validation
and in its transaction (D-21), so every validated import has exactly one run.

The figures come from the database after validation has written its rows: the source rows
and the source totals of each contract control-total column, against the accepted rows
(with a canonical record) and the held rows (with a quarantine item), plus whatever the
uploader declared. Every item is `variance = source - target`, with `held` the part of the
gap that held rows explain; its status follows from the variance and the tolerance. The
outcome:

- BROKEN when a gap is not explained by held rows within tolerance (count conservation
  fails, or a declared figure differs);
- else REVIEW_REQUIRED when any item is not MATCHED;
- else MATCHED. It is never MATCHED while any item is not (the database refuses it too).

Decimal throughout, never float; arithmetic is exact (an inexact result raises). The
tolerance is PROVISIONAL (RECONCILIATION_TOLERANCE, default 0); row counts always use 0.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Context, Decimal, Inexact, InvalidOperation, Rounded, localcontext
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.principal import Principal
from app.governance.constants import ClassificationCode
from app.governance.service import audit_by
from app.ingestion.models import ImportJob
from app.ingestion.service import ImportService
from app.reconciliation.constants import (
    COUNT_UNIT,
    RECONCILER_VERSION,
    ReconciliationCheck,
    ReconciliationItemStatus,
    ReconciliationOutcome,
)
from app.reconciliation.models import ReconciliationItem, ReconciliationRun
from app.reconciliation.repository import ReconciliationRepository

# Audit action of the reconciliation written with an import's validation.
RECONCILE_IMPORT = "import.reconcile"
# Exact arithmetic: wide enough for any D-16 cell, and any rounding raises (fail closed).
EXACT = Context(prec=100_000, traps=[Inexact, Rounded, InvalidOperation])
ZERO = Decimal(0)
_CLASS_RANK = {c.value: i for i, c in enumerate(ClassificationCode)}


def most_sensitive(*codes: str) -> str:
    """The most sensitive of these classification codes; an unknown code wins (fail
    closed: no role is cleared for it)."""
    return max(codes, key=lambda c: _CLASS_RANK.get(c, len(_CLASS_RANK)))


def item_status(variance: Decimal, tolerance: Decimal) -> ReconciliationItemStatus:
    """D-23: MATCHED at zero, WITHIN_TOLERANCE up to the tolerance, else VARIANCE."""
    if variance == 0:
        return ReconciliationItemStatus.MATCHED
    if variance.copy_abs() <= tolerance:
        return ReconciliationItemStatus.WITHIN_TOLERANCE
    return ReconciliationItemStatus.VARIANCE


@dataclass(frozen=True)
class ItemSpec:
    """One check before it has ids. `variance = source - target`."""

    check: ReconciliationCheck
    source: Decimal
    target: Decimal
    held: Decimal | None
    tolerance: Decimal
    column: str | None = None
    unit: str | None = COUNT_UNIT
    classification: str = ClassificationCode.PUBLIC.value
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def variance(self) -> Decimal:
        with localcontext(EXACT):
            return self.source - self.target

    @property
    def unexplained(self) -> Decimal:
        """The part of the gap that no held row explains."""
        with localcontext(EXACT):
            return self.variance - (self.held if self.held is not None else ZERO)

    @property
    def status(self) -> ReconciliationItemStatus:
        return item_status(self.variance, self.tolerance)

    @property
    def broken(self) -> bool:
        return self.unexplained.copy_abs() > self.tolerance


def outcome_of(items: Sequence[ItemSpec]) -> ReconciliationOutcome:
    """BROKEN on any unexplained gap, else REVIEW_REQUIRED on any item that is not MATCHED,
    else MATCHED (so never MATCHED while an item is not)."""
    if any(i.broken for i in items):
        return ReconciliationOutcome.BROKEN
    if any(i.status != ReconciliationItemStatus.MATCHED for i in items):
        return ReconciliationOutcome.REVIEW_REQUIRED
    return ReconciliationOutcome.MATCHED


@dataclass(frozen=True)
class ColumnFigures:
    """One control-total column of an import, as stored: totals of its parseable values
    (plain decimal text, spaces trimmed) over all rows, the accepted rows and the held rows,
    and how many values were parseable, unparseable or empty."""

    source_total: Decimal
    accepted_total: Decimal
    held_total: Decimal
    parseable: int
    unparseable: int
    empty: int


@dataclass(frozen=True)
class ImportFigures:
    """What reconciliation compares, read from the database after validation wrote."""

    source_rows: int
    accepted_rows: int  # rows with a canonical record
    held_rows: int  # rows with a quarantine item (quarantined or rejected)
    columns: Mapping[str, ColumnFigures]


@dataclass(frozen=True)
class ColumnMeta:
    """How a control-total column's figures are labelled and masked."""

    unit: str | None
    classification: str


@dataclass(frozen=True)
class Declared:
    """What the uploader declared (both optional)."""

    expected_row_count: int | None
    control_totals: Mapping[str, str]  # column -> decimal text


def build_items(
    figures: ImportFigures,
    declared: Declared,
    meta: Mapping[str, ColumnMeta],
    tolerance: Decimal,
) -> list[ItemSpec]:
    """The checks of BE-12.2, in a fixed order: the row count, the expected row count, then
    per control-total column its total and its declared total."""
    items = [
        ItemSpec(
            ReconciliationCheck.ROW_COUNT,
            Decimal(figures.source_rows),
            Decimal(figures.accepted_rows),
            Decimal(figures.held_rows),
            ZERO,
        )
    ]
    if declared.expected_row_count is not None:
        items.append(
            ItemSpec(
                ReconciliationCheck.EXPECTED_ROW_COUNT,
                Decimal(declared.expected_row_count),
                Decimal(figures.source_rows),
                None,
                ZERO,
            )
        )
    for column, totals in figures.columns.items():
        about = meta[column]
        counts = {
            "parseable_values": totals.parseable,
            "unparseable_values": totals.unparseable,
            "empty_values": totals.empty,
        }
        items.append(
            ItemSpec(
                ReconciliationCheck.CONTROL_TOTAL,
                totals.source_total,
                totals.accepted_total,
                totals.held_total,
                tolerance,
                column,
                about.unit,
                about.classification,
                counts,
            )
        )
        text = declared.control_totals.get(column)
        if text is not None:
            items.append(
                ItemSpec(
                    ReconciliationCheck.DECLARED_CONTROL_TOTAL,
                    Decimal(text),
                    totals.source_total,
                    None,
                    tolerance,
                    column,
                    about.unit,
                    about.classification,
                    counts,
                )
            )
    return items


def decimal_text(value: Decimal) -> str:
    """Exact decimal text, never in exponent form."""
    return format(value, "f")


def tolerance_policy(tolerance: Decimal) -> dict[str, Any]:
    """What a run records about the tolerance it applied (PROVISIONAL)."""
    return {
        "kind": "ABSOLUTE",
        "tolerance": decimal_text(tolerance),
        "count_tolerance": "0",
        "provisional": True,
        "setting": "RECONCILIATION_TOLERANCE",
    }


# The reconciler's parts, for the test that proves it holds no float (BE-12.2).
RECONCILER_PARTS = (
    "item_status",
    "ItemSpec",
    "outcome_of",
    "build_items",
    "decimal_text",
    "tolerance_policy",
)


class ReconciliationService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = ReconciliationRepository(session, principal.scope)

    async def record(
        self,
        job: ImportJob,
        figures: ImportFigures,
        declared: Declared,
        meta: Mapping[str, ColumnMeta],
        tolerance: Decimal,
    ) -> ReconciliationRun:
        """Write the run and its items for an import that the caller's validation holds
        locked, audited `import.reconcile` (counts and codes only). The caller commits."""
        items = build_items(figures, declared, meta, tolerance)
        outcome = outcome_of(items)
        run = ReconciliationRun(
            tenant_id=job.tenant_id,
            import_job_id=job.id,
            outcome=outcome,
            tolerance_policy=tolerance_policy(tolerance),
            reconciler_version=RECONCILER_VERSION,
            created_by=self.principal.actor,
        )
        self.repo.add(run)
        await self.repo.flush()
        await self.repo.insert_items(
            [
                {
                    "id": uuid.uuid4(),
                    "reconciliation_run_id": run.id,
                    "sequence": position,
                    "check_code": item.check.value,
                    "column_name": item.column,
                    "unit": item.unit,
                    "classification": item.classification,
                    "source_value": item.source,
                    "target_value": item.target,
                    "held_value": item.held,
                    "variance": item.variance,
                    "tolerance": item.tolerance,
                    "status": item.status.value,
                    "details": item.details,
                    "created_by": self.principal.actor,
                }
                for position, item in enumerate(items, start=1)
            ]
        )
        details: dict[str, Any] = {
            "reconciliation_id": str(run.id),
            "outcome": outcome.value,
            "item_count": len(items),
            "statuses": sorted({i.status.value for i in items}),
            "tolerance": decimal_text(tolerance),
            "reconciler_version": RECONCILER_VERSION,
        }
        audit_by(
            self.session,
            self.principal,
            RECONCILE_IMPORT,
            "import_job",
            resource_id=str(job.id),
            details=details,
            details_classification=dict.fromkeys(details, ClassificationCode.PUBLIC.value),
        )
        return run

    async def for_import(self, job: ImportJob) -> ReconciliationRun | None:
        """The reconciliation of an import already reached through this scope (WS3 dataset
        snapshots read its outcome, D-30 §3); None before validation."""
        return await self.repo.for_import(job.id)

    async def view(
        self, import_id: uuid.UUID
    ) -> tuple[ImportJob, ReconciliationRun, list[ReconciliationItem]]:
        """An import's reconciliation (GET /imports/{id}/reconciliation): 404 alike for an
        absent and a foreign import (D-04); 404 reconciliation_not_found before validation."""
        job = await ImportService(self.session, self.principal).get(import_id)
        run = await self.repo.for_import(job.id)
        if run is None:
            raise ApiError(
                404,
                "reconciliation_not_found",
                "This import has no reconciliation yet: one is written when it is validated.",
            )
        return job, run, await self.repo.items(run.id)
