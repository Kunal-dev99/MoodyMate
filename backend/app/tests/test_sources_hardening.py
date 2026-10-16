"""Phase 7 review follow-ups in the API (docs/evidence/phase-7/REPORT.md D2, D3, D4, D8,
D9, D12; D-24 §4): the create body cannot mark a profile approved, every refused write is
audited with its reason, the switch guards every write route, request text is safe,
concurrent writes serialise, and the page total matches its items."""

import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.governance.constants import DEFAULT_TENANT_ID
from app.ingestion.constants import CONTRACT_TRANSITIONS, ContractStatus
from app.ingestion.router import ingestion_enabled
from app.main import create_app
from app.tests.conftest import dev_token, sql
from app.tests.test_sources import (
    BASE,
    contract_body,
    create_source,
    source_body,
)

pytestmark = pytest.mark.db

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture
def steward(api: TestClient) -> dict[str, str]:
    return dev_token(api, "data_steward")


# --- D2: the registrant cannot mark a profile approved -------------------------------------


def test_registrant_cannot_mark_a_profile_approved(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    body = source_body(db)
    body["profile"] = {**body["profile"], "provisional": False}
    r = api.post(BASE, json=body, headers=steward)
    # The API ignores unknown body fields (no extra=forbid anywhere); `provisional` is not
    # part of the create body, so it is ignored and the profile is provisional.
    assert r.status_code == 201, r.text
    assert r.json()["provisional"] is True and r.json()["profile"]["provisional"] is True
    stored = sql(
        db,
        "SELECT p.provisional FROM source_profile p JOIN source_system s"
        " ON s.id = p.source_system_id WHERE s.code = $1",
        body["code"],
    )
    assert [r["provisional"] for r in stored] == [True]


def test_create_body_has_no_provisional_field(api: TestClient) -> None:
    spec = api.app.openapi()  # type: ignore[attr-defined]
    assert "provisional" not in spec["components"]["schemas"]["ProfileIn"]["properties"]


# --- D3 (D-24 §4): every refused WS2 write is audited with its reason -------------------------


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


Setup = Callable[[TestClient, str, dict[str, str]], tuple[str, Any]]


def _create(change: dict[str, Any]) -> Setup:
    def setup(api: TestClient, db: str, steward: dict[str, str]) -> tuple[str, Any]:
        return BASE, {**source_body(db, code="refused_feed"), **change}

    return setup


def _create_with_profile(**profile: Any) -> Setup:
    def setup(api: TestClient, db: str, steward: dict[str, str]) -> tuple[str, Any]:
        body = source_body(db, code="refused_feed")
        return BASE, {**body, "profile": {**body["profile"], **profile}}

    return setup


def _create_against(schema: Callable[[str], str]) -> Setup:
    def setup(api: TestClient, db: str, steward: dict[str, str]) -> tuple[str, Any]:
        contract = contract_body(db, schema_id=schema(db))
        return BASE, source_body(db, code="refused_feed", contract=contract)

    return setup


def _duplicate(api: TestClient, db: str, steward: dict[str, str]) -> tuple[str, Any]:
    create_source(api, db, steward, code="refused_feed")
    return BASE, source_body(db, code="refused_feed")


def _seeded_source(api: TestClient, steward: dict[str, str]) -> str:
    return str(api.get(BASE, headers=steward).json()["items"][0]["id"])


def _same_contract(api: TestClient, db: str, steward: dict[str, str]) -> tuple[str, Any]:
    created = create_source(api, db, steward, code="refused_feed")
    return f"{BASE}/{created['id']}/contracts", contract_body(db)


def _contract_for_missing_source(
    api: TestClient, db: str, steward: dict[str, str]
) -> tuple[str, Any]:
    return f"{BASE}/{uuid.uuid4()}/contracts", contract_body(db)


def _contract_against(schema: Callable[[str], str]) -> Setup:
    def setup(api: TestClient, db: str, steward: dict[str, str]) -> tuple[str, Any]:
        source = _seeded_source(api, steward)
        return f"{BASE}/{source}/contracts", contract_body(db, schema_id=schema(db))

    return setup


def _missing_schema(db: str) -> str:
    return str(uuid.uuid4())


REFUSALS: list[Any] = [
    pytest.param(
        _create({"kind": "WAREHOUSE"}),
        422,
        "unsupported_source_kind",
        "source.create",
        id="unsupported_source_kind",
    ),
    pytest.param(
        _create_with_profile(default_classification="secret"),
        422,
        "unknown_classification",
        "source.create",
        id="unknown_classification",
    ),
    pytest.param(
        _create_against(_draft_only_schema),
        409,
        "no_active_version",
        "source.create",
        id="create_no_active_version",
    ),
    pytest.param(
        _create_against(_missing_schema),
        404,
        "not_found",
        "source.create",
        id="create_schema_not_found",
    ),
    pytest.param(_duplicate, 409, "duplicate_code", "source.create", id="duplicate_code"),
    pytest.param(
        _same_contract,
        422,
        "no_change",
        "source_contract.create",
        id="no_change",
    ),
    pytest.param(
        _contract_for_missing_source,
        404,
        "not_found",
        "source_contract.create",
        id="contract_source_not_found",
    ),
    pytest.param(
        _contract_against(_draft_only_schema),
        409,
        "no_active_version",
        "source_contract.create",
        id="contract_no_active_version",
    ),
    pytest.param(
        _contract_against(_missing_schema),
        404,
        "not_found",
        "source_contract.create",
        id="contract_schema_not_found",
    ),
]


@pytest.mark.parametrize(("setup", "status", "reason", "action"), REFUSALS)
def test_business_refusals_are_audited(
    api: TestClient,
    db: str,
    steward: dict[str, str],
    setup: Setup,
    status: int,
    reason: str,
    action: str,
) -> None:
    path, body = setup(api, db, steward)
    cid = f"test-{uuid.uuid4().hex[:10]}"
    sources_before = sql(db, "SELECT id FROM source_system ORDER BY id")
    contracts_before = sql(db, "SELECT id, status FROM source_contract ORDER BY id")
    r = api.post(path, json=body, headers={**steward, "X-Correlation-ID": cid})
    assert (r.status_code, r.json()["code"]) == (status, reason), r.text
    [event] = sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    assert (event["outcome"], event["action"]) == ("DENIED", action)
    assert event["resource_type"] == "source_system"
    assert f'"reason": "{reason}"' in event["details"]
    # Nothing was written.
    assert sql(db, "SELECT id FROM source_system ORDER BY id") == sources_before
    assert sql(db, "SELECT id, status FROM source_contract ORDER BY id") == contracts_before


def test_invalid_status_refusal_is_audited(
    api: TestClient, db: str, steward: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreachable with today's table (every live status may be superseded); a table that
    forbids it must still refuse with an audited invalid_status."""
    monkeypatch.setitem(CONTRACT_TRANSITIONS, ContractStatus.PROVISIONAL, frozenset())
    source = _seeded_source(api, steward)
    columns = [*contract_body(db)["expected_columns"], {"name": "Extra", "required": False}]
    cid = f"test-{uuid.uuid4().hex[:10]}"
    r = api.post(
        f"{BASE}/{source}/contracts",
        json=contract_body(db, expected_columns=columns),
        headers={**steward, "X-Correlation-ID": cid},
    )
    assert (r.status_code, r.json()["code"]) == (409, "invalid_status"), r.text
    [event] = sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid)
    assert (event["outcome"], event["action"]) == ("DENIED", "source_contract.create")
    assert '"reason": "invalid_status"' in event["details"]


def test_malformed_bodies_are_not_audited(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    cid = f"test-{uuid.uuid4().hex[:10]}"
    r = api.post(
        BASE,
        json={**source_body(db), "code": "Bad Code"},
        headers={**steward, "X-Correlation-ID": cid},
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")
    assert sql(db, "SELECT * FROM audit_event WHERE correlation_id = $1", cid) == []


# --- D4: every WS2 write route honours INGESTION_ENABLED ----------------------------------------


def _calls(dependant: Any) -> Iterator[Any]:
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def test_every_ws2_write_route_honours_ingestion_enabled(api: TestClient) -> None:
    writes = {
        f"{m} {route.path}": route
        for route in api.app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute) and route.endpoint.__module__ == "app.ingestion.router"
        for m in route.methods
        if m in MUTATING
    }
    assert writes, "no WS2 write routes found: the test would be vacuous"
    missing = sorted(
        k for k, r in writes.items() if ingestion_enabled not in set(_calls(r.dependant))
    )
    assert missing == []


# --- D8: safe text ----------------------------------------------------------------------------


def _profile(**change: Any) -> Callable[[str], dict[str, Any]]:
    def body(db: str) -> dict[str, Any]:
        base = source_body(db)
        return {**base, "profile": {**base["profile"], **change}}

    return body


def _xlsx(sheet: str) -> Callable[[str], dict[str, Any]]:
    return _profile(format="XLSX", sheet_name=sheet)


def _top(**change: Any) -> Callable[[str], dict[str, Any]]:
    return lambda db: {**source_body(db), **change}


def _contract(**change: Any) -> Callable[[str], dict[str, Any]]:
    return lambda db: source_body(db, contract=contract_body(db, **change))


def _columns(name: str) -> Callable[[str], dict[str, Any]]:
    columns = [{"name": "Instrument ID", "required": True}, {"name": name, "required": False}]
    return _contract(expected_columns=columns)


UNSAFE = [
    pytest.param(_top(name="Bad\x00name"), id="name-nul"),
    pytest.param(_top(name="   "), id="name-blank"),
    pytest.param(_top(name="Bell\x07"), id="name-control"),
    pytest.param(_top(name="Two\nlines"), id="name-newline"),
    pytest.param(_top(description="nul\x00here"), id="description-nul"),
    pytest.param(_top(description="  \t "), id="description-blank"),
    pytest.param(_profile(owner="\x00"), id="owner-nul"),
    pytest.param(_profile(owner="   "), id="owner-blank"),
    pytest.param(_profile(cadence="daily\x1b"), id="cadence-escape"),
    pytest.param(_profile(cadence=" "), id="cadence-blank"),
    pytest.param(_columns("Bad\x00col"), id="column-nul"),
    pytest.param(_columns("Tab\tcol"), id="column-tab"),
    pytest.param(_columns("\u0085"), id="column-c1-control"),
    pytest.param(_contract(business_key_columns=["Instrument\x00ID"]), id="key-nul"),
    pytest.param(_contract(change_summary="sum\x00mary"), id="summary-nul"),
    pytest.param(_contract(change_summary="   "), id="summary-blank"),
    *[
        pytest.param(_xlsx(f"Sheet{c}1"), id=f"sheet-{name}")
        for c, name in [
            ("[", "lbracket"),
            ("]", "rbracket"),
            (":", "colon"),
            ("*", "star"),
            ("?", "question"),
            ("/", "slash"),
            ("\\", "backslash"),
        ]
    ],
    pytest.param(_xlsx("Sheet\x001"), id="sheet-nul"),
    pytest.param(_xlsx("  "), id="sheet-blank"),
]


@pytest.mark.parametrize("make", UNSAFE)
def test_create_refuses_unsafe_text(
    api: TestClient, db: str, steward: dict[str, str], make: Callable[[str], dict[str, Any]]
) -> None:
    r = api.post(BASE, json=make(db), headers=steward)
    assert (r.status_code, r.json()["code"]) == (422, "validation_error"), r.text
    assert sql(db, "SELECT count(*) AS n FROM source_system")[0]["n"] == 1


def test_contract_route_refuses_unsafe_text(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    source = _seeded_source(api, steward)
    columns = [{"name": "Instrument ID", "required": True}, {"name": "X\x00", "required": False}]
    r = api.post(
        f"{BASE}/{source}/contracts",
        json=contract_body(db, expected_columns=columns),
        headers=steward,
    )
    assert (r.status_code, r.json()["code"]) == (422, "validation_error"), r.text


def test_safe_text_keeps_ordinary_text(api: TestClient, db: str, steward: dict[str, str]) -> None:
    """Strips edges; multi-line description and summary keep their line breaks; an XLSX
    sheet name may hold spaces, dots and dashes."""
    body = source_body(
        db,
        name="  Treasury feed  ",
        description="Line one.\nLine two.",
        contract=contract_body(db, change_summary="First cut.\r\nFrom the sample."),
    )
    body["profile"] = {
        "format": "XLSX",
        "sheet_name": "Positions - 2026.10",
        "owner": " Treasury data team ",
        "default_classification": "confidential",
    }
    r = api.post(BASE, json=body, headers=steward)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["name"], out["description"]) == ("Treasury feed", "Line one.\nLine two.")
    assert out["profile"]["owner"] == "Treasury data team"
    assert out["profile"]["sheet_name"] == "Positions - 2026.10"
    assert out["contracts"][0]["change_summary"] == "First cut.\r\nFrom the sample."


# --- D9: concurrent writes serialise ---------------------------------------------------------


@pytest.fixture
def clients(db: str) -> Iterator[list[TestClient]]:
    """Independent apps (own engine, own event loop, own connections) for real concurrency."""
    apps = [create_app(Settings(ENV="test", DATABASE_URL=db)) for _ in range(4)]
    opened = [TestClient(a) for a in apps]
    for c in opened:
        c.__enter__()
    try:
        yield opened
    finally:
        for c in opened:
            c.__exit__(None, None, None)


def _race(clients: list[TestClient], calls: list[tuple[str, Any]], headers: Any) -> list[Any]:
    barrier = threading.Barrier(len(calls))

    def run(i: int) -> Any:
        path, body = calls[i]
        barrier.wait()
        return clients[i].post(path, json=body, headers=headers)

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(run, range(len(calls))))


def _outcome(r: Any) -> tuple[int, str | None]:
    return r.status_code, (r.json()["code"] if r.status_code >= 400 else None)


def test_concurrent_new_versions_serialise(
    api: TestClient, db: str, steward: dict[str, str], clients: list[TestClient]
) -> None:
    created = create_source(api, db, steward)
    path = f"{BASE}/{created['id']}/contracts"
    base_cols = contract_body(db)["expected_columns"]

    # Different content at once: v2..v5, one current, a linear supersedes chain.
    calls = [
        (
            path,
            contract_body(db, expected_columns=[*base_cols, {"name": f"C{i}", "required": False}]),
        )
        for i in range(4)
    ]
    results = _race(clients, calls, steward)
    assert [r.status_code for r in results] == [201] * 4, [r.text for r in results]
    assert sorted(r.json()["version"] for r in results) == [2, 3, 4, 5]
    rows = sql(
        db,
        "SELECT c.* FROM source_contract c JOIN source_profile p ON p.id = c.source_profile_id"
        " WHERE p.source_system_id = $1 ORDER BY c.version",
        uuid.UUID(created["id"]),
    )
    assert [r["version"] for r in rows] == [1, 2, 3, 4, 5]
    assert [r["status"] for r in rows if r["status"] != "SUPERSEDED"] == ["PROVISIONAL"]
    assert rows[-1]["status"] == "PROVISIONAL"
    assert [r["supersedes_id"] for r in rows[1:]] == [r["id"] for r in rows[:-1]]

    # The same new content at once: one new version, the other is no_change.
    same = contract_body(db, expected_columns=[*base_cols, {"name": "Same", "required": True}])
    results = _race(clients, [(path, same), (path, same)], steward)
    assert sorted(_outcome(r) for r in results) == [
        (201, None),
        (422, "no_change"),
    ], [r.text for r in results]

    # The same code registered at once: one 201, one 409.
    results = _race(
        clients,
        [(BASE, source_body(db, code="raced")), (BASE, source_body(db, code="raced"))],
        steward,
    )
    assert sorted(_outcome(r) for r in results) == [
        (201, None),
        (409, "duplicate_code"),
    ], [r.text for r in results]
    assert sql(db, "SELECT count(*) AS n FROM source_system WHERE code = 'raced'")[0]["n"] == 1


# --- D12: profile-less sources are filtered in the query ------------------------------------


def test_page_total_matches_items_with_a_profile_less_source(
    api: TestClient, db: str, steward: dict[str, str]
) -> None:
    # A source row without a profile (not reachable through the API, which registers both in
    # one transaction) sorts first by code.
    sql(
        db,
        "INSERT INTO source_system (id, tenant_id, code, name, kind, created_by)"
        " VALUES (gen_random_uuid(), $1, 'aaa_orphan', 'Orphan', 'FILE_DROP', 'test')",
        DEFAULT_TENANT_ID,
    )
    page = api.get(BASE, params={"limit": 1}, headers=steward).json()
    assert [s["code"] for s in page["items"]] == ["core_banking"]
    assert page["total"] == 1
