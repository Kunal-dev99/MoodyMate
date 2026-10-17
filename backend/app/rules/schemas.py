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

from pydantic import BaseModel, Field

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
