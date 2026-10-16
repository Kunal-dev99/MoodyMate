"""ingestion.schemas

Request/response models for source registration (BE-7.4, DI-01).
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from app.core.text import SafeMultilineText, SafeText, single_line
from app.ingestion.constants import (
    ContractStatus,
    CsvDelimiter,
    SourceEncoding,
    SourceFormat,
    SourceKind,
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
