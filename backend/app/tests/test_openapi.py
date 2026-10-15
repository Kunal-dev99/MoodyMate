import json

from openapi_spec_validator import validate

from scripts.export_openapi import OUT, render


def test_exported_contract_is_valid_openapi_31() -> None:
    spec = json.loads(render())
    assert spec["openapi"].startswith("3.1")
    validate(spec)


def test_committed_contract_matches_code() -> None:
    assert OUT.read_text() == render(), "run `python -m scripts.export_openapi`"


def test_error_envelope_is_in_contract() -> None:
    spec = json.loads(render())
    assert "ErrorEnvelope" in spec["components"]["schemas"]
    health = spec["paths"]["/api/v1/health"]["get"]["responses"]
    assert {"4XX", "5XX"} <= set(health)
