"""domain.schemas

Response models of the WS3 domain API (BE-14.3, BE-14.4). Amounts and rates are exact
decimal text and dates ISO-8601 text, never JSON numbers. Every domain value is masked by
the classification of the schema field it was projected from, in the schema version the
row pins (BE-3.5), so a caller not cleared for it gets `MaskedValue`.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.classification import MaskedValue
from app.core.pagination import HasMore, NextCursor
from app.core.text import SafeMultilineText
from app.domain.constants import (
    FX_CONSOLIDATION_REASON,
    RATING_COMMENT_MAX,
    RATING_RATIONALE_MAX,
    CreditVocabularyKind,
    DomainSchemaCode,
    IdentityLinkMethod,
    IdentitySourceKind,
    IdentityTargetKind,
    ObligorRatingStatus,
)
from app.governance.constants import FieldType, ProposalDecision
from app.ingestion.constants import ImportStatus

Value = str | MaskedValue


class ProjectionSummary(BaseModel):
    """Value-free: counts and schema field names only (D-29 §2)."""

    records: int = Field(description="Accepted canonical records of the import read.")
    rows_projected: int = Field(
        description="Version or event rows written: one per accepted canonical record."
    )
    new_identities: int = Field(
        description="Instruments or positions seen for the first time (0 for cash flows)."
    )
    attribute_values: int = Field(description="Instrument attribute values written.")
    type_not_projected: dict[str, int] = Field(
        description="TYPE_NOT_PROJECTED: per schema field, the values not projected into "
        "their typed column because the pinned field type differs (e.g. `instrument` v1 "
        "`maturity_date` is free text). They stay in the canonical record."
    )
    unknown_values: dict[str, int] = Field(
        description="UNKNOWN_VALUE: per schema field, the values outside a seeded PROVISIONAL "
        "vocabulary (instrument type, cash-flow type, origin). Still projected; the "
        "vocabulary reference stays empty."
    )
    canonical_only_fields: list[str] = Field(
        description="Schema fields with no typed column: they stay in the canonical record, "
        "reached through the row's canonical_record_id (lineage)."
    )
    missing_values: dict[str, int] | None = Field(
        default=None,
        description="Projector 2 only (the typed-maturity instrument contract, D-57): per "
        "counted field (`maturity_date`), the records without a value; the column is NULL. "
        "Absent for projector 1, whose summary is unchanged.",
    )
    term_rows: int | None = Field(
        default=None,
        description="Projector 3 only (the rate-terms instrument contract, D-61): the "
        "`instrument_term_version` rows written, one per version whose terms are consistent. "
        "Absent for projectors 1 and 2, whose summaries are unchanged.",
    )
    terms_inconsistent: int | None = Field(
        default=None,
        description="Projector 3 only: TERMS_INCONSISTENT, the records whose terms are "
        "inconsistent (FIXED with a reference index, FLOATING with no reset frequency, or "
        "floor > cap). Their instrument version is written; no term row is (D-61 §4). Those "
        "records' term values, unknown ones included, are not counted in `unknown_values` "
        "and stay in the canonical record.",
    )


class ProjectionOut(BaseModel):
    projection_id: uuid.UUID
    import_id: uuid.UUID
    import_status: ImportStatus
    schema_code: DomainSchemaCode
    schema_version_id: uuid.UUID
    schema_version: int
    projector_version: int = Field(description="The frozen field projection spec (D-29 §4).")
    accepted_count: int = Field(description="The import manifest's accepted rows.")
    projected_count: int = Field(description="Rows written; equals accepted_count.")
    summary: ProjectionSummary
    projected_at: datetime
    projected_by: str


class InstrumentTypeOut(BaseModel):
    code: str
    name: str
    provisional: bool = Field(description="A PROVISIONAL / illustrative seeded value (D-11).")


class InstrumentSummary(BaseModel):
    instrument_id: uuid.UUID
    business_key: Value = Field(description="The source's identifier of the instrument.")
    source_id: uuid.UUID
    source_code: str
    version_count: int
    latest_version: int = Field(
        description="The highest version number. 'Current' is not defined: a calculation "
        "pins versions through a dataset snapshot (D-29 §3)."
    )
    latest_import_id: uuid.UUID
    instrument_type: Value | None = Field(description="As given, in the latest version.")
    currency: Value | None = Field(description="ISO 4217, in the latest version.")
    created_at: datetime


class InstrumentPage(BaseModel):
    items: list[InstrumentSummary]
    total: int
    limit: int
    offset: int
    instrument_types: list[InstrumentTypeOut] = Field(
        description="The instrument type vocabulary of the caller's tenant (for filters)."
    )
    has_more: HasMore
    next_cursor: NextCursor


class AttributeOut(BaseModel):
    code: str = Field(description="The extension-eligible schema field.")
    data_type: FieldType
    classification: str = Field(description="The pinned schema field's classification.")
    value: Value = Field(description="The canonical typed text, or masked.")


class CashFlowOut(BaseModel):
    cashflow_event_id: uuid.UUID
    business_key: Value
    import_id: uuid.UUID
    canonical_record_id: uuid.UUID
    source_ordinal: int
    schema_version_id: uuid.UUID
    due_date: Value | None
    amount: Value | None = Field(description="Exact decimal text, in `currency`.")
    currency: Value | None
    cashflow_type: Value | None
    origin: Value | None


class InstrumentTermsOut(BaseModel):
    """D-61 §1-§4: the typed rate terms of one instrument version (its `instrument_term_version`
    row, written only when the terms are consistent). Values are masked by the classification
    of the schema field they were projected from. Vocabulary values not in the seeded
    PROVISIONAL vocabularies are kept as given text and named in `unknown_fields`."""

    term_version_id: uuid.UUID
    rate_type: Value | None = Field(description="FIXED or FLOATING (PROVISIONAL), or as given.")
    reference_index: Value | None = Field(
        description="As given: no reference index is seeded (D-61 §3)."
    )
    spread: Value | None = Field(description="Exact decimal text (11,6), in `rate_unit`.")
    reset_frequency: Value | None = Field(description="ISO-8601 period (P1M, P3M, P6M, P1Y).")
    next_reset_date: Value | None = Field(description="ISO date.")
    cap_rate: Value | None = Field(description="Exact decimal text (11,6), in `rate_unit`.")
    floor_rate: Value | None = Field(description="Exact decimal text (11,6), in `rate_unit`.")
    day_count: Value | None = Field(
        description="ACT_360, ACT_365F, 30_360 or ACT_ACT, or as given."
    )
    payment_frequency: Value | None = Field(description="ISO-8601 period, or as given.")
    lifecycle_state: Value | None = Field(
        description="ACTIVE, MATURED, PREPAID, RESTRUCTURED or DEFAULTED, or as given."
    )
    rate_unit: str | None = Field(
        description="The unit of `spread`, `cap_rate` and `floor_rate` from the pinned schema."
    )
    unknown_fields: list[str] = Field(
        description="Term fields whose value is not in the seeded vocabulary (kept as text, "
        "counted UNKNOWN_VALUE). Field names only, never values."
    )
    provisional: bool = Field(
        description="The term vocabularies are PROVISIONAL seeds (D-61 §3, D-11)."
    )
    created_at: datetime


class InstrumentVersionOut(BaseModel):
    version_id: uuid.UUID
    version: int
    import_id: uuid.UUID = Field(description="The import this version was projected from.")
    projection_id: uuid.UUID
    canonical_record_id: uuid.UUID = Field(
        description="Lineage: canonical record -> source record -> import."
    )
    source_record_id: uuid.UUID
    source_ordinal: int = Field(description="The source row's ordinal within its import.")
    schema_version_id: uuid.UUID
    schema_version: int | None
    payload_sha256: str = Field(
        description="The canonical payload's SHA-256, or the text 'masked' unless the caller "
        "is cleared for the most sensitive field of the pinned schema version (a hash of a "
        "small payload could otherwise be brute-forced to masked values)."
    )
    projected_at: datetime
    instrument_type: Value | None = Field(description="As given.")
    instrument_type_known: bool = Field(
        description="The type is in the seeded vocabulary (else modules warn, D-29 §5)."
    )
    currency: Value | None
    notional: Value | None = Field(description="Exact decimal text, in `currency`.")
    interest_rate: Value | None = Field(description="Exact decimal text, in `interest_rate_unit`.")
    interest_rate_unit: str | None
    maturity_date: Value | None = Field(
        description="ISO date; empty while the pinned field is free text (TYPE_NOT_PROJECTED)."
    )
    maturity_typed: bool = Field(
        description="The pinned schema version types `maturity_date` as DATE (the typed "
        "contract, projector v2, D-57). False: schema v1 free text, so `maturity_date` is "
        "empty and the value stays in the canonical record ('Not stated')."
    )
    effective_from: Value | None = Field(
        description="The business date the version is effective from (ISO date, D-61 §5), "
        "kept apart from `imported_at`; empty when the pinned contract has no such field. "
        "Null, too, for a version with no term row (TERMS_INCONSISTENT: `terms_typed` true, "
        "`terms` null), whose date is not stored: it is not an `?effective_on=` candidate, "
        "and the pick is then undetermined (`effective_version_id` null, fail closed)."
    )
    imported_at: datetime = Field(
        description="When the import this version came from was created (system time)."
    )
    terms_typed: bool = Field(
        description="The pinned schema version is the INSTRUMENT_RATE_TERMS contract "
        "(projector v3, D-61 §1)."
    )
    terms: InstrumentTermsOut | None = Field(
        description="The typed rate terms; null when the contract has none or the terms were "
        "inconsistent (TERMS_INCONSISTENT: FIXED with an index, FLOATING with no reset, or "
        "floor > cap)."
    )
    attributes: list[AttributeOut]
    cash_flows: list[CashFlowOut] = Field(
        description="Projected cash-flow events of the instrument's own source naming its "
        "business key, in the caller's tenant (matched by source and key, so every version "
        "lists the same events)."
    )


class InstrumentDetail(BaseModel):
    instrument_id: uuid.UUID
    business_key: Value
    source_id: uuid.UUID
    source_code: str
    created_at: datetime
    versions: list[InstrumentVersionOut] = Field(description="Oldest first.")
    effective_on: date | None = Field(
        description="The `?effective_on=` business date asked for, echoed; null without it."
    )
    effective_version_id: uuid.UUID | None = Field(
        description="With `?effective_on=`: the version with the latest `effective_from` on or "
        "before that date, picked by business date, not by import order (ties: the higher "
        "version). Only versions whose `effective_from` the caller is cleared for take part; "
        "a version with no term row (TERMS_INCONSISTENT) shows a null `effective_from` and is "
        "not a candidate. Null without the parameter, when no version is effective by then, "
        "or when the pick cannot be determined (fail closed): some version of the rate-terms "
        "contract (`terms_typed`) has no term row or an `effective_from` the caller is not "
        "cleared for, either of which could change the pick. This is a read aid only: runs "
        "pin snapshots (D-30) and no 'current' is defined (D-61 §5)."
    )


# --- Feature 04 credit reads (BE-19.5, D-37, D-43) -------------------------------------------
# Masking (D-43 §1): counterparty_name, drawn_amount, undrawn_amount and collateral_value are
# confidential; segment, grades, DPD, default flag, EIR, facility type, collateral type and
# business keys are internal; country and currency are public. Every value is masked by the
# classification of the schema field it was projected from, in the version the row pins.

IntValue = int | MaskedValue
BoolValue = bool | MaskedValue


class CreditVocabularyEntryOut(BaseModel):
    vocabulary: CreditVocabularyKind
    code: str = Field(description="Neutral code (grades G01..G08 and D; no agency symbols).")
    name: str
    rank: int | None = Field(
        description="rating_grade only: the order of the grade, 1 = best; else null."
    )
    is_default_grade: bool | None = Field(
        description="rating_grade only: the grade denotes default; else null."
    )
    provisional: bool = Field(description="A PROVISIONAL / illustrative seeded value (D-11).")


class CreditVocabularyList(BaseModel):
    items: list[CreditVocabularyEntryOut] = Field(
        description="The caller's tenant's credit vocabularies, by vocabulary then rank/code."
    )


class CounterpartySummary(BaseModel):
    counterparty_id: uuid.UUID
    business_key: Value = Field(description="The source's identifier of the counterparty.")
    source_id: uuid.UUID
    source_code: str
    version_count: int
    latest_version: int = Field(
        description="The highest version number. 'Current' is not defined: a calculation "
        "pins versions through a dataset snapshot (D-29 §3)."
    )
    latest_import_id: uuid.UUID
    counterparty_name: Value | None = Field(description="Confidential; in the latest version.")
    segment: Value | None = Field(description="As given, in the latest version.")
    segment_known: bool = Field(
        description="The segment is in the credit_segment vocabulary (else modules warn)."
    )
    country: Value | None = Field(description="ISO 3166-1 alpha-2, in the latest version.")
    created_at: datetime


class CounterpartyPage(BaseModel):
    items: list[CounterpartySummary]
    total: int
    limit: int
    offset: int
    has_more: HasMore
    next_cursor: NextCursor


class CounterpartyVersionOut(BaseModel):
    version_id: uuid.UUID
    version: int
    import_id: uuid.UUID = Field(description="The import this version was projected from.")
    projection_id: uuid.UUID
    canonical_record_id: uuid.UUID = Field(
        description="Lineage: canonical record -> source record -> import."
    )
    source_record_id: uuid.UUID
    source_ordinal: int = Field(description="The source row's ordinal within its import.")
    schema_version_id: uuid.UUID
    schema_version: int | None
    payload_sha256: str = Field(
        description="The canonical payload's SHA-256, or the text 'masked' unless the caller "
        "is cleared for the most sensitive field of the pinned schema version."
    )
    projected_at: datetime
    counterparty_name: Value | None = Field(description="Confidential (D-43 §1).")
    segment: Value | None = Field(description="As given.")
    segment_known: bool
    country: Value | None


class CreditExposureVersionOut(BaseModel):
    version_id: uuid.UUID
    version: int
    credit_exposure_id: uuid.UUID
    business_key: Value = Field(description="The source's exposure identifier.")
    import_id: uuid.UUID = Field(description="The import this version was projected from.")
    projection_id: uuid.UUID
    canonical_record_id: uuid.UUID = Field(
        description="Lineage: canonical record -> source record -> import."
    )
    source_record_id: uuid.UUID
    source_ordinal: int = Field(description="The source row's ordinal within its import.")
    schema_version_id: uuid.UUID
    schema_version: int | None
    payload_sha256: str = Field(
        description="The canonical payload's SHA-256, or the text 'masked' unless the caller "
        "is cleared for the most sensitive field of the pinned schema version."
    )
    projected_at: datetime
    counterparty_key: Value | None = Field(
        description="The counterparty's business key as given. It is resolved within a "
        "snapshot and the tenant (D-37 §4), not here."
    )
    instrument_key: Value | None = Field(description="Optional; not read by v1 modules.")
    as_of_date: Value | None = Field(description="ISO date.")
    currency: Value | None = Field(description="ISO 4217; the currency of every amount.")
    facility_type: Value | None = Field(description="As given.")
    facility_type_known: bool
    drawn_amount: Value | None = Field(description="Exact decimal text, in `currency`.")
    undrawn_amount: Value | None = Field(description="Exact decimal text, in `currency`.")
    days_past_due: IntValue | None
    default_flag: BoolValue | None
    origination_date: Value | None = Field(description="ISO date.")
    origination_grade: Value | None = Field(description="As given.")
    origination_grade_known: bool = Field(
        description="The grade is in the rating_grade vocabulary (else modules warn)."
    )
    maturity_date: Value | None = Field(description="ISO date.")
    effective_interest_rate: Value | None = Field(
        description="Exact decimal text, in `effective_interest_rate_unit`."
    )
    effective_interest_rate_unit: str | None = Field(
        description="The pinned schema field's unit (a fraction in credit_exposure v1)."
    )
    collateral_type: Value | None = Field(description="As given.")
    collateral_type_known: bool
    collateral_value: Value | None = Field(description="Exact decimal text, in `currency`.")


# --- Phase 20: the obligor rating workflow (BE-20.2, BE-20.3, D-39) -------------------------


class ObligorRatingCreate(BaseModel):
    """A proposal (D-39 §1): written IN_REVIEW. A correction is a new proposal."""

    model_config = ConfigDict(extra="forbid")

    grade: str = Field(
        min_length=1,
        max_length=50,
        description="A `rating_grade` code of the caller's tenant's credit vocabulary (GET "
        "/credit-vocabularies). Unknown: 422 invalid_grade.",
    )
    effective_from: date = Field(description="The first date the grade governs (ISO date).")
    rationale: SafeMultilineText = Field(min_length=1, max_length=RATING_RATIONALE_MAX)


class ObligorRatingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ProposalDecision = Field(
        description="APPROVE makes the version ACTIVE and any ACTIVE version for the same "
        "counterparty and `effective_from` SUPERSEDED, in one transaction; REJECT ends it."
    )
    comment: SafeMultilineText = Field(min_length=1, max_length=RATING_COMMENT_MAX)


class ObligorRatingOut(BaseModel):
    """One immutable rating version (D-39 §1-2). Content never changes; the decision fields
    are set once."""

    rating_id: uuid.UUID
    counterparty_id: uuid.UUID
    counterparty_key: Value = Field(
        description="The counterparty's business key, masked like the counterparty's own."
    )
    grade: Value = Field(description="A `rating_grade` vocabulary code (internal, D-43 §2).")
    grade_rank: int | None = Field(
        description="The grade's `rating_grade` rank (higher is worse); null if the code has "
        "no vocabulary entry or the grade is masked."
    )
    effective_from: date
    rationale: Value = Field(
        description="Confidential (D-43 §2): masked for a caller not cleared for it, the "
        "proposer included."
    )
    status: ObligorRatingStatus
    proposed_by: str = Field(description="The proposer's actor id.")
    proposer_user_id: uuid.UUID = Field(
        description="The proposer never decides their own proposal (409 self_approval)."
    )
    proposed_at: datetime
    decided_by: str | None = Field(description="Who approved or rejected it.")
    decided_at: datetime | None
    decision_comment: Value | None = Field(
        description="Confidential (D-43 §2): masked for a caller not cleared for it (masked "
        "even while undecided, so its absence is not revealed)."
    )
    superseded_at: datetime | None = Field(
        description="When a later approval for the same counterparty and date replaced it."
    )
    superseded_by_rating_id: uuid.UUID | None


class ObligorRatingPage(BaseModel):
    items: list[ObligorRatingOut] = Field(
        description="Newest proposal first (proposed_at descending, then rating_id)."
    )
    total: int
    limit: int
    offset: int
    has_more: HasMore
    next_cursor: NextCursor


class CounterpartyDetail(BaseModel):
    counterparty_id: uuid.UUID
    business_key: Value
    source_id: uuid.UUID
    source_code: str
    created_at: datetime
    versions: list[CounterpartyVersionOut] = Field(description="Oldest first.")
    exposure_versions: list[CreditExposureVersionOut] = Field(
        description="Projected credit-exposure versions naming this counterparty's business "
        "key, in the caller's tenant."
    )
    ratings: list[ObligorRatingOut] = Field(
        description="The rating history (D-39 §4): every version in every status, by "
        "effective_from descending, then proposed_at descending. Superseded and rejected "
        "versions are kept, never deleted."
    )


class CreditExposureSummary(BaseModel):
    credit_exposure_id: uuid.UUID
    business_key: Value
    source_id: uuid.UUID
    source_code: str
    version_count: int
    latest_version: int
    latest_import_id: uuid.UUID
    counterparty_key: Value | None = Field(description="In the latest version.")
    as_of_date: Value | None
    currency: Value | None
    facility_type: Value | None
    drawn_amount: Value | None = Field(description="Exact decimal text, in `currency`.")
    days_past_due: IntValue | None
    default_flag: BoolValue | None
    created_at: datetime


class CreditExposurePage(BaseModel):
    items: list[CreditExposureSummary]
    total: int
    limit: int
    offset: int
    has_more: HasMore
    next_cursor: NextCursor


class CreditExposureDetail(BaseModel):
    credit_exposure_id: uuid.UUID
    business_key: Value
    source_id: uuid.UUID
    source_code: str
    created_at: datetime
    versions: list[CreditExposureVersionOut] = Field(description="Oldest first.")


# --- Phase 31: legal entities and identity links (BE-31.4, BE-31.5, D-58) ------------------
# Links carry ids only (D-58 §7): the target's own read applies its classification.


class LegalEntityOut(BaseModel):
    legal_entity_id: uuid.UUID = Field(
        description="Stable per (tenant, source system, code) (D-58 §2)."
    )
    code: Value = Field(
        description="The source's legal entity code (positions' `legal_entity_ref`), masked "
        "by that schema field's classification (unknown: unclassified, fail closed)."
    )
    source_id: uuid.UUID
    source_code: str
    provisional: bool = Field(
        description="Always true: attributes and the source-of-truth rule are deferred to "
        "the client (D-58 §2, Data Model §11)."
    )
    created_at: datetime


class LegalEntityPage(BaseModel):
    items: list[LegalEntityOut] = Field(description="By code, then source.")
    total: int
    limit: int
    offset: int
    has_more: HasMore
    next_cursor: NextCursor


class LegalEntityDetail(LegalEntityOut):
    link_counts: dict[IdentitySourceKind, int] = Field(
        description="Links of the caller's tenant targeting this entity, per source kind."
    )


class IdentityLinkOut(BaseModel):
    link_id: uuid.UUID
    source_kind: IdentitySourceKind
    source_row_id: uuid.UUID = Field(
        description="The position_version, cashflow_event or credit_exposure_version row."
    )
    target_kind: IdentityTargetKind
    target_id: uuid.UUID = Field(
        description="The instrument, counterparty or legal entity id; read it through its "
        "own route, which applies its classification."
    )
    method: IdentityLinkMethod
    resolver_version: int
    created_by: str
    created_at: datetime


class IdentityLinkPage(BaseModel):
    items: list[IdentityLinkOut] = Field(
        description="Oldest first (created_at, then link_id). An empty page for a source row "
        "means its text key is unresolved or ambiguous ('Unresolved (text key)')."
    )
    total: int
    limit: int
    offset: int
    has_more: HasMore
    next_cursor: NextCursor


class IdentityLinkKindCount(BaseModel):
    """Value-free counts for one (source kind, target kind) pair."""

    source_kind: IdentitySourceKind
    target_kind: IdentityTargetKind
    rows: int = Field(description="Source rows of the tenant examined.")
    linked: int = Field(description="Rows with a link after the reconcile (new or existing).")
    links_written: int = Field(description="Links written by this call (0 on a repeat).")
    unresolved: int = Field(description="LINK_UNRESOLVED: no identity has the key; no link.")
    ambiguous: int = Field(
        description="LINK_AMBIGUOUS: several identities have the key (e.g. two source "
        "systems); no link written."
    )
    no_key: int = Field(description="Rows whose key field is empty; no link.")
    linked_now_ambiguous: int = Field(
        description="Of `linked`: rows whose key has since become ambiguous. The link is "
        "kept, never changed, and reported here (D-58 §5). "
        "rows = linked + unresolved + ambiguous + no_key."
    )


class IdentityLinkReconcileOut(BaseModel):
    """The result of POST /identity-links/reconcile (D-58 §4, §5). Idempotent: a repeat
    writes nothing and returns the same counts except `links_written` (0)."""

    resolver_version: int
    links_written: int
    counts: list[IdentityLinkKindCount] = Field(
        description="One entry per (source kind, target kind) pair, in a fixed order."
    )
    reconciled_at: datetime
    reconciled_by: str


# --- Phase 32: currency reference and FX consolidation status (BE-32.4, D-61 §6) -------------


class CurrencyOut(BaseModel):
    code: str = Field(description="ISO 4217 alphabetic code.")
    name: str
    minor_units: int | None = Field(description="ISO 4217 minor units (decimal places).")
    provisional: bool = Field(
        description="A tagged PROVISIONAL seed (`0031_currencies`, D-61 §6, D-11)."
    )


class CurrencyList(BaseModel):
    items: list[CurrencyOut] = Field(description="The caller's tenant's currencies, by code.")


class FxConsolidationStatusOut(BaseModel):
    """D-61 §6: FX consolidation is blocked. There is no conversion code and no FX write route;
    the rate source, rate types, reporting currency and conversion method are a client
    decision."""

    status: Literal["BLOCKED"] = Field(
        description="Always BLOCKED until a client-approved FX policy exists."
    )
    reason_code: Literal["fx_policy_not_approved"] = Field(
        description=f"`{FX_CONSOLIDATION_REASON}`: no approved rate source or conversion policy."
    )
    observation_count: int = Field(
        description="FX rate observations stored for the caller's tenant (append-only; none "
        "are used by any calculation)."
    )
