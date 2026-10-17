"""rules.assumptions_schemas

Response bodies of the read-only assumption-set routes (BE-16.4, D-32 §6). Assumption
values are never returned: only their keys and the content hash a run manifest pins.
"""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.rules.constants import RuleVersionStatus


class AssumptionSetVersionSummary(BaseModel):
    assumption_set_id: uuid.UUID
    version: int
    status: RuleVersionStatus
    effective_from: date | None
    effective_to: date | None
    approved_at: datetime | None


class AssumptionSetItem(BaseModel):
    assumption_set_id: uuid.UUID = Field(description="The id a run manifest pins.")
    code: str
    name: str
    version: int
    status: RuleVersionStatus
    owner: str
    provenance: str
    effective_from: date | None
    effective_to: date | None
    authored_by: str
    approved_by: str | None
    approved_at: datetime | None
    assumption_keys: list[str] = Field(description="Keys only, never values (empty in v1).")
    content_sha256: str
    provisional: bool
    illustrative: bool
    created_at: datetime


class AssumptionSetList(BaseModel):
    items: list[AssumptionSetItem] = Field(description="By code, then version.")
    total: int


class AssumptionSetDetailOut(AssumptionSetItem):
    versions: list[AssumptionSetVersionSummary] = Field(
        description="Every version of the same code, by version number."
    )
