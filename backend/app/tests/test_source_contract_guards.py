"""Phase 7 review follow-ups in the database (docs/evidence/phase-7/REPORT.md D1, D5, D6,
D7, deviation 4; D-24 §3): a source contract's content is frozen from creation in every
status, status moves only along the transition table, a source never moves tenant, a source
has one profile, and the seed keeps its IDs across a downgrade and upgrade."""

import uuid
from typing import Any

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.governance.constants import DEFAULT_TENANT_ID
from app.ingestion.constants import CONTRACT_TRANSITIONS, SOURCE_SEED_TAG, ContractStatus
from app.tests.conftest import alembic_config, dev_token, insert_tenant, sql
from app.tests.test_sources import (
    IMMUTABLE,
    _migration,
    create_source,
    seeded_contract,
)

pytestmark = pytest.mark.db

STATUSES = [s.value for s in ContractStatus]


@pytest.fixture
def steward(api: TestClient) -> dict[str, str]:
    return dev_token(api, "data_steward")


def _row(db: str, contract_id: Any) -> Any:
    return sql(db, "SELECT * FROM source_contract WHERE id = $1", contract_id)[0]


def _move_seed_to(db: str, status: str) -> Any:
    """The seeded contract, moved along an allowed path to `status`."""
    contract = seeded_contract(db)
    path = {
        "PROVISIONAL": [],
        "IN_REVIEW": ["IN_REVIEW"],
        "APPROVED": ["APPROVED"],
        "SUPERSEDED": ["SUPERSEDED"],
    }[status]
    for step in path:
        sql(db, "UPDATE source_contract SET status = $2 WHERE id = $1", contract["id"], step)
    return _row(db, contract["id"])


# --- D1 + D6 (D-24 §3): content frozen from creation; transitions in the trigger ------------

CONTENT_UPDATES = [
    "UPDATE source_contract SET expected_columns = '[]' WHERE id = $1",
    "UPDATE source_contract SET business_key_columns = '[\"currency\"]' WHERE id = $1",
    "UPDATE source_contract SET control_total_columns = '[]' WHERE id = $1",
    "UPDATE source_contract SET content_sha256 = repeat('0', 64) WHERE id = $1",
    "UPDATE source_contract SET version = 7 WHERE id = $1",
    "UPDATE source_contract SET schema_id = (SELECT id FROM schema_definition"
    " WHERE code = 'position') WHERE id = $1",
    "UPDATE source_contract SET supersedes_id = id WHERE id = $1",
    "UPDATE source_contract SET change_summary = 'rewritten' WHERE id = $1",
    "UPDATE source_contract SET seeded_by = NULL WHERE id = $1",
    "UPDATE source_contract SET created_by = 'someone-else' WHERE id = $1",
    "UPDATE source_contract SET created_at = now() - interval '1 day' WHERE id = $1",
    "UPDATE source_contract SET id = gen_random_uuid() WHERE id = $1",
]


@pytest.mark.parametrize("statement", CONTENT_UPDATES)
def test_provisional_contract_content_is_frozen(db: str, statement: str) -> None:
    before = seeded_contract(db)
    assert before["status"] == "PROVISIONAL"
    with pytest.raises(IMMUTABLE, match="immutable"):
        sql(db, statement, before["id"])
    assert _row(db, before["id"]) == before


@pytest.mark.parametrize("status", ["IN_REVIEW", "APPROVED"])
@pytest.mark.parametrize("statement", CONTENT_UPDATES)
def test_contract_content_is_frozen_in_every_status(db: str, status: str, statement: str) -> None:
    before = _move_seed_to(db, status)
    with pytest.raises(IMMUTABLE, match="immutable"):
        sql(db, statement, before["id"])
    assert _row(db, before["id"]) == before


def test_provisional_contract_may_still_touch_updated_at(db: str) -> None:
    contract = seeded_contract(db)
    sql(db, "UPDATE source_contract SET updated_at = now() WHERE id = $1", contract["id"])
    assert _row(db, contract["id"])["status"] == "PROVISIONAL"


@pytest.mark.parametrize("target", STATUSES)
@pytest.mark.parametrize("start", STATUSES)
def test_contract_status_moves_only_along_the_transition_table(
    db: str, start: str, target: str
) -> None:
    before = _move_seed_to(db, start)
    allowed = ContractStatus(target) in CONTRACT_TRANSITIONS[ContractStatus(start)]
    statement = "UPDATE source_contract SET status = $2 WHERE id = $1"
    if allowed:
        sql(db, statement, before["id"], target)
        assert _row(db, before["id"])["status"] == target
    elif start == target and start != "SUPERSEDED":
        sql(db, statement, before["id"], target)  # not a move: nothing to refuse
        assert _row(db, before["id"])["status"] == target
    else:
        with pytest.raises(IMMUTABLE, match="immutable"):
            sql(db, statement, before["id"], target)
        assert _row(db, before["id"]) == before


def test_trigger_transition_table_matches_the_service() -> None:
    frozen = _migration("0009_source_registration_tables").CONTRACT_TRANSITIONS
    assert frozen == {str(k): sorted(str(v) for v in vs) for k, vs in CONTRACT_TRANSITIONS.items()}


# --- D5: a source never moves tenant, a profile never moves source --------------------------


def test_sources_cannot_move_tenant(api: TestClient, db: str, steward: dict[str, str]) -> None:
    other = insert_tenant(db, f"other-{uuid.uuid4().hex[:6]}")
    seeded = sql(db, "SELECT * FROM source_system WHERE seeded_by = $1", SOURCE_SEED_TAG)[0]
    with pytest.raises(IMMUTABLE, match="cannot move"):
        sql(db, "UPDATE source_system SET tenant_id = $2 WHERE id = $1", seeded["id"], other)
    created = create_source(api, db, steward)
    profile = sql(db, "SELECT * FROM source_profile WHERE source_system_id = $1", seeded["id"])[0]
    with pytest.raises(IMMUTABLE, match="cannot move"):
        sql(
            db,
            "UPDATE source_profile SET source_system_id = $2 WHERE id = $1",
            profile["id"],
            uuid.UUID(created["id"]),
        )
    after = sql(db, "SELECT tenant_id FROM source_system WHERE id = $1", seeded["id"])[0]
    assert after["tenant_id"] == DEFAULT_TENANT_ID
    assert sql(db, "SELECT * FROM source_profile WHERE id = $1", profile["id"])[0] == profile


# --- Deviation 4: one profile per source ---------------------------------------------------


def test_a_source_has_at_most_one_profile(db: str) -> None:
    seeded = sql(db, "SELECT id FROM source_system WHERE seeded_by = $1", SOURCE_SEED_TAG)[0]
    with pytest.raises(asyncpg.UniqueViolationError):
        sql(
            db,
            "INSERT INTO source_profile (id, source_system_id, format, encoding, delimiter,"
            " owner, default_classification, provisional, created_by) VALUES"
            " (gen_random_uuid(), $1, 'CSV', 'UTF-8', ',', 'x', 'public', true, 'test')",
            seeded["id"],
        )


# --- D7: seed IDs survive a downgrade and upgrade ------------------------------------------------


def _seed_ids(db: str) -> dict[str, Any]:
    return {
        table: sql(db, f"SELECT id FROM {table} WHERE seeded_by = $1", SOURCE_SEED_TAG)[0]["id"]  # noqa: S608
        for table in ("source_system", "source_profile", "source_contract")
    }


def test_seed_ids_are_stable(db: str) -> None:
    before = _seed_ids(db)
    cfg = alembic_config(db)
    command.downgrade(cfg, "0010")
    command.upgrade(cfg, "head")
    assert _seed_ids(db) == before
