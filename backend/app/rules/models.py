"""rules.models

The rule, module and assumption registry of Phase 15 (BE-15.1, FC-07, Data Model §5, D-32),
migration 0018.

- `calculation_module` and `module_version`: a module identity and its versions, with the
  `implementation_status` (AVAILABLE or PLANNED), the declared input and output contracts
  and the engine version. A module version is registered in code and seeded (D-31 §1);
  there is no API to create one.
- `rule_definition`: a stable rule identity with its category, target module, owner and a
  `parameter_schema` from the closed kinds (D-32 §1).
- `rule_version`: immutable `parameters` with `parameters_sha256`, the D-06 status set, the
  author and the approver. At most one ACTIVE version per definition (partial unique index).
- `rule_set` and `rule_set_member`: the content-addressed set of rule versions a run pins,
  keyed by the hash of the sorted version ids (D-32 §4).
- `maturity_bucket_definition`: the immutable rows materialised from one DAY_BUCKETS rule
  version (D-32 §5).
- `assumption_set`: versioned assumptions with provenance, owner, effective dates and status
  (D-32 §6); read-only in Feature 03.

Every table carries `tenant_id` and is read through `ScopedRepository` (D-04). Definitions,
module versions, rule sets, their members and bucket definitions are append-only. Rule
versions and assumption sets are edited only while DRAFT; outside DRAFT only their status
moves (RULE_VERSION_TRANSITIONS), and nothing is ever deleted or truncated (DB triggers).
Seeded rows carry `seeded_by` (D-11).
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
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, ScopedModel
from app.rules.constants import ImplementationStatus, ParameterKind, RuleCategory, RuleVersionStatus

SHA256 = "~ '^[0-9a-f]{64}$'"
CODE = "~ '^[a-z][a-z0-9_]*$'"
KIND = "(parameter_schema ->> 'kind')"


def _in(column: str, values: Iterable[StrEnum]) -> str:
    """CHECK expression limiting a column to enum values (in enum order)."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False)


class _Labelled:
    """PROVISIONAL / ILLUSTRATIVE labels (D-11, D-35) and the seed tag."""

    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    illustrative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class CalculationModule(_Labelled, ScopedModel, Base):
    __tablename__ = "calculation_module"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(f"code {CODE}", name="code"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))


class ModuleVersion(_Labelled, ScopedModel, Base):
    """A module version: immutable (UPDATE, DELETE and TRUNCATE refused)."""

    __tablename__ = "module_version"
    __table_args__ = (
        UniqueConstraint("module_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(
            _in("implementation_status", ImplementationStatus), name="implementation_status"
        ),
        CheckConstraint("jsonb_typeof(input_contract) = 'object'", name="input_contract"),
        CheckConstraint("jsonb_typeof(output_contract) = 'object'", name="output_contract"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calculation_module.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    implementation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))


class RuleDefinition(_Labelled, ScopedModel, Base):
    """A stable rule identity (append-only)."""

    __tablename__ = "rule_definition"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(f"code {CODE}", name="code"),
        CheckConstraint(_in("category", RuleCategory), name="category"),
        CheckConstraint(
            f"jsonb_typeof(parameter_schema) = 'object' AND " f"{_in(KIND, ParameterKind)}",
            name="parameter_schema",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    target_module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calculation_module.id"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    parameter_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))


class _Versioned:
    """The lifecycle of a rule version or an assumption set version (D-32 §2)."""

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    authored_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(String(1000))
    change_summary: Mapped[str | None] = mapped_column(String(1000))


def _versioned_checks() -> tuple[Any, ...]:
    return (
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(_in("status", RuleVersionStatus), name="status"),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="effective",
        ),
        # D-32 §2: an ACTIVE version is always an approved one.
        CheckConstraint(
            "status <> 'ACTIVE' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="approved",
        ),
    )


class RuleVersion(_Versioned, _Labelled, ScopedModel, Base):
    __tablename__ = "rule_version"
    __table_args__ = (
        UniqueConstraint("rule_definition_id", "version"),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters"),
        CheckConstraint(f"parameters_sha256 {SHA256}", name="parameters_sha256"),
        *_versioned_checks(),
        # D-32 §2: at most one ACTIVE version per definition.
        Index(
            "uq_rule_version_one_active",
            "rule_definition_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    rule_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_definition.id"), index=True, nullable=False
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parameters_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class RuleSet(ScopedModel, Base):
    """The content-addressed set of rule versions a run pins (append-only)."""

    __tablename__ = "rule_set"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_sha256"),
        CheckConstraint(f"content_sha256 {SHA256}", name="content_sha256"),
        CheckConstraint("member_count >= 1", name="member_count"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)


class RuleSetMember(ScopedModel, Base):
    """One rule version of a rule set: one per definition, at most `member_count` per set."""

    __tablename__ = "rule_set_member"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "rule_version_id"),
        UniqueConstraint("rule_definition_id", "rule_set_id"),
        CheckConstraint("precedence >= 1", name="precedence"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    rule_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_set.id"), index=True, nullable=False
    )
    rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_version.id"), nullable=False
    )
    rule_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_definition.id"), nullable=False
    )
    precedence: Mapped[int] = mapped_column(Integer, nullable=False)


class MaturityBucketDefinition(_Labelled, ScopedModel, Base):
    """One bucket of a DAY_BUCKETS rule version: calendar days after the reporting date in
    (lower_exclusive_days, upper_inclusive_days], the last bucket open (upper NULL)."""

    __tablename__ = "maturity_bucket_definition"
    __table_args__ = (
        UniqueConstraint("rule_version_id", "sequence"),
        UniqueConstraint("code", "rule_version_id"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint(
            "upper_inclusive_days IS NULL OR upper_inclusive_days > lower_exclusive_days",
            name="bounds",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_version.id"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    lower_exclusive_days: Mapped[int] = mapped_column(Integer, nullable=False)
    upper_inclusive_days: Mapped[int | None] = mapped_column(Integer)


class AssumptionSet(_Versioned, _Labelled, ScopedModel, Base):
    """A version of an assumption set (one ACTIVE version per code)."""

    __tablename__ = "assumption_set"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", "version"),
        CheckConstraint(f"code {CODE}", name="code"),
        CheckConstraint("jsonb_typeof(assumptions) = 'object'", name="assumptions"),
        CheckConstraint(f"content_sha256 {SHA256}", name="content_sha256"),
        *_versioned_checks(),
        Index(
            "uq_assumption_set_one_active",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    provenance: Mapped[str] = mapped_column(String(1000), nullable=False)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
