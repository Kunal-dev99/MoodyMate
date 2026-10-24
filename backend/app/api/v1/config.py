"""Served limits (BE-35.3). `GET /config/limits` is `authenticated`: any signed-in principal.

The rule, scenario and upload bounds the server's own validators enforce, so that a client's
pre-checks read them instead of mirroring literals (FE-35.3). Each value is the validator's
own object (rules: BE-16.1 / D-32 §1; scenarios: D-44 §1-4; uploads: D-16 §1, the settings'
`IMPORT_MAX_*`, which may only lower the hard ceilings). The server stays authoritative: a
client pre-check only saves a pointless request.

Not tenant data and not request-scoped: nothing here comes from a request parameter (D-04).
The route lives at the API root because it composes three modules' limits through their
services; no module, and not `app.core`, gains an import of another (BE-35.1). Ingestion's
`limits_from` comes from where it is defined (`ingestion.imports`, a part of the split service,
BE-35.2), not through the `service.py` re-export kept for one release.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import authenticated
from app.core.config import Settings
from app.core.principal import Principal
from app.ingestion.imports import limits_from
from app.orchestration.service import scenario_limits
from app.rules.service import rule_parameter_limits

router = APIRouter(tags=["platform"])


class RuleLimits(BaseModel):
    """The rule-parameter bounds BE-16.1 enforces on a proposed rule version (D-32 §1)."""

    model_config = ConfigDict(extra="forbid")

    max_buckets: int = Field(description="BUCKETS: at most this many buckets.")
    max_map_entries: int = Field(description="ENUM_MAP / DECIMAL_MAP: at most this many keys.")
    max_decimal_scale: int = Field(description="DECIMAL: at most this many decimal places.")
    max_days: int = Field(description="BUCKETS: the largest lower or upper bound, in days.")
    max_bucket_code_length: int = Field(description="BUCKETS: a bucket code's maximum length.")


class ScenarioLimits(BaseModel):
    """The scenario bounds the template validator and the write schemas enforce (D-44 §1-4)."""

    model_config = ConfigDict(extra="forbid")

    min_shocks: int = Field(description="A version carries at least this many shocks.")
    max_shocks: int = Field(description="A version carries at most this many shocks.")
    max_instrument_keys: int = Field(description="INSTRUMENT_KEYS: at most this many keys.")
    max_instrument_key_length: int = Field(description="An instrument key's maximum length.")
    fraction_scale: int = Field(description="DECIMAL fractions: at most this many places.")
    max_delay_days: int = Field(description="DELAYED_INFLOWS: the largest delay, in days.")
    max_unavailable_days: int = Field(
        description="FUNDING_UNAVAILABLE: the longest unavailability, in days."
    )
    max_name_length: int = Field(description="ScenarioCreate `name` maximum length.")
    max_description_length: int = Field(description="ScenarioCreate `description` maximum length.")
    max_change_summary_length: int = Field(description="`change_summary` maximum length.")
    max_comment_length: int = Field(description="Submit / decision / close `comment` maximum.")


class UploadLimits(BaseModel):
    """The upload bounds `POST imports` enforces (D-16 §1, D-25 §1): the deployment's
    `IMPORT_MAX_*` settings, each at most its hard ceiling. Field names are the adapter's."""

    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(description="The largest file, in bytes (413 above it).")
    max_rows: int = Field(description="At most this many data rows.")
    max_columns: int = Field(description="At most this many columns.")
    max_cell_chars: int = Field(description="A cell's maximum length, in characters.")
    max_xlsx_uncompressed_bytes: int = Field(
        description="XLSX: the largest total uncompressed size, in bytes."
    )
    max_xlsx_ratio: int = Field(description="XLSX: the largest compression ratio.")
    max_decoded_chars: int = Field(description="The largest total of decoded characters.")


class ConfigLimits(BaseModel):
    """`GET /config/limits`: the bounds the server's own validators enforce (BE-35.3)."""

    model_config = ConfigDict(extra="forbid")

    rule: RuleLimits
    scenario: ScenarioLimits
    upload: UploadLimits


@router.get(
    "/config/limits",
    response_model=ConfigLimits,
    operation_id="getConfigLimits",
    summary="The rule, scenario and upload limits the server enforces",
)
async def config_limits(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated())],
) -> ConfigLimits:
    # Each group is built field for field from the object its validator reads; the schemas
    # forbid unknown fields, so a bound added on either side without the other fails loudly.
    settings: Settings = request.app.state.settings
    return ConfigLimits(
        rule=RuleLimits(**asdict(rule_parameter_limits())),
        scenario=ScenarioLimits(**asdict(scenario_limits())),
        upload=UploadLimits(**asdict(limits_from(settings))),
    )
