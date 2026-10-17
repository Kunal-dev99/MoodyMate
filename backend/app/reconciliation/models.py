"""reconciliation.models

Import reconciliation (BE-12.1, DI-05, D-23): one `reconciliation_run` per validated import,
written in the validation's transaction, with one `reconciliation_item` per check. Both are
append-only (DB triggers, TRUNCATE included, migration 0016).

An item compares a `source` figure with a `target` figure (`variance = source - target`,
CHECK); `held` is the part of the gap that held (quarantined or rejected) rows explain. Its
status follows from the variance and the tolerance (CHECK). The database refuses an item
that is not MATCHED under a MATCHED run, and an item with an unexplained gap under a run
that is not BROKEN. Figures are NUMERIC (Decimal), never float.
"""

import uuid
from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditColumns, Base, ScopedModel
from app.reconciliation.constants import (
    COUNT_CHECKS,
    ReconciliationCheck,
    ReconciliationItemStatus,
    ReconciliationOutcome,
)


def _in(column: str, values: Iterable[StrEnum]) -> str:
    """CHECK expression limiting a column to enum values (in enum order)."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


_COUNTS = [c for c in ReconciliationCheck if c in COUNT_CHECKS]


class ReconciliationRun(ScopedModel, Base):
    """The reconciliation of one validated import: its outcome and the tolerance policy it
    applied (PROVISIONAL until the client sets one)."""

    __tablename__ = "reconciliation_run"
    __table_args__ = (
        CheckConstraint(_in("outcome", ReconciliationOutcome), name="outcome"),
        CheckConstraint("jsonb_typeof(tolerance_policy) = 'object'", name="tolerance_policy"),
        CheckConstraint("reconciler_version >= 1", name="reconciler_version"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False
    )
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), unique=True, index=True, nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    # {"tolerance": decimal text, "count_tolerance": "0", "kind": "ABSOLUTE", "provisional"}
    tolerance_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reconciler_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ReconciliationItem(AuditColumns, Base):
    """One check of a run. `classification` is what masks its figures on read: public for
    row counts, the control-total column's for totals."""

    __tablename__ = "reconciliation_item"
    __table_args__ = (
        UniqueConstraint("reconciliation_run_id", "sequence"),
        CheckConstraint(_in("check_code", ReconciliationCheck), name="check_code"),
        CheckConstraint(_in("status", ReconciliationItemStatus), name="status"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("variance = source_value - target_value", name="variance"),
        CheckConstraint("tolerance >= 0", name="tolerance"),
        CheckConstraint(
            "status = CASE WHEN variance = 0 THEN 'MATCHED'"
            " WHEN abs(variance) <= tolerance THEN 'WITHIN_TOLERANCE' ELSE 'VARIANCE' END",
            name="status_of_variance",
        ),
        CheckConstraint(
            f"NOT ({_in('check_code', _COUNTS)}) OR (tolerance = 0 AND column_name IS NULL)",
            name="count_checks",
        ),
        CheckConstraint(f"{_in('check_code', _COUNTS)} OR column_name IS NOT NULL", name="column"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details"),
    )

    reconciliation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reconciliation_run.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    check_code: Mapped[str] = mapped_column(String(30), nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(50))
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    source_value: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    held_value: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    variance: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    tolerance: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Counts only (parseable / unparseable / empty values), never a value.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
