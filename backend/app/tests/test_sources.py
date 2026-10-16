"""Sources (Phase 7): registration with contract v1, a new contract version supersedes v1
unchanged, contract content is frozen in the database, and every business refusal (and the
INGESTION_ENABLED switch) is audited with its reason and writes nothing."""

import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.governance.constants import DEFAULT_TENANT_ID
from app.ingestion.constants import SOURCE_SEED_TAG
from app.main import create_app
from app.tests.conftest import dev_token, sql
from app.tests.helpers import SOURCES, assert_denied, cid, contract_body, events, source_body

pytestmark = pytest.mark.db

IMMUTABLE = asyncpg.InsufficientPrivilegeError


def contract_row(db: str, contract_id: Any) -> Any:
    return sql(db, "SELECT * FROM source_contract WHERE id = $1", contract_id)[0]


def test_register_a_source_and_supersede_its_contract(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    correlation = cid()
    r = api.post(
        SOURCES, json=source_body(db), headers={**steward, "X-Correlation-ID": correlation}
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert (created["code"], created["kind"], created["provisional"]) == (
        "treasury_feed",
        "FILE_DROP",
        True,
    )
    [v1] = created["contracts"]
    assert (v1["version"], v1["status"], v1["schema_code"]) == (1, "PROVISIONAL", "instrument")
    [event] = events(db, correlation)
    assert (event["action"], event["outcome"], event["resource_id"]) == (
        "source.create",
        "SUCCESS",
        created["id"],
    )
    reader = dev_token(api, "risk_analyst")
    assert api.get(f"{SOURCES}/{created['id']}", headers=reader).json() == created
    assert api.get(SOURCES, headers=reader).json()["total"] == 2  # with the seeded source

    row_before = contract_row(db, uuid.UUID(v1["id"]))
    columns = [*contract_body(db)["expected_columns"], {"name": "Maturity", "required": False}]
    correlation = cid()
    r = api.post(
        f"{SOURCES}/{created['id']}/contracts",
        json=contract_body(db, expected_columns=columns, change_summary="Adds maturity."),
        headers={**steward, "X-Correlation-ID": correlation},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert (v2["version"], v2["status"], v2["supersedes_id"]) == (2, "PROVISIONAL", v1["id"])
    assert v2["content_sha256"] != v1["content_sha256"]
    detail = api.get(f"{SOURCES}/{created['id']}", headers=reader).json()
    assert [c["status"] for c in detail["contracts"]] == ["SUPERSEDED", "PROVISIONAL"]
    assert detail["current_contract"]["id"] == v2["id"]
    # v1 was not edited: only its status (and updated_at) moved.
    row_after = contract_row(db, row_before["id"])
    unchanged = [k for k in row_before.keys() if k not in ("status", "updated_at")]
    assert {k: row_after[k] for k in unchanged} == {k: row_before[k] for k in unchanged}
    assert {e["action"] for e in events(db, correlation)} == {
        "source_contract.create",
        "source_contract.supersede",
    }
    # SUPERSEDED is final.
    with pytest.raises(IMMUTABLE):
        sql(db, "UPDATE source_contract SET status = 'APPROVED' WHERE id = $1", row_before["id"])


CONTENT_UPDATES = [
    "UPDATE source_contract SET expected_columns = '[]' WHERE id = $1",
    "UPDATE source_contract SET business_key_columns = '[\"currency\"]' WHERE id = $1",
    "UPDATE source_contract SET control_total_columns = '[]' WHERE id = $1",
    "UPDATE source_contract SET content_sha256 = repeat('0', 64) WHERE id = $1",
    "UPDATE source_contract SET version = 7 WHERE id = $1",
    "UPDATE source_contract SET schema_id = (SELECT id FROM schema_definition"
    " WHERE code = 'position') WHERE id = $1",
    "UPDATE source_contract SET change_summary = 'rewritten' WHERE id = $1",
    "UPDATE source_contract SET seeded_by = NULL WHERE id = $1",
    "UPDATE source_contract SET created_by = 'someone-else' WHERE id = $1",
    "UPDATE source_contract SET id = gen_random_uuid() WHERE id = $1",
]


def test_contract_content_is_frozen_in_the_database(db: str) -> None:
    [seeded] = sql(db, "SELECT * FROM source_contract WHERE seeded_by = $1", SOURCE_SEED_TAG)
    contract_id = seeded["id"]
    # PROVISIONAL -> IN_REVIEW -> APPROVED: content frozen in every status.
    for status in ("PROVISIONAL", "IN_REVIEW", "APPROVED"):
        if status != "PROVISIONAL":
            sql(db, "UPDATE source_contract SET status = $2 WHERE id = $1", contract_id, status)
        before = contract_row(db, contract_id)
        for statement in CONTENT_UPDATES:
            with pytest.raises(IMMUTABLE, match="immutable"):
                sql(db, statement, contract_id)
            assert contract_row(db, contract_id) == before, (status, statement)
    with pytest.raises(IMMUTABLE):
        sql(db, "DELETE FROM source_contract WHERE id = $1", contract_id)
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE source_contract CASCADE")
    for table in ("source_system", "source_profile"):
        for statement in (f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(IMMUTABLE, match="never deleted"):
                sql(db, statement)
    assert sql(db, "SELECT count(*) AS n FROM source_contract")[0]["n"] == 1


def _draft_only_schema(db: str) -> str:
    sid = sql(
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
        sid,
    )
    return str(sid)


def test_business_refusals_are_audited_and_write_nothing(api: TestClient, db: str) -> None:
    steward = dev_token(api, "data_steward")
    existing = api.post(SOURCES, json=source_body(db, code="refused_feed"), headers=steward)
    assert existing.status_code == 201, existing.text
    mine = existing.json()["id"]
    draft_only = _draft_only_schema(db)
    body = source_body(db, code="new_feed")
    cases: list[tuple[str, str, Any, int, str]] = [
        ("source.create", SOURCES, {**body, "kind": "WAREHOUSE"}, 422, "unsupported_source_kind"),
        (
            "source.create",
            SOURCES,
            {**body, "profile": {**body["profile"], "default_classification": "secret"}},
            422,
            "unknown_classification",
        ),
        (
            "source.create",
            SOURCES,
            {**body, "contract": contract_body(db, schema_id=draft_only)},
            409,
            "no_active_version",
        ),
        (
            "source.create",
            SOURCES,
            {**body, "contract": contract_body(db, schema_id=str(uuid.uuid4()))},
            404,
            "not_found",
        ),
        ("source.create", SOURCES, source_body(db, code="refused_feed"), 409, "duplicate_code"),
        (
            "source_contract.create",
            f"{SOURCES}/{mine}/contracts",
            contract_body(db),
            422,
            "no_change",
        ),
        (
            "source_contract.create",
            f"{SOURCES}/{uuid.uuid4()}/contracts",
            contract_body(db),
            404,
            "not_found",
        ),
    ]
    sources = sql(db, "SELECT id FROM source_system ORDER BY id")
    contracts = sql(db, "SELECT id, status FROM source_contract ORDER BY id")
    for action, path, payload, status, reason in cases:
        correlation = cid()
        r = api.post(path, json=payload, headers={**steward, "X-Correlation-ID": correlation})
        assert (r.status_code, r.json()["code"]) == (status, reason), (reason, r.text)
        assert_denied(db, correlation, reason, action=action)
    assert sql(db, "SELECT id FROM source_system ORDER BY id") == sources
    assert sql(db, "SELECT id, status FROM source_contract ORDER BY id") == contracts
    # A malformed body is a plain 422 and is not audited.
    correlation = cid()
    r = api.post(
        SOURCES,
        json={**body, "code": "Bad Code"},
        headers={**steward, "X-Correlation-ID": correlation},
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")
    assert events(db, correlation) == []

    # INGESTION_ENABLED=false: writes refused with one DENIED event, reads still work.
    with TestClient(
        create_app(Settings(ENV="test", DATABASE_URL=db, INGESTION_ENABLED=False))
    ) as off:
        headers = dev_token(off, "data_steward")
        correlation = cid()
        r = off.post(SOURCES, json=body, headers={**headers, "X-Correlation-ID": correlation})
        assert (r.status_code, r.json()["code"]) == (409, "ingestion_disabled")
        [event] = events(db, correlation)
        assert (event["outcome"], event["action"]) == ("DENIED", "POST /api/v1/sources")
        r = off.post(f"{SOURCES}/{mine}/contracts", json=contract_body(db), headers=headers)
        assert (r.status_code, r.json()["code"]) == (409, "ingestion_disabled")
        assert off.get(SOURCES, headers=headers).json()["total"] == 2
    assert sql(db, "SELECT id FROM source_system ORDER BY id") == sources
