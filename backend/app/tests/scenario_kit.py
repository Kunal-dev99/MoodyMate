"""Phase 24 (BE-24.1 - BE-24.4; D-44, D-47): the stress-scenario checks, folded into existing
test functions (D-26, the Feature 05 test budget: +0 tests).

`check_scenarios` is part 5 of `test_rules.py::test_governed_rule_changes_and_pinning` (the
governed-change pattern):

1. Templates (BE-24.2): GET scenario-templates lists exactly the three templates with their
   bounds; a table of invalid bodies each gets 422 `invalid_shock` with the expected
   value-free code, audited DENIED, nothing written; the three templates are accepted alone
   and combined (the design §5 three-shock example).
2. Lifecycle (BE-24.3, D-44 §5-6, D-47 §3): the author confirms their own DRAFT for
   exploration with no second person; another analyst gets 409 `not_author`; maker-checker
   promotion (`self_approval`, `invalid_status`, a missing comment is 422); the
   `scenario_change` rows equal the shocks once a version leaves DRAFT; the default list is
   the shared library (APPROVED_FOR_TEST only); CLOSED hides a version from pickers and it
   stays readable by id.
3. Triggers (BE-24.1): a shock UPDATE outside DRAFT, an illegal move, and DELETE and TRUNCATE
   on each of the three tables are refused by the database itself.
4. The switch (D-44 §9): SCENARIOS_ENABLED=false answers 409 `scenarios_disabled` to the
   seven writes, audited DENIED, nothing written; reads keep working.
Every response carries `labels.statement` (BE-24.4, D-44 §8).

The WS1 functions use `create_scenario` and the route constants.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.orchestration.constants import SCENARIO_STATEMENT, ApprovalLabel, ShockProblem
from app.rules.service import canonical_sha256
from app.tests.conftest import sql
from app.tests.helpers import assert_denied, cid, events, token_for

SCENARIOS = "/api/v1/scenarios"
TEMPLATES = "/api/v1/scenario-templates"
SCENARIO_TABLES = ("scenario_change", "scenario_version", "scenario")
SENTINEL = "SENTINEL-4711"  # no answer and no audit row may repeat it (value-free)

# The design §5 combined example: delay 20% of loan repayments by 14 days, add 15% to deposit
# outflows, make a funding source unavailable for 7 days.
DELAYED: dict[str, Any] = {
    "template": "DELAYED_INFLOWS",
    "parameters": {
        "cashflow_type": "LOAN",
        "affected_fraction": "0.20",
        "delay_days": 14,
        "delay_calendar": "CALENDAR_DAYS",
    },
}
DEPOSIT: dict[str, Any] = {
    "template": "DEPOSIT_WITHDRAWAL",
    "parameters": {"cashflow_type": "DEPOSIT", "increase_fraction": "0.15"},
}
FUNDING: dict[str, Any] = {
    "template": "FUNDING_UNAVAILABLE",
    "parameters": {"unavailable_days": 7, "affected_fraction": "1"},
    "selector": {"instrument_type": "LOAN", "instrument_keys": ["R-LOAN"]},
}
THREE_SHOCKS = [DELAYED, DEPOSIT, FUNDING]


def no_float(text: str) -> Any:
    """Parse JSON, failing on any JSON number with a fraction or exponent."""

    def refuse(value: str) -> Any:
        raise AssertionError(f"a JSON float: {value}")

    return json.loads(text, parse_float=refuse)


def shock(base: dict[str, Any], section: str = "parameters", **changes: Any) -> dict[str, Any]:
    """A copy of `base` with these keys of one section set (None removes a key)."""
    copy = json.loads(json.dumps(base))
    part = copy.setdefault(section, {})
    for key, value in changes.items():
        if value is None:
            part.pop(key, None)
        else:
            part[key] = value
    return dict(copy)


def body(name: str, shocks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"name": name, "description": "Phase 24 test scenario.", "shocks": shocks, **extra}


def create_scenario(
    api: TestClient,
    headers: dict[str, str],
    name: str = "Two-week loan repayment delay",
    shocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A scenario and its first DRAFT version (POST scenarios)."""
    r = api.post(SCENARIOS, json=body(name, shocks or [DELAYED, DEPOSIT]), headers=headers)
    assert r.status_code == 201, r.text
    return dict(no_float(r.text))


def version_path(scenario: dict[str, Any], version: int) -> str:
    return f"{SCENARIOS}/{scenario['scenario_id']}/versions/{version}"


def scenario_state(db: str) -> dict[str, list[str]]:
    """Every row of the three scenario tables, as a comparable snapshot."""
    try:
        return {
            t: [r["j"] for r in sql(db, f"SELECT to_jsonb(t)::text AS j FROM {t} t ORDER BY id")]
            for t in SCENARIO_TABLES
        }
    except asyncpg.UndefinedColumnError:  # a table without an id column: by its whole row
        return {
            t: sorted(r["j"] for r in sql(db, f"SELECT to_jsonb(t)::text AS j FROM {t} t"))
            for t in SCENARIO_TABLES
        }


def stored_shocks(db: str, version_id: str) -> list[Any]:
    [row] = sql(db, "SELECT shocks::text AS s FROM scenario_version WHERE id = $1",
                uuid.UUID(version_id))  # fmt: skip
    return list(no_float(row["s"]))


def stored_changes(db: str, version_id: str) -> list[Any]:
    """The version's scenario_change rows, in shock order (indices 0..n-1)."""
    rows = sql(
        db,
        "SELECT shock_index AS i, template AS t, shock::text AS s FROM scenario_change"
        " WHERE scenario_version_id = $1 ORDER BY shock_index",
        uuid.UUID(version_id),
    )
    assert [r["i"] for r in rows] == list(range(len(rows)))
    shocks = [no_float(r["s"]) for r in rows]
    assert [r["t"] for r in rows] == [s["template"] for s in shocks]
    return shocks


def problems(r: Any) -> list[dict[str, Any]]:
    """The value-free problem list of a 422 invalid_shock: details exactly
    {"problems": [{shock_index, field, code}]} (the contract, Q9)."""
    details = r.json()["details"]
    assert isinstance(details, dict) and list(details) == ["problems"], r.text
    items = details["problems"]
    assert isinstance(items, list) and items, r.text
    return list(items)


def assert_labelled(payload: dict[str, Any]) -> None:
    """D-44 §8: the page, and every version in it, carries the statement."""
    assert payload["labels"]["statement"] == SCENARIO_STATEMENT, payload.get("labels")
    for version in payload.get("versions", []):
        assert version["labels"]["statement"] == SCENARIO_STATEMENT


def listed(api: TestClient, headers: dict[str, str], **params: Any) -> dict[str, list[int]]:
    """GET scenarios: scenario id -> the versions that match the filter."""
    r = api.get(SCENARIOS, params={"limit": 200, **params}, headers=headers)
    assert r.status_code == 200, r.text
    page = no_float(r.text)
    assert_labelled(page)
    assert page["total"] == len(page["items"]), page
    return {i["scenario_id"]: i["matching_versions"] for i in page["items"]}


# --- 1. Templates and static validation (BE-24.2) --------------------------------------------

# (label, shocks, the refused shock's index (None: the list), its structural field path, the
# expected value-free code, the text that must never be echoed).
P = ShockProblem
INVALID: list[tuple[str, list[dict[str, Any]], int | None, str, ShockProblem, str | None]] = [
    ("unknown template", [{"template": "FORMULA_SHOCK", "parameters": {}}], 0, "template",
     P.UNKNOWN_TEMPLATE, "FORMULA_SHOCK"),
    # Never the unknown key's name either.
    ("extra field", [shock(DELAYED, formula_4711=SENTINEL)], 0, "parameters", P.UNKNOWN_FIELD,
     "formula_4711"),
    ("formula-like string", [shock(DELAYED, affected_fraction="amount*0.2")], 0,
     "parameters.affected_fraction", P.NOT_DECIMAL, "amount*0.2"),
    ("exponent", [shock(DELAYED, affected_fraction="2E-1")], 0, "parameters.affected_fraction",
     P.INEXACT_NUMBER, "2E-1"),
    ("JSON fractional number", [shock(DELAYED, affected_fraction=0.2)], 0,
     "parameters.affected_fraction", P.INEXACT_NUMBER, None),
    ("5 decimal places", [shock(DELAYED, affected_fraction="0.12345")], 0,
     "parameters.affected_fraction", P.TOO_MANY_PLACES, "0.12345"),
    ("affected_fraction 0", [shock(DELAYED, affected_fraction="0")], 0,
     "parameters.affected_fraction", P.OUT_OF_RANGE, None),
    ("affected_fraction 1.0001", [shock(DELAYED, affected_fraction="1.0001")], 0,
     "parameters.affected_fraction", P.OUT_OF_RANGE, "1.0001"),
    ("increase_fraction 1.0001", [DELAYED, shock(DEPOSIT, increase_fraction="1.0001")], 1,
     "parameters.increase_fraction", P.OUT_OF_RANGE, "1.0001"),
    ("delay_days 0", [shock(DELAYED, delay_days=0)], 0, "parameters.delay_days",
     P.OUT_OF_RANGE, None),
    ("delay_days 3651", [shock(DELAYED, delay_days=3651)], 0, "parameters.delay_days",
     P.OUT_OF_RANGE, "3651"),
    ("delay_calendar BUSINESS_DAYS", [shock(DELAYED, delay_calendar="BUSINESS_DAYS")], 0,
     "parameters.delay_calendar", P.UNSUPPORTED_VALUE, "BUSINESS_DAYS"),
    ("unknown instrument type", [shock(DELAYED, cashflow_type="SWAPTION_4711")], 0,
     "parameters.cashflow_type", P.UNKNOWN_INSTRUMENT_TYPE, "SWAPTION_4711"),
    ("direction mismatch", [DEPOSIT, shock(DELAYED, cashflow_type="DEPOSIT")], 1,
     "parameters.cashflow_type", P.DIRECTION_MISMATCH, None),
    ("due_from_day > due_to_day",
     [shock(DELAYED, "selector", due_from_day=30, due_to_day=10)], 0, "selector.due_from_day",
     P.INVALID_WINDOW, None),
    ("51 instrument keys",
     [shock(FUNDING, "selector", instrument_keys=[f"K-{i:04d}" for i in range(51)])], 0,
     "selector.instrument_keys", P.TOO_MANY_ITEMS, "K-0050"),
    ("6 shocks", [DELAYED, DEPOSIT, FUNDING, DELAYED, DEPOSIT, FUNDING], None, "shocks",
     P.TOO_MANY_SHOCKS, None),
    # `fullmatch`, never `$`: a trailing newline is refused, not stored (an unreadable row).
    ("currency with a trailing newline", [shock(DELAYED, "selector", currency="USD\n")], 0,
     "selector.currency", P.UNSUPPORTED_VALUE, None),
    ("fraction with a trailing newline", [shock(DELAYED, affected_fraction="0.2\n")], 0,
     "parameters.affected_fraction", P.NOT_DECIMAL, None),
    # D-33: a key that is not a visible LOAN instrument of the tenant (R-DEP is a DEPOSIT).
    ("unknown instrument", [shock(FUNDING, "selector", instrument_keys=["R-LOAN", "NO-4711"])],
     0, "selector.instrument_keys", P.UNKNOWN_INSTRUMENT, "NO-4711"),
    ("instrument of another type", [shock(FUNDING, "selector", instrument_keys=["R-DEP"])], 0,
     "selector.instrument_keys", P.UNKNOWN_INSTRUMENT, None),
]  # fmt: skip


def check_templates(api: TestClient, tokens: dict[str, dict[str, str]]) -> None:
    for role, headers in tokens.items():
        r = api.get(TEMPLATES, headers=headers)
        assert r.status_code == 200, (role, r.text)
    page = no_float(r.text)
    assert_labelled(page)
    assert page["max_shocks"] == 5
    by = {t["template"]: t for t in page["items"]}
    assert [t["template"] for t in page["items"]] == [
        "DELAYED_INFLOWS", "DEPOSIT_WITHDRAWAL", "FUNDING_UNAVAILABLE"
    ]  # fmt: skip
    assert {k: (t["direction"], t["changes_amounts"]) for k, t in by.items()} == {
        "DELAYED_INFLOWS": ("INFLOW", False),
        "DEPOSIT_WITHDRAWAL": ("OUTFLOW", True),
        "FUNDING_UNAVAILABLE": ("INFLOW", False),
    }

    def field(template: str, name: str, section: str = "parameters") -> dict[str, Any]:
        [f] = [f for f in by[template][section] if f["name"] == name]
        return dict(f)

    for template, name in (
        ("DELAYED_INFLOWS", "affected_fraction"),
        ("DEPOSIT_WITHDRAWAL", "increase_fraction"),
        ("FUNDING_UNAVAILABLE", "affected_fraction"),
    ):
        f = field(template, name)
        assert (f["kind"], f["min"], f["min_exclusive"], f["max"], f["scale"]) == (
            "DECIMAL", "0", True, "1", 4
        ), f  # fmt: skip
    for template, name, high in (
        ("DELAYED_INFLOWS", "delay_days", "3650"),
        ("FUNDING_UNAVAILABLE", "unavailable_days", "365"),
    ):
        f = field(template, name)
        assert (f["kind"], f["min"], f["min_exclusive"], f["max"]) == ("INTEGER", "1", False, high)
    for template in ("DELAYED_INFLOWS", "FUNDING_UNAVAILABLE"):
        assert field(template, "delay_calendar")["values"] == ["CALENDAR_DAYS"]
    # The instrument types of the ACTIVE cashflow_direction rule, with the needed direction.
    inflows = set(field("DELAYED_INFLOWS", "cashflow_type")["values"])
    outflows = set(field("DEPOSIT_WITHDRAWAL", "cashflow_type")["values"])
    assert {"LOAN", "BOND"} <= inflows and "DEPOSIT" not in inflows, inflows
    assert "DEPOSIT" in outflows and not outflows & {"LOAN", "BOND"}, outflows
    funding = field("FUNDING_UNAVAILABLE", "instrument_type", "selector")
    assert set(funding["values"]) == inflows and funding["required"] is True
    keys = field("FUNDING_UNAVAILABLE", "instrument_keys", "selector")
    assert (keys["kind"], keys["max_items"]) == ("INSTRUMENT_KEYS", 50)


def check_validation(api: TestClient, db: str, analyst: dict[str, str]) -> None:
    for label, shocks, index, name, code, leak in INVALID:
        before = scenario_state(db)
        c = cid()
        r = api.post(
            SCENARIOS, json=body(label, shocks), headers={**analyst, "X-Correlation-ID": c}
        )
        assert (r.status_code, r.json().get("code")) == (422, "invalid_shock"), (label, r.text)
        found = problems(r)
        # Exactly the expected refusal, and only value-free fields.
        assert found == [{"shock_index": index, "field": name, "code": code.value}], (label, found)
        assert_denied(db, c, "invalid_shock", "scenario.create")
        [event] = events(db, c)
        answer = r.json()["message"] + json.dumps(r.json()["details"]) + (event["details"] or "")
        for text in filter(None, (leak, SENTINEL, "amount*0.2")):
            assert text not in answer, (label, text)
        assert scenario_state(db) == before, label  # nothing written
    # The same validator guards a new version.
    created = create_scenario(api, analyst, "Validator check", [DEPOSIT])
    c = cid()
    r = api.post(
        f"{SCENARIOS}/{created['scenario_id']}/versions",
        json={"shocks": [shock(DEPOSIT, increase_fraction="0.12345")]},
        headers={**analyst, "X-Correlation-ID": c},
    )
    assert (r.status_code, r.json().get("code")) == (422, "invalid_shock"), r.text
    assert_denied(db, c, "invalid_shock", "scenario.version")

    # Accepted: each template alone, and the three combined (design §5). Fractions are exact
    # text padded to scale 4; the calendar defaults to CALENDAR_DAYS.
    for shocks in ([DELAYED], [DEPOSIT], [FUNDING], THREE_SHOCKS):
        c = cid()
        r = api.post(
            SCENARIOS,
            json=body(f"Accepted {len(shocks)}", shocks),
            headers={**analyst, "X-Correlation-ID": c},
        )
        assert r.status_code == 201, (shocks, r.text)
        created = no_float(r.text)
        assert_labelled(created)
        [v1] = created["versions"]
        assert (v1["status"], v1["origin"], v1["version"]) == ("DRAFT", "MANUAL", 1)
        assert [s["template"] for s in v1["shocks"]] == [s["template"] for s in shocks]
        trail = [(e["action"], e["outcome"]) for e in events(db, c)]
        assert ("scenario.create", "SUCCESS") in trail, trail
    by_template = {s["template"]: s for s in v1["shocks"]}
    assert by_template["DELAYED_INFLOWS"]["parameters"]["affected_fraction"] == "0.2000"
    assert by_template["DELAYED_INFLOWS"]["parameters"]["delay_calendar"] == "CALENDAR_DAYS"
    assert by_template["DEPOSIT_WITHDRAWAL"]["parameters"]["increase_fraction"] == "0.1500"
    assert by_template["FUNDING_UNAVAILABLE"]["parameters"]["affected_fraction"] == "1.0000"
    assert by_template["FUNDING_UNAVAILABLE"]["parameters"]["delay_calendar"] == "CALENDAR_DAYS"
    assert by_template["FUNDING_UNAVAILABLE"]["selector"]["instrument_keys"] == ["R-LOAN"]
    # Stored as JSON text (never a JSON number), hashed canonically.
    for row in sql(db, "SELECT shocks::text AS s, shocks_sha256 AS h FROM scenario_version"):
        assert row["h"] == canonical_sha256(no_float(row["s"]))
    stored = sql(
        db,
        "SELECT shocks_sha256 AS h FROM scenario_version WHERE id = $1",
        uuid.UUID(v1["scenario_version_id"]),
    )
    assert stored[0]["h"] == v1["shocks_sha256"]
    # D-33: instrument keys are masked on read by the caller's clearance; a stored key that
    # matches no instrument the reader may see answers a MaskedValue, never its text.
    planted = create_scenario(api, analyst, "Masked keys", [FUNDING])
    sql(db, "UPDATE scenario_version SET shocks = jsonb_set(shocks,"
            " '{0,selector,instrument_keys}', '[\"R-LOAN\", \"GHOST-4711\"]') WHERE id = $1",
        uuid.UUID(planted["versions"][0]["scenario_version_id"]))  # fmt: skip
    r = api.get(f"{SCENARIOS}/{planted['scenario_id']}", headers=tokens_any(api))
    assert r.status_code == 200 and "GHOST-4711" not in r.text, r.text
    [seen] = no_float(r.text)["versions"][0]["shocks"]
    assert seen["selector"]["instrument_keys"] == [
        "R-LOAN", {"masked": True, "classification": "unclassified"}
    ], seen  # fmt: skip


# --- 2. Lifecycle (BE-24.3, BE-24.4) ----------------------------------------------------------


def post(
    api: TestClient, path: str, payload: dict[str, Any], headers: dict[str, str]
) -> tuple[Any, str]:
    c = cid()
    return api.post(path, json=payload, headers={**headers, "X-Correlation-ID": c}), c


def refused(
    db: str, r: Any, c: str, status: int, code: str, action: str, before: dict[str, list[str]]
) -> None:
    """A refusal: the code, audited DENIED with that reason, nothing written."""
    assert (r.status_code, r.json().get("code")) == (status, code), r.text
    assert_denied(db, c, code, action)
    assert scenario_state(db) == before, (code, action)


def succeeded(db: str, r: Any, c: str, action: str) -> dict[str, Any]:
    assert r.status_code in (200, 201), r.text
    trail = [(e["action"], e["outcome"]) for e in events(db, c)]
    assert (action, "SUCCESS") in trail, trail
    out = dict(no_float(r.text))
    assert_labelled(out)
    return out


def check_lifecycle(
    api: TestClient,
    db: str,
    artifact_dir: Path,
    analyst: dict[str, str],
    approver: dict[str, str],
    both: dict[str, str],
) -> None:
    analyst_id = api.get("/api/v1/me", headers=analyst).json()["user_id"]
    other = token_for(db, "second-analyst", ("risk_analyst",))
    decide = {"decision": "APPROVE", "comment": "Reviewed for the shared library"}

    # Create, with an explicit code; the code is unique per tenant.
    r, c = post(api, SCENARIOS, body("Two-week loan repayment delay", [DELAYED, DEPOSIT],
                                     code="TWO_WEEK_DELAY"), analyst)  # fmt: skip
    scenario = succeeded(db, r, c, "scenario.create")
    [v1] = scenario["versions"]
    assert scenario["code"] == "TWO_WEEK_DELAY" and scenario["scenarios_enabled"] is True
    assert (v1["status"], v1["author_user_id"], v1["changes"], v1["runnable"]) == (
        "DRAFT", analyst_id, [], False
    )  # fmt: skip
    assert (v1["approval_label"], v1["parent_version"], v1["parameter_sources"]) == (None,) * 3
    [event] = [e for e in events(db, c) if e["action"] == "scenario.create"]
    assert v1["shocks_sha256"] in (event["details"] or ""), "the audit row carries the hash"
    assert "0.2000" not in (event["details"] or ""), "never the shocks' text"
    before = scenario_state(db)
    r, c = post(api, SCENARIOS, body("Same code", [DEPOSIT], code="TWO_WEEK_DELAY"), analyst)
    refused(db, r, c, 409, "duplicate_code", "scenario.create", before)
    path = version_path(scenario, 1)

    # Only the author edits, confirms and submits their DRAFT (409 not_author, audited).
    edit = {"shocks": [shock(DELAYED, delay_days=30), DEPOSIT], "change_summary": "30 days"}
    for action, method, url, payload in (
        ("scenario.edit", "PUT", path, edit),
        ("scenario.confirm", "POST", f"{path}/confirm", {}),
        ("scenario.submit", "POST", f"{path}/submit", {}),
    ):
        before = scenario_state(db)
        c = cid()
        r = api.request(method, url, json=payload, headers={**other, "X-Correlation-ID": c})
        refused(db, r, c, 409, "not_author", action, before)
    c = cid()
    r = api.put(path, json=edit, headers={**analyst, "X-Correlation-ID": c})
    edited = succeeded(db, r, c, "scenario.edit")
    assert edited["status"] == "DRAFT" and edited["shocks"][0]["parameters"]["delay_days"] == 30
    assert edited["shocks_sha256"] != v1["shocks_sha256"]

    # The author confirms their own DRAFT for exploration: no second person.
    r, c = post(api, f"{path}/confirm", {}, analyst)
    confirmed = succeeded(db, r, c, "scenario.confirm")
    assert confirmed["status"] == "CONFIRMED_FOR_EXPLORATION" and confirmed["confirmed_at"]
    assert (confirmed["runnable"], confirmed["approval_label"]) == (True, ApprovalLabel.EXPLORATION)
    assert (confirmed["decided_by"], confirmed["submitted_by"]) == (None, None)
    # Content froze when it left DRAFT: one scenario_change row per shock.
    assert [ch["shock"] for ch in confirmed["changes"]] == confirmed["shocks"]
    assert [ch["shock_index"] for ch in confirmed["changes"]] == [0, 1]
    assert [ch["template"] for ch in confirmed["changes"]] == [
        "DELAYED_INFLOWS", "DEPOSIT_WITHDRAWAL"
    ]  # fmt: skip
    assert stored_changes(db, confirmed["scenario_version_id"]) == stored_shocks(
        db, confirmed["scenario_version_id"]
    )
    assert len(stored_shocks(db, confirmed["scenario_version_id"])) == 2
    for method, url, payload, action in (
        ("PUT", path, edit, "scenario.edit"),
        ("POST", f"{path}/confirm", {}, "scenario.confirm"),
    ):
        before = scenario_state(db)
        c = cid()
        r = api.request(method, url, json=payload, headers={**analyst, "X-Correlation-ID": c})
        refused(db, r, c, 409, "invalid_status", action, before)
    # Exploration versions are never in the shared library (the default list) ...
    assert scenario["scenario_id"] not in listed(api, approver)
    # ... but are in "My explorations".
    mine = listed(api, analyst, mine="true", status="CONFIRMED_FOR_EXPLORATION")
    assert mine.get(scenario["scenario_id"]) == [1], mine
    assert scenario["scenario_id"] not in listed(api, other, mine="true",
                                                 status="CONFIRMED_FOR_EXPLORATION")  # fmt: skip

    # A new DRAFT adapting v1 (D-44 §7); deciding a DRAFT is 409 invalid_status.
    r, c = post(api, f"{SCENARIOS}/{scenario['scenario_id']}/versions",
                {"shocks": [DELAYED], "parent_version": 1}, analyst)  # fmt: skip
    v2 = succeeded(db, r, c, "scenario.version")
    assert (v2["version"], v2["status"], v2["parent_version"]) == (2, "DRAFT", 1)
    assert v2["parent_version_id"] == confirmed["scenario_version_id"] and v2["changes"] == []
    for target in (2, 1):  # a DRAFT, and a CONFIRMED_FOR_EXPLORATION version (not IN_REVIEW)
        before = scenario_state(db)
        r, c = post(api, f"{version_path(scenario, target)}/approval", decide, approver)
        refused(db, r, c, 409, "invalid_status", "scenario.approve", before)
    # A DRAFT is never closed (Any status but DRAFT -> CLOSED).
    before = scenario_state(db)
    r, c = post(api, f"{version_path(scenario, 2)}/close", {}, analyst)
    refused(db, r, c, 409, "invalid_status", "scenario.close", before)

    # Submit for promotion, by the author (from CONFIRMED_FOR_EXPLORATION and from DRAFT).
    r, c = post(api, f"{path}/submit", {"comment": "For the shared library"}, analyst)
    submitted = succeeded(db, r, c, "scenario.submit")
    assert submitted["status"] == "IN_REVIEW" and submitted["submitted_by"]
    assert submitted["submit_comment"] == "For the shared library"
    r, c = post(api, f"{version_path(scenario, 2)}/submit", {}, analyst)
    assert succeeded(db, r, c, "scenario.submit")["status"] == "IN_REVIEW"
    assert [ch["shock"] for ch in r.json()["changes"]] == r.json()["shocks"]
    assert stored_changes(db, v2["scenario_version_id"]) == stored_shocks(
        db, v2["scenario_version_id"]
    )

    # 3. The database refuses what the service never does (BE-24.1).
    check_triggers(db, submitted["scenario_version_id"], v2["scenario_version_id"])

    # A missing comment is 422; nothing written.
    before = scenario_state(db)
    r = api.post(f"{path}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 422, r.text
    assert scenario_state(db) == before

    # Promotion is maker-checker: the author holding both roles is refused (409
    # self_approval, audited); a different approver approves.
    r, c = post(api, SCENARIOS, body("Deposit run", [DEPOSIT]), both)
    own = succeeded(db, r, c, "scenario.create")
    own_path = version_path(own, 1)
    r, c = post(api, f"{own_path}/submit", {}, both)
    assert succeeded(db, r, c, "scenario.submit")["status"] == "IN_REVIEW"
    for decision, action in (("APPROVE", "scenario.approve"), ("REJECT", "scenario.reject")):
        before = scenario_state(db)
        r, c = post(api, f"{own_path}/approval", {"decision": decision, "comment": "Mine"}, both)
        refused(db, r, c, 409, "self_approval", action, before)
    r, c = post(api, f"{own_path}/approval", decide, approver)
    promoted = succeeded(db, r, c, "scenario.approve")
    assert (promoted["status"], promoted["decision_comment"]) == ("APPROVED_FOR_TEST",
                                                                  decide["comment"])  # fmt: skip
    assert promoted["approval_label"] == ApprovalLabel.APPROVED and promoted["runnable"] is True
    r, c = post(api, f"{path}/approval", decide, approver)
    approved = succeeded(db, r, c, "scenario.approve")
    assert approved["status"] == "APPROVED_FOR_TEST" and approved["decided_by"]
    assert approved["decided_at"] and approved["approval_label"] == ApprovalLabel.APPROVED
    # Deciding twice is 409 invalid_status.
    before = scenario_state(db)
    r, c = post(api, f"{path}/approval", decide, approver)
    refused(db, r, c, 409, "invalid_status", "scenario.approve", before)
    # REJECT ends v2; its author adapts it into v3.
    r, c = post(api, f"{version_path(scenario, 2)}/approval",
                {"decision": "REJECT", "comment": "Too mild"}, approver)  # fmt: skip
    rejected = succeeded(db, r, c, "scenario.reject")
    assert (rejected["status"], rejected["runnable"], rejected["approval_label"]) == (
        "REJECTED", False, None
    )  # fmt: skip
    adapted = {"shocks": [shock(DELAYED, delay_days=30)], "parent_version": 2}
    r, c = post(api, f"{SCENARIOS}/{scenario['scenario_id']}/versions", adapted, analyst)
    assert succeeded(db, r, c, "scenario.version")["parent_version"] == 2

    # The shared library: APPROVED_FOR_TEST versions only, never exploration ones.
    exploration = create_scenario(api, analyst, "Exploration only", [FUNDING])
    r, c = post(api, f"{version_path(exploration, 1)}/confirm", {}, analyst)
    assert succeeded(db, r, c, "scenario.confirm")["status"] == "CONFIRMED_FOR_EXPLORATION"
    library = listed(api, tokens_any(api))
    assert library == {scenario["scenario_id"]: [1], own["scenario_id"]: [1]}, library
    r = api.get(SCENARIOS, headers=approver)
    summary = {i["scenario_id"]: i for i in r.json()["items"]}[scenario["scenario_id"]]
    assert (summary["approved_version"], summary["latest_version"]) == (1, 3)

    # CLOSED hides a version from pickers; it stays readable by id. An approver closes an
    # approved version, the author their own exploration.
    r, c = post(api, f"{path}/close", {"comment": "Superseded by v3"}, approver)
    closed = succeeded(db, r, c, "scenario.close")
    assert (closed["status"], closed["runnable"]) == ("CLOSED", False) and closed["closed_at"]
    r, c = post(api, f"{version_path(exploration, 1)}/close", {}, analyst)
    assert succeeded(db, r, c, "scenario.close")["status"] == "CLOSED"
    assert listed(api, approver) == {own["scenario_id"]: [1]}
    assert exploration["scenario_id"] not in listed(
        api, analyst, mine="true", status="CONFIRMED_FOR_EXPLORATION"
    )
    assert listed(api, approver, status="CLOSED") == {
        scenario["scenario_id"]: [1], exploration["scenario_id"]: [1]
    }  # fmt: skip
    r = api.get(f"{SCENARIOS}/{scenario['scenario_id']}", headers=approver)
    assert r.status_code == 200, r.text
    detail = no_float(r.text)
    assert_labelled(detail)
    assert [(v["version"], v["status"]) for v in detail["versions"]] == [
        (1, "CLOSED"), (2, "REJECTED"), (3, "DRAFT")
    ]  # fmt: skip
    assert detail["versions"][0]["decision_comment"] == decide["comment"]
    assert [ch["shock"] for ch in detail["versions"][0]["changes"]] == detail["versions"][0][
        "shocks"
    ]
    r = api.get(f"{SCENARIOS}/{uuid.uuid4()}", headers=approver)
    assert (r.status_code, r.json().get("code")) == (404, "not_found"), r.text

    # 4. The switch (D-44 §9): every write is 409 scenarios_disabled, audited, nothing written;
    # reads keep working.
    off = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, SCENARIOS_ENABLED=False
    )
    v3 = version_path(scenario, 3)
    own_versions = f"{SCENARIOS}/{scenario['scenario_id']}/versions"
    writes: tuple[tuple[str, str, str, dict[str, Any], dict[str, str]], ...] = (
        ("scenario.create", "POST", SCENARIOS, body("Switched off", [DEPOSIT]), analyst),
        ("scenario.version", "POST", own_versions, {"shocks": [DEPOSIT]}, analyst),
        ("scenario.edit", "PUT", v3, {"shocks": [DEPOSIT]}, analyst),
        ("scenario.confirm", "POST", f"{v3}/confirm", {}, analyst),
        ("scenario.submit", "POST", f"{v3}/submit", {}, analyst),
        ("scenario.approve", "POST", f"{own_path}/approval", decide, approver),
        ("scenario.close", "POST", f"{own_path}/close", {}, approver),
    )
    with TestClient(create_app(off)) as frozen:
        before = scenario_state(db)
        for action, method, url, payload, headers in writes:
            c = cid()
            headers = {**headers, "X-Correlation-ID": c}
            r = frozen.request(method, url, json=payload, headers=headers)
            refused(db, r, c, 409, "scenarios_disabled", action, before)
        assert frozen.get(TEMPLATES, headers=analyst).status_code == 200
        assert listed(frozen, analyst) == {own["scenario_id"]: [1]}
        shown = frozen.get(f"{SCENARIOS}/{scenario['scenario_id']}", headers=analyst)
        assert shown.status_code == 200 and shown.json()["scenarios_enabled"] is False
        assert_labelled(shown.json())


def tokens_any(api: TestClient) -> dict[str, str]:
    """An auditor: reads use calc:read, which every role holds."""
    r = api.post("/api/v1/dev/sessions", json={"role": "auditor"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# --- 3. Triggers (BE-24.1) --------------------------------------------------------------------


def check_triggers(db: str, in_review_id: str, other_id: str) -> None:
    """The 0026 triggers: shocks change only while DRAFT, only the allowed moves, and DELETE
    and TRUNCATE are refused on all three tables. Each refusal leaves the rows unchanged."""
    before = scenario_state(db)
    version = uuid.UUID(in_review_id)
    for statement in (
        "UPDATE scenario_version SET shocks = '[]'::jsonb WHERE id = $1",  # IN_REVIEW content
        "UPDATE scenario_version SET status = 'DRAFT' WHERE id = $1",  # backwards
        "UPDATE scenario_version SET status = 'CONFIRMED_FOR_EXPLORATION' WHERE id = $1",
        "UPDATE scenario_version SET status = 'TESTED' WHERE id = $1",  # reserved
    ):
        with pytest.raises(asyncpg.PostgresError) as refused_:
            sql(db, statement, version)
        assert not isinstance(refused_.value, asyncpg.ForeignKeyViolationError), statement
    for table in SCENARIO_TABLES:
        for statement in (f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(asyncpg.PostgresError) as refused_:
                sql(db, statement)
            # The trigger, not a foreign key, refuses (row triggers fire before FK checks;
            # CASCADE takes FKs out of TRUNCATE).
            assert not isinstance(refused_.value, asyncpg.ForeignKeyViolationError), statement
    assert sql(db, "SELECT count(*) AS n FROM scenario_version WHERE id = $1",
               uuid.UUID(other_id))[0]["n"] == 1  # fmt: skip
    assert scenario_state(db) == before


def check_scenarios(
    api: TestClient,
    db: str,
    artifact_dir: Path,
    tokens: dict[str, dict[str, str]],
    both: dict[str, str],
) -> None:
    """Part 5 of test_governed_rule_changes_and_pinning (Phase 24)."""
    analyst, approver = tokens["risk_analyst"], tokens["approver"]
    check_templates(api, tokens)
    check_validation(api, db, analyst)
    check_lifecycle(api, db, artifact_dir, analyst, approver, both)
