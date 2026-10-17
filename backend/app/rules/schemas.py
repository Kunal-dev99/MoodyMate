"""rules.schemas

Response models of the registry reads of Phase 15 (BE-15.5, FC-07): `GET /calculation-modules`
lists every module version of the caller's tenant with its declared contracts and, for the
"New run" panel, exactly what a run submitted now would pin (D-31 §4): the ACTIVE version of
every rule the module reads, the ACTIVE baseline assumption set and the engine version.

Every seeded row is PROVISIONAL and ILLUSTRATIVE (D-11, D-35); `statement` carries the D-35
label. Parameters are typed data from the closed kinds (D-32 §1); a decimal parameter is
exact decimal text.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.calculations.constants import CalculationRunStatus
from app.core.text import SafeMultilineText
from app.governance.constants import ProposalDecision
from app.rules.constants import ImplementationStatus, RuleCategory, RuleVersionStatus


class SchemaRequirement(BaseModel):
    schema_code: str = Field(description="A canonical schema a snapshot member provides.")
    required: bool = Field(description="Missing from the snapshot: the run is BLOCKED.")
    fields: list[str] = Field(description="The schema fields the module reads.")


class ModuleInputContract(BaseModel):
    """What a module reads (D-31 §1)."""

    schemas: list[SchemaRequirement]
    rules: list[str] = Field(description="The rule definition codes the module reads.")
    assumption_keys: list[str] = Field(description="The assumption keys it reads (none in v1).")
    parameters: list[str] = Field(description="Run parameters, e.g. `reporting_date`.")


class ModuleOutputContract(BaseModel):
    """What a module writes (D-31 §1)."""

    results: list[str] = Field(description="Result tables.")
    intermediates: list[str] = Field(description="Intermediate value codes.")
    warning_codes: list[str] = Field(description="Warning codes it may raise.")


class RuleVersionPin(BaseModel):
    """The ACTIVE version of a rule: what a run submitted now pins."""

    rule_version_id: uuid.UUID
    version: int
    status: RuleVersionStatus
    parameters: dict[str, Any] = Field(description="Typed by the definition's parameter schema.")
    parameters_sha256: str
    effective_from: date | None
    authored_by: str
    approved_by: str | None = Field(description="The approver, or the seed tag (D-32 §7).")
    approved_at: datetime | None
    provisional: bool
    illustrative: bool


class ModuleRuleOut(BaseModel):
    rule_id: uuid.UUID
    code: str
    name: str
    category: RuleCategory
    owner: str
    parameter_schema: dict[str, Any] = Field(description="A closed parameter kind (D-32 §1).")
    description: str | None
    active_version: RuleVersionPin | None = Field(
        description="None: the rule has no ACTIVE version, so the module cannot run."
    )


class AssumptionSetPin(BaseModel):
    """The ACTIVE version of the baseline assumption set (D-32 §6): every run pins one."""

    assumption_set_id: uuid.UUID
    code: str
    name: str
    version: int
    status: RuleVersionStatus
    owner: str
    provenance: str
    effective_from: date | None
    effective_to: date | None
    assumptions: dict[str, Any] = Field(description="Empty in v1 (no module reads one yet).")
    content_sha256: str
    provisional: bool
    illustrative: bool


class ModuleVersionOut(BaseModel):
    module_id: uuid.UUID
    module_version_id: uuid.UUID
    code: str
    name: str
    description: str | None
    version: int
    implementation_status: ImplementationStatus
    runnable: bool = Field(
        description="AVAILABLE, implemented in this release, every rule it reads has an ACTIVE "
        "version and a baseline assumption set is ACTIVE. Otherwise POST /calculation-runs "
        "answers 409 module_not_available."
    )
    engine_version: str = Field(description="The engine a run of this version pins.")
    input_contract: ModuleInputContract
    output_contract: ModuleOutputContract
    rules: list[ModuleRuleOut] = Field(description="The rules the module reads, in contract order.")
    assumption_set: AssumptionSetPin | None
    provisional: bool
    illustrative: bool
    statement: str = Field(description="The D-35 label: not regulatory, no vendor parity.")
    created_at: datetime


class ModulePage(BaseModel):
    items: list[ModuleVersionOut] = Field(description="By module code, then version.")
    total: int


# --- Phase 16: rules, governed rule changes and assumption sets (BE-16.1 - BE-16.4) ----------


class RuleVersionCreate(BaseModel):
    """A new DRAFT: a copy of the rule's ACTIVE version's parameters with this patch applied."""

    model_config = ConfigDict(extra="forbid")

    parameters_patch: dict[str, Any] = Field(
        description="A JSON merge patch (RFC 7386) over the ACTIVE version's parameters: an "
        "object merges key by key, `null` removes a key (e.g. a map entry), anything else "
        "replaces (e.g. the whole `buckets` list). The result must fit the rule's parameter "
        "kind (D-32 §1): DECIMAL values are strings of decimal text, never JSON numbers. "
        "Otherwise 422 invalid_parameters (value-free details); an unchanged result is 422 "
        "no_change."
    )
    change_summary: SafeMultilineText = Field(min_length=1, max_length=1000)


class RuleVersionSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: SafeMultilineText | None = Field(default=None, max_length=1000)


class RuleVersionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ProposalDecision = Field(
        description="APPROVE activates the version and supersedes the previous ACTIVE one, in "
        "one transaction; REJECT ends it."
    )
    comment: SafeMultilineText = Field(min_length=1, max_length=1000)


class RuleModuleRef(BaseModel):
    module_id: uuid.UUID
    code: str
    name: str


class PinningRun(BaseModel):
    """A calculation run that pins a rule version (through its rule set)."""

    run_id: uuid.UUID
    status: CalculationRunStatus
    reporting_date: date
    rerun_of_run_id: uuid.UUID | None
    started_at: datetime


class RuleVersionOut(BaseModel):
    rule_version_id: uuid.UUID
    rule_id: uuid.UUID
    rule_code: str
    version: int
    status: RuleVersionStatus
    parameters: dict[str, Any] = Field(
        description="Typed by the rule's parameter schema (D-32 §1). Decimals are exact "
        "decimal text at the declared scale, never JSON numbers."
    )
    parameters_sha256: str = Field(description="SHA-256 of the canonical JSON of `parameters`.")
    change_summary: str | None
    authored_by: str = Field(description="The author's actor id, or the seed tag (D-32 §7).")
    author_user_id: uuid.UUID | None = Field(
        description="The author's user id (None for a seeded version): an author never "
        "decides on their own version (409 self_approval)."
    )
    approved_by: str | None = Field(description="Who activated it (set once it was ACTIVE).")
    approved_at: datetime | None
    decided_by: str | None = Field(description="Who approved or rejected it.")
    decided_at: datetime | None
    decision_comment: str | None
    superseded_at: datetime | None
    effective_from: date | None
    effective_to: date | None
    provisional: bool
    illustrative: bool
    seeded: bool
    created_at: datetime
    run_count: int = Field(description="Runs that pin this version.")
    runs: list[PinningRun] = Field(description="The newest pinning runs, at most 20.")


class RuleSummary(BaseModel):
    rule_id: uuid.UUID
    code: str
    name: str
    category: RuleCategory
    owner: str
    description: str | None
    target_module: RuleModuleRef
    parameter_schema: dict[str, Any] = Field(
        description="One closed parameter kind (D-32 §1): `kind` is DECIMAL (min, max, scale), "
        "INTEGER (min, max), ENUM (values), DAY_BUCKETS, ENUM_MAP (key_vocabulary, values) or "
        "DECIMAL_MAP (key_vocabulary, min, max, scale); an optional `unit`."
    )
    active_version: int | None
    active_rule_version_id: uuid.UUID | None
    version_count: int
    provisional: bool
    illustrative: bool


class RulePage(BaseModel):
    items: list[RuleSummary] = Field(description="By rule code.")
    total: int


class RuleDetail(RuleSummary):
    versions: list[RuleVersionOut] = Field(description="Every version, by version number.")
    vocabulary: list[str] = Field(
        description="The codes of the parameter schema's key vocabulary (map kinds), else []."
    )
    change_enabled: bool = Field(description="RULE_CHANGE_ENABLED: false means 409 on writes.")
    statement: str = Field(description="The D-35 label: not regulatory, no vendor parity.")
