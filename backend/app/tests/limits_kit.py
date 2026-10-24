"""BE-35.3: `GET /config/limits` serves the validators' own bound objects (test helpers).

Called from the WS1 matrix fold (test_ws1_verification.py), which also proves the route is
200 for every seeded role and 401 without a token. Here:

* the identity check: every served value `is` the object the validator itself reads (the
  rule, scenario and upload bounds), not an equal literal kept beside it (FE-35.3 reads
  these instead of mirroring literals); each group's field set is its bounds object's, and
  the services hand out the constants modules' own bounds objects;
* the bucket code length bound is the one the rule validator enforces (at it: accepted; one
  more: invalid_code);
* a deployment that lowers `IMPORT_MAX_ROWS` serves the lowered value.
"""

import asyncio
import dataclasses
from pathlib import Path
from typing import Any, cast

from annotated_types import MaxLen
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request

import app.orchestration.constants as scenario_bounds
import app.rules.constants as rule_bounds
from app.core.config import IMPORT_HARD_LIMITS, Settings
from app.core.routes import served_routes
from app.ingestion.adapters.base import Limits
from app.main import create_app
from app.orchestration.schemas import ScenarioCreate, ScenarioVersionCreate, ScenarioVersionDecision
from app.orchestration.service import scenario_limits
from app.rules.constants import ParameterKind, ParameterProblem
from app.rules.service import check_parameters, rule_parameter_limits

CONFIG_LIMITS = "/api/v1/config/limits"
UPLOAD_SETTINGS = {
    field.name: f"IMPORT_{field.name.upper()}" for field in dataclasses.fields(Limits)
}
RULE_FIELDS = {f.name for f in dataclasses.fields(rule_bounds.RuleParameterLimits)}
SCENARIO_FIELDS = {f.name for f in dataclasses.fields(scenario_bounds.ScenarioBounds)}


def _max_length(model: type[BaseModel], field: str) -> Any:
    """The `max_length` object a write schema's own field validator enforces (its `MaxLen`;
    a `StringConstraints` of the field's type carries `max_length=None`)."""
    [bound] = [m.max_length for m in model.model_fields[field].metadata if isinstance(m, MaxLen)]
    return bound


def _served_in_process(client: TestClient) -> Any:
    """The route's own return value (no JSON round trip), so `is` compares objects. Found as
    served (core.routes): FastAPI keeps an included router as one entry in `app.routes`."""
    [served] = [
        s for s in served_routes(client.app) if s.path == CONFIG_LIMITS and "GET" in s.methods
    ]
    assert isinstance(served.route, APIRoute), served.route
    scope = {"type": "http", "app": client.app, "headers": [], "method": "GET"}
    return asyncio.run(served.route.endpoint(request=Request(scope), principal=None))


def _bucket_code_refused(length: int) -> bool:
    """Whether the rule validator refuses a one-bucket DAY_BUCKETS whose code is this long."""
    bucket = {"code": "B" * length, "lower_exclusive": -1, "upper_inclusive": None}
    _, problems = check_parameters(
        {"kind": ParameterKind.DAY_BUCKETS.value}, {"buckets": [bucket]}, frozenset()
    )
    codes = {(p.path, p.code) for p in problems}
    assert codes <= {("buckets[0].code", ParameterProblem.INVALID_CODE)}, codes
    return bool(codes)


def check_config_limits(
    api: TestClient, token: dict[str, str], db: str, artifact_dir: Path
) -> None:
    settings: Settings = cast(FastAPI, api.app).state.settings
    r = api.get(CONFIG_LIMITS, headers=token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"rule", "scenario", "upload"}, body

    served = _served_in_process(api)
    # The services hand out the constants modules' own bounds objects (one object each).
    assert rule_parameter_limits() is rule_bounds.RULE_PARAMETER_LIMITS
    assert scenario_limits() is scenario_bounds.SCENARIO_BOUNDS
    # Rules (BE-16.1, D-32 §1): the objects the parameter validator reads.
    rule_objects = {
        "max_buckets": rule_bounds.MAX_BUCKETS,
        "max_map_entries": rule_bounds.MAX_MAP_ENTRIES,
        "max_decimal_scale": rule_bounds.MAX_DECIMAL_SCALE,
        "max_days": rule_bounds.MAX_DAYS,
        "max_bucket_code_length": rule_bounds.MAX_BUCKET_CODE_LENGTH,
    }
    assert set(body["rule"]) == set(type(served.rule).model_fields) == RULE_FIELDS
    assert set(rule_objects) == RULE_FIELDS
    for field, bound in rule_objects.items():
        assert getattr(served.rule, field) is bound, ("rule", field)
        assert getattr(rule_bounds.RULE_PARAMETER_LIMITS, field) is bound, ("rule", field)
        assert body["rule"][field] == bound, ("rule", field)
    # The bucket code bound is the validator's: a code at it passes, one more is refused.
    longest = served.rule.max_bucket_code_length
    assert not _bucket_code_refused(longest) and _bucket_code_refused(longest + 1)
    # Scenarios (D-44 §1-4): the template validator's and the write schemas' own objects.
    scenario_objects = {
        "min_shocks": scenario_bounds.MIN_SHOCKS,
        "max_shocks": scenario_bounds.MAX_SHOCKS,
        "max_instrument_keys": scenario_bounds.MAX_INSTRUMENT_KEYS,
        "max_instrument_key_length": scenario_bounds.MAX_TEXT_FIELD,
        "fraction_scale": scenario_bounds.FRACTION_SCALE,
        "max_delay_days": scenario_bounds.MAX_DELAY_DAYS,
        "max_unavailable_days": scenario_bounds.MAX_UNAVAILABLE_DAYS,
        "max_name_length": _max_length(ScenarioCreate, "name"),
        "max_description_length": _max_length(ScenarioCreate, "description"),
        "max_change_summary_length": _max_length(ScenarioVersionCreate, "change_summary"),
        "max_comment_length": _max_length(ScenarioVersionDecision, "comment"),
    }
    assert set(body["scenario"]) == set(type(served.scenario).model_fields) == SCENARIO_FIELDS
    assert set(scenario_objects) == SCENARIO_FIELDS
    for field, bound in scenario_objects.items():
        assert getattr(served.scenario, field) is bound, ("scenario", field)
        assert getattr(scenario_bounds.SCENARIO_BOUNDS, field) is bound, ("scenario", field)
        assert body["scenario"][field] == bound, ("scenario", field)
    # Uploads (D-16 §1): this deployment's IMPORT_MAX_* settings objects, field for field.
    assert set(body["upload"]) == set(type(served.upload).model_fields) == set(UPLOAD_SETTINGS)
    for field, setting in UPLOAD_SETTINGS.items():
        assert getattr(served.upload, field) is getattr(settings, setting), ("upload", field)
        assert body["upload"][field] == IMPORT_HARD_LIMITS[setting], ("upload", field)

    # Lowering IMPORT_MAX_ROWS changes the response (and nothing else in it).
    lowered = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, IMPORT_MAX_ROWS=7
    )
    with TestClient(create_app(lowered)) as client:
        r = client.get(CONFIG_LIMITS, headers=token)
        assert r.status_code == 200, r.text
        assert r.json()["upload"]["max_rows"] == 7
        assert r.json() == {**body, "upload": {**body["upload"], "max_rows": 7}}
        assert (
            _served_in_process(client).upload.max_rows
            is cast(FastAPI, client.app).state.settings.IMPORT_MAX_ROWS
        )
