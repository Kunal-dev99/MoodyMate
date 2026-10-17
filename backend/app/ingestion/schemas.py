"""ingestion.schemas

Request/response models for source registration (BE-7.4, DI-01) and imports (BE-8.4, DI-02).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from app.core.classification import MaskedValue
from app.core.text import SafeMultilineText, SafeText, single_line
from app.governance.constants import FieldType
from app.ingestion.constants import (
    ContractStatus,
    CsvDelimiter,
    DataClass,
    ImportStatus,
    IssueDisposition,
    QuarantineStatus,
    RetentionState,
    SourceEncoding,
    SourceFormat,
    SourceKind,
    ValidationCode,
    ValidationSeverity,
)

CODE = r"^[a-z][a-z0-9_]{0,99}$"
# A source column name as it appears in the file header: visible text on one line, bounded
# (D8: no control characters, so no NUL, tab or line break).
ColumnName = Annotated[SafeText, Field(max_length=200)]
# An XLSX sheet name: safe one-line text without the characters Excel forbids (D8). The
# pattern sits in the string constraints so the OpenAPI contract shows it.
SHEET_NAME = r"^[^\[\]:*?/\\]+$"
SheetName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=31, pattern=SHEET_NAME),
    AfterValidator(single_line),
]


class ContractColumn(BaseModel):
    name: ColumnName = Field(description="Column name exactly as in the file header.")
    required: bool = Field(description="Explicit: an omitted value is never assumed.")


class ContractIn(BaseModel):
    """A source contract version: the expected shape of the files, and the canonical
    schema they feed. Becomes PROVISIONAL until the client confirms it."""

    schema_id: uuid.UUID = Field(
        description="Target canonical schema; must exist in your tenant with an ACTIVE version."
    )
    expected_columns: list[ContractColumn] = Field(min_length=1, max_length=200)
    business_key_columns: list[ColumnName] = Field(
        min_length=1, max_length=20, description="Columns that identify a row; all expected."
    )
    control_total_columns: list[ColumnName] = Field(
        default_factory=list, max_length=20, description="Columns summed for reconciliation."
    )
    change_summary: SafeMultilineText | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _columns(self) -> "ContractIn":
        names = [c.name for c in self.expected_columns]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate expected columns: {', '.join(duplicates)}")
        for label, cols in (
            ("business_key_columns", self.business_key_columns),
            ("control_total_columns", self.control_total_columns),
        ):
            if len(set(cols)) != len(cols):
                raise ValueError(f"{label} has duplicates")
            unknown = sorted(set(cols) - set(names))
            if unknown:
                raise ValueError(f"{label} not among expected columns: {', '.join(unknown)}")
        return self


class ProfileIn(BaseModel):
    """A new source's profile. Always provisional on create (D2): only the client's approval,
    a later action, clears it. The API ignores unknown body fields, so a `provisional` sent
    here has no effect."""

    format: SourceFormat
    encoding: SourceEncoding = SourceEncoding.UTF_8
    delimiter: CsvDelimiter | None = Field(
        default=None, description="CSV only; defaults to ',' for CSV."
    )
    sheet_name: SheetName | None = Field(
        default=None,
        description="XLSX only; else the first sheet. Excel forbids [ ] : * ? / \\.",
    )
    cadence: SafeText | None = Field(default=None, max_length=100, description="e.g. 'daily'.")
    owner: SafeText = Field(max_length=200)
    default_classification: str = Field(
        min_length=1,
        max_length=50,
        description="Classification of values in columns no mapping has classified (D-20).",
    )

    @model_validator(mode="after")
    def _format_options(self) -> "ProfileIn":
        if self.delimiter is not None and self.format != SourceFormat.CSV:
            raise ValueError("delimiter applies to CSV only")
        if self.sheet_name is not None and self.format != SourceFormat.XLSX:
            raise ValueError("sheet_name applies to XLSX only")
        return self


class SourceCreate(BaseModel):
    code: str = Field(pattern=CODE, description="snake_case, unique in your tenant.")
    name: SafeText = Field(max_length=200)
    description: SafeMultilineText | None = Field(default=None, max_length=1000)
    kind: SourceKind = Field(
        default=SourceKind.FILE_DROP, description="Only FILE_DROP is usable in the MVP (D-23)."
    )
    profile: ProfileIn
    contract: ContractIn


class ContractOut(BaseModel):
    id: uuid.UUID
    version: int
    status: ContractStatus
    schema_id: uuid.UUID
    schema_code: str
    expected_columns: list[ContractColumn]
    business_key_columns: list[str]
    control_total_columns: list[str]
    content_sha256: str
    supersedes_id: uuid.UUID | None
    change_summary: str | None
    created_by: str
    created_at: datetime


class ProfileOut(BaseModel):
    id: uuid.UUID
    format: SourceFormat
    encoding: SourceEncoding
    delimiter: CsvDelimiter | None
    sheet_name: str | None
    cadence: str | None
    owner: str
    default_classification: str
    provisional: bool


class SourceSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    kind: SourceKind
    provisional: bool = Field(description="True until the client approves the source profile.")
    profile: ProfileOut
    current_contract: ContractOut | None = Field(
        description="The one contract version that is not SUPERSEDED."
    )
    contract_count: int
    created_by: str
    created_at: datetime


class SourceDetail(SourceSummary):
    contracts: list[ContractOut] = Field(description="Every contract version, oldest first.")


class SourcePage(BaseModel):
    items: list[SourceSummary]
    total: int
    limit: int
    offset: int


# --- Phase 8: imports (BE-8.4) ------------------------------------------------------------------

# A control total as exact decimal text (never a float): optional sign, up to 28 digits
# before and 18 after the point.
DECIMAL_TEXT = r"^-?\d{1,28}(\.\d{1,18})?$"
DecimalText = Annotated[str, StringConstraints(pattern=DECIMAL_TEXT)]


class ImportForm(BaseModel):
    """The non-file fields of a POST /imports multipart body, as validated."""

    source_id: uuid.UUID
    synthetic_attested: bool = False
    expected_row_count: int | None = Field(default=None, ge=0, le=1_000_000_000)
    control_totals: dict[ColumnName, DecimalText] | None = Field(default=None, max_length=20)
    retry_of_import_id: uuid.UUID | None = None


class ArtifactOut(BaseModel):
    sha256: str = Field(description="SHA-256 of the uploaded bytes (hex).")
    size_bytes: int
    detected_mime: str = Field(description="Detected from content, not from the file name.")
    original_filename: str | None
    classification: str = Field(description="The source profile's default classification.")
    retention_state: RetentionState


class BatchOut(BaseModel):
    id: uuid.UUID = Field(description="The batch id (GET /quarantine/{batch_id}).")
    batch_number: int
    sheet_name: str | None
    row_count: int
    column_count: int


class ImportSummary(BaseModel):
    id: uuid.UUID
    status: ImportStatus
    source_id: uuid.UUID
    source_code: str
    format: SourceFormat
    row_count: int = Field(description="Data rows read from the file (one source_record each).")
    expected_row_count: int | None
    control_totals: dict[str, str | MaskedValue] | None = Field(
        description="Declared control totals (decimal text), masked by the artifact's "
        "classification."
    )
    data_class: DataClass = Field(description="The environment's data class at upload (D-14).")
    synthetic_attested: bool
    synthetic_marker: bool = Field(
        description="The file carried the ILLUSTRATIVE / SYNTHETIC marker (D-24)."
    )
    source_contract_id: uuid.UUID
    contract_version: int | None
    schema_version_id: uuid.UUID = Field(description="The ACTIVE schema version at upload.")
    schema_code: str | None
    schema_version: int | None
    mapping_version_id: uuid.UUID | None = Field(
        default=None,
        description="The mapping version the import is validated with (D-18 §5); empty "
        "until validation (Phase 11).",
    )
    artifact: ArtifactOut
    retry_of_import_id: uuid.UUID | None
    correlation_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ManifestProfile(BaseModel):
    id: uuid.UUID
    code: str
    version: int
    content_sha256: str
    provisional: bool


class ManifestArtifact(BaseModel):
    sha256: str = Field(description="SHA-256 of the raw artifact (hex).")
    size_bytes: int
    classification: str


class ManifestQuality(BaseModel):
    """accepted + quarantined + rejected = row_count, always (D-20 §1, DB CHECK)."""

    row_count: int
    accepted_records: int
    quarantined_records: int
    rejected_records: int
    warnings: int = Field(description="WARNING-severity issues (file- and row-level).")


class ImportManifest(BaseModel):
    """What validation pinned and produced (BE-11.5, Data Model §10). Written once with the
    outcome and never changed: this is what a calculation run pins (D-22)."""

    manifest_id: uuid.UUID
    manifest_sha256: str = Field(description="SHA-256 of the manifest content.")
    manifest_version: int
    validated_at: datetime
    validated_by: str
    import_id: uuid.UUID
    status: ImportStatus = Field(description="The import's outcome status.")
    source_id: uuid.UUID
    source_code: str
    source_profile_id: uuid.UUID
    source_contract_id: uuid.UUID
    source_contract_version: int
    source_contract_sha256: str
    schema_id: uuid.UUID
    schema_code: str
    schema_version_id: uuid.UUID
    schema_version: int
    schema_sha256: str
    mapping_id: uuid.UUID
    mapping_code: str
    mapping_version_id: uuid.UUID
    mapping_version: int
    mapping_sha256: str
    validation_profile: ManifestProfile
    validator_version: int
    artifact: ManifestArtifact
    quality: ManifestQuality
    canonical_sha256: str = Field(
        description="SHA-256 over the accepted records' payload hashes in file order."
    )


class ImportDetail(ImportSummary):
    batches: list[BatchOut]
    manifest: ImportManifest | None = Field(
        default=None, description="Present once the import is validated (BE-11.5)."
    )


class ImportPage(BaseModel):
    items: list[ImportSummary]
    total: int
    limit: int
    offset: int


# --- Phase 9: profiles (BE-9.3) -------------------------------------------------------------


class TopValue(BaseModel):
    value: str | MaskedValue = Field(
        description="The value (first 100 characters), or masked by the column's " "classification."
    )
    count: int = Field(description="Rows holding the value.")
    truncated: bool = Field(description="The value is longer than shown.")


class ProfileSummary(BaseModel):
    """Row facts (D-25): nothing is dropped and nothing is invented."""

    blank_rows: int
    rows_with_missing_cells: int
    rows_with_extra_cells: int = Field(description="Rows with cells beyond the header.")
    max_extra_cells: int
    flag_counts: dict[str, int] = Field(description="Row flag code -> how many were raised.")


class ColumnProfile(BaseModel):
    """Observations of one source column. `min`, `max` and top values are data, masked by
    `classification`; everything else is metadata. `in_contract`, `suggested_target_field`
    and the candidate types are suggestions (`suggestion: true`): nothing is changed."""

    name: str = Field(description="Column name exactly as in the file header.")
    position: int = Field(description="1-based position in the header.")
    classification: str = Field(
        description="The mapped schema field's classification, else the source profile's "
        "default (no mapping exists before Phase 10)."
    )
    value_count: int = Field(description="Non-empty values.")
    null_count: int = Field(description="Empty: missing cell, JSON null, '' or only spaces.")
    null_rate: str | None = Field(
        description="null_count / rows as decimal text, 4 places (half-even); null when the "
        "import has no rows."
    )
    distinct_count: int = Field(description="Distinct non-empty values, up to the cap.")
    distinct_capped: bool = Field(description="More distinct values exist than were counted.")
    candidate_types: list[FieldType] = Field(
        description="Types every non-empty value fits, most specific first."
    )
    suggested_type: FieldType | None = Field(description="The first candidate type.")
    min: str | MaskedValue | None = Field(description="Smallest number, date or timestamp.")
    max: str | MaskedValue | None = Field(description="Largest number, date or timestamp.")
    max_length: int | None = Field(description="Longest value, in characters.")
    max_precision: int | None = Field(
        description="NUMERIC precision that holds every value (numeric columns)."
    )
    max_scale: int | None = Field(description="Most decimal places (numeric columns).")
    date_formats: dict[str, int] = Field(description="Date format -> values valid in it.")
    currency_pattern_hits: dict[str, int] = Field(
        description="ISO_4217_CODE (three capitals) and AMOUNT_WITH_CURRENCY counts."
    )
    top_values: list[TopValue] = Field(description="Most frequent first, then by value.")
    top_values_exact: bool = Field(description="False when the distinct cap was reached.")
    in_contract: bool = Field(description="The pinned source contract expects this column.")
    suggested_target_field: str | None = Field(
        description="The pinned schema version's field whose normalised name equals this "
        "column's, if exactly one does."
    )
    suggestion: Literal[True] = Field(
        default=True,
        description="in_contract, suggested_target_field and the types are suggestions only.",
    )


class ImportProfile(BaseModel):
    import_id: uuid.UUID
    import_status: ImportStatus
    snapshot_id: uuid.UUID
    profiler_version: int
    content_sha256: str = Field(
        description="SHA-256 of the observations: the same records give the same hash."
    )
    profiled_at: datetime
    row_count: int
    column_count: int
    source_contract_id: uuid.UUID
    contract_version: int
    schema_version_id: uuid.UUID = Field(description="The schema version the import pins.")
    schema_id: uuid.UUID = Field(description="The target schema (the contract's).")
    schema_code: str
    schema_version: int
    summary: ProfileSummary
    columns: list[ColumnProfile] = Field(description="In file order.")


# --- Phase 11: validation issues and quarantine (BE-11.3, BE-11.4) ----------------------------


class IssueOut(BaseModel):
    """A validation finding. It names a row, field, column and rule; never a value."""

    id: uuid.UUID
    sequence: int = Field(description="Order in which validation raised it.")
    row: int | None = Field(
        description="1-based data row (source record ordinal); null for " "a file-level issue."
    )
    source_record_id: uuid.UUID | None
    code: ValidationCode
    severity: ValidationSeverity
    disposition: IssueDisposition
    field: str | None = Field(description="The schema field concerned, if any.")
    source_column: str | None = Field(description="The source column concerned, if any.")
    rule_position: int | None = Field(description="The mapping rule's position, if any.")
    validation_profile_id: uuid.UUID
    message: str = Field(description="Actionable text; never contains a value.")


class IssuePage(BaseModel):
    import_id: uuid.UUID
    import_status: ImportStatus
    items: list[IssueOut]
    total: int
    limit: int
    offset: int
    severity_counts: dict[str, int] = Field(
        description="Severity -> issues of this import (unfiltered)."
    )


QuarantineCell = str | MaskedValue


class QuarantineItemOut(BaseModel):
    id: uuid.UUID
    import_id: uuid.UUID
    batch_id: uuid.UUID
    source_record_id: uuid.UUID
    row: int = Field(description="1-based data row (source record ordinal).")
    locator: dict[str, str | int] = Field(description="Where the row is in the file.")
    reason_codes: list[ValidationCode] = Field(description="Why the row is held.")
    status: QuarantineStatus
    owner: str
    resolved_by_import_id: uuid.UUID | None
    values: dict[str, QuarantineCell] = Field(
        description="The row as read (never edited), masked by each column's classification."
    )
    extra_values: list[QuarantineCell] = Field(
        description="Cells beyond the header, masked by the source's default classification."
    )
    allowed_dispositions: list[QuarantineStatus] = Field(
        description="The statuses a reviewer may move this item to now."
    )
    created_at: datetime
    updated_at: datetime


class QuarantineBatch(BaseModel):
    batch_id: uuid.UUID
    batch_number: int
    import_id: uuid.UUID
    import_status: ImportStatus
    source_id: uuid.UUID
    columns: list[str] = Field(description="The batch's header, in file order.")
    column_classifications: dict[str, str]
    items: list[QuarantineItemOut]
    total: int
    limit: int
    offset: int
    status_counts: dict[str, int] = Field(description="Status -> items of this batch.")


class DispositionIn(BaseModel):
    status: QuarantineStatus = Field(
        description="OPEN->UNDER_REVIEW; UNDER_REVIEW->REJECTED | RETRY_READY | OPEN. Other "
        "moves are 409 invalid_transition (CORRECTED and RESOLVED come from a retry import)."
    )
    comment: SafeMultilineText | None = Field(default=None, max_length=1000)
