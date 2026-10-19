"""domain.constants

Module constants and enums (D-06, D-29). Every enum column is CHECK-constrained in the
database (migration 0017, which holds frozen copies kept equal by test_domain).

The projection spec below is FROZEN for `PROJECTOR_VERSION` 1: changing what a schema
field projects into is a new projector version (D-29 §4), never an edit of this one. Feature
04 adds the `counterparty` and `credit_exposure` specs under version 1 (D-37 §3); the three
Feature 03 specs are unchanged. Credit frozen copies live in migration 0022.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.ingestion.constants import ImportStatus


class DomainSchemaCode(StrEnum):
    """The canonical schemas the projector supports (D-29 §1)."""

    INSTRUMENT = "instrument"
    POSITION = "position"
    CASHFLOW_EVENT = "cashflow_event"
    # Feature 04 (D-37 §1, D-38): the credit schemas, CHECK-widened by migration 0022.
    COUNTERPARTY = "counterparty"
    CREDIT_EXPOSURE = "credit_exposure"


class ProjectionRefusal(StrEnum):
    """409 refusals of POST /imports/{import_id}/projection, each audited DENIED."""

    DOMAIN_PROJECTION_DISABLED = "domain_projection_disabled"
    PROJECTION_UNSUPPORTED = "projection_unsupported"
    NOT_VALIDATED = "not_validated"


class ProjectionNote(StrEnum):
    """What the value-free projection summary counts, per schema field."""

    # A schema field whose pinned type differs from its typed column's (e.g. `instrument` v1
    # `maturity_date`, free text): the column stays NULL and the value stays in
    # canonical_record, reachable through lineage. Never parsed (D-29 §4).
    TYPE_NOT_PROJECTED = "TYPE_NOT_PROJECTED"
    # A value outside a seeded PROVISIONAL vocabulary (instrument type, cash-flow type or
    # origin): the vocabulary column stays NULL and the value is kept (as text, or in
    # canonical_record). Modules warn on it (D-29 §5).
    UNKNOWN_VALUE = "UNKNOWN_VALUE"


class CashflowType(StrEnum):
    """`cashflow_event` v1 `cashflow_type` (the seeded schema's description)."""

    PRINCIPAL = "PRINCIPAL"
    INTEREST = "INTEREST"
    FEE = "FEE"


class CashflowOrigin(StrEnum):
    """`cashflow_event` v1 `origin` (the seeded schema's description)."""

    CONTRACTUAL = "CONTRACTUAL"
    BEHAVIOURAL = "BEHAVIOURAL"


class CreditVocabularyKind(StrEnum):
    """The seeded PROVISIONAL `credit_vocabulary` kinds (D-37 §5, migration 0022). Grade
    codes are neutral (G01..G08, D): no rating-agency symbol is used."""

    RATING_GRADE = "rating_grade"
    CREDIT_SEGMENT = "credit_segment"
    FACILITY_TYPE = "facility_type"
    COLLATERAL_TYPE = "collateral_type"


class ObligorRatingStatus(StrEnum):
    """The obligor rating lifecycle (D-39 §2, a D-06 subset). A proposal is written IN_REVIEW
    (no DRAFT: a correction is a new proposal); IN_REVIEW -> ACTIVE or REJECTED; ACTIVE ->
    SUPERSEDED when another version for the same counterparty and `effective_from` is
    approved. RETIRED is reserved (withdrawal is deferred)."""

    IN_REVIEW = "IN_REVIEW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


class ObligorRatingAction(StrEnum):
    """Audit actions of the rating workflow (D-39 §3); refusals are audited DENIED."""

    PROPOSE = "obligor_rating.propose"
    APPROVE = "obligor_rating.approve"
    REJECT = "obligor_rating.reject"
    SUPERSEDE = "obligor_rating.supersede"


class ObligorRatingRefusal(StrEnum):
    """Refusals of the two rating writes (D-39 §3), each audited DENIED with its code."""

    RATING_CHANGE_DISABLED = "rating_change_disabled"  # 409: RATING_CHANGE_ENABLED=false
    INVALID_STATUS = "invalid_status"  # 409: the version is not IN_REVIEW
    SELF_APPROVAL = "self_approval"  # 409: the proposer decides their own proposal
    STALE_VERSION = "stale_version"  # 409: another version was activated since the proposal
    INVALID_GRADE = "invalid_grade"  # 422: not a rating_grade code of the tenant


# D-39 §2: status -> the statuses it may move to (also enforced by the 0023 trigger).
RATING_TRANSITIONS: dict[ObligorRatingStatus, tuple[ObligorRatingStatus, ...]] = {
    ObligorRatingStatus.IN_REVIEW: (ObligorRatingStatus.ACTIVE, ObligorRatingStatus.REJECTED),
    ObligorRatingStatus.ACTIVE: (ObligorRatingStatus.SUPERSEDED, ObligorRatingStatus.RETIRED),
}

# D-43 §2: the grade is internal; the rationale and the decision comment are confidential
# (masked for risk_analyst, the proposer included, and auditor).
RATING_CLASSIFICATIONS = {
    "grade": "internal",
    "rationale": "confidential",
    "decision_comment": "confidential",
}

RATING_RATIONALE_MAX = 2000  # D-39 §1
RATING_COMMENT_MAX = 1000  # D-39 §3


class ColumnKind(StrEnum):
    """The type of a typed domain column, as the schema field type it accepts. A field
    projects into the column only when its pinned data type is exactly this one: a STRING
    is never parsed into a DECIMAL or DATE column (D-29 §4)."""

    TEXT = "STRING"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    # Feature 04 (D-37 §3): days past due and the default flag.
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"


# PROVISIONAL / ILLUSTRATIVE instrument type vocabulary (D-11, D-29 §5), seeded by 0017 for
# the default tenant (a frozen copy there, kept equal by test_domain).
SEEDED_INSTRUMENT_TYPES: dict[str, str] = {
    "LOAN": "Loan (illustrative)",
    "DEPOSIT": "Deposit (illustrative)",
    "BOND": "Bond (illustrative)",
}
DOMAIN_SEED_TAG = "0017_domain"

# Feature 04 (D-37): the credit schema codes, the 0022 seed tag and the seeded PROVISIONAL
# credit vocabularies (a frozen copy in 0022, kept equal by test_domain). Grade codes are
# neutral: no rating-agency symbol (no vendor or agency parity is implied).
CREDIT_SCHEMA_CODES = frozenset({DomainSchemaCode.COUNTERPARTY, DomainSchemaCode.CREDIT_EXPOSURE})
CREDIT_SEED_TAG = "0022_credit_domain"
# (vocabulary, code) -> (rank, default grade); rank and flag are for rating_grade only.
SEEDED_CREDIT_VOCABULARY: dict[tuple[CreditVocabularyKind, str], tuple[int | None, bool | None]] = {
    **{(CreditVocabularyKind.RATING_GRADE, f"G0{i}"): (i, False) for i in range(1, 9)},
    (CreditVocabularyKind.RATING_GRADE, "D"): (9, True),
    **{
        (CreditVocabularyKind.CREDIT_SEGMENT, c): (None, None)
        for c in ("CORPORATE", "SME", "RETAIL", "SOVEREIGN")
    },
    **{
        (CreditVocabularyKind.FACILITY_TYPE, c): (None, None)
        for c in ("TERM_LOAN", "REVOLVER", "GUARANTEE")
    },
    **{
        (CreditVocabularyKind.COLLATERAL_TYPE, c): (None, None)
        for c in ("CASH", "SECURITIES", "REAL_ESTATE")
    },
}

PROJECTOR_VERSION = 1
# D-29 §1: an import is projected once it has a manifest and one of these statuses; only
# its accepted rows (canonical records) are projected.
PROJECTABLE_STATUSES = frozenset(
    {
        ImportStatus.COMPLETED,
        ImportStatus.COMPLETED_WITH_WARNINGS,
        ImportStatus.QUARANTINED,
    }
)
# Audit action of the projection and of each of its refusals.
PROJECT_IMPORT = "import.project"
# The instrument field whose value is matched against the instrument type vocabulary.
INSTRUMENT_TYPE_FIELD = "instrument_type"


@dataclass(frozen=True)
class Column:
    """A typed column of a domain row and the schema field it is projected from."""

    column: str
    field: str
    kind: ColumnKind
    # A seeded vocabulary the value must belong to; any other value leaves the column NULL
    # and is counted UNKNOWN_VALUE.
    vocabulary: frozenset[str] | None = None
    # Feature 04 (D-37 §3): a credit vocabulary the value is checked against but kept as
    # given; a value outside it is counted UNKNOWN_VALUE (modules map it, never silently).
    credit_vocabulary: CreditVocabularyKind | None = None


@dataclass(frozen=True)
class ProjectionSpec:
    """How projector v1 maps one schema's canonical payload (D-29 §4).

    - `business_key`: the field that identifies the row within its source (D-29 §3).
    - `instrument_key`: the field naming the instrument (positions and cash flows).
    - `columns`: the typed columns. Fields neither here nor extension-eligible stay in
      `canonical_record`, reachable through `canonical_record_id` and lineage.
    - `schema_versions`: the schema versions this projector version was written for; any
      other version is `projection_unsupported` until a new projector version exists.
    - `attributes`: extension-eligible fields become `instrument_attribute_value` rows.
    """

    schema_code: DomainSchemaCode
    business_key: str
    instrument_key: str | None
    columns: tuple[Column, ...]
    schema_versions: frozenset[int]
    attributes: bool = False

    @property
    def mapped_fields(self) -> frozenset[str]:
        keys = {self.business_key, *([self.instrument_key] if self.instrument_key else [])}
        return frozenset(keys | {c.field for c in self.columns})


PROJECTION_SPECS: dict[DomainSchemaCode, ProjectionSpec] = {
    DomainSchemaCode.INSTRUMENT: ProjectionSpec(
        DomainSchemaCode.INSTRUMENT,
        business_key="instrument_id",
        instrument_key=None,
        columns=(
            Column("instrument_type_code", INSTRUMENT_TYPE_FIELD, ColumnKind.TEXT),
            Column("currency", "currency", ColumnKind.TEXT),
            Column("notional", "notional", ColumnKind.DECIMAL),
            Column("interest_rate", "interest_rate", ColumnKind.DECIMAL),
            Column("maturity_date", "maturity_date", ColumnKind.DATE),
        ),
        schema_versions=frozenset({1}),
        attributes=True,
    ),
    DomainSchemaCode.POSITION: ProjectionSpec(
        DomainSchemaCode.POSITION,
        business_key="position_id",
        instrument_key="instrument_id",
        columns=(
            Column("legal_entity_ref", "legal_entity_id", ColumnKind.TEXT),
            Column("as_of_date", "as_of_date", ColumnKind.DATE),
            Column("quantity", "quantity", ColumnKind.DECIMAL),
            Column("balance", "balance", ColumnKind.DECIMAL),
            Column("currency", "currency", ColumnKind.TEXT),
            Column("valuation_date", "valuation_date", ColumnKind.DATE),
        ),
        schema_versions=frozenset({1}),
    ),
    DomainSchemaCode.CASHFLOW_EVENT: ProjectionSpec(
        DomainSchemaCode.CASHFLOW_EVENT,
        business_key="cashflow_id",
        instrument_key="instrument_id",
        columns=(
            Column("due_date", "due_date", ColumnKind.DATE),
            Column("amount", "amount", ColumnKind.DECIMAL),
            Column("currency", "currency", ColumnKind.TEXT),
            Column(
                "cashflow_type",
                "cashflow_type",
                ColumnKind.TEXT,
                frozenset(c.value for c in CashflowType),
            ),
            Column("origin", "origin", ColumnKind.TEXT, frozenset(c.value for c in CashflowOrigin)),
        ),
        schema_versions=frozenset({1}),
    ),
}

# Feature 04 (D-37 §3): the credit specs, added under projector version 1. The three specs
# above stay frozen.
PROJECTION_SPECS[DomainSchemaCode.COUNTERPARTY] = ProjectionSpec(
    DomainSchemaCode.COUNTERPARTY,
    business_key="counterparty_id",
    instrument_key=None,
    columns=(
        Column("counterparty_name", "counterparty_name", ColumnKind.TEXT),
        Column(
            "segment",
            "segment",
            ColumnKind.TEXT,
            credit_vocabulary=CreditVocabularyKind.CREDIT_SEGMENT,
        ),
        Column("country", "country", ColumnKind.TEXT),
    ),
    schema_versions=frozenset({1}),
)
PROJECTION_SPECS[DomainSchemaCode.CREDIT_EXPOSURE] = ProjectionSpec(
    DomainSchemaCode.CREDIT_EXPOSURE,
    business_key="exposure_id",
    instrument_key="instrument_id",
    columns=(
        Column("counterparty_key", "counterparty_id", ColumnKind.TEXT),
        Column("as_of_date", "as_of_date", ColumnKind.DATE),
        Column("currency", "currency", ColumnKind.TEXT),
        Column(
            "facility_type",
            "facility_type",
            ColumnKind.TEXT,
            credit_vocabulary=CreditVocabularyKind.FACILITY_TYPE,
        ),
        Column("drawn_amount", "drawn_amount", ColumnKind.DECIMAL),
        Column("undrawn_amount", "undrawn_amount", ColumnKind.DECIMAL),
        Column("days_past_due", "days_past_due", ColumnKind.INTEGER),
        Column("default_flag", "default_flag", ColumnKind.BOOLEAN),
        Column("origination_date", "origination_date", ColumnKind.DATE),
        Column(
            "origination_grade",
            "origination_grade",
            ColumnKind.TEXT,
            credit_vocabulary=CreditVocabularyKind.RATING_GRADE,
        ),
        Column("maturity_date", "maturity_date", ColumnKind.DATE),
        Column("effective_interest_rate", "effective_interest_rate", ColumnKind.DECIMAL),
        Column(
            "collateral_type",
            "collateral_type",
            ColumnKind.TEXT,
            credit_vocabulary=CreditVocabularyKind.COLLATERAL_TYPE,
        ),
        Column("collateral_value", "collateral_value", ColumnKind.DECIMAL),
    ),
    schema_versions=frozenset({1}),
)
