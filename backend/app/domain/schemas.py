"""domain.schemas

Response models of the WS3 domain API (BE-14.3, BE-14.4). Amounts and rates are exact
decimal text and dates ISO-8601 text, never JSON numbers. Every domain value is masked by
the classification of the schema field it was projected from, in the schema version the
row pins (BE-3.5), so a caller not cleared for it gets `MaskedValue`.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.classification import MaskedValue
from app.domain.constants import DomainSchemaCode
from app.governance.constants import FieldType
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
