"""Phase 19 (Feature 04, D-37): the synthetic counterparty and credit-exposure samples through
the unchanged WS2 pipeline (a CSV source and contract of the file's columns, a mapping of every
column made ACTIVE, the import validated) and projected. Shared by `test_credit.py` and the WS1
matrix and isolation tests."""

from typing import Any

from fastapi.testclient import TestClient

from app.ingestion.adapters import AdapterOptions, Limits, parse_upload
from app.ingestion.constants import SourceFormat
from app.tests.helpers import (
    MAPPINGS,
    SOURCES,
    activate_version,
    contract_body,
    project,
    sample,
    sample_rules,
    source_body,
    upload,
    validate_import,
)

COUNTERPARTIES = "/api/v1/counterparties"
EXPOSURES = "/api/v1/credit-exposures"
VOCABULARIES = "/api/v1/credit-vocabularies"
CREDIT_SAMPLES = {"counterparty": "counterparty.csv", "credit_exposure": "credit_exposure.csv"}
CREDIT_KEYS = {"counterparty": ["counterparty_id"], "credit_exposure": ["exposure_id"]}


def csv_columns(data: bytes) -> list[str]:
    return list(parse_upload(data, SourceFormat.CSV, AdapterOptions(), Limits()).columns)


def credit_source(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    code: str,
    columns: list[str],
    *,
    source_code: str | None = None,
    key: list[str] | None = None,
) -> dict[str, Any]:
    """A CSV source whose contract is these columns of `code` (counterparty or
    credit_exposure), keyed by `key` (default: the schema's business key)."""
    body = source_body(
        db,
        code=source_code or f"{code}_feed",
        profile={
            "format": "CSV",
            "owner": "Credit data team",
            "default_classification": "confidential",
        },
        contract=contract_body(
            db,
            schema=code,
            expected_columns=[{"name": c, "required": True} for c in columns],
            business_key_columns=key or CREDIT_KEYS[code],
            control_total_columns=[],
        ),
    )
    r = api.post(SOURCES, json=body, headers=steward)
    assert r.status_code == 201, r.text
    return dict(r.json())


def validated_credit(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    approver: dict[str, str],
    code: str,
    data: bytes | None = None,
    *,
    source_code: str | None = None,
    key: list[str] | None = None,
) -> dict[str, Any]:
    """The credit sample of `code` (or `data`) on its own new source, mapped (every column to
    the same-named v1 field) and validated: COMPLETED. Returns the validate response."""
    data = sample(CREDIT_SAMPLES[code]) if data is None else data
    columns = csv_columns(data)
    source = credit_source(api, db, steward, code, columns, source_code=source_code, key=key)
    r = upload(api, steward, source["id"], data, filename=f"{code}.csv")
    assert r.status_code == 201, r.text
    imported = r.json()
    mapping = {
        "code": f"{source['code']}_map",
        "name": f"{source['code']} (test)",
        "source_contract_id": source["current_contract"]["id"],
        "rules": sample_rules(api, db, steward, code, columns),
    }
    r = api.post(MAPPINGS, json=mapping, headers=steward)
    assert r.status_code == 201, r.text
    activate_version(api, steward, approver, r.json()["versions"][0], imported["id"])
    r = validate_import(api, steward, imported["id"])
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
    return dict(r.json())


def projected_credit(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> dict[str, str]:
    """Both credit samples validated and projected: their import ids, one counterparty id and
    one credit-exposure id."""
    out: dict[str, str] = {}
    for code in ("counterparty", "credit_exposure"):
        imported = validated_credit(api, db, steward, approver, code)
        r = project(api, steward, imported["id"])
        assert r.status_code == 201, r.text
        out[f"{code}_import_id"] = imported["id"]
    for name, path, field in (
        ("counterparty_id", COUNTERPARTIES, "counterparty_id"),
        ("exposure_id", EXPOSURES, "credit_exposure_id"),
    ):
        page = api.get(path, headers=steward)
        assert page.status_code == 200 and page.json()["items"], page.text
        out[name] = str(page.json()["items"][0][field])
    return out
