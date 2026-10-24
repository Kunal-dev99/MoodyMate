"""domain.models

The WS3 domain tables of Phase 14 (BE-14.2, FC-01, Data Model §4, D-29), migration 0017.

- `domain_projection`: one row per projected import, with the projector version and a
  value-free summary (counts and field names only).
- `instrument_type`: a seeded PROVISIONAL vocabulary (LOAN, DEPOSIT, BOND; D-11).
- `instrument` and `position`: stable identities, unique per tenant, source system and
  business key (D-29 §3). A later import of the same key adds a version, never an update.
- `instrument_version`, `position_version` and `cashflow_event`: one row per accepted
  canonical record, which each row names (unique `canonical_record_id`) together with its
  import, projection, source record and ordinal, pinned schema version and payload hash.
  Amounts are exact NUMERIC (Decimal, never float, never rounded) and dates are DATE.
- `instrument_attribute_definition` and `instrument_attribute_value`: extension-eligible
  instrument fields, kept as the canonical typed text.

Feature 04 (BE-19.1, D-37), migration 0022: `counterparty` and `credit_exposure` identities,
their `counterparty_version` and `credit_exposure_version` rows (as above: one per accepted
canonical record; days past due INTEGER, the default flag BOOLEAN), and the seeded
PROVISIONAL `credit_vocabulary`.

Phase 20 (BE-20.1, D-39), migration 0023: `obligor_rating`, one immutable version per rating
proposal, lifecycle-guarded (not append-only: its status and decision fields move once).

Phase 31 (BE-31.1, D-58), migration 0030: `legal_entity`, a stable identity per tenant, source
system and code (`provisional`; its attributes are deferred to the client), and
`domain_identity_link`, one append-only link from a position version, cash-flow event or
credit-exposure version to the one instrument, counterparty or legal entity of the tenant its
text key names. No column is added to any existing table (D-58 §1).

Every table carries `tenant_id` and is read through `ScopedRepository` (D-04). All of them
are append-only: UPDATE, DELETE and TRUNCATE raise (DB triggers). Insert triggers check that
a row is in the tenant of the import, source, identity or projection it names, and that a
version or event row repeats its canonical record's import, source record, ordinal, schema
version and payload hash. Every enum column has a CHECK (D-06).
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, ScopedModel
from app.domain.constants import (
    RATING_COMMENT_MAX,
    RATING_RATIONALE_MAX,
    CashflowOrigin,
    CashflowType,
    CreditVocabularyKind,
    DomainSchemaCode,
    IdentityLinkMethod,
    IdentitySourceKind,
    IdentityTargetKind,
    ObligorRatingStatus,
)
from app.governance.constants import FieldType

SHA256 = "~ '^[0-9a-f]{64}$'"
CURRENCY = "~ '^[A-Z]{3}$'"


def _in(column: str, values: Iterable[StrEnum]) -> str:
    """CHECK expression limiting a column to enum values (in enum order)."""
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenant.id"), index=True, nullable=False)


class DomainProjection(ScopedModel, Base):
    """The projection of one validated import (D-29 §2): written once, with its summary
    (counts and schema field names, never a value)."""

    __tablename__ = "domain_projection"
    __table_args__ = (
        CheckConstraint(_in("schema_code", DomainSchemaCode), name="schema_code"),
        CheckConstraint("projector_version >= 1", name="projector_version"),
        CheckConstraint("record_count >= 0", name="record_count"),
        CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), unique=True, index=True, nullable=False
    )
    import_manifest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_manifest.id"), unique=True, nullable=False
    )
    schema_code: Mapped[str] = mapped_column(String(30), nullable=False)
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    projector_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Rows written: one per accepted canonical record (conservation).
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class InstrumentType(ScopedModel, Base):
    """The instrument type vocabulary: seeded PROVISIONAL / ILLUSTRATIVE (D-11, D-29 §5)."""

    __tablename__ = "instrument_type"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name="code"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


class _Identity:
    """A stable identity: one per tenant, source system and business key (D-29 §3)."""

    tenant_id: Mapped[uuid.UUID] = _tenant()
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), nullable=False
    )
    business_key: Mapped[str] = mapped_column(String, nullable=False)


class Instrument(_Identity, ScopedModel, Base):
    __tablename__ = "instrument"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "business_key"),)


class Position(_Identity, ScopedModel, Base):
    __tablename__ = "position"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "business_key"),)


class _Projected:
    """What every version and event row names (D-29 §6): its import and projection, the
    canonical record it was projected from (unique), that record's source record and
    ordinal, the pinned schema version and the payload hash."""

    tenant_id: Mapped[uuid.UUID] = _tenant()
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_job.id"), index=True, nullable=False
    )
    domain_projection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domain_projection.id"), nullable=False
    )
    canonical_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_record.id"), unique=True, nullable=False
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_record.id"), nullable=False
    )
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_version.id"), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


def _projected_checks() -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint(f"payload_sha256 {SHA256}", name="payload_sha256"),
        CheckConstraint("source_ordinal >= 1", name="source_ordinal"),
        CheckConstraint(f"currency IS NULL OR currency {CURRENCY}", name="currency"),
    )


class InstrumentVersion(_Projected, ScopedModel, Base):
    """One projected instrument record. `instrument_type_code` keeps the value as given;
    `instrument_type_id` is set only for a seeded type. `maturity_date` is NULL while the
    pinned field is free text (`instrument` v1, TYPE_NOT_PROJECTED)."""

    __tablename__ = "instrument_version"
    __table_args__ = (
        UniqueConstraint("instrument_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        *_projected_checks(),
    )

    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instrument.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_type_code: Mapped[str | None] = mapped_column(String)
    instrument_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instrument_type.id")
    )
    currency: Mapped[str | None] = mapped_column(String(3))
    notional: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    maturity_date: Mapped[date | None] = mapped_column(Date)


class PositionVersion(_Projected, ScopedModel, Base):
    """One projected position record; `instrument_key` names its instrument by business key
    (resolved within a snapshot, D-30 §4)."""

    __tablename__ = "position_version"
    __table_args__ = (
        UniqueConstraint("position_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        *_projected_checks(),
    )

    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("position.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_key: Mapped[str | None] = mapped_column(String)
    legal_entity_ref: Mapped[str | None] = mapped_column(String)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    currency: Mapped[str | None] = mapped_column(String(3))
    valuation_date: Mapped[date | None] = mapped_column(Date)


class CashflowEvent(_Projected, ScopedModel, Base):
    """One projected cash-flow event. `instrument_key` names its instrument by business key
    in the same tenant; `cashflow_type` and `origin` are NULL outside their vocabularies
    (UNKNOWN_VALUE). v1 has no direction (D-35 seeds it as a rule)."""

    __tablename__ = "cashflow_event"
    __table_args__ = (
        CheckConstraint(
            f"cashflow_type IS NULL OR {_in('cashflow_type', CashflowType)}", name="cashflow_type"
        ),
        CheckConstraint(f"origin IS NULL OR {_in('origin', CashflowOrigin)}", name="origin"),
        *_projected_checks(),
    )

    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), nullable=False
    )
    business_key: Mapped[str] = mapped_column(String, nullable=False)
    instrument_key: Mapped[str | None] = mapped_column(String, index=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    currency: Mapped[str | None] = mapped_column(String(3))
    cashflow_type: Mapped[str | None] = mapped_column(String(20))
    origin: Mapped[str | None] = mapped_column(String(20))


class InstrumentAttributeDefinition(ScopedModel, Base):
    """An extension-eligible instrument field, defined on its first projection."""

    __tablename__ = "instrument_attribute_definition"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(_in("data_type", FieldType), name="data_type"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class InstrumentAttributeValue(ScopedModel, Base):
    """An extension-eligible field's value on one instrument version, as the canonical typed
    text (never parsed, never a float). Masked on read by the pinned schema field."""

    __tablename__ = "instrument_attribute_value"
    __table_args__ = (UniqueConstraint("instrument_version_id", "attribute_definition_id"),)

    tenant_id: Mapped[uuid.UUID] = _tenant()
    instrument_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instrument_version.id"), index=True, nullable=False
    )
    attribute_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("instrument_attribute_definition.id"), nullable=False
    )
    value_text: Mapped[str] = mapped_column(String, nullable=False)


# --- Feature 04: credit data (BE-19.1, D-37), migration 0022 ---------------------------------


class Counterparty(_Identity, ScopedModel, Base):
    __tablename__ = "counterparty"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "business_key"),)


class CreditExposure(_Identity, ScopedModel, Base):
    __tablename__ = "credit_exposure"
    __table_args__ = (UniqueConstraint("tenant_id", "source_system_id", "business_key"),)


def _version_checks() -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint(f"payload_sha256 {SHA256}", name="payload_sha256"),
        CheckConstraint("source_ordinal >= 1", name="source_ordinal"),
        CheckConstraint("version >= 1", name="version"),
    )


class CounterpartyVersion(_Projected, ScopedModel, Base):
    """One projected counterparty record. `segment` is kept as given (D-37 §3); `country`
    is NULL unless ISO 3166-1 alpha-2 (UNKNOWN_VALUE)."""

    __tablename__ = "counterparty_version"
    __table_args__ = (
        UniqueConstraint("counterparty_id", "version"),
        CheckConstraint("country IS NULL OR country ~ '^[A-Z]{2}$'", name="country"),
        *_version_checks(),
    )

    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("counterparty.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    counterparty_name: Mapped[str | None] = mapped_column(String)
    segment: Mapped[str | None] = mapped_column(String)
    country: Mapped[str | None] = mapped_column(String(2))


class CreditExposureVersion(_Projected, ScopedModel, Base):
    """One projected credit-exposure record. `counterparty_key` names its counterparty by
    business key, resolved within a snapshot and the tenant (D-37 §4). Vocabulary fields
    (facility type, origination grade, collateral type) are kept as given (D-37 §3)."""

    __tablename__ = "credit_exposure_version"
    __table_args__ = (
        UniqueConstraint("credit_exposure_id", "version"),
        CheckConstraint(f"currency IS NULL OR currency {CURRENCY}", name="currency"),
        *_version_checks(),
    )

    credit_exposure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("credit_exposure.id"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    counterparty_key: Mapped[str | None] = mapped_column(String, index=True)
    instrument_key: Mapped[str | None] = mapped_column(String)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    facility_type: Mapped[str | None] = mapped_column(String)
    drawn_amount: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    undrawn_amount: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    days_past_due: Mapped[int | None] = mapped_column(Integer)
    default_flag: Mapped[bool | None] = mapped_column(Boolean)
    origination_date: Mapped[date | None] = mapped_column(Date)
    origination_grade: Mapped[str | None] = mapped_column(String)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    effective_interest_rate: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))
    collateral_type: Mapped[str | None] = mapped_column(String)
    collateral_value: Mapped[Decimal | None] = mapped_column(Numeric(asdecimal=True))


class CreditVocabulary(ScopedModel, Base):
    """The seeded PROVISIONAL credit vocabularies (D-37 §5): neutral grade codes with rank
    and default flag, credit segments, facility types and collateral types."""

    __tablename__ = "credit_vocabulary"
    __table_args__ = (
        UniqueConstraint("tenant_id", "vocabulary", "code"),
        CheckConstraint(_in("vocabulary", CreditVocabularyKind), name="vocabulary"),
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name="code"),
        CheckConstraint(
            "(vocabulary = 'rating_grade') = (rank IS NOT NULL AND is_default_grade IS NOT NULL)"
            " AND (rank IS NULL OR rank >= 1)",
            name="grade",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    vocabulary: Mapped[str] = mapped_column(String(30), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    is_default_grade: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(String(1000))
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seeded_by: Mapped[str | None] = mapped_column(String(100))


# --- Feature 04: the obligor rating workflow (BE-20.1, D-39), migration 0023 -----------------


class ObligorRating(ScopedModel, Base):
    """One immutable obligor rating version (D-39 §1-2): a grade of the tenant's
    `rating_grade` vocabulary for one counterparty identity from `effective_from`, with its
    rationale and proposer. Written IN_REVIEW; the decision fields (decider, time, comment)
    are set once, on the move out of IN_REVIEW, and the superseded fields once, on the move
    ACTIVE -> SUPERSEDED. A DB trigger freezes the content, allows only the D-39 §2 moves
    and refuses DELETE and TRUNCATE; a partial unique index keeps one ACTIVE version per
    (counterparty, `effective_from`)."""

    __tablename__ = "obligor_rating"
    __table_args__ = (
        CheckConstraint(_in("status", ObligorRatingStatus), name="status"),
        CheckConstraint(
            f"char_length(rationale) BETWEEN 1 AND {RATING_RATIONALE_MAX}", name="rationale"
        ),
        CheckConstraint(
            "decision_comment IS NULL OR char_length(decision_comment) BETWEEN 1 AND "
            f"{RATING_COMMENT_MAX}",
            name="decision_comment",
        ),
        CheckConstraint(
            "(decided_by IS NULL) = (decided_at IS NULL)"
            " AND (decided_by IS NULL) = (decision_comment IS NULL)"
            " AND (status = 'IN_REVIEW') = (decided_at IS NULL)",
            name="decision",
        ),
        CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by_rating_id IS NULL)"
            " AND (status = 'SUPERSEDED') = (superseded_at IS NOT NULL)",
            name="superseded",
        ),
        CheckConstraint(
            "superseded_by_rating_id IS NULL OR superseded_by_rating_id <> id",
            name="superseded_by",
        ),
        Index(
            "uq_obligor_rating_one_active",
            "counterparty_id",
            "effective_from",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    counterparty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("counterparty.id"), index=True, nullable=False
    )
    grade: Mapped[str] = mapped_column(String(50), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    rationale: Mapped[str] = mapped_column(String(RATING_RATIONALE_MAX), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    proposer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_account.id"), nullable=False
    )
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(String(RATING_COMMENT_MAX))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_rating_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligor_rating.id")
    )


# --- Phase 31: legal entities and identity links (BE-31.1, D-58), migration 0030 ------------


class LegalEntity(ScopedModel, Base):
    """A legal entity identity (D-58 §2): one per tenant, source system and code (a position's
    `legal_entity_ref`). Always `provisional`: attributes and the source-of-truth rule wait
    for the client (Data Model §11). Append-only; the source is of the same tenant (trigger)."""

    __tablename__ = "legal_entity"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_system_id", "code"),
        CheckConstraint("char_length(code) >= 1", name="code"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_system.id"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DomainIdentityLink(ScopedModel, Base):
    """One link from an append-only domain row to the identity its text key names (D-58 §2):
    written only when exactly one identity of the tenant has the key (D-58 §3), never
    changed. `UNIQUE (source_kind, source_row_id, target_kind)`. Insert trigger: both ends are
    rows of the link's tenant, the pair is one D-58 allows, and the target's key is the source
    row's key. Append-only (UPDATE, DELETE and TRUNCATE raise). Links carry ids only."""

    __tablename__ = "domain_identity_link"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_row_id", "target_kind"),
        CheckConstraint(_in("source_kind", IdentitySourceKind), name="source_kind"),
        CheckConstraint(_in("target_kind", IdentityTargetKind), name="target_kind"),
        CheckConstraint(_in("method", IdentityLinkMethod), name="method"),
        CheckConstraint("resolver_version >= 1", name="resolver_version"),
        Index("ix_domain_identity_link_target", "target_kind", "target_id"),
    )

    tenant_id: Mapped[uuid.UUID] = _tenant()
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    source_row_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    resolver_version: Mapped[int] = mapped_column(Integer, nullable=False)
