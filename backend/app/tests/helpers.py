"""Domain helpers shared by the DB tests: tokens for custom users, schema/source bodies,
uploads and the audit/record lookups the import tests assert on."""

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import Settings
from app.core.tokens import DevTokens
from app.governance.constants import DEFAULT_TENANT_ID, DEV_ISSUER
from app.tests.conftest import insert_user, sql

IMPORTS = "/api/v1/imports"
SOURCES = "/api/v1/sources"
PROPOSALS = "/api/v1/schema-change-proposals"
MIB = 1024 * 1024
SAMPLES = {"CSV": "instrument.csv", "XLSX": "position.xlsx", "JSON": "cashflow_event.json"}
SCHEMAS = {"CSV": "instrument", "XLSX": "position", "JSON": "cashflow_event"}
IMPORT_TABLES = ("import_job", "import_artifact", "import_batch", "source_record")
FIELD_KEYS = (
    "name",
    "data_type",
    "required",
    "unit",
    "precision",
    "scale",
    "classification",
    "extension_eligible",
)


def cid() -> str:
    return f"test-{uuid.uuid4().hex[:10]}"


def events(db: str, correlation_id: str) -> list[Any]:
    return sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", correlation_id)


def token_for(
    db: str, subject: str, roles: tuple[str, ...], tenant_id: Any = DEFAULT_TENANT_ID
) -> dict[str, str]:
    """A dev token for a user with exactly these roles (dev personas hold one role each)."""
    insert_user(db, DEV_ISSUER, subject, roles=roles, tenant_id=tenant_id)
    settings = Settings(ENV="test")
    token = DevTokens(settings.AUTH_DEV_SECRET, settings.AUTH_DEV_TOKEN_TTL_MINUTES).issue(
        subject, f"{subject}@example.test", subject
    )
    return {"Authorization": f"Bearer {token}"}


# --- schema registry and change proposals ----------------------------------------------------


def schema_id(db: str, code: str) -> Any:
    return sql(db, "SELECT id FROM schema_definition WHERE code = $1", code)[0]["id"]


def version_id(db: str, code: str, version: int) -> Any:
    return sql(
        db,
        "SELECT v.id FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id"
        " WHERE d.code = $1 AND v.version = $2",
        code,
        version,
    )[0]["id"]


def v1_fields(api: TestClient, headers: dict[str, str], sid: Any) -> list[dict[str, Any]]:
    versions = api.get(f"/api/v1/schemas/{sid}/versions", headers=headers).json()["items"]
    [v1] = [v for v in versions if v["version"] == 1]
    return [{k: f[k] for k in FIELD_KEYS} for f in v1["fields"]]


def removal_proposal(api: TestClient, db: str, headers: dict[str, str]) -> Any:
    """Instrument v1 minus `counterparty_id`: a BREAKING change, as a DRAFT proposal."""
    sid = schema_id(db, "instrument")
    fields = [f for f in v1_fields(api, headers, sid) if f["name"] != "counterparty_id"]
    body = {"schema_id": str(sid), "title": "Drop counterparty_id", "fields": fields}
    r = api.post(PROPOSALS, json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- sources -----------------------------------------------------------------------------------


def contract_body(db: str, schema: str = "instrument", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_id": str(schema_id(db, schema)),
        "expected_columns": [
            {"name": "Instrument ID", "required": True},
            {"name": "Type", "required": True},
            {"name": "Ccy", "required": True},
            {"name": "Notional", "required": True},
        ],
        "business_key_columns": ["Instrument ID"],
        "control_total_columns": ["Notional"],
        "change_summary": "First cut from the sample file.",
    }
    return {**body, **overrides}


def source_body(db: str, code: str = "treasury_feed", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "name": "Treasury feed (test)",
        "description": "Synthetic test source.",
        "profile": {
            "format": "CSV",
            "cadence": "daily",
            "owner": "Treasury data team",
            "default_classification": "confidential",
        },
        "contract": contract_body(db),
    }
    return {**body, **overrides}


def seeded_source_id(db: str) -> str:
    return str(sql(db, "SELECT id FROM source_system WHERE code = 'core_banking'")[0]["id"])


def source_for(api: TestClient, db: str, steward: dict[str, str], fmt: str) -> str:
    """The seeded CSV source, or a new XLSX/JSON source (profile + contract v1)."""
    if fmt == "CSV":
        return seeded_source_id(db)
    body = source_body(
        db,
        code=f"{fmt.lower()}_feed",
        profile={
            "format": fmt,
            "owner": "Test data team",
            "default_classification": "confidential",
        },
        contract=contract_body(db, schema=SCHEMAS[fmt]),
    )
    r = api.post(SOURCES, json=body, headers=steward)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# --- imports -----------------------------------------------------------------------------------


def upload(
    api: TestClient,
    headers: dict[str, str],
    source_id: str,
    data: bytes,
    *,
    filename: str = "upload.bin",
    key: str | None = None,
    attested: bool | None = True,
    cid: str | None = None,
    **fields: str,
) -> Response:
    form: dict[str, str] = {"source_id": source_id, **fields}
    if attested is not None:
        form["synthetic_attested"] = "true" if attested else "false"
    extra = {"Idempotency-Key": key or uuid.uuid4().hex}
    if cid:
        extra["X-Correlation-ID"] = cid
    return api.post(
        IMPORTS,
        data=form,
        files={"file": (filename, data, "application/octet-stream")},
        headers={**headers, **extra},
    )


def counts(db: str) -> dict[str, int]:
    return {t: sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in IMPORT_TABLES}


def stored_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def assert_denied(db: str, correlation_id: str, reason: str, action: str = "import.create") -> None:
    [event] = events(db, correlation_id)
    assert (event["outcome"], event["action"]) == ("DENIED", action), dict(event)
    assert json.loads(event["details"])["reason"] == reason


# --- Phase 11: an ACTIVE mapping for the seeded instrument contract, and validation ----------

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "samples" / "synthetic"
MAPPINGS = "/api/v1/mappings"


def mrule(source: str, target: str | None, code: str = "COPY", **kw: Any) -> dict[str, Any]:
    return {
        "source_column": source,
        "target_field": target,
        "transform": {"code": code, "params": kw.pop("params", {})},
        **kw,
    }


# instrument.csv -> instrument v1: every column mapped (book_code -> legacy_book_code), the
# maturity date parsed so that a bad date is caught, decimals parsed exactly.
INSTRUMENT_RULES = [
    mrule("instrument_id", "instrument_id"),
    mrule("instrument_type", "instrument_type"),
    mrule("currency", "currency"),
    mrule("counterparty_id", "counterparty_id"),
    mrule("product_family", "product_family"),
    mrule("notional", "notional", "PARSE_DECIMAL"),
    mrule("interest_rate", "interest_rate", "PARSE_DECIMAL"),
    mrule("maturity_date", "maturity_date", "PARSE_DATE", params={"format": "YYYY-MM-DD"}),
    mrule("book_code", "legacy_book_code"),
]


def seeded_contract_id(db: str) -> str:
    return str(
        sql(
            db,
            "SELECT c.id FROM source_contract c"
            " JOIN source_profile p ON p.id = c.source_profile_id"
            " JOIN source_system s ON s.id = p.source_system_id"
            " WHERE s.code = 'core_banking' AND c.status <> 'SUPERSEDED'",
        )[0]["id"]
    )


def sample(name: str) -> bytes:
    return (SAMPLE_DIR / name).read_bytes()


def import_sample(api: TestClient, headers: dict[str, str], db: str, data: bytes, **kw: Any) -> Any:
    r = upload(api, headers, seeded_source_id(db), data, **kw)
    assert r.status_code == 201, r.text
    return r.json()


def activate_version(
    api: TestClient,
    steward: dict[str, str],
    approver: dict[str, str],
    version: dict[str, Any],
    import_id: str,
) -> dict[str, Any]:
    """Dry-run (ok), submit and approve a mapping version: it becomes ACTIVE."""
    r = api.post(
        f"/api/v1/imports/{import_id}/mappings/validate",
        json={"mapping_version_id": version["id"]},
        headers=steward,
    )
    assert r.status_code == 201 and r.json()["ok"], r.text
    base = f"{MAPPINGS}/{version['mapping_id']}/versions/{version['version']}"
    assert api.post(f"{base}/submit", json={}, headers=steward).status_code == 200
    r = api.post(f"{base}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
    return dict(r.json())


def instrument_mapping(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    approver: dict[str, str],
    import_id: str,
    rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The core_banking contract's mapping, v1 ACTIVE (dry-run against `import_id`)."""
    body = {
        "code": "core_instrument",
        "name": "Core banking instruments",
        "source_contract_id": seeded_contract_id(db),
        "rules": rules or INSTRUMENT_RULES,
    }
    r = api.post(MAPPINGS, json=body, headers=steward)
    assert r.status_code == 201, r.text
    return activate_version(api, steward, approver, r.json()["versions"][0], import_id)


def validate_import(
    api: TestClient, headers: dict[str, str], import_id: str, **kw: Any
) -> Response:
    return api.post(f"{IMPORTS}/{import_id}/validate", headers={**headers, **kw})
