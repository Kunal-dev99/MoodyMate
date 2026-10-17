"""calculations.models

Dataset snapshots and calculation runs of Phase 15 (BE-15.2, FC-04, Data Model §5, D-30,
D-31), migration 0018.

- `dataset_snapshot` and `dataset_snapshot_member`: an immutable, content-addressed set of
  projected imports, at most one per schema code. Each member pins the import, its
  `import_manifest` (and `manifest_sha256`), its `domain_projection` and its schema code,
  with the manifest's counts and the reconciliation outcome.
- `calculation_run`: one execution, pinned to its snapshot, reporting date, module version,
  rule set, assumption set and engine version, with the manifest of every pin and its hash,
  the result hash, the idempotency key and its start and end times. Data Model §9 statuses.
- `calculation_input_ref`: one row per pin of a run.
- `liquidity_gap_result`: one row per run, currency and bucket.
- `intermediate_value`: one row per per-input contribution, referencing its cash-flow event
  and so its lineage (D-31 §6).
- `calculation_warning`: value-free warnings by run and entity.
- `lcr_result` (Phase 17, migration 0020): one row per lcr_hqla run and currency: HQLA by
  level, outflows, inflows before and after the cap, the denominator and the ratio (NULL with
  LCR_DENOMINATOR_ZERO when the denominator is not positive).
- `hqla_asset_result` (Phase 17): one row per asset position of an lcr_hqla run, included at
  its HQLA level or excluded with its reason, referencing its position version and lineage.

Amounts are exact NUMERIC (Decimal) with an explicit currency. Every table carries
`tenant_id` (D-04). Triggers: snapshots are append-only; a run is inserted RUNNING and moves
once, to one terminal status, changing only its status, result hash, end time and outcome;
the output tables take inserts only while their run is RUNNING and are append-only. Nothing
here is ever updated in place, deleted or truncated (Arch §6.2 step 7).
"""

import uuid
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.calculations.constants import (
    TERMINAL_STATUSES,
    CalculationRunStatus,
    CalculationWarningCode,
    HqlaExclusionReason,
    InputRefType,
    IntermediateCode,
    LcrWarningCode,
    WarningSeverity,
)
from app.db.base import Base, ScopedModel
from app.domain.constants import CashflowOrigin, DomainSchemaCode
from app.reconciliation.constants import ReconciliationOutcome
from app.rules.constants import CashflowDirection, HqlaLevel

SHA256 = "~ '^[0-9a-f]{64}$'"
CURRENCY = "~ '^[A-Z]{3}$'"


def _in(column: str, values: Iterable[StrEnum]) -> str:
    """CHECK expression limiting a column to enum values (in enum order)."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False)


def _fk(target: str, index: bool = False, nullable: bool = False) -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{target}.id"), index=index, nullable=nullable
    )


# --- dataset snapshots (D-30) -----------------------------------------------------------------


class DatasetSnapshot(ScopedModel, Base):
    __tablename__ = "dataset_snapshot"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_sha256"),
        CheckConstraint(f"content_sha256 {SHA256}", name="content_sha256"),
        CheckConstraint("member_count >= 1", name="member_count"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DatasetSnapshotMember(ScopedModel, Base):
    __tablename__ = "dataset_snapshot_member"
    __table_args__ = (
        UniqueConstraint("dataset_snapshot_id", "schema_code"),
        UniqueConstraint("import_job_id", "dataset_snapshot_id"),
        CheckConstraint(_in("schema_code", DomainSchemaCode), name="schema_code"),
        CheckConstraint(f"manifest_sha256 {SHA256}", name="manifest_sha256"),
        CheckConstraint(
            # D-30 §3: a BROKEN reconciliation is refused (fail closed).
            _in("reconciliation_outcome", [o for o in ReconciliationOutcome if o != "BROKEN"]),
            name="reconciliation_outcome",
        ),
        CheckConstraint(
            "accepted_count >= 0 AND quarantined_count >= 0 AND rejected_count >= 0",
            name="counts",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    dataset_snapshot_id: Mapped[uuid.UUID] = _fk("dataset_snapshot", index=True)
    import_job_id: Mapped[uuid.UUID] = _fk("import_job")
    import_manifest_id: Mapped[uuid.UUID] = _fk("import_manifest")
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    domain_projection_id: Mapped[uuid.UUID] = _fk("domain_projection")
    projector_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_code: Mapped[str] = mapped_column(String(30), nullable=False)
    schema_version_id: Mapped[uuid.UUID] = _fk("schema_version")
    mapping_version_id: Mapped[uuid.UUID] = _fk("mapping_version")
    source_system_id: Mapped[uuid.UUID] = _fk("source_system")
    reconciliation_run_id: Mapped[uuid.UUID] = _fk("reconciliation_run")
    reconciliation_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quarantined_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)


# --- calculation runs (D-31) -----------------------------------------------------------------


class CalculationRun(ScopedModel, Base):
    __tablename__ = "calculation_run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "created_by", "idempotency_key"),
        CheckConstraint(_in("status", CalculationRunStatus), name="status"),
        CheckConstraint(f"manifest_sha256 {SHA256}", name="manifest_sha256"),
        CheckConstraint(f"request_sha256 {SHA256}", name="request_sha256"),
        CheckConstraint(f"result_sha256 IS NULL OR result_sha256 {SHA256}", name="result_sha256"),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="manifest"),
        # A finished run has its result hash, end time and outcome; an unfinished one none.
        CheckConstraint(
            f"({_in('status', TERMINAL_STATUSES)}) = (result_sha256 IS NOT NULL"
            " AND ended_at IS NOT NULL AND outcome IS NOT NULL)",
            name="finished",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="times"),
        CheckConstraint("warning_count >= 0", name="warning_count"),
        Index("ix_calculation_run_tenant_started", "tenant_id", "started_at"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    dataset_snapshot_id: Mapped[uuid.UUID] = _fk("dataset_snapshot", index=True)
    module_version_id: Mapped[uuid.UUID] = _fk("module_version", index=True)
    rule_set_id: Mapped[uuid.UUID] = _fk("rule_set")
    assumption_set_id: Mapped[uuid.UUID] = _fk("assumption_set")
    reporting_date: Mapped[date] = mapped_column(Date, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    # Value-free counts set when the run ends (inputs, bucketed, excluded, post-checks).
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # What a replay of the key must repeat (snapshot, module version, reporting date).
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rerun_of_run_id: Mapped[uuid.UUID | None] = _fk("calculation_run", nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _RunOutput:
    """Every output row names its run (and is written only while the run is RUNNING)."""

    tenant_id: Mapped[uuid.UUID] = _tenant()
    calculation_run_id: Mapped[uuid.UUID] = _fk("calculation_run", index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)


class CalculationInputRef(_RunOutput, ScopedModel, Base):
    __tablename__ = "calculation_input_ref"
    __table_args__ = (
        UniqueConstraint("calculation_run_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(_in("ref_type", InputRefType), name="ref_type"),
        CheckConstraint(f"sha256 IS NULL OR sha256 {SHA256}", name="sha256"),
    )

    ref_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))


class LiquidityGapResult(_RunOutput, ScopedModel, Base):
    """One run, currency and bucket of a maturity ladder: inflows, outflows, net gap and the
    cumulative gap, with the contractual and behavioural parts shown separately (D-35 §1)."""

    __tablename__ = "liquidity_gap_result"
    __table_args__ = (
        UniqueConstraint("calculation_run_id", "sequence"),
        UniqueConstraint("currency", "calculation_run_id", "bucket_definition_id"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(f"currency {CURRENCY}", name="currency"),
        CheckConstraint("inflows = inflows_contractual + inflows_behavioural", name="inflows"),
        CheckConstraint("outflows = outflows_contractual + outflows_behavioural", name="outflows"),
        CheckConstraint("net_gap = inflows - outflows", name="net_gap"),
        CheckConstraint(
            "inflows_contractual >= 0 AND inflows_behavioural >= 0"
            " AND outflows_contractual >= 0 AND outflows_behavioural >= 0",
            name="non_negative",
        ),
        CheckConstraint("contribution_count >= 0", name="contribution_count"),
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    bucket_definition_id: Mapped[uuid.UUID] = _fk("maturity_bucket_definition")
    bucket_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket_code: Mapped[str] = mapped_column(String(20), nullable=False)
    inflows: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    outflows: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    net_gap: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    cumulative_gap: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    inflows_contractual: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    inflows_behavioural: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    outflows_contractual: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    outflows_behavioural: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    contribution_count: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )


class IntermediateValue(_RunOutput, ScopedModel, Base):
    """A named intermediate (D-31 §6): here one bucketed cash flow's contribution, with its
    value, unit (currency), dimensions, the rule versions that placed it and its lineage."""

    __tablename__ = "intermediate_value"
    __table_args__ = (
        UniqueConstraint("calculation_run_id", "sequence"),
        UniqueConstraint("cashflow_event_id", "calculation_run_id"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(_in("code", IntermediateCode), name="code"),
        CheckConstraint(f"unit {CURRENCY}", name="unit"),
        CheckConstraint(_in("direction", CashflowDirection), name="direction"),
        CheckConstraint(_in("origin", CashflowOrigin), name="origin"),
        CheckConstraint("value >= 0", name="value"),
        CheckConstraint("jsonb_typeof(rule_version_ids) = 'array'", name="rule_version_ids"),
        CheckConstraint("source_ordinal >= 1", name="source_ordinal"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(asdecimal=True), nullable=False)
    unit: Mapped[str] = mapped_column(String(3), nullable=False)
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    liquidity_gap_result_id: Mapped[uuid.UUID] = _fk("liquidity_gap_result", index=True)
    bucket_code: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    days_after_reporting_date: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # Lineage (D-31 §6): the cash-flow event and the instrument version it was read with,
    # and the event's canonical record, import and source row (checked on insert).
    cashflow_event_id: Mapped[uuid.UUID] = _fk("cashflow_event")
    instrument_version_id: Mapped[uuid.UUID | None] = _fk("instrument_version", nullable=True)
    canonical_record_id: Mapped[uuid.UUID] = _fk("canonical_record")
    import_job_id: Mapped[uuid.UUID] = _fk("import_job")
    source_record_id: Mapped[uuid.UUID] = _fk("source_record")
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class CalculationWarning(_RunOutput, ScopedModel, Base):
    __tablename__ = "calculation_warning"
    __table_args__ = (
        UniqueConstraint("calculation_run_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(_in("code", CalculationWarningCode), name="code"),
        CheckConstraint(_in("severity", WarningSeverity), name="severity"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


# --- lcr_hqla outputs (Phase 17, BE-17.1, migration 0020) ------------------------------------

LCR_WARNINGS_JSON = "[" + ", ".join(f'"{c.value}"' for c in LcrWarningCode) + "]"


def _numeric() -> Mapped[Decimal]:
    return mapped_column(Numeric(asdecimal=True), nullable=False)


class LcrResult(_RunOutput, ScopedModel, Base):
    """One lcr_hqla run and currency (D-35 §2). Exact NUMERIC; the identities between the
    figures are CHECK-constrained, so a row cannot disagree with itself."""

    __tablename__ = "lcr_result"
    __table_args__ = (
        UniqueConstraint("calculation_run_id", "sequence"),
        UniqueConstraint("currency", "calculation_run_id"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(f"currency {CURRENCY}", name="currency"),
        CheckConstraint("hqla_total = hqla_level1 + hqla_level2a + hqla_level2b", name="hqla"),
        CheckConstraint(
            "hqla_level1 >= 0 AND hqla_level2a >= 0 AND hqla_level2b >= 0 AND outflows >= 0"
            " AND outflow_balance >= 0 AND inflows_gross >= 0 AND inflows_before_cap >= 0",
            name="non_negative",
        ),
        CheckConstraint(
            "level1_count >= 0 AND level2a_count >= 0 AND level2b_count >= 0"
            " AND excluded_asset_count >= 0 AND outflow_count >= 0 AND inflow_count >= 0",
            name="counts",
        ),
        CheckConstraint(
            "level1_nonzero_count BETWEEN 0 AND level1_count"
            " AND level2a_nonzero_count BETWEEN 0 AND level2a_count"
            " AND level2b_nonzero_count BETWEEN 0 AND level2b_count"
            " AND outflow_nonzero_count BETWEEN 0 AND outflow_count"
            " AND inflow_nonzero_count BETWEEN 0 AND inflow_count",
            name="nonzero_counts",
        ),
        CheckConstraint("inflow_cap_rate >= 0 AND inflow_cap_rate <= 1", name="inflow_cap_rate"),
        CheckConstraint("inflow_cap_amount = inflow_cap_rate * outflows", name="inflow_cap"),
        CheckConstraint(
            "inflows_after_cap = LEAST(inflows_before_cap, inflow_cap_amount)"
            " AND inflow_cap_applied = (inflows_before_cap > inflow_cap_amount)",
            name="inflows",
        ),
        CheckConstraint("net_outflows = outflows - inflows_after_cap", name="net_outflows"),
        CheckConstraint("(ratio IS NULL) = (net_outflows <= 0)", name="ratio"),
        CheckConstraint(
            "jsonb_typeof(warning_codes) = 'array'"
            f" AND warning_codes <@ '{LCR_WARNINGS_JSON}'::jsonb"
            " AND (warning_codes @> '[\"LCR_DENOMINATOR_ZERO\"]') = (net_outflows <= 0)",
            name="warning_codes",
        ),
        CheckConstraint("jsonb_typeof(rule_version_ids) = 'array'", name="rule_version_ids"),
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    hqla_level1: Mapped[Decimal] = _numeric()
    hqla_level2a: Mapped[Decimal] = _numeric()
    hqla_level2b: Mapped[Decimal] = _numeric()
    hqla_total: Mapped[Decimal] = _numeric()
    level1_count: Mapped[int] = mapped_column(Integer, nullable=False)
    level2a_count: Mapped[int] = mapped_column(Integer, nullable=False)
    level2b_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_asset_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # D-33: the contributions with a non-zero value (what the masking decision counts).
    level1_nonzero_count: Mapped[int] = mapped_column(Integer, nullable=False)
    level2a_nonzero_count: Mapped[int] = mapped_column(Integer, nullable=False)
    level2b_nonzero_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outflow_balance: Mapped[Decimal] = _numeric()
    outflows: Mapped[Decimal] = _numeric()
    outflow_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outflow_nonzero_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inflows_gross: Mapped[Decimal] = _numeric()
    inflows_before_cap: Mapped[Decimal] = _numeric()
    inflow_cap_rate: Mapped[Decimal] = _numeric()
    inflow_cap_amount: Mapped[Decimal] = _numeric()
    inflows_after_cap: Mapped[Decimal] = _numeric()
    inflow_cap_applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    inflow_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inflow_nonzero_count: Mapped[int] = mapped_column(Integer, nullable=False)
    net_outflows: Mapped[Decimal] = _numeric()
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    warning_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rule_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # D-33: the row is an aggregate (`internal`); its contributions carry this one.
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    contribution_classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )


class HqlaAssetResult(_RunOutput, ScopedModel, Base):
    """One asset position of an lcr_hqla run: included at its level (value = balance x
    (1 - haircut)) or excluded with its reason (value 0), with its position version's
    lineage (D-31 §6)."""

    __tablename__ = "hqla_asset_result"
    __table_args__ = (
        UniqueConstraint("calculation_run_id", "sequence"),
        UniqueConstraint("position_version_id", "calculation_run_id"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(f"currency {CURRENCY}", name="currency"),
        CheckConstraint(_in("level", HqlaLevel), name="level"),
        CheckConstraint(_in("exclusion_reason", HqlaExclusionReason), name="exclusion_reason"),
        CheckConstraint(
            "included = (level IS NOT NULL) AND included = (exclusion_reason IS NULL)"
            " AND included = (haircut IS NOT NULL)",
            name="included",
        ),
        CheckConstraint(
            "balance >= 0 AND (haircut IS NULL OR (haircut >= 0 AND haircut <= 1))",
            name="balance",
        ),
        CheckConstraint(
            "hqla_value = CASE WHEN included THEN balance * (1 - haircut) ELSE 0 END",
            name="hqla_value",
        ),
        CheckConstraint("jsonb_typeof(rule_version_ids) = 'array'", name="rule_version_ids"),
        CheckConstraint("source_ordinal >= 1", name="source_ordinal"),
    )

    lcr_result_id: Mapped[uuid.UUID] = _fk("lcr_result", index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    level: Mapped[str | None] = mapped_column(String(3))
    exclusion_reason: Mapped[str | None] = mapped_column(String(30))
    instrument_type: Mapped[str] = mapped_column(String(50), nullable=False)
    balance: Mapped[Decimal] = _numeric()
    haircut: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    hqla_value: Mapped[Decimal] = _numeric()
    rule_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    classification: Mapped[str] = mapped_column(
        String(50), ForeignKey("data_classification.code"), nullable=False
    )
    # Lineage (D-31 §6): the position version and the instrument version it was read with,
    # and the position's canonical record, import and source row (checked on insert).
    position_version_id: Mapped[uuid.UUID] = _fk("position_version")
    instrument_version_id: Mapped[uuid.UUID] = _fk("instrument_version")
    canonical_record_id: Mapped[uuid.UUID] = _fk("canonical_record")
    import_job_id: Mapped[uuid.UUID] = _fk("import_job")
    source_record_id: Mapped[uuid.UUID] = _fk("source_record")
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
