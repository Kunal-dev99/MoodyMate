"""orchestration.models

Stress scenarios (Phase 24, BE-24.1; D-44 §5-7), migration 0026:

- `scenario`: a named scenario, its code unique per tenant. Append-only.
- `scenario_version`: one version of its shocks (1 to 5, `shocks` JSONB, normalised by the
  template validators, with `shocks_sha256`), its provenance (`origin`, `parent_version_id`,
  `parameter_sources`) and its lifecycle (D-44 §5): the author, `confirmed_at`, the submit,
  decision and close fields, `ai_acknowledged` and `acknowledged_defaults`. Lifecycle-guarded:
  the shocks change only while DRAFT, the status moves only along D-44 §5 (once each), and
  each move sets only its own fields.
- `scenario_change`: one row per shock, written when the version leaves DRAFT. Append-only;
  each row repeats its version's shock at that index.
- `scenario_run` (Phase 25, migration 0027, D-45 §9): one run of a version against a
  baseline maturity_ladder run. Append-only. Its member runs are the calculation runs whose
  `calculation_scenario_pin` names it (re-runs excluded).

DELETE and TRUNCATE are refused on all three (DB triggers). Every enum column has a CHECK
(D-06). Every table carries `tenant_id` and is read through the tenant scope (D-04).
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, ScopedModel
from app.orchestration.constants import (
    MAX_CHANGE_SUMMARY,
    MAX_CODE,
    MAX_DECISION_COMMENT,
    MAX_DESCRIPTION,
    MAX_NAME,
    MAX_SHOCKS,
    MIN_SHOCKS,
    ApprovalState,
    ScenarioOrigin,
    ScenarioTemplate,
    ScenarioVersionStatus,
)


def _in(column: str, values: Iterable[StrEnum]) -> str:
    """CHECK expression limiting a column to enum values (in enum order)."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False)


def _comment(column: str) -> str:
    return f"{column} IS NULL OR char_length({column}) BETWEEN 1 AND {MAX_DECISION_COMMENT}"


# The lifecycle CHECKs, shared with migration 0026 (frozen copy there).
LIFECYCLE_PAIRS = (
    "(submitted_by IS NULL) = (submitted_at IS NULL)"
    " AND (decided_by IS NULL) = (decided_at IS NULL)"
    " AND (decided_by IS NULL) = (decision_comment IS NULL)"
    " AND (closed_by IS NULL) = (closed_at IS NULL)"
)
LIFECYCLE = (
    "(status <> 'DRAFT' OR (confirmed_at IS NULL AND submitted_at IS NULL"
    " AND decided_at IS NULL AND closed_at IS NULL))"
    " AND (status <> 'CONFIRMED_FOR_EXPLORATION' OR (confirmed_at IS NOT NULL"
    " AND submitted_at IS NULL))"
    " AND (status <> 'IN_REVIEW' OR (submitted_at IS NOT NULL AND decided_at IS NULL))"
    " AND (status NOT IN ('APPROVED_FOR_TEST', 'REJECTED') OR decided_at IS NOT NULL)"
    " AND ((status = 'CLOSED') = (closed_at IS NOT NULL))"
)


class Scenario(ScopedModel, Base):
    """A named stress scenario (D-44 §1). Append-only; its versions carry the content."""

    __tablename__ = "scenario"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_scenario_tenant_code"),
        CheckConstraint(f"char_length(name) BETWEEN 1 AND {MAX_NAME}", name="name"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(MAX_CODE), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_NAME), nullable=False)
    description: Mapped[str | None] = mapped_column(String(MAX_DESCRIPTION))
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_account.id"), nullable=False
    )


class ScenarioVersion(ScopedModel, Base):
    """One version of a scenario's shocks and its lifecycle (D-44 §5-7)."""

    __tablename__ = "scenario_version"
    __table_args__ = (
        UniqueConstraint("scenario_id", "version", name="uq_scenario_version_scenario_version"),
        CheckConstraint(_in("status", ScenarioVersionStatus), name="status"),
        CheckConstraint(_in("origin", ScenarioOrigin), name="origin"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            "jsonb_typeof(shocks) = 'array' AND jsonb_array_length(shocks) BETWEEN "
            f"{MIN_SHOCKS} AND {MAX_SHOCKS}",
            name="shocks",
        ),
        CheckConstraint("shocks_sha256 ~ '^[0-9a-f]{64}$'", name="shocks_sha256"),
        CheckConstraint(
            "origin <> 'MANUAL' OR parameter_sources IS NULL", name="parameter_sources"
        ),
        CheckConstraint(
            "jsonb_typeof(acknowledged_defaults) = 'array'", name="acknowledged_defaults"
        ),
        CheckConstraint("parent_version_id IS NULL OR parent_version_id <> id", name="parent"),
        CheckConstraint(_comment("submit_comment"), name="submit_comment"),
        CheckConstraint(_comment("decision_comment"), name="decision_comment"),
        CheckConstraint(_comment("close_comment"), name="close_comment"),
        CheckConstraint(LIFECYCLE_PAIRS, name="lifecycle_pairs"),
        CheckConstraint(LIFECYCLE, name="lifecycle"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_version.id")
    )
    # none_as_null: a MANUAL version stores SQL NULL, never JSON null (CHECK parameter_sources).
    parameter_sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    shocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    shocks_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(MAX_CHANGE_SUMMARY))
    authored_by: Mapped[str] = mapped_column(String(255), nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_account.id"), index=True, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submit_comment: Mapped[str | None] = mapped_column(String(MAX_DECISION_COMMENT))
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(String(MAX_DECISION_COMMENT))
    closed_by: Mapped[str | None] = mapped_column(String(255))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_comment: Mapped[str | None] = mapped_column(String(MAX_DECISION_COMMENT))
    ai_acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    acknowledged_defaults: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class ScenarioChange(ScopedModel, Base):
    """One shock of a version, materialised when the version leaves DRAFT (D-44 §5)."""

    __tablename__ = "scenario_change"
    __table_args__ = (
        UniqueConstraint(
            "scenario_version_id", "shock_index", name="uq_scenario_change_version_index"
        ),
        CheckConstraint(_in("template", ScenarioTemplate), name="template"),
        CheckConstraint(f"shock_index BETWEEN 0 AND {MAX_SHOCKS - 1}", name="shock_index"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    scenario_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_version.id"), index=True, nullable=False
    )
    shock_index: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(String(30), nullable=False)
    shock: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ScenarioRun(ScopedModel, Base):
    """One run of a scenario version against a baseline maturity_ladder run (D-45 §9, Data
    Model §6). Append-only: UPDATE, DELETE and TRUNCATE are refused. It holds the version, its
    shocks' sha256, the approval state pinned at run time, the baseline and value-free counts
    per shock (matched flows, parts, direction mismatches, skipped flows). The member runs
    (k + 1 for k > 1 shocks) name it in their `calculation_scenario_pin`."""

    __tablename__ = "scenario_run"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "created_by", "idempotency_key", name="uq_scenario_run_idempotency"
        ),
        CheckConstraint(_in("approval_state", ApprovalState), name="approval_state"),
        CheckConstraint("shocks_sha256 ~ '^[0-9a-f]{64}$'", name="shocks_sha256"),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="request_sha256"),
        CheckConstraint(f"shock_count BETWEEN {MIN_SHOCKS} AND {MAX_SHOCKS}", name="shock_count"),
        CheckConstraint(
            "run_count = CASE WHEN shock_count = 1 THEN 1 ELSE shock_count + 1 END",
            name="run_count",
        ),
        CheckConstraint(
            "jsonb_typeof(shock_counts) = 'array' AND jsonb_array_length(shock_counts) ="
            " shock_count",
            name="shock_counts",
        ),
        CheckConstraint("version >= 1", name="version"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario.id"), index=True, nullable=False
    )
    scenario_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_version.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    shocks_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_state: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calculation_run.id"), index=True, nullable=False
    )
    reporting_date: Mapped[date] = mapped_column(Date, nullable=False)
    shock_count: Mapped[int] = mapped_column(Integer, nullable=False)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Per shock, in declared order: {shock_index (1-based), template, matched_flows, parts,
    # direction_mismatches, skipped_past_due, skipped_incomplete}. Counts only (public).
    shock_counts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
