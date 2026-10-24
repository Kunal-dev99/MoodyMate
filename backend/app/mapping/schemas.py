"""mapping.schemas

Request/response models for the mapping workbench (BE-10.3 – BE-10.5). A rule's transform is
a discriminated union on `code`: the D-19 allowlist, each with its own parameter JSON Schema
(strict: unknown keys and wrong types are refused with 422).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.classification import MaskedValue
from app.core.pagination import HasMore, NextCursor
from app.core.text import SafeMultilineText, SafeText
from app.governance.constants import FieldType, ProposalDecision
from app.mapping.constants import (
    MAX_RULES,
    ColumnPolicy,
    DriftStatus,
    DriftType,
    IssueCode,
    IssueSeverity,
    MappingVersionStatus,
    NullPolicy,
    TransformCode,
    ValidationOutcome,
)
from app.mapping.transforms import (
    MapValuesParams,
    NoParams,
    ParseBooleanParams,
    ParseDateParams,
    ParseDecimalParams,
    ScaleParams,
    ValueParams,
)

CODE = r"^[a-z][a-z0-9_]{0,99}$"
ColumnName = Annotated[SafeText, Field(max_length=200)]
FieldName = Annotated[SafeText, Field(max_length=100)]


class CopyTransform(BaseModel):
    code: Literal[TransformCode.COPY]
    params: NoParams = Field(default_factory=NoParams)


class TrimTransform(BaseModel):
    code: Literal[TransformCode.TRIM]
    params: NoParams = Field(default_factory=NoParams)


class UpperTransform(BaseModel):
    code: Literal[TransformCode.UPPER]
    params: NoParams = Field(default_factory=NoParams)


class LowerTransform(BaseModel):
    code: Literal[TransformCode.LOWER]
    params: NoParams = Field(default_factory=NoParams)


class ParseDecimalTransform(BaseModel):
    code: Literal[TransformCode.PARSE_DECIMAL]
    params: ParseDecimalParams = Field(default_factory=ParseDecimalParams)


class ScaleTransform(BaseModel):
    code: Literal[TransformCode.SCALE]
    params: ScaleParams


class ParseDateTransform(BaseModel):
    code: Literal[TransformCode.PARSE_DATE]
    params: ParseDateParams


class ParseBooleanTransform(BaseModel):
    code: Literal[TransformCode.PARSE_BOOLEAN]
    params: ParseBooleanParams


class MapValuesTransform(BaseModel):
    code: Literal[TransformCode.MAP_VALUES]
    params: MapValuesParams


class DefaultTransform(BaseModel):
    code: Literal[TransformCode.DEFAULT]
    params: ValueParams


class ConstantTransform(BaseModel):
    code: Literal[TransformCode.CONSTANT]
    params: ValueParams


TransformIn = Annotated[
    CopyTransform
    | TrimTransform
    | UpperTransform
    | LowerTransform
    | ParseDecimalTransform
    | ScaleTransform
    | ParseDateTransform
    | ParseBooleanTransform
    | MapValuesTransform
    | DefaultTransform
    | ConstantTransform,
    Field(discriminator="code"),
]


class RuleIn(BaseModel):
    """One rule. MAP: `source_column` (none for CONSTANT) → `target_field` through
    `transform`. PRESERVE: `source_column` is kept as it is, with no target and COPY."""

    source_column: ColumnName | None = Field(
        default=None, description="Exactly as in the file header; absent only for CONSTANT."
    )
    target_field: FieldName | None = Field(
        default=None, description="A field of the pinned schema version; absent for PRESERVE."
    )
    column_policy: ColumnPolicy = ColumnPolicy.MAP
    transform: TransformIn = Field(default_factory=lambda: CopyTransform(code=TransformCode.COPY))
    null_policy: NullPolicy = NullPolicy.ALLOW_NULL
    default_value: Annotated[SafeText, Field(max_length=200)] | None = Field(
        default=None, description="Required with USE_DEFAULT (and only then)."
    )

    @model_validator(mode="after")
    def _shape(self) -> "RuleIn":
        code = self.transform.code
        if self.column_policy == ColumnPolicy.PRESERVE:
            if self.target_field is not None or self.source_column is None or code != "COPY":
                raise ValueError("PRESERVE keeps a source column: no target, transform COPY")
        elif self.target_field is None:
            raise ValueError("MAP needs a target_field")
        if (self.source_column is None) != (code == TransformCode.CONSTANT):
            raise ValueError("source_column is required, except for CONSTANT, which reads none")
        if (self.null_policy == NullPolicy.USE_DEFAULT) != (self.default_value is not None):
            raise ValueError("default_value goes with null_policy USE_DEFAULT, and only with it")
        return self


class MappingVersionIn(BaseModel):
    """The next DRAFT version: its rules, pinned to the target's ACTIVE schema version."""

    rules: list[RuleIn] = Field(default_factory=list, max_length=MAX_RULES)
    change_summary: SafeMultilineText | None = Field(default=None, max_length=1000)


class MappingCreate(MappingVersionIn):
    """A mapping for one source contract, with DRAFT version 1."""

    code: str = Field(pattern=CODE, description="Unique in your tenant; lower_snake_case.")
    name: Annotated[SafeText, Field(max_length=200)]
    description: SafeMultilineText | None = Field(default=None, max_length=1000)
    source_contract_id: uuid.UUID = Field(description="The (current) contract it maps.")
    schema_id: uuid.UUID | None = Field(
        default=None, description="Must be the contract's target schema; defaults to it."
    )


class SubmitIn(BaseModel):
    comment: SafeMultilineText | None = Field(default=None, max_length=2000)


class ApprovalIn(BaseModel):
    decision: ProposalDecision
    comment: SafeMultilineText | None = Field(
        default=None, max_length=2000, description="Required to REJECT."
    )


class ValidateIn(BaseModel):
    mapping_version_id: uuid.UUID


# --- responses -----------------------------------------------------------------------------


class TransformOut(BaseModel):
    code: TransformCode
    params: dict[str, Any]


class RuleOut(BaseModel):
    position: int
    source_column: str | None
    target_field: str | None
    column_policy: ColumnPolicy
    transform: TransformOut
    null_policy: NullPolicy
    default_value: str | None


class ValidationRef(BaseModel):
    id: uuid.UUID
    import_id: uuid.UUID
    outcome: ValidationOutcome
    ok: bool
    mapping_sha256: str
    created_at: datetime


class MappingVersionOut(BaseModel):
    id: uuid.UUID
    mapping_id: uuid.UUID
    version: int
    status: MappingVersionStatus
    schema_version_id: uuid.UUID
    schema_version: int | None = Field(description="The pinned schema version's number.")
    content_sha256: str
    author: str
    author_user_id: uuid.UUID
    change_summary: str | None
    submitted_at: datetime | None
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None
    last_validation: ValidationRef | None
    rules: list[RuleOut]
    created_at: datetime


class MappingSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    source_id: uuid.UUID
    source_code: str
    source_contract_id: uuid.UUID
    contract_version: int | None
    schema_id: uuid.UUID
    schema_code: str
    active_version: int | None = Field(description="The ACTIVE version's number, if any.")
    latest_version: int
    version_count: int
    created_by: str
    created_at: datetime


class MappingDetail(MappingSummary):
    versions: list[MappingVersionOut] = Field(description="Oldest first.")


class MappingPage(BaseModel):
    items: list[MappingSummary]
    total: int
    limit: int
    offset: int
    has_more: HasMore
    next_cursor: NextCursor


class ValidationIssue(BaseModel):
    """What is wrong, where (rule, field, column) and how often. Never a source value."""

    severity: IssueSeverity
    code: IssueCode
    message: str
    rule_position: int | None = None
    target_field: str | None = None
    source_column: str | None = None
    count: int | None = Field(default=None, description="Rows affected, for row-level issues.")
    reasons: dict[str, int] | None = Field(
        default=None, description="Failure reason code -> rows, for transform_failed."
    )


class RuleResult(BaseModel):
    """One rule over every row: counts only."""

    position: int
    source_column: str | None
    target_field: str | None
    target_type: FieldType | None
    column_policy: ColumnPolicy
    transform: TransformCode
    rows: int
    mapped: int = Field(description="Rows with a value for the target.")
    empty: int = Field(description="Rows left empty (allowed by the null policy).")
    failed: int
    failures: dict[str, int] = Field(description="Failure reason code -> rows.")


class MappingValidationOut(BaseModel):
    id: uuid.UUID
    mapping_id: uuid.UUID
    mapping_version_id: uuid.UUID
    version: int
    import_id: uuid.UUID
    mapping_sha256: str
    outcome: ValidationOutcome
    ok: bool = Field(description="OK or WARNINGS: the version may be submitted.")
    row_count: int
    blocker_count: int
    error_count: int
    warning_count: int
    issues: list[ValidationIssue]
    rule_results: list[RuleResult]
    created_by: str
    created_at: datetime


# --- Phase 12: drift events (BE-12.3, BE-12.4) ---------------------------------------------------


class DriftSummary(BaseModel):
    """A column's profile statistics on one side of a drift event. `min` and `max` are data,
    masked by the event's classification; everything else is metadata."""

    position: int | None
    value_count: int | None
    null_count: int | None
    null_rate: str | None = Field(description="Decimal text (4 places), never a number.")
    distinct_count: int | None
    candidate_types: list[str]
    suggested_type: str | None
    min: str | MaskedValue | None
    max: str | MaskedValue | None


class ProposalLink(BaseModel):
    """Where a person proposes a schema change (the Phase 5 page, target schema prefilled).
    A hint only: nothing was created."""

    kind: Literal["SCHEMA_CHANGE_PROPOSAL"] = "SCHEMA_CHANGE_PROPOSAL"
    href: str = Field(description="Frontend route, e.g. /schemas/proposals/new?schema={id}.")
    schema_id: uuid.UUID


class DriftEventOut(BaseModel):
    id: uuid.UUID
    import_id: uuid.UUID
    sequence: int
    baseline_import_id: uuid.UUID = Field(
        description="The latest COMPLETED or COMPLETED_WITH_WARNINGS import of the same source."
    )
    drift_type: DriftType
    column: str
    classification: str = Field(description="Masks the summaries' min and max (BE-9.3 rule).")
    observed: DriftSummary | None = Field(description="This import's column (none if removed).")
    baseline: DriftSummary | None = Field(description="The baseline's column (none if added).")
    thresholds: dict[str, Any] = Field(description="PROVISIONAL thresholds applied (text).")
    detector_version: int
    status: DriftStatus
    allowed_outcomes: list[DriftStatus] = Field(description="Review outcomes still possible.")
    reviewed_by: str | None
    review_comment: str | None
    reviewed_at: datetime | None
    proposal_link: ProposalLink | None = Field(
        description="Set when the review is ACTION_REQUIRED: a link hint, nothing created."
    )
    created_at: datetime


class DriftPage(BaseModel):
    import_id: uuid.UUID
    baseline_import_id: uuid.UUID | None = Field(
        description="The baseline the events compare with (none when there are no events)."
    )
    items: list[DriftEventOut]
    total: int
    status_counts: dict[str, int]


class DriftReviewIn(BaseModel):
    outcome: Literal[DriftStatus.ACKNOWLEDGED, DriftStatus.ACTION_REQUIRED] = Field(
        description="ACKNOWLEDGED, or ACTION_REQUIRED (a schema change should be proposed)."
    )
    comment: SafeMultilineText = Field(max_length=1000, description="Required.")
