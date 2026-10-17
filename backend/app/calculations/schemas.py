"""calculations.schemas

Request and response models of dataset snapshots and calculation runs (BE-15.3 – BE-15.5,
D-30, D-31, D-33).

- Every amount is exact decimal text (`format(d, "f")`, never exponent form, never a JSON
  number). Money always comes with its currency.
- Aggregates (ladder rows) are `internal` and per-cash-flow contributions carry the most
  sensitive classification among the fields they read (D-33): a caller not cleared for a
  value gets `MaskedValue` in its place.
- Every run is labelled with the D-35 statement: an illustrative prototype, not a regulatory
  calculation, with no vendor parity claimed.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.calculations.constants import (
    CalculationRunStatus,
    CalculationWarningCode,
    InputRefType,
    IntermediateCode,
    PostCheck,
    WarningSeverity,
)
from app.core.classification import MaskedValue
from app.domain.constants import CashflowOrigin, DomainSchemaCode
from app.reconciliation.constants import ReconciliationOutcome
from app.rules.constants import CashflowDirection

Value = str | MaskedValue

# --- dataset snapshots (BE-15.3, D-30) ----------------------------------------------------------


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_ids: list[uuid.UUID] = Field(
        min_length=1,
        max_length=10,
        description="Projected imports, at most one per schema code (D-30 §1). The order does "
        "not matter: the same set always gives the same snapshot.",
    )

    @field_validator("import_ids")
    @classmethod
    def _distinct(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("each import may appear once")
        return value


class SnapshotMemberOut(BaseModel):
    import_id: uuid.UUID
    schema_code: DomainSchemaCode
    schema_version_id: uuid.UUID
    schema_version: int | None
    mapping_version_id: uuid.UUID
    import_manifest_id: uuid.UUID
    manifest_sha256: str = Field(description="The pinned import manifest's hash (D-27 §1).")
    projection_id: uuid.UUID
    projector_version: int
    source_id: uuid.UUID
    source_code: str
    accepted_count: int
    quarantined_count: int = Field(description="Held rows: carried into run manifests (D-30 §3).")
    rejected_count: int
    reconciliation_outcome: ReconciliationOutcome = Field(
        description="MATCHED or REVIEW_REQUIRED: a BROKEN import is refused (D-28, D-30 §3)."
    )


class SnapshotOut(BaseModel):
    snapshot_id: uuid.UUID
    content_sha256: str = Field(
        description="SHA-256 of the canonical JSON of the members' (schema_code, import_id, "
        "manifest_sha256), sorted by schema code: the snapshot's identity (D-30 §2)."
    )
    member_count: int
    members: list[SnapshotMemberOut] = Field(description="By schema code.")
    created_at: datetime
    created_by: str


class SnapshotPage(BaseModel):
    items: list[SnapshotOut] = Field(description="Newest first.")
    total: int
    limit: int
    offset: int


# --- calculation runs (BE-15.4, BE-15.5, D-31) -------------------------------------------------


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_snapshot_id: uuid.UUID
    module_version_id: uuid.UUID = Field(
        description="An AVAILABLE module version from GET /calculation-modules."
    )
    reporting_date: date = Field(description="Buckets count calendar days after this date.")


class RunRerun(BaseModel):
    """A re-run (D-31 §5, BE-16.3): a new run on exactly the original run's pins (snapshot,
    reporting date, module and engine versions, rule set and assumption set), even when those
    rule versions are SUPERSEDED. It reproduces the original's manifest_sha256 and
    result_sha256."""

    model_config = ConfigDict(extra="forbid")

    rerun_of: uuid.UUID = Field(
        description="The run to re-run (404 alike for an absent and a foreign id). 409 "
        "rerun_not_reproducible when the engine or manifest version changed since."
    )


class RunModuleRef(BaseModel):
    module_id: uuid.UUID
    module_version_id: uuid.UUID
    code: str
    name: str
    version: int


class RunSummary(BaseModel):
    run_id: uuid.UUID
    status: CalculationRunStatus
    reporting_date: date
    module: RunModuleRef
    dataset_snapshot_id: uuid.UUID
    rule_set_id: uuid.UUID
    assumption_set_id: uuid.UUID
    engine_version: str
    manifest_sha256: str = Field(
        description="SHA-256 of the canonical JSON of `manifest`: the pinned content only, never "
        "the run id, actor or times, so a run on the same pins has the same hash (D-31 §4)."
    )
    result_sha256: str | None = Field(
        description="SHA-256 of the canonical serialisation of every output (ladder rows, "
        "contributions, warnings); set when the run ends. Equal across runs on the same pins."
    )
    warning_count: int
    rerun_of_run_id: uuid.UUID | None = Field(
        description="Set on a re-run (BE-16.3): the run whose pins it repeats."
    )
    started_at: datetime
    ended_at: datetime | None
    created_by: str
    illustrative: bool = Field(description="Always true in Feature 03 (D-35).")


class RunPage(BaseModel):
    items: list[RunSummary] = Field(description="Newest first.")
    total: int
    limit: int
    offset: int


class ManifestModule(BaseModel):
    module_id: uuid.UUID
    module_version_id: uuid.UUID
    code: str
    version: int
    engine_version: str = Field(description="The engine version the module version declares.")


class ManifestMember(BaseModel):
    schema_code: DomainSchemaCode
    import_id: uuid.UUID
    import_manifest_id: uuid.UUID
    manifest_sha256: str
    projection_id: uuid.UUID
    projector_version: int
    schema_version_id: uuid.UUID
    schema_version: int | None
    mapping_version_id: uuid.UUID
    accepted_count: int
    quarantined_count: int
    rejected_count: int
    reconciliation_outcome: ReconciliationOutcome


class ManifestSnapshot(BaseModel):
    dataset_snapshot_id: uuid.UUID
    content_sha256: str
    members: list[ManifestMember] = Field(description="By schema code.")


class ManifestRuleVersion(BaseModel):
    rule_id: uuid.UUID
    rule_code: str
    rule_version_id: uuid.UUID
    version: int
    parameters_sha256: str
    parameters: dict[str, Any]


class ManifestRuleSet(BaseModel):
    rule_set_id: uuid.UUID
    content_sha256: str = Field(description="SHA-256 of the sorted rule version ids (D-32 §4).")
    members: list[ManifestRuleVersion] = Field(description="By rule code.")


class ManifestAssumptionSet(BaseModel):
    assumption_set_id: uuid.UUID
    code: str
    version: int
    content_sha256: str


class ManifestLabels(BaseModel):
    illustrative: bool
    provisional: bool
    statement: str


class RunManifest(BaseModel):
    """Every version a run pinned (D-31 §4), written once with the run. Statuses, times and
    the actor are not part of it (they are on the run)."""

    manifest_version: int
    engine_version: str
    reporting_date: date
    module: ManifestModule
    dataset_snapshot: ManifestSnapshot
    rule_set: ManifestRuleSet
    assumption_set: ManifestAssumptionSet
    labels: ManifestLabels


class InputRefOut(BaseModel):
    sequence: int
    ref_type: InputRefType
    ref_id: uuid.UUID | None
    label: str = Field(description="What the pin is, e.g. `instrument v1` or an engine version.")
    sha256: str | None = Field(description="The pinned content's hash, where it has one.")


class WarningOut(BaseModel):
    sequence: int
    code: CalculationWarningCode
    severity: WarningSeverity
    message: str = Field(description="Value-free.")
    entity_type: str | None = Field(description="e.g. `cashflow_event`.")
    entity_id: uuid.UUID | None
    details: dict[str, Any] = Field(description="Value-free: codes, counts and field names.")


class PostCheckOut(BaseModel):
    check: PostCheck
    passed: bool


class RunOutcome(BaseModel):
    """Value-free counts of a finished run."""

    inputs: dict[str, int] = Field(description="Rows read per schema code.")
    in_scope: int = Field(description="Cash flows in scope: every row of the cash-flow member.")
    bucketed: int
    excluded: int
    warnings_by_code: dict[str, int]
    currencies: list[str]
    post_checks: list[PostCheckOut]


class RunDetail(RunSummary):
    manifest: RunManifest
    input_refs: list[InputRefOut] = Field(description="One row per pin (D-31 §4).")
    warnings: list[WarningOut]
    outcome: RunOutcome | None = Field(description="Set when the run ends.")
    correlation_id: str | None
    statement: str = Field(description="The D-35 label: not regulatory, no vendor parity.")


# --- results and the maturity ladder (BE-15.5) --------------------------------------------------


class ContributionLineage(BaseModel):
    """cash-flow event -> canonical record -> source row -> import (D-31 §6)."""

    cashflow_event_id: uuid.UUID
    cashflow_business_key: Value
    due_date: Value
    instrument_id: uuid.UUID | None
    instrument_version_id: uuid.UUID | None
    instrument_business_key: Value | None
    canonical_record_id: uuid.UUID
    import_id: uuid.UUID
    source_record_id: uuid.UUID
    source_ordinal: int


class ResultItem(BaseModel):
    intermediate_value_id: uuid.UUID
    sequence: int
    code: IntermediateCode
    value: Value = Field(description="Exact decimal text in `unit`, or masked (D-33).")
    unit: str = Field(description="The ISO 4217 currency of the value.")
    classification: str = Field(description="The most sensitive classification it reads.")
    currency: str
    bucket_code: str
    bucket_sequence: int
    direction: CashflowDirection
    origin: CashflowOrigin
    days_after_reporting_date: int | MaskedValue
    liquidity_gap_result_id: uuid.UUID
    rule_version_ids: list[uuid.UUID] = Field(description="The rule versions that placed it.")
    lineage: ContributionLineage


class ResultPage(BaseModel):
    run_id: uuid.UUID
    status: CalculationRunStatus
    items: list[ResultItem] = Field(description="In run order.")
    total: int
    limit: int
    offset: int
    statement: str


class BucketOut(BaseModel):
    bucket_definition_id: uuid.UUID
    sequence: int
    code: str
    label: str
    lower_exclusive_days: int
    upper_inclusive_days: int | None = Field(description="None: the open last bucket.")


class LadderRow(BaseModel):
    liquidity_gap_result_id: uuid.UUID
    bucket_code: str
    bucket_sequence: int
    bucket_label: str
    classification: str = Field(
        description="`internal`: an aggregate (D-33). A figure built from fewer than "
        "`min_aggregate_count` contributions (but at least one), and every figure derived from "
        "it, takes the contributions' classification and is masked for a role not cleared "
        "for it."
    )
    inflows: Value
    outflows: Value
    net_gap: Value = Field(description="inflows - outflows.")
    cumulative_gap: Value = Field(
        description="Running sum of net_gap over the buckets so far. Once a net gap of the "
        "currency is masked, every later cumulative gap is masked, even when it is exactly "
        "zero (D-33: masked wins over 'zeros stay clear')."
    )
    inflows_contractual: Value
    inflows_behavioural: Value
    outflows_contractual: Value
    outflows_behavioural: Value
    contribution_count: int


class LadderCurrency(BaseModel):
    currency: str
    rows: list[LadderRow] = Field(description="Every bucket, in bucket order.")


class MaturityLadderOut(BaseModel):
    run_id: uuid.UUID
    status: CalculationRunStatus
    reporting_date: date
    module: RunModuleRef
    bucket_rule_version_id: uuid.UUID
    buckets: list[BucketOut]
    currencies: list[LadderCurrency] = Field(
        description="Per currency, no FX (D-35). Empty unless the run SUCCEEDED (with or "
        "without warnings)."
    )
    min_aggregate_count: int = Field(
        description="D-33 minimum aggregation (CALC_MIN_AGGREGATE_COUNT, PROVISIONAL): a "
        "figure from 1 to this many minus one contributions is masked like them."
    )
    illustrative: bool
    statement: str
