"""Phase 10 (BE-10.1 – BE-10.6): the versioned mapping workbench, in six essential tests.

1. DB immutability of non-DRAFT versions and their rules (TRUNCATE included, no SET-based
   bypass) and the downgrade refusal.
2. The D-19 transform allowlist: every transform, Decimal only, 422 for unknown/bad params.
3. Create/list/get, a new version leaves the previous unchanged, cross-tenant 404, 403 audited.
4. The dry run: BLOCKER, unmapped-column issue, ok with per-rule counts, no values in issues.
5. Submit and maker-checker approval, activation/deprecation, impact analysis, schema change.
6. MAPPING_CHANGE_ENABLED off: 409 audited on writes, reads work.

Self-contained on purpose: it uses only the shared conftest fixtures and helpers.
"""

import inspect
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from httpx import Response

from app.core.config import Settings
from app.core.tokens import DevTokens
from app.governance.constants import DEFAULT_TENANT_ID, DEV_ISSUER, FieldType
from app.main import create_app
from app.mapping import transforms as transforms_package
from app.mapping.constants import MAPPING_VERSION_TRANSITIONS, TransformCode
from app.mapping.transforms import TRANSFORMS, TransformError, compile_transform, engine
from app.tests.conftest import (
    alembic_config,
    dev_token,
    insert_tenant,
    insert_user,
    script,
    sql,
)

pytestmark = pytest.mark.db

MAPPINGS = "/api/v1/mappings"
IMMUTABLE = asyncpg.InsufficientPrivilegeError
SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "synthetic" / "instrument.csv"
SENTINEL = "SENTINEL-7f3a9"
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"

# The mapping rows these tests create. The app never deletes them, so the triggers are lifted
# for this one transaction (needs the table owner), before the shared cleanup runs.
_CLEANUP = """
BEGIN;
ALTER TABLE import_job DISABLE TRIGGER import_job_immutable;
ALTER TABLE import_job DISABLE TRIGGER import_job_mapping_pin;
ALTER TABLE mapping_version DISABLE TRIGGER mapping_version_immutable;
ALTER TABLE mapping_version DISABLE TRIGGER mapping_version_checks;
ALTER TABLE mapping_rule DISABLE TRIGGER mapping_rule_immutable;
ALTER TABLE mapping_validation DISABLE TRIGGER mapping_validation_append_only;
ALTER TABLE field_mapping DISABLE TRIGGER field_mapping_immutable;
UPDATE import_job SET mapping_version_id = NULL WHERE mapping_version_id IS NOT NULL;
UPDATE mapping_version SET last_validation_id = NULL WHERE last_validation_id IS NOT NULL;
DELETE FROM mapping_validation;
DELETE FROM mapping_rule;
DELETE FROM mapping_version;
DELETE FROM field_mapping;
ALTER TABLE import_job ENABLE TRIGGER import_job_immutable;
ALTER TABLE import_job ENABLE TRIGGER import_job_mapping_pin;
ALTER TABLE mapping_version ENABLE TRIGGER mapping_version_immutable;
ALTER TABLE mapping_version ENABLE TRIGGER mapping_version_checks;
ALTER TABLE mapping_rule ENABLE TRIGGER mapping_rule_immutable;
ALTER TABLE mapping_validation ENABLE TRIGGER mapping_validation_append_only;
ALTER TABLE field_mapping ENABLE TRIGGER field_mapping_immutable;
COMMIT;
"""


@pytest.fixture
def mdb(db: str) -> Iterator[str]:
    """`db`, with this phase's rows removed first on teardown (they reference imports,
    contracts and schemas, which the shared cleanup removes after)."""
    yield db
    script(db, _CLEANUP)


@pytest.fixture
def mapi(mdb: str, artifact_dir: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(Settings(ENV="test", DATABASE_URL=mdb, ARTIFACT_STORE_DIR=artifact_dir))
    ) as c:
        yield c


# --- helpers ---------------------------------------------------------------------------------


def token_for(
    db: str, subject: str, roles: tuple[str, ...], tenant_id: Any = DEFAULT_TENANT_ID
) -> dict[str, str]:
    insert_user(db, DEV_ISSUER, subject, roles=roles, tenant_id=tenant_id)
    settings = Settings(ENV="test")
    token = DevTokens(settings.AUTH_DEV_SECRET, settings.AUTH_DEV_TOKEN_TTL_MINUTES).issue(
        subject, f"{subject}@example.test", subject
    )
    return {"Authorization": f"Bearer {token}"}


def cid() -> str:
    return f"p10-{uuid.uuid4().hex[:10]}"


def events(db: str, correlation_id: str) -> list[Any]:
    return sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", correlation_id)


def seeded_contract(db: str) -> Any:
    return sql(
        db,
        "SELECT c.id, c.schema_id, s.id AS source_id FROM source_contract c"
        " JOIN source_profile p ON p.id = c.source_profile_id"
        " JOIN source_system s ON s.id = p.source_system_id"
        " WHERE s.code = 'core_banking' AND c.status <> 'SUPERSEDED'",
    )[0]


def upload(api: TestClient, headers: dict[str, str], source_id: Any, data: bytes) -> Any:
    r = api.post(
        "/api/v1/imports",
        data={"source_id": str(source_id), "synthetic_attested": "true"},
        files={"file": ("upload.csv", data, "application/octet-stream")},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 201, r.text
    return r.json()


def rule(source: str | None, target: str | None, code: str = "COPY", **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"source_column": source, "target_field": target}
    body["transform"] = {"code": code, "params": kw.pop("params", {})}
    return {**body, **kw}


# instrument.csv -> instrument v1: every column mapped or preserved.
FULL_RULES = [
    rule("instrument_id", "instrument_id"),
    rule("instrument_type", "instrument_type", "UPPER"),
    rule("currency", "currency", "TRIM"),
    rule("counterparty_id", "counterparty_id"),
    rule("product_family", "product_family"),
    rule("notional", "notional", "PARSE_DECIMAL"),
    rule("interest_rate", "interest_rate", "PARSE_DECIMAL"),
    rule("maturity_date", "maturity_date"),
    rule("book_code", None, column_policy="PRESERVE"),
]


def create_mapping(
    api: TestClient,
    db: str,
    headers: dict[str, str],
    rules: list[dict[str, Any]],
    code: str = "core_instrument",
) -> Any:
    body = {
        "code": code,
        "name": "Core banking instruments",
        "source_contract_id": str(seeded_contract(db)["id"]),
        "rules": rules,
    }
    r = api.post(MAPPINGS, json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def validate(
    api: TestClient, headers: dict[str, str], import_id: str, version_id: str, **kw: Any
) -> Response:
    return api.post(
        f"/api/v1/imports/{import_id}/mappings/validate",
        json={"mapping_version_id": version_id},
        headers={**headers, **kw},
    )


def sample_import(api: TestClient, db: str, headers: dict[str, str]) -> Any:
    return upload(api, headers, seeded_contract(db)["source_id"], SAMPLE.read_bytes())


def activate(
    api: TestClient,
    db: str,
    author: dict[str, str],
    approver: dict[str, str],
    mapping: Any,
    number: int,
    import_id: str,
) -> Any:
    """Validate (ok), submit and approve version `number`."""
    version = (
        next(v for v in mapping["versions"] if v["version"] == number)
        if "versions" in mapping
        else mapping
    )
    r = validate(api, author, import_id, version["id"])
    assert r.status_code == 201 and r.json()["ok"], r.text
    base = f"{MAPPINGS}/{version['mapping_id']}/versions/{number}"
    r = api.post(f"{base}/submit", json={}, headers=author)
    assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW", r.text
    r = api.post(f"{base}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
    return r.json()


# --- 1. DB immutability, no bypass, downgrade refusal (BE-10.1) ---------------------------------


def test_non_draft_versions_and_rules_are_immutable_in_the_database(
    mapi: TestClient, mdb: str
) -> None:
    steward, approver = dev_token(mapi, "data_steward"), dev_token(mapi, "approver")
    imported = sample_import(mapi, mdb, steward)
    mapping = create_mapping(mapi, mdb, steward, FULL_RULES)
    active = activate(mapi, mdb, steward, approver, mapping, 1, imported["id"])
    vid = active["id"]
    rules_before = sql(
        mdb, "SELECT * FROM mapping_rule WHERE mapping_version_id = $1 ORDER BY position", vid
    )
    version_before = sql(mdb, "SELECT * FROM mapping_version WHERE id = $1", vid)
    first_rule = rules_before[0]["id"]

    statements = [
        # Rules of an ACTIVE version: INSERT, UPDATE and DELETE.
        (
            "INSERT INTO mapping_rule (id, mapping_version_id, position, source_column,"
            " target_field, transform_code, transform_params, null_policy, column_policy,"
            " created_by) VALUES (gen_random_uuid(), $1, 99, 'x', 'legacy_book_code', 'COPY',"
            " '{}', 'ALLOW_NULL', 'MAP', 't')",
            vid,
        ),
        ("UPDATE mapping_rule SET target_field = 'legacy_book_code' WHERE id = $1", first_rule),
        ("UPDATE mapping_rule SET transform_params = '{\"x\": 1}' WHERE id = $1", first_rule),
        ("DELETE FROM mapping_rule WHERE id = $1", first_rule),
        # The version itself: content, status backwards, decision, delete.
        ("UPDATE mapping_version SET content_sha256 = repeat('a', 64) WHERE id = $1", vid),
        ("UPDATE mapping_version SET status = 'DRAFT' WHERE id = $1", vid),
        ("UPDATE mapping_version SET decision_comment = 'edited' WHERE id = $1", vid),
        ("UPDATE mapping_version SET schema_version_id = gen_random_uuid() WHERE id = $1", vid),
        ("DELETE FROM mapping_version WHERE id = $1", vid),
        ("UPDATE field_mapping SET code = 'renamed' WHERE id = $1", mapping["id"]),
        ("DELETE FROM field_mapping WHERE id = $1", mapping["id"]),
        ("UPDATE mapping_validation SET outcome = 'FAILED' WHERE mapping_version_id = $1", vid),
        # No maker-checker bypass by INSERT: a version is born an undecided DRAFT.
        (
            "INSERT INTO mapping_version (id, field_mapping_id, version, status,"
            " schema_version_id, content_sha256, author_user_id, created_by)"
            " SELECT gen_random_uuid(), field_mapping_id, 50, 'ACTIVE', schema_version_id,"
            " content_sha256, author_user_id, 't'"
            " FROM mapping_version WHERE id = $1",
            vid,
        ),
    ]
    for statement, arg in statements:
        with pytest.raises(IMMUTABLE):
            sql(mdb, statement, arg)
    for table in ("mapping_rule", "mapping_version", "field_mapping", "mapping_validation"):
        with pytest.raises(IMMUTABLE, match="TRUNCATE|append-only"):
            sql(mdb, f"TRUNCATE {table} CASCADE")

    # Review P10-2: no status move skips maker-checker in SQL either. A DRAFT cannot be moved
    # to review unsubmitted or unvalidated, nor decided without a decision or by its author.
    draft = mapi.post(
        f"{MAPPINGS}/{mapping['id']}/versions",
        json={"rules": [rule("currency", "currency"), *FULL_RULES[:2], *FULL_RULES[3:]]},
        headers=steward,
    ).json()["id"]
    for statement in (
        "UPDATE mapping_version SET status = 'IN_REVIEW', last_validation_id = NULL"
        " WHERE id = $1",
        "UPDATE mapping_version SET status = 'IN_REVIEW', submitted_at = now() WHERE id = $1",
    ):
        with pytest.raises(IMMUTABLE):
            sql(mdb, statement, draft)
    # Validated (ok) and submitted, but decided by its own author: refused.
    assert validate(mapi, steward, imported["id"], draft).json()["ok"]
    with pytest.raises(IMMUTABLE, match="author"):
        script(
            mdb,
            "BEGIN; ALTER TABLE mapping_version DISABLE TRIGGER mapping_version_immutable;"
            f" UPDATE mapping_version SET status = 'ACTIVE', submitted_at = now(),"
            f" decided_at = now(), decided_by = 'self', decided_by_user_id = author_user_id"
            f" WHERE id = '{draft}'; COMMIT;",
        )

    # No session setting unlocks a trigger: none reads one, and plausible ones change nothing.
    for fn in ("mapping_rule_guard", "mapping_version_guard", "field_mapping_guard"):
        body = sql(mdb, f"SELECT pg_get_functiondef('{fn}'::regproc) AS src")[0]["src"]
        assert "current_setting" not in body, fn
    with pytest.raises(IMMUTABLE):
        script(
            mdb,
            "BEGIN; SELECT set_config('app.bypass_triggers', 'on', true);"
            " SELECT set_config('app.allow_mapping_edit', 'on', true);"
            f" SET LOCAL app.mapping_unlocked = 'on';"
            f" DELETE FROM mapping_rule WHERE id = '{first_rule}'; COMMIT;",
        )
    assert (
        sql(mdb, "SELECT * FROM mapping_rule WHERE mapping_version_id = $1 ORDER BY position", vid)
        == rules_before
    )
    assert sql(mdb, "SELECT * FROM mapping_version WHERE id = $1", vid) == version_before
    # The DB transition table is a frozen copy of the code's.
    migration = (MIGRATIONS / "0014_mapping_tables.py").read_text()
    for status, targets in MAPPING_VERSION_TRANSITIONS.items():
        assert f'"{status}": {sorted(t.value for t in targets)}'.replace("'", '"') in migration

    # The downgrade refuses while a mapping exists, and leaves everything in place.
    head = sql(mdb, "SELECT version_num FROM alembic_version")[0]["version_num"]
    try:
        with pytest.raises(Exception, match="field_mapping has rows"):
            command.downgrade(alembic_config(mdb), "0013")
        assert sql(mdb, "SELECT version_num FROM alembic_version")[0]["version_num"] == head
        rules_now = sql(
            mdb, "SELECT count(*) AS n FROM mapping_rule WHERE mapping_version_id = $1", vid
        )
        assert rules_now[0]["n"] == len(rules_before)
    finally:
        command.upgrade(alembic_config(mdb), "head")


# --- 2. The transform allowlist (BE-10.2) -------------------------------------------------

D19 = {
    "COPY", "TRIM", "UPPER", "LOWER", "PARSE_DECIMAL", "SCALE", "PARSE_DATE",
    "PARSE_BOOLEAN", "MAP_VALUES", "DEFAULT", "CONSTANT",
}  # fmt: skip


def test_transform_allowlist(mapi: TestClient, mdb: str) -> None:
    assert set(TRANSFORMS) == set(TransformCode) == D19
    # (code, params, input, expected); Decimal results are compared as Decimal, by type too.
    cases: list[tuple[str, dict[str, Any], str | None, Any]] = [
        ("COPY", {}, " a b ", " a b "),
        ("TRIM", {}, "  a b  ", "a b"),
        ("UPPER", {}, "loan", "LOAN"),
        ("LOWER", {}, "LOAN", "loan"),
        ("PARSE_DECIMAL", {}, " -1234.50 ", Decimal("-1234.50")),
        (
            "PARSE_DECIMAL",
            {"decimal_sep": ",", "thousands_sep": "."},
            "1.234.567,891",
            Decimal("1234567.891"),
        ),
        ("PARSE_DECIMAL", {}, "0.1", Decimal("0.1")),
        (
            "PARSE_DECIMAL",
            {},
            "12345678901234567890.123456789",
            Decimal("12345678901234567890.123456789"),
        ),
        ("SCALE", {"power_of_ten": -2}, "12345", Decimal("123.45")),
        ("SCALE", {"power_of_ten": 3}, "0.0015", Decimal("1.5")),
        ("PARSE_DATE", {"format": "DD/MM/YYYY"}, "29/02/2024", "2024-02-29"),
        ("PARSE_DATE", {"format": "YYYYMMDD"}, "20261016", "2026-10-16"),
        ("PARSE_BOOLEAN", {"true_values": ["Y"], "false_values": ["N"]}, " y ", True),
        ("PARSE_BOOLEAN", {"true_values": ["Y"], "false_values": ["N"]}, "N", False),
        ("MAP_VALUES", {"table": {"L": "LOAN"}, "on_missing": "KEEP"}, "L", "LOAN"),
        ("MAP_VALUES", {"table": {"L": "LOAN"}, "on_missing": "KEEP"}, "B", "B"),
        ("MAP_VALUES", {"table": {"L": "LOAN"}, "on_missing": "NULL"}, "B", None),
        ("DEFAULT", {"value": "EUR"}, None, "EUR"),
        ("DEFAULT", {"value": "EUR"}, "GBP", "GBP"),
        ("CONSTANT", {"value": "FILE_DROP"}, "anything", "FILE_DROP"),
    ]
    failures: list[tuple[str, dict[str, Any], str, str]] = [
        ("PARSE_DECIMAL", {}, "1e5", "not_a_decimal"),
        ("PARSE_DECIMAL", {}, "NaN", "not_a_decimal"),
        ("PARSE_DECIMAL", {}, "1_000", "not_a_decimal"),
        ("PARSE_DECIMAL", {"thousands_sep": ","}, "1,23,4", "not_a_decimal"),
        ("SCALE", {"power_of_ten": 2}, "1.5.0", "not_a_decimal"),
        ("PARSE_DATE", {"format": "YYYY-MM-DD"}, "2026-02-30", "invalid_date"),
        (
            "PARSE_BOOLEAN",
            {"true_values": ["Y"], "false_values": ["N"]},
            "maybe",
            "invalid_boolean",
        ),
        ("MAP_VALUES", {"table": {"L": "LOAN"}}, "B", "unmapped_value"),
    ]
    seen = set()
    for code, params, value, expected in cases:
        result = compile_transform(code, params)(value)
        seen.add(code)
        if isinstance(expected, Decimal):
            assert isinstance(result, Decimal) and result == expected, (code, result)
            assert str(result) == str(expected) or result.normalize() == expected.normalize()
        elif isinstance(result, bool) or result is None:
            assert result is expected, (code, value, result)
        else:
            assert str(result) == expected, (code, value, result)
    assert seen == D19
    for code, params, value, reason in failures:
        with pytest.raises(engine.ValueFailure) as caught:
            compile_transform(code, params)(value)
        assert caught.value.reason == reason and value not in str(caught.value)
    # Fitting to a NUMERIC(20,2) field: never rounded, never a float.
    notional = engine.TargetField("notional", FieldType.DECIMAL, True, 20, 2)
    parse = compile_transform("PARSE_DECIMAL", {})
    assert engine.apply(parse, "1.50", "ALLOW_NULL", None, notional).text == "1.50"
    assert engine.apply(parse, "1.505", "ALLOW_NULL", None, notional).reason == "scale_exceeded"
    assert (
        engine.apply(parse, "1" * 19, "ALLOW_NULL", None, notional).reason == "precision_exceeded"
    )
    assert engine.apply(parse, " ", "REJECT_NULL", None, notional).reason == "null_value"
    assert engine.apply(parse, "", "USE_DEFAULT", "0", notional).text == "0.00"
    assert (
        engine.apply(parse, None, "ALLOW_NULL", None, notional).reason == "required_value_missing"
    )
    # No float, no regex, no eval anywhere in the engine.
    source = inspect.getsource(engine)
    for forbidden in (
        "float(",
        "import re\n",
        "eval(",
        "exec(",
        "compile(",
        "strptime(",
        "format_map",
    ):
        assert forbidden not in source, forbidden
    assert transforms_package.ENGINE_PARTS and all(hasattr(engine, p) for p in engine.ENGINE_PARTS)

    # Unknown codes and bad params: refused by the engine and by the API (422).
    refused: list[tuple[str, dict[str, Any]]] = [
        ("EVAL", {}),
        ("SCALE", {"power_of_ten": "2"}),
        ("SCALE", {"power_of_ten": 19}),
        ("COPY", {"x": 1}),
        ("PARSE_DATE", {"format": "%Y-%m-%d"}),
        ("PARSE_BOOLEAN", {"true_values": ["y"], "false_values": ["Y"]}),
        ("PARSE_DECIMAL", {"decimal_sep": ",", "thousands_sep": ","}),
    ]
    for code, params in refused:
        with pytest.raises(TransformError):
            compile_transform(code, params)
        body = {
            "code": "bad_params",
            "name": "x",
            "source_contract_id": str(seeded_contract(mdb)["id"]),
            "rules": [rule("notional", "notional", code, params=params)],
        }
        r = mapi.post(MAPPINGS, json=body, headers=dev_token(mapi, "data_steward"))
        assert r.status_code == 422 and r.json()["code"] == "validation_error", (code, r.text)
    assert sql(mdb, "SELECT count(*) AS n FROM field_mapping")[0]["n"] == 0


# --- 3. Create, list, get, versions, tenancy and permission (BE-10.3) ---------------------------


def test_create_list_get_and_versions(mapi: TestClient, mdb: str) -> None:
    steward = dev_token(mapi, "data_steward")
    contract = seeded_contract(mdb)
    c = cid()
    r = mapi.post(
        MAPPINGS,
        json={
            "code": "core_instrument",
            "name": "Core banking instruments",
            "source_contract_id": str(contract["id"]),
            "rules": FULL_RULES[:3],
            "change_summary": "First cut.",
        },
        headers={**steward, "X-Correlation-ID": c},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    [v1] = created["versions"]
    assert v1["version"] == 1 and v1["status"] == "DRAFT" and v1["schema_version"] == 1
    assert [x["target_field"] for x in v1["rules"]] == [
        "instrument_id",
        "instrument_type",
        "currency",
    ]
    assert created["schema_code"] == "instrument" and created["source_code"] == "core_banking"
    assert [e["action"] for e in events(mdb, c)] == ["mapping.create"]
    pinned = sql(
        mdb,
        "SELECT v.id FROM schema_version v JOIN schema_definition d ON d.id = v.schema_id"
        " WHERE d.code = 'instrument' AND v.status = 'ACTIVE'",
    )[0]["id"]
    assert v1["schema_version_id"] == str(pinned)

    reader = dev_token(mapi, "risk_analyst")
    listed = mapi.get(
        MAPPINGS, params={"source_id": str(contract["source_id"])}, headers=reader
    ).json()
    assert listed["total"] == 1 and listed["items"][0]["latest_version"] == 1
    other_schema = sql(mdb, "SELECT id FROM schema_definition WHERE code = 'position'")[0]["id"]
    assert (
        mapi.get(MAPPINGS, params={"schema_id": str(other_schema)}, headers=reader).json()["total"]
        == 0
    )
    got = mapi.get(f"{MAPPINGS}/{created['id']}", headers=reader)
    assert got.status_code == 200 and got.json()["versions"][0]["id"] == v1["id"]
    # One mapping per contract, codes unique: both refusals audited.
    for body, code in (
        ({"code": "core_instrument", "source_contract_id": str(contract["id"])}, "duplicate_code"),
        ({"code": "second", "source_contract_id": str(contract["id"])}, "contract_already_mapped"),
    ):
        c = cid()
        r = mapi.post(
            MAPPINGS, json={"name": "x", **body}, headers={**steward, "X-Correlation-ID": c}
        )
        assert r.status_code == 409 and r.json()["code"] == code
        assert [(e["outcome"], e["details"]) for e in events(mdb, c)] == [
            ("DENIED", f'{{"reason": "{code}"}}')
        ]

    # A new version leaves the previous one's row and rules exactly as they were.
    v1_row = sql(mdb, "SELECT * FROM mapping_version WHERE id = $1", uuid.UUID(v1["id"]))
    v1_rules = sql(
        mdb,
        "SELECT * FROM mapping_rule WHERE mapping_version_id = $1 ORDER BY position",
        uuid.UUID(v1["id"]),
    )
    r = mapi.post(
        f"{MAPPINGS}/{created['id']}/versions", json={"rules": FULL_RULES}, headers=steward
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["status"] == "DRAFT" and len(v2["rules"]) == len(FULL_RULES)
    assert v2["content_sha256"] != v1["content_sha256"]
    assert sql(mdb, "SELECT * FROM mapping_version WHERE id = $1", uuid.UUID(v1["id"])) == v1_row
    assert (
        sql(
            mdb,
            "SELECT * FROM mapping_rule WHERE mapping_version_id = $1 ORDER BY position",
            uuid.UUID(v1["id"]),
        )
        == v1_rules
    )
    r = mapi.post(
        f"{MAPPINGS}/{created['id']}/versions", json={"rules": FULL_RULES}, headers=steward
    )
    assert r.status_code == 422 and r.json()["code"] == "no_change"
    r = mapi.post(
        f"{MAPPINGS}/{created['id']}/versions",
        json={"rules": [rule("x", "no_such_field")]},
        headers=steward,
    )
    assert r.status_code == 422 and r.json()["code"] == "unknown_target_field"

    # Another tenant: the same 404 as a random id, an empty list, and no writes.
    outsider = token_for(
        mdb,
        "dev:outsider-p10",
        ("data_steward", "approver"),
        tenant_id=insert_tenant(mdb, f"o-{uuid.uuid4().hex[:6]}"),
    )
    foreign = mapi.get(f"{MAPPINGS}/{created['id']}", headers=outsider)
    random = mapi.get(f"{MAPPINGS}/{uuid.uuid4()}", headers=outsider)
    assert foreign.status_code == random.status_code == 404
    foreign_body, random_body = foreign.json(), random.json()
    foreign_body.pop("correlation_id")
    random_body.pop("correlation_id")
    assert foreign_body == random_body
    assert mapi.get(MAPPINGS, headers=outsider).json()["total"] == 0
    writes: list[tuple[str, dict[str, Any]]] = [
        (f"{MAPPINGS}/{created['id']}/versions", {"rules": FULL_RULES[:1]}),
        (f"{MAPPINGS}/{created['id']}/versions/1/submit", {}),
        (f"{MAPPINGS}/{created['id']}/versions/1/approval", {"decision": "APPROVE"}),
    ]
    for path, body in writes:
        assert mapi.post(path, json=body, headers=outsider).status_code == 404, path
    r = mapi.post(
        MAPPINGS,
        json={"code": "theirs", "name": "x", "source_contract_id": str(contract["id"])},
        headers=outsider,
    )
    assert r.status_code == 404
    assert sql(mdb, "SELECT count(*) AS n FROM mapping_version")[0]["n"] == 2

    # risk_analyst may read but not propose: 403 with exactly one DENIED event.
    c = cid()
    r = mapi.post(
        MAPPINGS,
        json={"code": "nope", "name": "x", "source_contract_id": str(contract["id"])},
        headers={**reader, "X-Correlation-ID": c},
    )
    assert r.status_code == 403 and r.json()["details"]["required_permission"] == "mapping:propose"
    assert [e["outcome"] for e in events(mdb, c)] == ["DENIED"]


# --- 4. The dry run (BE-10.4) ---------------------------------------------------------------------


def test_validate_dry_run(mapi: TestClient, mdb: str) -> None:
    steward = dev_token(mapi, "data_steward")
    source = seeded_contract(mdb)["source_id"]
    header = (
        "instrument_id,instrument_type,currency,counterparty_id,product_family,notional,"
        "interest_rate,maturity_date,book_code\n"
    )
    bad = upload(
        mapi,
        steward,
        source,
        (header + "A1,LOAN,EUR,CP-1,RETAIL,10.00,1.5,2030-01-01,BK-1\n"
         f"A2,LOAN,EUR,CP-2,RETAIL,{SENTINEL},1.5,2030-01-01,BK-1\n"
         f"A2,LOAN,EUR,CP-2,RETAIL,5.123,{SENTINEL},2030-01-01,BK-1\n").encode(),
    )  # fmt: skip
    # v1: notional (required) unmapped, book_code without MAP or PRESERVE.
    partial = [r for r in FULL_RULES if r["source_column"] not in ("notional", "book_code")]
    mapping = create_mapping(mapi, mdb, steward, partial)
    records = sql(mdb, "SELECT count(*) AS n FROM source_record")[0]["n"]
    status = sql(mdb, "SELECT status FROM import_job WHERE id = $1", uuid.UUID(bad["id"]))[0][
        "status"
    ]
    c = cid()
    r = validate(mapi, steward, bad["id"], mapping["versions"][0]["id"], **{"X-Correlation-ID": c})
    assert r.status_code == 201, r.text
    result = r.json()
    assert result["outcome"] == "FAILED" and not result["ok"] and result["row_count"] == 3
    by_code = {
        (i["code"], i.get("target_field") or i.get("source_column")): i for i in result["issues"]
    }
    assert by_code[("required_field_unmapped", "notional")]["severity"] == "BLOCKER"
    assert by_code[("column_without_policy", "book_code")]["severity"] == "ERROR"
    assert [e["action"] for e in events(mdb, c)] == ["mapping.validate"]

    # v2: every column covered; the sentinel cells fail their rules, counted, never echoed.
    r = mapi.post(
        f"{MAPPINGS}/{mapping['id']}/versions", json={"rules": FULL_RULES}, headers=steward
    )
    v2 = r.json()
    r = validate(mapi, steward, bad["id"], v2["id"])
    result = r.json()
    assert result["outcome"] == "FAILED"
    failed = {i["target_field"]: i for i in result["issues"] if i["code"] == "transform_failed"}
    assert failed["notional"]["count"] == 2  # the sentinel and an over-scale 5.123
    assert failed["notional"]["reasons"] == {"not_a_decimal": 1, "scale_exceeded": 1}
    assert failed["interest_rate"]["reasons"] == {"not_a_decimal": 1}
    assert {i["code"] for i in result["issues"]} >= {"duplicate_business_key"}
    stored = sql(mdb, "SELECT issues::text AS i, rule_results::text AS r FROM mapping_validation")
    assert SENTINEL not in r.text and all(SENTINEL not in row["i"] + row["r"] for row in stored)
    assert "5.123" not in r.text

    # A clean import: ok, with counts per rule.
    clean = sample_import(mapi, mdb, steward)
    r = validate(mapi, steward, clean["id"], v2["id"])
    result = r.json()
    rows = clean["row_count"]
    assert result["outcome"] == "OK" and result["ok"] and result["issues"] == [], result["issues"]
    assert result["row_count"] == rows and len(result["rule_results"]) == len(FULL_RULES)
    for res in result["rule_results"]:
        assert (
            res["rows"] == rows and res["failed"] == 0 and res["mapped"] + res["empty"] == rows
        ), res
    assert {r["target_field"]: r["mapped"] for r in result["rule_results"]}["notional"] == rows
    got = mapi.get(f"{MAPPINGS}/{mapping['id']}", headers=steward).json()
    assert got["versions"][1]["last_validation"]["id"] == result["id"]
    # A dry run writes nothing else: no records, no status change. (canonical_record arrives in
    # Phase 11; its count is asserted there.)
    assert sql(mdb, "SELECT count(*) AS n FROM source_record")[0]["n"] == records + rows
    assert (
        sql(mdb, "SELECT status FROM import_job WHERE id = $1", uuid.UUID(bad["id"]))[0]["status"]
        == status
    )
    # An import of another tenant's view: 404 alike, audited.
    r = validate(mapi, steward, str(uuid.uuid4()), v2["id"])
    assert r.status_code == 404


# --- 5. Submit and approval (BE-10.5) ------------------------------------------------------------


def test_submit_and_maker_checker_approval(mapi: TestClient, mdb: str) -> None:
    steward, approver = dev_token(mapi, "data_steward"), dev_token(mapi, "approver")
    both = token_for(mdb, "dev:maker-checker-p10", ("data_steward", "approver"))
    imported = sample_import(mapi, mdb, steward)
    assert imported["status"] == "MAPPING_REQUIRED"  # no mapping yet (D-21)
    mapping = create_mapping(mapi, mdb, both, FULL_RULES)
    base = f"{MAPPINGS}/{mapping['id']}/versions"

    # Submit before any validation: 409, audited.
    c = cid()
    r = mapi.post(f"{base}/1/submit", json={}, headers={**both, "X-Correlation-ID": c})
    assert r.status_code == 409 and r.json()["code"] == "mapping_validation_required"
    assert [e["outcome"] for e in events(mdb, c)] == ["DENIED"]
    assert validate(mapi, both, imported["id"], mapping["versions"][0]["id"]).json()["ok"]
    r = mapi.post(f"{base}/1/submit", json={}, headers=steward)
    assert r.status_code == 403 and r.json()["code"] == "not_author"
    assert mapi.post(f"{base}/1/submit", json={}, headers=both).json()["status"] == "IN_REVIEW"

    # The author cannot approve: 403 self_approval with exactly one DENIED event.
    c = cid()
    r = mapi.post(
        f"{base}/1/approval", json={"decision": "APPROVE"}, headers={**both, "X-Correlation-ID": c}
    )
    assert r.status_code == 403 and r.json()["code"] == "self_approval"
    denied = events(mdb, c)
    assert [(e["action"], e["outcome"]) for e in denied] == [("mapping.approve", "DENIED")]
    assert '"self_approval"' in denied[0]["details"]
    r = mapi.post(f"{base}/1/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"

    # Phase 9 seams: a new upload now ends RECEIVED, and its profile masks by mapped field.
    again = sample_import(mapi, mdb, steward)
    assert again["status"] == "RECEIVED"
    profile = mapi.get(f"/api/v1/imports/{again['id']}/profile", headers=steward).json()
    classes = {col["name"]: col["classification"] for col in profile["columns"]}
    assert classes["currency"] == "public" and classes["counterparty_id"] == "confidential"
    assert profile["schema_id"] == str(seeded_contract(mdb)["schema_id"])

    # v2 by another author, approved: v2 ACTIVE, v1 DEPRECATED with its hash unchanged, and
    # every event of the approval shares one correlation id.
    v1_sha = sql(mdb, "SELECT content_sha256 FROM mapping_version WHERE version = 1")[0][
        "content_sha256"
    ]
    rules_v2 = [rule("instrument_id", "instrument_id", "TRIM"), *FULL_RULES[1:]]
    v2 = mapi.post(base, json={"rules": rules_v2}, headers=steward).json()
    assert validate(mapi, steward, imported["id"], v2["id"]).json()["ok"]
    assert mapi.post(f"{base}/2/submit", json={}, headers=steward).status_code == 200
    c = cid()
    r = mapi.post(
        f"{base}/2/approval",
        json={"decision": "APPROVE", "comment": "ok"},
        headers={**approver, "X-Correlation-ID": c},
    )
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
    versions = {
        v["version"]: v
        for v in mapi.get(f"{MAPPINGS}/{mapping['id']}", headers=steward).json()["versions"]
    }
    assert versions[1]["status"] == "DEPRECATED" and versions[1]["content_sha256"] == v1_sha
    assert versions[2]["status"] == "ACTIVE" and versions[2]["decided_by"]
    assert {e["action"] for e in events(mdb, c)} == {
        "mapping.approve",
        "mapping_version.deprecate",
        "mapping_version.activate",
    }
    assert all(e["outcome"] == "SUCCESS" for e in events(mdb, c))

    # Phase 5 impact analysis on the target schema lists this mapping as a dependent.
    sid = seeded_contract(mdb)["schema_id"]
    schema_versions = mapi.get(f"/api/v1/schemas/{sid}/versions", headers=steward).json()["items"]
    keep = (
        "name",
        "data_type",
        "required",
        "unit",
        "precision",
        "scale",
        "classification",
        "extension_eligible",
    )
    v1_fields = next(v for v in schema_versions if v["version"] == 1)["fields"]
    fields = [{k: f[k] for k in keep} for f in v1_fields if f["name"] != "legacy_book_code"]
    proposal = mapi.post(
        "/api/v1/schema-change-proposals",
        json={"schema_id": str(sid), "title": "Drop legacy book code", "fields": fields},
        headers=steward,
    ).json()
    pbase = f"/api/v1/schema-change-proposals/{proposal['id']}"
    impact = mapi.post(f"{pbase}/impact-analysis", headers=steward).json()["impact"]
    assert {"dependent_type": "MAPPING", "dependent_ref": "core_instrument"}.items() <= next(
        d for d in impact["dependents"] if d["dependent_ref"] == "core_instrument"
    ).items()

    # v3 in review when the schema change deprecates its pinned schema version: 409, audited.
    v3 = mapi.post(base, json={"rules": FULL_RULES}, headers=steward).json()
    assert validate(mapi, steward, imported["id"], v3["id"]).json()["ok"]
    assert mapi.post(f"{base}/3/submit", json={}, headers=steward).status_code == 200
    assert mapi.post(f"{pbase}/submit", json={}, headers=steward).status_code == 200
    assert (
        mapi.post(f"{pbase}/approval", json={"decision": "APPROVE"}, headers=approver).status_code
        == 200
    )
    c = cid()
    r = mapi.post(
        f"{base}/3/approval",
        json={"decision": "APPROVE"},
        headers={**approver, "X-Correlation-ID": c},
    )
    assert r.status_code == 409 and r.json()["code"] == "schema_version_not_active"
    assert [e["outcome"] for e in events(mdb, c)] == ["DENIED"]
    versions = {
        v["version"]: v["status"]
        for v in mapi.get(f"{MAPPINGS}/{mapping['id']}", headers=steward).json()["versions"]
    }
    assert versions == {1: "DEPRECATED", 2: "ACTIVE", 3: "IN_REVIEW"}
    # D-18 §4: the next import pins the new schema version, so it needs a new mapping version.
    assert sample_import(mapi, mdb, steward)["status"] == "MAPPING_REQUIRED"


# --- 6. The switch (BE-10.6) ----------------------------------------------------------------------


def test_mapping_change_switch(mdb: str, artifact_dir: Path) -> None:
    settings = Settings(
        ENV="test", DATABASE_URL=mdb, ARTIFACT_STORE_DIR=artifact_dir, MAPPING_CHANGE_ENABLED=False
    )
    with TestClient(create_app(settings)) as api:
        steward = dev_token(api, "data_steward")
        c = cid()
        r = api.post(
            MAPPINGS,
            json={
                "code": "off",
                "name": "x",
                "source_contract_id": str(seeded_contract(mdb)["id"]),
            },
            headers={**steward, "X-Correlation-ID": c},
        )
        assert r.status_code == 409 and r.json()["code"] == "mapping_change_disabled"
        assert [(e["outcome"], e["action"]) for e in events(mdb, c)] == [
            ("DENIED", "POST /api/v1/mappings")
        ]
        assert api.get(MAPPINGS, headers=steward).status_code == 200
        for path in (
            f"{MAPPINGS}/{uuid.uuid4()}/versions",
            f"/api/v1/imports/{uuid.uuid4()}/mappings/validate",
        ):
            assert (
                api.post(
                    path,
                    json={"rules": [], "mapping_version_id": str(uuid.uuid4())},
                    headers=steward,
                ).json()["code"]
                == "mapping_change_disabled"
            )
        # The permission is checked first: the switch reveals nothing to a caller without it.
        r = api.post(MAPPINGS, json={}, headers=dev_token(api, "risk_analyst"))
        assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert sql(mdb, "SELECT count(*) AS n FROM field_mapping")[0]["n"] == 0
