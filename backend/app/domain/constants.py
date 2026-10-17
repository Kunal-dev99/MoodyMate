"""domain.constants

Module constants and enums (D-06, D-29). Every enum column is CHECK-constrained in the
database (migration 0017, which holds frozen copies kept equal by test_domain).

The projection spec below is FROZEN for `PROJECTOR_VERSION` 1: changing what a schema
field projects into is a new projector version (D-29 §4), never an edit of this one.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.ingestion.constants import ImportStatus


class DomainSchemaCode(StrEnum):
    """The canonical schemas the projector supports (D-29 §1)."""

    INSTRUMENT = "instrument"
    POSITION = "position"
    CASHFLOW_EVENT = "cashflow_event"


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


class ColumnKind(StrEnum):
    """The type of a typed domain column, as the schema field type it accepts. A field
    projects into the column only when its pinned data type is exactly this one: a STRING
    is never parsed into a DECIMAL or DATE column (D-29 §4)."""

    TEXT = "STRING"
    DECIMAL = "DECIMAL"
    DATE = "DATE"


# PROVISIONAL / ILLUSTRATIVE instrument type vocabulary (D-11, D-29 §5), seeded by 0017 for
# the default tenant (a frozen copy there, kept equal by test_domain).
SEEDED_INSTRUMENT_TYPES: dict[str, str] = {
    "LOAN": "Loan (illustrative)",
    "DEPOSIT": "Deposit (illustrative)",
    "BOND": "Bond (illustrative)",
}
DOMAIN_SEED_TAG = "0017_domain"

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
