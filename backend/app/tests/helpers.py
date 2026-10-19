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
from app.governance.constants import DEFAULT_TENANT_ID, DEV_ISSUER, FieldType
from app.ingestion.adapters import AdapterOptions, Limits, parse_upload
from app.ingestion.constants import SourceFormat
from app.tests.conftest import dev_token, insert_user, sql

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
    """In the order written: heap order is not insertion order once cleanup frees pages."""
    return sql(
        db,
        "SELECT * FROM audit_event WHERE correlation_id = $1 ORDER BY occurred_at, ctid",
        correlation_id,
    )


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


# --- Phase 14: projection into the WS3 domain (D-29) ------------------------------------------

INSTRUMENTS = "/api/v1/instruments"


def project(api: TestClient, headers: dict[str, str], import_id: str, **kw: Any) -> Response:
    return api.post(f"{IMPORTS}/{import_id}/projection", headers={**headers, **kw})


def validated_instruments(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(import, ACTIVE mapping version): the synthetic instrument CSV on the seeded source,
    its core mapping activated against it, validated (COMPLETED)."""
    imported = import_sample(api, steward, db, sample("instrument.csv"))
    version = instrument_mapping(api, db, steward, approver, imported["id"])
    r = validate_import(api, steward, imported["id"])
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
    return dict(r.json()), version


def projected_instruments(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str]
) -> dict[str, Any]:
    """The synthetic instrument CSV validated and projected: its import, mapping and one
    instrument id."""
    imported, version = validated_instruments(api, db, steward, approver)
    r = project(api, steward, imported["id"])
    assert r.status_code == 201, r.text
    page = api.get(INSTRUMENTS, params={"import_id": imported["id"]}, headers=steward).json()
    return {
        "import_id": imported["id"],
        "mapping_id": version["mapping_id"],
        "instrument_id": page["items"][0]["instrument_id"],
    }


def validated_sample(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str], code: str
) -> dict[str, Any]:
    """The synthetic position (XLSX) or cash-flow (JSON) sample: its own source and contract
    (the file's columns, its business key), a mapping of every column (decimals and dates
    parsed) made ACTIVE against it, and the import validated."""
    name, fmt, key = {
        "position": ("position.xlsx", SourceFormat.XLSX, "position_id"),
        "cashflow_event": ("cashflow_event.json", SourceFormat.JSON, "cashflow_id"),
    }[code]
    data = sample(name)
    columns = parse_upload(data, fmt, AdapterOptions(), Limits()).columns
    body = source_body(
        db,
        code=f"{code}_feed",
        profile={
            "format": fmt.value,
            "owner": "Test data team",
            "default_classification": "confidential",
        },
        contract=contract_body(
            db,
            schema=code,
            expected_columns=[{"name": c, "required": True} for c in columns],
            business_key_columns=[key],
            control_total_columns=[],
        ),
    )
    r = api.post(SOURCES, json=body, headers=steward)
    assert r.status_code == 201, r.text
    source = r.json()
    r = upload(api, steward, source["id"], data, filename=name)
    assert r.status_code == 201, r.text
    imported = r.json()
    rules = sample_rules(api, db, steward, code, columns)
    mapping = {
        "code": f"{code}_map",
        "name": f"{code} (test)",
        "source_contract_id": source["current_contract"]["id"],
        "rules": rules,
    }
    r = api.post(MAPPINGS, json=mapping, headers=steward)
    assert r.status_code == 201, r.text
    activate_version(api, steward, approver, r.json()["versions"][0], imported["id"])
    r = validate_import(api, steward, imported["id"])
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
    return dict(r.json())


def sample_rules(
    api: TestClient, db: str, steward: dict[str, str], code: str, columns: list[str]
) -> list[dict[str, Any]]:
    """Map each column to the same-named field of the schema's v1: decimals and dates
    parsed, everything else copied."""
    types = {f["name"]: f["data_type"] for f in v1_fields(api, steward, schema_id(db, code))}
    rules = []
    for column in columns:
        kind, params = "COPY", {}
        if types[column] == FieldType.DECIMAL:
            kind = "PARSE_DECIMAL"
        elif types[column] == FieldType.DATE:
            kind, params = "PARSE_DATE", {"format": "YYYY-MM-DD"}
        rules.append(
            {
                "source_column": column,
                "target_field": column,
                "transform": {"code": kind, "params": params},
            }
        )
    return rules


# --- Phase 15: dataset snapshots and calculation runs (D-30, D-31) ---------------------------

SNAPSHOTS = "/api/v1/dataset-snapshots"
RUNS = "/api/v1/calculation-runs"
MODULES = "/api/v1/calculation-modules"


def module_version_id(
    api: TestClient, headers: dict[str, str], code: str = "maturity_ladder"
) -> str:
    """The id of the module's (first) version in GET /calculation-modules."""
    items = api.get(MODULES, headers=headers).json()["items"]
    return str(next(m["module_version_id"] for m in items if m["code"] == code))


def make_snapshot(
    api: TestClient, headers: dict[str, str], import_ids: list[str], **kw: Any
) -> Response:
    return api.post(SNAPSHOTS, json={"import_ids": import_ids}, headers={**headers, **kw})


def start_run(
    api: TestClient,
    headers: dict[str, str],
    body: dict[str, Any],
    key: str | None = None,
    **kw: Any,
) -> Response:
    extra = {"Idempotency-Key": key or uuid.uuid4().hex}
    return api.post(RUNS, json=body, headers={**headers, **extra, **kw})


def blocked_run(api: TestClient, import_id: str) -> dict[str, Any]:
    """A snapshot of one projected import and a maturity-ladder run on it, as risk_analyst:
    BLOCKED unless the snapshot holds both schemas the ladder needs. Returns both ids."""
    analyst = dev_token(api, "risk_analyst")
    r = make_snapshot(api, analyst, [import_id])
    assert r.status_code == 201, r.text
    body = {
        "dataset_snapshot_id": r.json()["snapshot_id"],
        "module_version_id": module_version_id(api, analyst),
        "reporting_date": "2026-12-31",
    }
    run = start_run(api, analyst, body)
    assert run.status_code == 201, run.text
    return {"snapshot_id": r.json()["snapshot_id"], "run_id": run.json()["run_id"]}


# --- Phase 16: rules and governed rule changes (D-32) ------------------------------------------

RULES = "/api/v1/rules"


def rule_id(db: str, code: str) -> str:
    """The id of a rule definition of the default tenant, by code."""
    return str(sql(db, "SELECT id FROM rule_definition WHERE code = $1", code)[0]["id"])
