"""Feature 02 Phase 7: source registration tables (BE-7.1), the INGESTION_ENABLED switch
(BE-7.3), the sources API (BE-7.4) and the seeded illustrative source (BE-7.5)."""

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.governance.constants import DEFAULT_TENANT_ID
from app.ingestion.constants import SOURCE_SEED_TAG
from app.ingestion.service import contract_sha256
from app.main import create_app
from app.tests.conftest import alembic_config, dev_token, insert_tenant, sql
from app.tests.test_schema_change import token_for
from app.tests.test_schema_registry import schema_id
from scripts.make_synthetic_samples import build

pytestmark = pytest.mark.db

BASE = "/api/v1/sources"
VERSIONS = Path(__file__).resolve().parent.parent / "db" / "migrations" / "versions"


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


def create_source(api: TestClient, db: str, headers: dict[str, str], **kw: Any) -> Any:
    r = api.post(BASE, json=source_body(db, **kw), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def seeded_contract(db: str) -> Any:
    return sql(db, "SELECT * FROM source_contract WHERE seeded_by = $1", SOURCE_SEED_TAG)[0]


@pytest.fixture
def steward(api: TestClient) -> dict[str, str]:
    return dev_token(api, "data_steward")


# --- BE-7.1 tables, triggers and checks ---------------------------------------------------

IMMUTABLE = asyncpg.InsufficientPrivilegeError


@pytest.fixture
def approved(db: str) -> Any:
    """The seeded contract, moved PROVISIONAL -> APPROVED (cleanup puts it back)."""
    contract = seeded_contract(db)
    sql(db, "UPDATE source_contract SET status = 'APPROVED' WHERE id = $1", contract["id"])
    return contract


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE source_contract SET expected_columns = '[]' WHERE id = $1",
        "UPDATE source_contract SET business_key_columns = '[\"currency\"]' WHERE id = $1",
        "UPDATE source_contract SET control_total_columns = '[]' WHERE id = $1",
        "UPDATE source_contract SET content_sha256 = repeat('0', 64) WHERE id = $1",
        "UPDATE source_contract SET version = 7 WHERE id = $1",
        "UPDATE source_contract SET schema_id = (SELECT id FROM schema_definition"
        " WHERE code = 'position') WHERE id = $1",
        "UPDATE source_contract SET seeded_by = NULL WHERE id = $1",
        "UPDATE source_contract SET status = 'PROVISIONAL' WHERE id = $1",
        "DELETE FROM source_contract WHERE id = $1",
    ],
)
def test_approved_contract_rejects_update_and_delete(
    db: str, approved: Any, statement: str
) -> None:
    with pytest.raises(IMMUTABLE, match="immutable"):
        sql(db, statement, approved["id"])
    after = sql(db, "SELECT * FROM source_contract WHERE id = $1", approved["id"])[0]
    assert after["status"] == "APPROVED"
    assert {k: after[k] for k in after.keys() if k not in ("status", "updated_at")} == {
        k: approved[k] for k in approved.keys() if k not in ("status", "updated_at")
    }


def test_approved_contract_rejects_truncate(db: str, approved: Any) -> None:
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE source_contract CASCADE")
    assert sql(db, "SELECT count(*) AS n FROM source_contract")[0]["n"] == 1


def test_approved_contract_can_still_be_superseded(db: str, approved: Any) -> None:
    sql(db, "UPDATE source_contract SET status = 'SUPERSEDED' WHERE id = $1", approved["id"])
    # ...and SUPERSEDED is final.
    with pytest.raises(IMMUTABLE, match="final"):
        sql(db, "UPDATE source_contract SET status = 'APPROVED' WHERE id = $1", approved["id"])


def test_provisional_contract_is_never_deleted(db: str) -> None:
    with pytest.raises(IMMUTABLE, match="DELETE"):
        sql(db, "DELETE FROM source_contract WHERE id = $1", seeded_contract(db)["id"])


@pytest.mark.parametrize("table", ["source_system", "source_profile"])
def test_sources_and_profiles_are_never_deleted_or_truncated(db: str, table: str) -> None:
    with pytest.raises(IMMUTABLE, match="never deleted"):
        sql(db, f"DELETE FROM {table}")
    with pytest.raises(IMMUTABLE, match="never deleted"):
        sql(db, f"TRUNCATE {table} CASCADE")
    assert sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"] == 1


INSERT_CONTRACT_WITH_STATUS = (
    "INSERT INTO source_contract (id, source_profile_id, version, status, schema_id,"
    " expected_columns, business_key_columns, control_total_columns, content_sha256,"
    " created_by) SELECT gen_random_uuid(), source_profile_id, 2, '{status}', schema_id, '[]',"
    " '[]', '[]', repeat('a', 64), 'test' FROM source_contract"
)


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE source_system SET kind = 'file_drop'", "ck_source_system_kind"),
        ("UPDATE source_system SET kind = 'SFTP'", "ck_source_system_kind"),
        ("UPDATE source_profile SET format = 'XLS', delimiter = NULL", "ck_source_profile_format"),
        ("UPDATE source_profile SET encoding = 'LATIN-1'", "ck_source_profile_encoding"),
        ("UPDATE source_profile SET delimiter = '#'", "ck_source_profile_delimiter"),
        ("UPDATE source_profile SET delimiter = NULL", "ck_source_profile_delimiter_format"),
        ("UPDATE source_profile SET sheet_name = 'Sheet1'", "ck_source_profile_sheet_name_format"),
        # On INSERT: on UPDATE the trigger refuses an unknown status first (D-06 table).
        (INSERT_CONTRACT_WITH_STATUS.format(status="DRAFT"), "ck_source_contract_status"),
        (INSERT_CONTRACT_WITH_STATUS.format(status="approved"), "ck_source_contract_status"),
    ],
)
def test_enum_columns_are_checked(db: str, statement: str, constraint: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError, match=constraint):
        sql(db, statement)


def test_tab_is_an_accepted_delimiter(db: str) -> None:
    sql(db, "UPDATE source_profile SET delimiter = chr(9)")
    sql(db, "UPDATE source_profile SET delimiter = ','")


def test_contract_cannot_target_another_tenants_schema(db: str) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    foreign = sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'foreign', 'Foreign', 'x', false, 'test') RETURNING id",
        other,
    )[0]["id"]
    contract = seeded_contract(db)
    with pytest.raises(IMMUTABLE, match="tenant"):
        sql(
            db,
            "INSERT INTO source_contract (id, source_profile_id, version, status, schema_id,"
            " expected_columns, business_key_columns, control_total_columns, content_sha256,"
            " created_by) VALUES (gen_random_uuid(), $1, 2, 'SUPERSEDED', $2, '[]', '[]', '[]',"
            " repeat('a', 64), 'test')",
            contract["source_profile_id"],
            foreign,
        )


SOURCE_TABLES = ("source_system", "source_profile", "source_contract")


def source_rows(db: str) -> dict[str, list[Any]]:
    """Every source row's identity and creation time: a row removed and re-inserted (even
    with the same uuid5 ID) has a new created_at."""
    return {
        t: [tuple(r) for r in sql(db, f"SELECT id, created_at FROM {t} ORDER BY id")]  # noqa: S608
        for t in SOURCE_TABLES
    }


def head() -> str:
    """The migration head (the revision a refused downgrade leaves in place)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())


def revision(db: str) -> str:
    return str(sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"])


def test_downgrade_refuses_while_a_user_source_exists(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    created = create_source(api, db, steward)
    before = source_rows(db)
    assert len(before["source_system"]) == 2
    try:
        with pytest.raises(Exception, match="source_system has rows"):
            command.downgrade(alembic_config(db), "0008")
        # One transaction: nothing was downgraded, so nothing needs re-applying...
        assert revision(db) == head()
        # ...and nothing was removed, the seeded source included (same IDs, same rows).
        assert source_rows(db) == before
    finally:
        command.upgrade(alembic_config(db), "head")
    assert source_rows(db) == before
    codes = {r["code"] for r in sql(db, "SELECT code FROM source_system")}
    assert codes == {created["code"], "core_banking"}


def test_source_migrations_round_trip_without_user_sources(db: str) -> None:
    cfg = alembic_config(db)
    command.downgrade(cfg, "0008")
    assert sql(db, "SELECT to_regclass('source_contract') AS t")[0]["t"] is None
    command.upgrade(cfg, "head")
    assert seeded_contract(db)["status"] == "PROVISIONAL"


# --- BE-7.3 INGESTION_ENABLED ---------------------------------------------------------------


def test_switch_off_refuses_writes_with_one_audit_event_and_reads_work(db: str) -> None:
    app = create_app(Settings(ENV="test", DATABASE_URL=db, INGESTION_ENABLED=False))
    with TestClient(app) as off:
        headers = dev_token(off, "data_steward")
        cid = f"test-{uuid.uuid4().hex[:10]}"
        r = off.post(BASE, json=source_body(db), headers={**headers, "X-Correlation-ID": cid})
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "ingestion_disabled"
        [event] = sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
        assert event["outcome"] == "DENIED"
        assert event["action"] == "POST /api/v1/sources"
        assert "ingestion_disabled" in event["details"]
        listed = off.get(BASE, headers=headers)
        assert listed.status_code == 200 and listed.json()["total"] == 1
        seeded = listed.json()["items"][0]["id"]
        assert off.get(f"{BASE}/{seeded}", headers=headers).status_code == 200
        r = off.post(f"{BASE}/{seeded}/contracts", json=contract_body(db), headers=headers)
        assert r.status_code == 409 and r.json()["code"] == "ingestion_disabled"
        # Without the permission it is still a plain 403: the switch reveals nothing.
        r = off.post(BASE, json=source_body(db), headers=dev_token(off, "risk_analyst"))
        assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert sql(db, "SELECT count(*) AS n FROM source_system")[0]["n"] == 1


# --- BE-7.4 sources API -----------------------------------------------------------------------


def test_create_list_and_get(api: TestClient, db: str, steward: dict[str, str]) -> None:
    cid = f"test-{uuid.uuid4().hex[:10]}"
    r = api.post(BASE, json=source_body(db), headers={**steward, "X-Correlation-ID": cid})
    assert r.status_code == 201, r.text
    created = r.json()
    assert r.headers["X-Data-Class"] == "synthetic"
    assert (created["code"], created["kind"], created["provisional"]) == (
        "treasury_feed",
        "FILE_DROP",
        True,
    )
    profile = created["profile"]
    assert (profile["format"], profile["encoding"], profile["delimiter"]) == ("CSV", "UTF-8", ",")
    assert profile["default_classification"] == "confidential"
    [v1] = created["contracts"]
    assert created["current_contract"] == v1 and created["contract_count"] == 1
    assert (v1["version"], v1["status"], v1["schema_code"]) == (1, "PROVISIONAL", "instrument")
    assert v1["supersedes_id"] is None and len(v1["content_sha256"]) == 64
    assert v1["business_key_columns"] == ["Instrument ID"]
    # One transaction, audited as source.create.
    [event] = sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    assert (event["action"], event["outcome"], event["resource_id"]) == (
        "source.create",
        "SUCCESS",
        created["id"],
    )

    reader = dev_token(api, "risk_analyst")
    page = api.get(BASE, params={"limit": 1}, headers=reader).json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 1, 0)
    assert [s["code"] for s in page["items"]] == ["core_banking"]  # ordered by code
    page2 = api.get(BASE, params={"limit": 1, "offset": 1}, headers=reader).json()
    assert [s["code"] for s in page2["items"]] == ["treasury_feed"]
    got = api.get(f"{BASE}/{created['id']}", headers=reader)
    assert got.status_code == 200 and got.json() == created


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"kind": "WAREHOUSE"}, 422, "unsupported_source_kind"),
        ({"kind": "SFTP"}, 422, "validation_error"),
        ({"code": "Bad Code"}, 422, "validation_error"),
        (
            {
                "profile": {
                    "format": "JSON",
                    "delimiter": ",",
                    "owner": "x",
                    "default_classification": "public",
                }
            },
            422,
            "validation_error",
        ),
        (
            {
                "profile": {
                    "format": "CSV",
                    "sheet_name": "S1",
                    "owner": "x",
                    "default_classification": "public",
                }
            },
            422,
            "validation_error",
        ),
        (
            {"profile": {"format": "CSV", "owner": "x", "default_classification": "secret"}},
            422,
            "unknown_classification",
        ),
        (
            {
                "profile": {
                    "format": "CSV",
                    "encoding": "LATIN-1",
                    "owner": "x",
                    "default_classification": "public",
                }
            },
            422,
            "validation_error",
        ),
    ],
)
def test_create_refuses_invalid_sources(
    api: TestClient, db: str, steward: dict[str, str], change: Any, status: int, code: str
) -> None:
    r = api.post(BASE, json={**source_body(db), **change}, headers=steward)
    assert (r.status_code, r.json()["code"]) == (status, code), r.text
    assert sql(db, "SELECT count(*) AS n FROM source_system")[0]["n"] == 1


@pytest.mark.parametrize(
    "contract_change",
    [
        {"business_key_columns": ["Not expected"]},
        {"control_total_columns": ["Not expected"]},
        {"business_key_columns": []},
        {"expected_columns": [{"name": "A", "required": True}, {"name": "A", "required": False}]},
        {"expected_columns": [{"name": "Instrument ID"}]},  # required must be explicit
    ],
)
def test_create_refuses_inconsistent_contracts(
    api: TestClient, db: str, steward: dict[str, str], contract_change: Any
) -> None:
    body = source_body(db, contract=contract_body(db, **contract_change))
    r = api.post(BASE, json=body, headers=steward)
    assert r.status_code == 422 and r.json()["code"] == "validation_error", r.text


def test_target_schema_must_exist_here_and_be_active(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    missing = contract_body(db, schema_id=str(uuid.uuid4()))
    r = api.post(BASE, json=source_body(db, contract=missing), headers=steward)
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    draft_only = sql(
        db,
        "INSERT INTO schema_definition (id, tenant_id, code, name, owner, provisional, created_by)"
        " VALUES (gen_random_uuid(), $1, 'draft_only', 'Draft only', 'x', true, 'test')"
        " RETURNING id",
        DEFAULT_TENANT_ID,
    )[0]["id"]
    sql(
        db,
        "INSERT INTO schema_version (id, schema_id, version, status, content_sha256, created_by)"
        " VALUES (gen_random_uuid(), $1, 1, 'DRAFT', repeat('a', 64), 'test')",
        draft_only,
    )
    body = source_body(db, contract=contract_body(db, schema_id=str(draft_only)))
    r = api.post(BASE, json=body, headers=steward)
    assert (r.status_code, r.json()["code"]) == (409, "no_active_version")


def test_codes_are_unique_per_tenant(api: TestClient, db: str, steward: dict[str, str]) -> None:
    create_source(api, db, steward)
    r = api.post(BASE, json=source_body(db), headers=steward)
    assert (r.status_code, r.json()["code"]) == (409, "duplicate_code")


def test_cross_tenant_get_is_the_same_404_as_a_random_id(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    here = create_source(api, db, steward)
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    outsider = token_for(db, "dev:src-outsider", ("data_steward", "auditor"), tenant_id=other)

    def body(r: Any) -> dict[str, Any]:
        out = dict(r.json())
        out.pop("correlation_id")
        return out

    foreign = api.get(f"{BASE}/{here['id']}", headers=outsider)
    random_id = api.get(f"{BASE}/{uuid.uuid4()}", headers=outsider)
    assert foreign.status_code == random_id.status_code == 404
    assert (
        body(foreign)
        == body(random_id)
        == {
            "code": "not_found",
            "message": "Source not found.",
            "details": None,
            "retryable": False,
        }
    )
    assert api.get(BASE, headers=outsider).json() == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
    }
    # Writes by id and against this tenant's schema find nothing either.
    r = api.post(f"{BASE}/{here['id']}/contracts", json=contract_body(db), headers=outsider)
    assert r.status_code == 404
    r = api.post(BASE, json=source_body(db, code="outsider_feed"), headers=outsider)
    assert (r.status_code, r.json()["message"]) == (404, "Schema not found.")
    assert (
        sql(db, "SELECT count(*) AS n FROM source_system WHERE tenant_id = $1", other)[0]["n"] == 0
    )


@pytest.mark.parametrize("role", ["risk_analyst", "auditor", "approver", "platform_admin"])
def test_post_without_source_manage_is_403_and_audited(api: TestClient, db: str, role: str) -> None:
    cid = f"test-{uuid.uuid4().hex[:10]}"
    headers = {**dev_token(api, role), "X-Correlation-ID": cid}
    r = api.post(BASE, json=source_body(db), headers=headers)
    assert r.status_code == 403
    assert r.json()["details"] == {"required_permission": "source:manage"}
    [event] = sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    assert (event["outcome"], event["action"]) == ("DENIED", "POST /api/v1/sources")
    assert sql(db, "SELECT count(*) AS n FROM source_system")[0]["n"] == 1


def test_new_contract_version_supersedes_the_previous_unchanged(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    created = create_source(api, db, steward)
    v1 = created["contracts"][0]
    row_before = sql(db, "SELECT * FROM source_contract WHERE id = $1", uuid.UUID(v1["id"]))[0]
    cid = f"test-{uuid.uuid4().hex[:10]}"
    columns = [*contract_body(db)["expected_columns"], {"name": "Maturity", "required": False}]
    r = api.post(
        f"{BASE}/{created['id']}/contracts",
        json=contract_body(db, expected_columns=columns, change_summary="Adds maturity."),
        headers={**steward, "X-Correlation-ID": cid},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert (v2["version"], v2["status"], v2["supersedes_id"]) == (2, "PROVISIONAL", v1["id"])
    assert v2["content_sha256"] != v1["content_sha256"]

    detail = api.get(f"{BASE}/{created['id']}", headers=dev_token(api, "auditor")).json()
    old, new = detail["contracts"]
    assert old["id"] == v1["id"] and old["status"] == "SUPERSEDED"
    assert old["content_sha256"] == v1["content_sha256"]
    assert detail["current_contract"]["id"] == v2["id"] and detail["contract_count"] == 2
    # v1 was not edited: only its status (and updated_at) moved.
    row_after = sql(db, "SELECT * FROM source_contract WHERE id = $1", uuid.UUID(v1["id"]))[0]
    unchanged = [k for k in row_before.keys() if k not in ("status", "updated_at")]
    assert {k: row_after[k] for k in unchanged} == {k: row_before[k] for k in unchanged}
    actions = {
        e["action"]: e for e in sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    }
    assert set(actions) == {"source_contract.create", "source_contract.supersede"}
    assert all(e["resource_id"] == created["id"] for e in actions.values())

    # The same content again is not a new version; a superseded contract stays frozen.
    r = api.post(
        f"{BASE}/{created['id']}/contracts",
        json=contract_body(db, expected_columns=columns),
        headers=steward,
    )
    assert (r.status_code, r.json()["code"]) == (422, "no_change")
    with pytest.raises(IMMUTABLE):
        sql(db, "UPDATE source_contract SET status = 'APPROVED' WHERE id = $1", row_before["id"])


def test_contracts_route_needs_source_manage_and_an_existing_source(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    created = create_source(api, db, steward)
    r = api.post(
        f"{BASE}/{created['id']}/contracts",
        json=contract_body(db),
        headers=dev_token(api, "approver"),
    )
    assert r.status_code == 403
    r = api.post(f"{BASE}/{uuid.uuid4()}/contracts", json=contract_body(db), headers=steward)
    assert (r.status_code, r.json()["code"]) == (404, "not_found")


# --- content hash: service and the seed migration's frozen copy agree ------------------------


def _migration(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_hash_matches_the_service(db: str) -> None:
    seeded = seeded_contract(db)
    import json

    assert seeded["content_sha256"] == contract_sha256(
        seeded["schema_id"],
        json.loads(seeded["expected_columns"]),
        json.loads(seeded["business_key_columns"]),
        json.loads(seeded["control_total_columns"]),
    )
    frozen = _migration("0011_ws2_source_seed").contract_sha256
    args = (
        str(uuid.uuid4()),
        [{"name": "b", "required": False}, {"name": "a", "required": True}],
        ["a"],
        ["b"],
    )
    assert frozen(*args) == contract_sha256(*args)


def test_column_order_is_not_part_of_the_hash() -> None:
    a, b = {"name": "a", "required": True}, {"name": "b", "required": False}
    sid = uuid.uuid4()
    assert contract_sha256(sid, [a, b], ["a"], []) == contract_sha256(sid, [b, a], ["a"], [])
    assert contract_sha256(sid, [a, b], ["a"], []) != contract_sha256(sid, [a, b], ["b"], [])


# --- BE-7.5 seeded illustrative source --------------------------------------------------------


def test_seeded_source_is_listed_as_provisional(api: TestClient, db: str) -> None:
    page = api.get(BASE, headers=dev_token(api, "auditor")).json()
    [seeded] = page["items"]
    assert (seeded["code"], seeded["name"]) == ("core_banking", "core_banking (illustrative)")
    assert seeded["provisional"] is True and seeded["profile"]["provisional"] is True
    contract = seeded["current_contract"]
    assert (contract["status"], contract["schema_code"]) == ("PROVISIONAL", "instrument")
    # The contract describes the synthetic instrument sample exactly.
    header = build()["instrument.csv"].decode().splitlines()[1].split(",")
    assert [c["name"] for c in contract["expected_columns"]] == header
    tags = sql(
        db,
        "SELECT (SELECT count(*) FROM source_system WHERE seeded_by = $1)"
        " + (SELECT count(*) FROM source_profile WHERE seeded_by = $1)"
        " + (SELECT count(*) FROM source_contract WHERE seeded_by = $1) AS n",
        SOURCE_SEED_TAG,
    )
    assert tags[0]["n"] == 3


def test_seed_downgrade_removes_only_its_rows(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    cfg = alembic_config(db)
    # A user's own source must survive the seed downgrade untouched.
    user = create_source(api, db, steward)
    user_rows = {
        t: [r for r in rows if r not in _seed_rows(db)[t]] for t, rows in source_rows(db).items()
    }
    assert all(len(rows) == 1 for rows in user_rows.values())
    seed_before = _seed_rows(db)
    command.downgrade(cfg, "0010")
    try:
        assert revision(db) == "0010"
        after = source_rows(db)
        assert after == user_rows  # the seed is gone, the user's rows are exactly as before
        assert (
            sql(
                db,
                "SELECT count(*) AS n FROM source_contract WHERE seeded_by = $1",
                SOURCE_SEED_TAG,
            )[0]["n"]
            == 0
        )
    finally:
        command.upgrade(cfg, "head")
    assert seeded_contract(db)["status"] == "PROVISIONAL"
    reseeded = _seed_rows(db)
    # Same uuid5 IDs, newly inserted rows.
    assert {t: [r[0] for r in rows] for t, rows in reseeded.items()} == {
        t: [r[0] for r in rows] for t, rows in seed_before.items()
    }
    got = api.get(f"{BASE}/{user['id']}", headers=steward)
    assert got.status_code == 200 and got.json() == user
    # With a user's contract version on the seeded source, the seed downgrade refuses on the
    # foreign key instead of deleting user data.
    seeded_id = sql(db, "SELECT id FROM source_system WHERE seeded_by = $1", SOURCE_SEED_TAG)[0][
        "id"
    ]
    r = api.post(f"{BASE}/{seeded_id}/contracts", json=contract_body(db), headers=steward)
    assert r.status_code == 201
    before = source_rows(db)
    try:
        with pytest.raises(Exception, match="ForeignKeyViolation|foreign key"):
            command.downgrade(cfg, "0010")
        assert revision(db) == head()
        assert source_rows(db) == before
    finally:
        command.upgrade(cfg, "head")
    assert source_rows(db) == before


def _seed_rows(db: str) -> dict[str, list[Any]]:
    return {
        t: [
            tuple(r)
            for r in sql(
                db,
                f"SELECT id, created_at FROM {t} WHERE seeded_by = $1 ORDER BY id",  # noqa: S608
                SOURCE_SEED_TAG,
            )
        ]
        for t in SOURCE_TABLES
    }
