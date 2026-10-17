"""reconciliation.schemas

Response models for an import's reconciliation (BE-12.2). Every figure is exact decimal
text (never a JSON number), so clients render it without parsing; a control total's
figures are masked by the column's classification for callers not cleared for it.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.classification import MaskedValue
from app.ingestion.constants import ImportStatus
from app.reconciliation.constants import (
    ReconciliationCheck,
    ReconciliationItemStatus,
    ReconciliationOutcome,
)

Figure = str | MaskedValue


class ReconciliationItemOut(BaseModel):
    sequence: int = Field(description="Check order within the run.")
    check: ReconciliationCheck
    column: str | None = Field(description="The control-total column (none for row counts).")
    unit: str | None = Field(
        description="'rows' for counts; for totals, the unit of the schema field the column "
        "is mapped to (none when it has none)."
    )
    classification: str = Field(description="Masks this item's figures (public for counts).")
    source: Figure = Field(
        description="Exact decimal text. ROW_COUNT: source rows; EXPECTED_ROW_COUNT: the "
        "declared count; CONTROL_TOTAL: the total of the column's parseable values over all "
        "rows; DECLARED_CONTROL_TOTAL: the declared total."
    )
    target: Figure = Field(
        description="ROW_COUNT: accepted rows; EXPECTED_ROW_COUNT: source rows; "
        "CONTROL_TOTAL: the accepted rows' total; DECLARED_CONTROL_TOTAL: the source total."
    )
    held: Figure | None = Field(
        description="The part of the gap that held (quarantined or rejected) rows explain."
    )
    variance: Figure = Field(description="source - target, exact.")
    tolerance: str = Field(description="Absolute tolerance applied (row counts: 0).")
    status: ReconciliationItemStatus
    details: dict[str, Any] = Field(
        description="Counts of parseable, unparseable and empty values (totals only)."
    )


class ReconciliationOut(BaseModel):
    reconciliation_id: uuid.UUID
    import_id: uuid.UUID
    import_status: ImportStatus
    outcome: ReconciliationOutcome = Field(
        description="MATCHED only when every item is MATCHED; BROKEN when a gap is not "
        "explained by held rows (or a declared figure differs); else REVIEW_REQUIRED."
    )
    tolerance_policy: dict[str, Any] = Field(description="PROVISIONAL tolerance policy applied.")
    reconciler_version: int
    reconciled_at: datetime
    reconciled_by: str
    items: list[ReconciliationItemOut]
