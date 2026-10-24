"""Phase 16 (BE-16.1 - BE-16.3): governed rule changes and pinning across them. Split by
D-60 (BE-35.5) from one function into five, along the lifecycle, permission and error
boundaries; every assertion is kept:

1. `test_rule_reads_and_the_closed_kind_list` (BE-16.1, BE-16.2, D-32 §1): GET /rules and GET
   /rules/{rule_id} for every role; a parameter schema outside the allowlist cannot exist (DB
   CHECK) or pass check_schema. Phase 30 (BE-30.2, D-56, validation_kit): GET
   /calculation-modules shows validation_status ILLUSTRATIVE on every module version, PLANNED
   included, for every role; MODULE_VALIDATION covers every module_version row; a
   non-ILLUSTRATIVE ValidationRecord without an approver, approved_on and evidence_ref raises.
2. `test_rule_proposals_are_checked_for_every_parameter_kind` (BE-16.2): for every kind
   (DECIMAL, INTEGER, ENUM, DAY_BUCKETS, ENUM_MAP, DECIMAL_MAP) a loop proposes valid and
   invalid parameter sets: each valid one is a DRAFT, each invalid one is 422
   invalid_parameters with a value-free answer, audited DENIED, and nothing is written; an
   unchanged result is 422 no_change. DECIMAL values are never stored or returned as a JSON
   number. A definition whose own schema is malformed refuses every proposal (409
   invalid_parameter_schema).
3. `test_rule_maker_checker_refusals_and_rejection` (BE-16.2, D-32 §3, D-34): only
   risk_analyst proposes and submits, only approver decides (403 otherwise, audited); a DRAFT
   is not approved without submit (409 invalid_status); the author never decides on their own
   version, even holding rule:approve (409 self_approval, audited); REJECT ends a version.
4. `test_rule_activation_switch_and_pinning` (BE-16.2, BE-16.3): APPROVE makes the bucket
   version ACTIVE and SUPERSEDES the previous one in the same transaction (same xmin), leaving
   exactly one ACTIVE version; a version proposed from the superseded one is then stale (409
   stale_version). RULE_CHANGE_ENABLED=false answers 409 rule_change_disabled to the three
   writes, audited, with nothing written, while reads and runs keep working. A run submitted
   after the approval pins the new version in a new rule_set and gives another result_sha256;
   the old run's rows are unchanged; a re-run of the old run (rerun_of) repeats its pins,
   manifest_sha256 and result_sha256; GET /rules/{rule_id} lists the runs that pin each
   version.
5. `test_stress_scenarios_follow_the_governed_change_pattern` (Phase 24, BE-24.1 - BE-24.4;
   D-44, D-47), the same governed-change pattern: templates and static validation,
   exploration confirmation, maker-checker promotion, the 0026 triggers and the
   SCENARIOS_ENABLED switch (app/tests/scenario_kit.py).
"""

import json
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.canonical import canonical_sha256
from app.core.config import Settings
from app.governance.constants import DEFAULT_TENANT_ID
from app.main import create_app
from app.rules.constants import (
    CASHFLOW_DIRECTION,
    ILLUSTRATIVE_STATEMENT,
    LCR_SEEDED_RULES,
    MATURITY_BUCKETS,
    MATURITY_LADDER,
    PAST_DUE_TREATMENT,
    SEEDED_BUCKETS,
    SEEDED_RULES,
    ParameterKind,
    ParameterProblem,
)
from app.rules.service import check_schema
from app.tests.conftest import dev_token, sql
from app.tests.helpers import (
    MAPPINGS,
    RUNS,
    SOURCES,
    activate_version,
    assert_denied,
    cid,
    contract_body,
    events,
    make_snapshot,
    module_version_id,
    mrule,
    project,
    sample_rules,
    source_body,
    start_run,
    token_for,
    upload,
    validate_import,
)
from app.tests.scenario_kit import check_scenarios
from app.tests.validation_kit import check_module_validation

pytestmark = pytest.mark.db

RULES = "/api/v1/rules"
ROLES = ("platform_admin", "auditor", "data_steward", "risk_analyst", "approver")
OUTPUT_TABLES = (
    "calculation_input_ref",
    "liquidity_gap_result",
    "intermediate_value",
    "calculation_warning",
)
REPORTING = date(2026, 12, 31)
INSTRUMENT_COLUMNS = ["instrument_id", "instrument_type", "currency", "notional"]
CASHFLOW_COLUMNS = [
    "cashflow_id",
    "instrument_id",
    "due_date",
    "amount",
    "currency",
    "cashflow_type",
    "origin",
]
INSTRUMENTS = [("R-LOAN", "LOAN", "EUR", "1000.00"), ("R-DEP", "DEPOSIT", "EUR", "500.00")]
# cashflow_id, instrument_id, days after the reporting date, amount, currency, type, origin
CASH_FLOWS = [
    ("RF-1", "R-LOAN", 1, "100.00", "EUR", "PRINCIPAL", "CONTRACTUAL"),
    ("RF-2", "R-DEP", 5, "40.00", "EUR", "PRINCIPAL", "CONTRACTUAL"),
    ("RF-3", "R-LOAN", 20, "25.00", "EUR", "INTEREST", "CONTRACTUAL"),
    ("RF-4", "R-DEP", 100, "10.00", "EUR", "PRINCIPAL", "CONTRACTUAL"),
    ("RF-5", "R-LOAN", 400, "12.34", "EUR", "INTEREST", "BEHAVIOURAL"),
]


def bucket(code: str, lower: int, upper: int | None) -> dict[str, Any]:
    label = f"({lower},{upper}]" if upper is not None else f"({lower},inf)"
    return {"code": code, "label": label, "lower_exclusive": lower, "upper_inclusive": upper}


# The new bucket set the analyst proposes: four coarser intervals, contiguous, last open.
NEW_BUCKETS = [bucket("B01", 0, 7), bucket("B02", 7, 90), bucket("B03", 90, 365),
               bucket("B04", 365, None)]  # fmt: skip
SENTINEL = "SENTINEL-4711"  # a value no answer and no audit row may repeat (value-free)


def no_float(text: str) -> Any:
    """Parse JSON, failing on any JSON number with a fraction or exponent."""

    def refuse(value: str) -> Any:
        raise AssertionError(f"a JSON float: {value}")

    return json.loads(text, parse_float=refuse)


def count(db: str, table: str) -> int:
    return int(sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"])


def versions_state(db: str) -> list[tuple[Any, ...]]:
    """Every rule version (and its bucket definitions), as a comparable snapshot."""
    rows = sql(
        db,
        "SELECT v.id, v.status, v.parameters::text AS p, v.approved_by,"
        " (SELECT count(*) FROM maturity_bucket_definition b WHERE b.rule_version_id = v.id)"
        " AS buckets FROM rule_version v ORDER BY v.id",
    )
    return [tuple(r.values()) for r in rows]


def rule_id_of(db: str, code: str) -> str:
    return str(sql(db, "SELECT id FROM rule_definition WHERE code = $1", code)[0]["id"])


def insert_rule(db: str, code: str, schema: dict[str, Any], parameters: dict[str, Any]) -> str:
    """A test rule definition of the maturity ladder (not in its input contract, so never
    pinned) with an ACTIVE v1, moved as the lifecycle trigger allows: DRAFT, IN_REVIEW,
    ACTIVE. Cleaned up as a non-seed registry row."""
    module = sql(db, "SELECT id FROM calculation_module WHERE code = $1", MATURITY_LADDER)
    rule_id, version_id = uuid.uuid4(), uuid.uuid4()
    sql(
        db,
        "INSERT INTO rule_definition (id, tenant_id, code, name, category, target_module_id,"
        " owner, parameter_schema, provisional, illustrative, created_by)"
        " VALUES ($1, $2, $3, $3, 'FACTOR', $4, 'Test owner', $5::jsonb, true, true, 'test')",
        rule_id,
        DEFAULT_TENANT_ID,
        code,
        module[0]["id"],
        json.dumps(schema),
    )
    sql(
        db,
        "INSERT INTO rule_version (id, tenant_id, rule_definition_id, version, status,"
        " parameters, parameters_sha256, authored_by, provisional, illustrative, created_by)"
        " VALUES ($1, $2, $3, 1, 'DRAFT', $4::jsonb, $5, 'test', true, true, 'test')",
        version_id,
        DEFAULT_TENANT_ID,
        rule_id,
        json.dumps(parameters),
        canonical_sha256(parameters),
    )
    sql(db, "UPDATE rule_version SET status = 'IN_REVIEW' WHERE id = $1", version_id)
    sql(
        db,
        "UPDATE rule_version SET status = 'ACTIVE', approved_by = 'test', approved_at = now(),"
        " decision_comment = 'test fixture' WHERE id = $1",
        version_id,
    )
    return str(rule_id)


def csv_source(
    api: TestClient, db: str, steward: dict[str, str], code: str, schema: str, columns: list[str]
) -> dict[str, Any]:
    key = ["instrument_id", "currency"] if schema == "instrument" else ["cashflow_id"]
    body = source_body(
        db,
        code=code,
        contract=contract_body(
            db,
            schema=schema,
            expected_columns=[{"name": c, "required": True} for c in columns],
            business_key_columns=key,
            control_total_columns=[],
        ),
    )
    r = api.post(SOURCES, json=body, headers=steward)
    assert r.status_code == 201, r.text
    return dict(r.json())


def csv(columns: list[str], rows: Sequence[Sequence[str]]) -> bytes:
    return ("\n".join([",".join(columns), *(",".join(r) for r in rows)]) + "\n").encode()


def ladder_snapshot(
    api: TestClient, db: str, steward: dict[str, str], approver: dict[str, str],
    analyst: dict[str, str],
) -> str:  # fmt: skip
    """Instruments and cash flows uploaded, mapped, validated and projected, then
    snapshotted: the snapshot id a maturity-ladder run needs."""
    cash_rows = [
        (k, i, (REPORTING + timedelta(days=d)).isoformat(), a, c, t, o)
        for k, i, d, a, c, t, o in CASH_FLOWS
    ]
    imports = []
    for code, schema, columns, data in (
        (
            "rule_instruments",
            "instrument",
            INSTRUMENT_COLUMNS,
            csv(INSTRUMENT_COLUMNS, INSTRUMENTS),
        ),
        ("rule_cash_flows", "cashflow_event", CASHFLOW_COLUMNS, csv(CASHFLOW_COLUMNS, cash_rows)),
    ):
        source = csv_source(api, db, steward, code, schema, columns)
        r = upload(api, steward, source["id"], data, filename="fixture.csv")
        assert r.status_code == 201, r.text
        imported = r.json()
        rules = (
            [
                mrule("instrument_id", "instrument_id"),
                mrule("instrument_type", "instrument_type"),
                mrule("currency", "currency"),
                mrule("notional", "notional", "PARSE_DECIMAL"),
            ]
            if schema == "instrument"
            else sample_rules(api, db, steward, schema, columns)
        )
        body = {
            "code": f"{code}_map",
            "name": f"{code} (test)",
            "source_contract_id": source["current_contract"]["id"],
            "rules": rules,
        }
        r = api.post(MAPPINGS, json=body, headers=steward)
        assert r.status_code == 201, r.text
        activate_version(api, steward, approver, r.json()["versions"][0], imported["id"])
        r = validate_import(api, steward, imported["id"])
        assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
        assert project(api, steward, imported["id"]).status_code == 201
        imports.append(imported["id"])
    r = make_snapshot(api, analyst, imports)
    assert r.status_code == 201, r.text
    return str(r.json()["snapshot_id"])


def run_rows_sha256(db: str, run_id: str) -> str:
    """The hash of a run's row and every output row it owns."""
    rid = uuid.UUID(run_id)
    rows = {
        t: [
            r["j"]
            for r in sql(
                db,
                f"SELECT to_jsonb(t)::text AS j FROM {t} t WHERE calculation_run_id = $1"
                " ORDER BY id",
                rid,
            )
        ]
        for t in OUTPUT_TABLES
    }
    rows["calculation_run"] = [
        r["j"] for r in sql(db, "SELECT to_jsonb(r)::text AS j FROM calculation_run r"
                            " WHERE id = $1", rid)
    ]  # fmt: skip
    assert rows["calculation_run"] and rows["liquidity_gap_result"]
    return canonical_sha256(rows)


def pinned(run: dict[str, Any], code: str) -> dict[str, Any]:
    [member] = [m for m in run["manifest"]["rule_set"]["members"] if m["rule_code"] == code]
    return dict(member)


# The DAY_BUCKETS proposal made from v1, which the activation of NEW_BUCKETS leaves stale.
STALE_BUCKETS = [bucket("B01", 0, 30), bucket("B02", 30, 365), bucket("B03", 365, None)]
# Test definitions for the kinds Phase 17 seeds: code, parameter schema, ACTIVE v1 parameters.
TEST_KIND_RULES: dict[ParameterKind, tuple[str, dict[str, Any], dict[str, Any]]] = {
    ParameterKind.DECIMAL: (
        "test_run_off",
        {"kind": "DECIMAL", "min": "0", "max": "1", "scale": 2, "unit": "ratio"},
        {"value": "0.10"},
    ),
    ParameterKind.INTEGER: (
        "test_horizon",
        {"kind": "INTEGER", "min": 1, "max": 365},
        {"value": 30},
    ),
    ParameterKind.DECIMAL_MAP: (
        "test_haircuts",
        {
            "kind": "DECIMAL_MAP",
            "key_vocabulary": "instrument_type",
            "min": "0",
            "max": "1",
            "scale": 2,
        },
        {"map": {"BOND": "0.00", "LOAN": "0.50"}},
    ),
}


def insert_kind_rule(db: str, kind: ParameterKind) -> str:
    """The test definition of this kind (TEST_KIND_RULES), with an ACTIVE v1."""
    code, schema, parameters = TEST_KIND_RULES[kind]
    return insert_rule(db, code, schema, parameters)


def both_roles(db: str) -> dict[str, str]:
    """Holds rule:propose and rule:approve: only maker-checker stops self-approval."""
    return token_for(db, "maker-and-checker", ("risk_analyst", "approver"))


def propose_draft(
    api: TestClient, headers: dict[str, str], route: str, patch: Any
) -> dict[str, Any]:
    """A DRAFT proposed on a rule's versions route, from its ACTIVE version."""
    r = api.post(
        route,
        json={"parameters_patch": patch, "change_summary": "Proposed from the ACTIVE version"},
        headers=headers,
    )
    assert r.status_code == 201 and r.json()["status"] == "DRAFT", r.text
    return dict(r.json())


# --- 1. Reads and the closed parameter kinds (BE-16.1, BE-16.2) ----------------------------------


@pytest.mark.ws3
def test_rule_reads_and_the_closed_kind_list(api: TestClient, db: str) -> None:
    """GET /rules and GET /rules/{rule_id} for every role (D-56 validation status folded in);
    no expression kind can be stored (DB CHECK) or pass check_schema."""
    analyst = dev_token(api, "risk_analyst")
    tokens = {role: dev_token(api, role) for role in ROLES}
    for role, headers in tokens.items():
        r = api.get(RULES, headers=headers)
        assert r.status_code == 200, (role, r.text)
        page = no_float(r.text)
        # The maturity_ladder seed (0018) and the lcr_hqla seed (0020, Phase 17).
        seeded = {**SEEDED_RULES, **LCR_SEEDED_RULES}
        assert page["total"] == len(seeded) == len(page["items"])
        assert [i["code"] for i in page["items"]] == sorted(seeded)
        assert all(
            (i["active_version"], i["version_count"], i["illustrative"], i["provisional"])
            == (1, 1, True, True)
            for i in page["items"]
        ), page
    rule_ids = {code: rule_id_of(db, code) for code in SEEDED_RULES}
    buckets_id = rule_ids[MATURITY_BUCKETS]
    detail = no_float(api.get(f"{RULES}/{buckets_id}", headers=tokens["auditor"]).text)
    [v1] = detail["versions"]
    assert (v1["status"], v1["version"], v1["seeded"], v1["author_user_id"]) == (
        "ACTIVE", 1, True, None
    )  # fmt: skip
    assert v1["parameters"] == {"buckets": SEEDED_BUCKETS} and v1["run_count"] == 0
    assert (detail["change_enabled"], detail["statement"]) == (True, ILLUSTRATIVE_STATEMENT)
    assert detail["target_module"]["code"] == MATURITY_LADDER
    direction = api.get(f"{RULES}/{rule_ids[CASHFLOW_DIRECTION]}", headers=analyst).json()
    assert set(direction["vocabulary"]) >= {"BOND", "DEPOSIT", "LOAN"}
    r = api.get(f"{RULES}/{uuid.uuid4()}", headers=analyst)
    assert (r.status_code, r.json().get("code")) == (404, "not_found"), r.text
    # Phase 30 (BE-30.2, D-56): ILLUSTRATIVE on every module version, PLANNED included, for
    # every role; an unapproved validated record raises (app/tests/validation_kit.py).
    check_module_validation(api, db)

    # The kind list is closed in the database itself: no expression kind can be stored.
    module = sql(db, "SELECT id FROM calculation_module WHERE code = $1", MATURITY_LADDER)
    with pytest.raises(asyncpg.CheckViolationError):
        sql(
            db,
            "INSERT INTO rule_definition (id, tenant_id, code, name, category,"
            " target_module_id, owner, parameter_schema, provisional, illustrative,"
            " created_by) VALUES (gen_random_uuid(), $1, 'formula_rule', 'f', 'FACTOR', $2,"
            " 'o', $3::jsonb, true, true, 'test')",
            DEFAULT_TENANT_ID,
            module[0]["id"],
            json.dumps({"kind": "EXPRESSION", "formula": "amount * 2"}),
        )

    for schema in ({"kind": "EXPRESSION"}, {"kind": "DECIMAL", "min": "0", "max": "1",
                   "scale": 2, "formula": SENTINEL}):  # fmt: skip
        problems = check_schema(schema)
        assert problems and all(p.code in set(ParameterProblem) for p in problems), problems
        assert SENTINEL not in repr(problems)
    assert [p.code for p in check_schema({"kind": "EXPRESSION"})] == ["unknown_kind"]


@pytest.mark.ws3
def test_rule_proposals_are_checked_for_every_parameter_kind(api: TestClient, db: str) -> None:
    """For every kind of the allowlist: each valid patch is a DRAFT; each invalid one is 422
    invalid_parameters, value-free, audited DENIED, with nothing written; an unchanged result
    is 422 no_change; a DECIMAL is never stored or returned as a JSON number. A definition
    whose own schema is malformed refuses every proposal (409 invalid_parameter_schema)."""
    analyst = dev_token(api, "risk_analyst")
    approver = dev_token(api, "approver")
    analyst_id = api.get("/api/v1/me", headers=analyst).json()["user_id"]
    rule_ids = {code: rule_id_of(db, code) for code in SEEDED_RULES}

    # One rule per kind, each with an ACTIVE v1: the seeded ENUM, DAY_BUCKETS and ENUM_MAP
    # rules, and test definitions for the kinds Phase 17 seeds (DECIMAL, INTEGER, DECIMAL_MAP).
    kind_rules = {
        ParameterKind.ENUM: rule_ids[PAST_DUE_TREATMENT],
        ParameterKind.DAY_BUCKETS: rule_ids[MATURITY_BUCKETS],
        ParameterKind.ENUM_MAP: rule_ids[CASHFLOW_DIRECTION],
        **{kind: insert_kind_rule(db, kind) for kind in TEST_KIND_RULES},
    }
    assert set(kind_rules) == set(ParameterKind)  # every kind of the allowlist is exercised
    gap = [bucket("B01", 0, 1), bucket("B02", 2, 7), bucket("B03", 7, None)]
    overlap = [bucket("B01", 0, 7), bucket("B02", 5, 30), bucket("B03", 30, None)]
    closed = [bucket("B01", 0, 7), bucket("B02", 7, 30)]
    reversed_ = [bucket("B01", 0, 7), bucket("B02", 7, 7), bucket("B03", 7, None)]
    mislabelled = [bucket("B01", 0, 7), {**bucket("B02", 7, None), "label": "(7,30]"}]
    twice = [bucket("B01", 0, 7), bucket("B01", 7, None)]
    fractional = [bucket("B01", 0, 7), {**bucket("B02", 7, None), "lower_exclusive": 7.5}]
    too_many = [bucket(f"B{i:02d}", i, i + 1) for i in range(60)] + [bucket("B99", 60, None)]
    # kind -> (valid patches, invalid patches, the patch that changes nothing)
    cases: dict[ParameterKind, tuple[list[Any], list[Any], Any]] = {
        ParameterKind.DECIMAL: (
            [{"value": "0.25"}],
            [
                {"value": 0.25},  # a JSON number, not decimal text
                {"value": 1},
                {"value": "47.11"},  # above max
                {"value": "-0.01"},  # below min
                {"value": "0.125"},  # scale exceeded
                {"value": SENTINEL},
                {"value": "NaN"},
                {"value": "1e-1"},
                {"value": None},  # removes the key
                {"value": "0.25", "formula": SENTINEL},  # unknown key: no free-form JSON
            ],
            {"value": "0.10"},
        ),
        ParameterKind.INTEGER: (
            [{"value": 45}],
            [
                {"value": 47110},
                {"value": 0},
                {"value": 30.5},
                {"value": "45"},
                {"value": True},
                {"value": None},
                {"value": 45, "expression": SENTINEL},
            ],
            {"value": 30},
        ),
        ParameterKind.ENUM: (
            [{"value": "INCLUDE_IN_FIRST_BUCKET"}],
            [
                {"value": SENTINEL},
                {"value": 1},
                {"value": ["INCLUDE_IN_FIRST_BUCKET"]},
                {"value": None},
                {"template": SENTINEL},
            ],
            {"value": "EXCLUDE_WITH_WARNING"},
        ),
        ParameterKind.DAY_BUCKETS: (
            [{"buckets": STALE_BUCKETS}],
            [
                {"buckets": gap},
                {"buckets": overlap},
                {"buckets": closed},
                {"buckets": reversed_},
                {"buckets": mislabelled},
                {"buckets": twice},
                {"buckets": fractional},
                {"buckets": too_many},
                {"buckets": []},
                {"buckets": [bucket("B01", -2, 7), bucket("B02", 7, None)]},
                {"buckets": [bucket("B01", 0, 47110), bucket("B02", 47110, None)]},
                {"buckets": [{**bucket("B01", 0, None), "code": SENTINEL}]},
                {"buckets": None},
                {"buckets": NEW_BUCKETS, "formula": SENTINEL},
            ],
            {"buckets": SEEDED_BUCKETS},
        ),
        ParameterKind.ENUM_MAP: (
            [{"map": {"DEPOSIT": "INFLOW"}}],
            [
                {"map": {"DEPOSIT": SENTINEL}},
                {"map": {"SENTINEL_TYPE": "INFLOW"}},
                {"map": {"LOAN": 1}},
                {"map": "INFLOW"},
                {"map": None},
                {"map": {"BOND": None, "DEPOSIT": None, "LOAN": None}},  # empty
            ],
            {"map": {"LOAN": "INFLOW"}},
        ),
        ParameterKind.DECIMAL_MAP: (
            [{"map": {"DEPOSIT": "0.15"}}, {"map": {"BOND": None}}],
            [
                {"map": {"LOAN": 0.5}},
                {"map": {"LOAN": "47.11"}},
                {"map": {"LOAN": "0.505"}},
                {"map": {"LOAN": SENTINEL}},
                {"map": {"SENTINEL_TYPE": "0.10"}},
                {"map": [SENTINEL]},
            ],
            {"map": {"LOAN": "0.50"}},
        ),
    }
    drafts: dict[ParameterKind, dict[str, Any]] = {}
    for kind, (valid, invalid, unchanged) in cases.items():
        rule_id = kind_rules[kind]
        for patch in invalid:
            before = versions_state(db)
            c = cid()
            r = api.post(
                f"{RULES}/{rule_id}/versions",
                json={"parameters_patch": patch, "change_summary": "invalid on purpose"},
                headers={**analyst, "X-Correlation-ID": c},
            )
            assert (r.status_code, r.json().get("code")) == (422, "invalid_parameters"), (
                kind,
                patch,
                r.text,
            )
            [event] = events(db, c)
            answer = r.json()["message"] + json.dumps(r.json()["details"])
            for leak in (SENTINEL, "47.11", "47110", "0.125", "0.505", "-0.01", "SENTINEL_TYPE"):
                assert leak not in answer + (event["details"] or ""), (kind, patch)
            codes = {p["code"] for p in r.json()["details"]["problems"]}
            assert codes and codes <= set(ParameterProblem), (kind, r.text)
            assert_denied(db, c, "invalid_parameters", "rule.propose")
            assert versions_state(db) == before, (kind, patch)  # nothing written
        c = cid()
        r = api.post(
            f"{RULES}/{rule_id}/versions",
            json={"parameters_patch": unchanged, "change_summary": "no change"},
            headers={**analyst, "X-Correlation-ID": c},
        )
        assert (r.status_code, r.json().get("code")) == (422, "no_change"), (kind, r.text)
        assert_denied(db, c, "no_change", "rule.propose")
        for patch in valid:
            c = cid()
            r = api.post(
                f"{RULES}/{rule_id}/versions",
                json={"parameters_patch": patch, "change_summary": f"valid {kind.value}"},
                headers={**analyst, "X-Correlation-ID": c},
            )
            assert r.status_code == 201, (kind, patch, r.text)
            draft = no_float(r.text)  # a DECIMAL is never a JSON number
            assert (draft["status"], draft["author_user_id"], draft["run_count"]) == (
                "DRAFT", analyst_id, 0
            )  # fmt: skip
            assert draft["parameters_sha256"] == canonical_sha256(draft["parameters"])
            assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
                ("rule.propose", "SUCCESS")
            ]
            drafts.setdefault(kind, draft)
    assert drafts[ParameterKind.DECIMAL]["parameters"] == {"value": "0.25"}
    assert drafts[ParameterKind.INTEGER]["parameters"] == {"value": 45}
    assert drafts[ParameterKind.ENUM_MAP]["parameters"]["map"] == {
        "BOND": "INFLOW", "DEPOSIT": "INFLOW", "LOAN": "INFLOW"
    }  # fmt: skip
    assert drafts[ParameterKind.DECIMAL_MAP]["parameters"] == {
        "map": {"BOND": "0.00", "DEPOSIT": "0.15", "LOAN": "0.50"}
    }
    stored = sql(
        db,
        "SELECT v.parameters::text AS p FROM rule_version v JOIN rule_definition d"
        " ON d.id = v.rule_definition_id WHERE d.code IN ('test_run_off', 'test_haircuts')",
    )
    assert len(stored) == 5  # v1 of each, a DRAFT of each, and the DECIMAL_MAP removal
    for row in stored:  # stored as JSON text, never as a JSON number
        no_float(row["p"])
    decimals = sql(
        db,
        "SELECT jsonb_typeof(v.parameters -> 'value') AS t FROM rule_version v"
        " JOIN rule_definition d ON d.id = v.rule_definition_id WHERE d.code = 'test_run_off'",
    )
    assert {r["t"] for r in decimals} == {"string"}
    for kind in (ParameterKind.DECIMAL, ParameterKind.DECIMAL_MAP):
        shown = no_float(api.get(f"{RULES}/{kind_rules[kind]}", headers=approver).text)
        assert shown["parameter_schema"]["kind"] == kind.value and len(shown["versions"]) >= 2

    # A definition whose own schema breaks the allowlist's rules refuses every proposal.
    broken = insert_rule(
        db, "test_broken", {"kind": "DECIMAL", "min": "1", "max": "0", "scale": 2}, {"value": "0.5"}
    )
    c = cid()
    r = api.post(
        f"{RULES}/{broken}/versions",
        json={"parameters_patch": {"value": "0.6"}, "change_summary": "against a bad schema"},
        headers={**analyst, "X-Correlation-ID": c},
    )
    assert (r.status_code, r.json().get("code")) == (409, "invalid_parameter_schema"), r.text
    assert_denied(db, c, "invalid_parameter_schema", "rule.propose")


# --- 2. Maker-checker (BE-16.2, D-32 §3, D-34) ---------------------------------------------------


@pytest.mark.ws3
def test_rule_maker_checker_refusals_and_rejection(api: TestClient, db: str) -> None:
    """Only risk_analyst proposes and submits, only approver decides (403 otherwise, audited);
    a DRAFT is not approved without submit (409 invalid_status); only the author submits (409
    not_author); the author never decides on their own version, even holding rule:approve
    (409 self_approval, audited); REJECT ends a version."""
    analyst = dev_token(api, "risk_analyst")
    approver = dev_token(api, "approver")
    tokens = {role: dev_token(api, role) for role in ROLES}
    analyst_id = api.get("/api/v1/me", headers=analyst).json()["user_id"]
    both = both_roles(db)
    proposal = {
        "parameters_patch": {"buckets": NEW_BUCKETS},
        "change_summary": "Coarser buckets for the review",
    }
    route = f"{RULES}/{rule_id_of(db, MATURITY_BUCKETS)}/versions"
    # Only rule:propose proposes and submits; only rule:approve decides. Each 403 is audited.
    for role in ("platform_admin", "auditor", "data_steward", "approver"):
        c = cid()
        r = api.post(route, json=proposal, headers={**tokens[role], "X-Correlation-ID": c})
        assert (r.status_code, r.json().get("code")) == (403, "forbidden"), (role, r.text)
        [event] = events(db, c)
        assert (event["action"], event["outcome"]) == (
            "POST /api/v1/rules/{rule_id}/versions", "DENIED"
        )  # fmt: skip
        assert json.loads(event["details"]) == {"required_permission": "rule:propose"}
    c = cid()
    r = api.post(route, json=proposal, headers={**analyst, "X-Correlation-ID": c})
    assert r.status_code == 201, r.text
    new = no_float(r.text)
    assert (new["status"], new["author_user_id"]) == ("DRAFT", analyst_id)
    assert new["parameters"] == {"buckets": NEW_BUCKETS}
    version = f"{route}/{new['version']}"
    decide = {"decision": "APPROVE", "comment": "Reviewed against the policy"}
    for role in ("platform_admin", "auditor", "data_steward", "risk_analyst"):
        r = api.post(f"{version}/approval", json=decide, headers=tokens[role])
        assert (r.status_code, r.json().get("code")) == (403, "forbidden"), (role, r.text)
    for role in ("platform_admin", "auditor", "data_steward", "approver"):
        r = api.post(f"{version}/submit", json={}, headers=tokens[role])
        assert (r.status_code, r.json().get("code")) == (403, "forbidden"), (role, r.text)
    # DRAFT -> ACTIVE without submit is refused, audited, and changes nothing.
    before = versions_state(db)
    c = cid()
    r = api.post(f"{version}/approval", json=decide, headers={**approver, "X-Correlation-ID": c})
    assert (r.status_code, r.json().get("code")) == (409, "invalid_status"), r.text
    assert_denied(db, c, "invalid_status", "rule.approve")
    assert versions_state(db) == before
    # Only the author releases a DRAFT: someone else holding rule:propose (here also
    # rule:approve, so they could then approve it alone) is refused, audited, nothing changes.
    c = cid()
    r = api.post(f"{version}/submit", json={}, headers={**both, "X-Correlation-ID": c})
    assert (r.status_code, r.json().get("code")) == (409, "not_author"), r.text
    assert_denied(db, c, "not_author", "rule.submit")
    assert versions_state(db) == before
    c = cid()
    r = api.post(
        f"{version}/submit", json={"comment": "Ready"}, headers={**analyst, "X-Correlation-ID": c}
    )
    assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW", r.text
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [("rule.submit", "SUCCESS")]
    materialised = sql(
        db,
        "SELECT code, label, lower_exclusive_days AS lower, upper_inclusive_days AS upper"
        " FROM maturity_bucket_definition WHERE rule_version_id = $1 ORDER BY sequence",
        uuid.UUID(new["rule_version_id"]),
    )
    assert [
        {"code": b["code"], "label": b["label"], "lower_exclusive": b["lower"],
         "upper_inclusive": b["upper"]}
        for b in materialised
    ] == NEW_BUCKETS  # fmt: skip
    r = api.post(f"{version}/submit", json={}, headers=analyst)
    assert (r.status_code, r.json().get("code")) == (409, "invalid_status"), r.text

    # Self-approval: the author holds rule:approve too, and is still refused (audited).
    past_due = f"{RULES}/{rule_id_of(db, PAST_DUE_TREATMENT)}/versions"
    own = api.post(
        past_due,
        json={"parameters_patch": {"value": "INCLUDE_IN_FIRST_BUCKET"}, "change_summary": "Own"},
        headers=both,
    )
    assert own.status_code == 201, own.text
    own_version = f"{past_due}/{own.json()['version']}"
    assert api.post(f"{own_version}/submit", json={}, headers=both).status_code == 200
    for decision in ("APPROVE", "REJECT"):
        before = versions_state(db)
        c = cid()
        r = api.post(
            f"{own_version}/approval",
            json={"decision": decision, "comment": "My own"},
            headers={**both, "X-Correlation-ID": c},
        )
        assert (r.status_code, r.json().get("code")) == (409, "self_approval"), r.text
        action = "rule.approve" if decision == "APPROVE" else "rule.reject"
        assert_denied(db, c, "self_approval", action)
        assert versions_state(db) == before
    c = cid()
    r = api.post(
        f"{own_version}/approval",
        json={"decision": "REJECT", "comment": "Not now"},
        headers={**approver, "X-Correlation-ID": c},
    )
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decision_comment"]) == ("REJECTED", "Not now")
    assert ("rule.reject", "SUCCESS") in [(e["action"], e["outcome"]) for e in events(db, c)]
    r = api.post(f"{own_version}/approval", json=decide, headers=approver)
    assert (r.status_code, r.json().get("code")) == (409, "invalid_status"), r.text


# --- 3. Activation and the switch, 4. pinning across the change (BE-16.2, BE-16.3) ---------------


@pytest.mark.ws3
def test_rule_activation_switch_and_pinning(api: TestClient, db: str, artifact_dir: Path) -> None:
    """APPROVE makes the bucket version ACTIVE and SUPERSEDES v1 in one transaction, leaving
    one ACTIVE version, and a DRAFT proposed from v1 is then stale (409 stale_version).
    RULE_CHANGE_ENABLED=false answers 409 rule_change_disabled to the three writes, audited,
    nothing written, while reads and runs keep working. A run after the approval pins the new
    version in a new rule_set with another result_sha256; the old run's rows are unchanged; a
    re-run repeats the old pins and hashes; GET /rules/{rule_id} lists each version's runs."""
    analyst = dev_token(api, "risk_analyst")
    approver = dev_token(api, "approver")
    steward = dev_token(api, "data_steward")
    auditor = dev_token(api, "auditor")
    buckets_id = rule_id_of(db, MATURITY_BUCKETS)
    route = f"{RULES}/{buckets_id}/versions"
    # Drafts from v1: the bucket proposal that goes stale, and the INTEGER test rule's v2 (the
    # submit refused under the switch).
    stale = propose_draft(api, analyst, route, {"buckets": STALE_BUCKETS})
    integer_rule = insert_kind_rule(db, ParameterKind.INTEGER)
    assert (
        propose_draft(api, analyst, f"{RULES}/{integer_rule}/versions", {"value": 45})["version"]
        == 2
    )

    snapshot_id = ladder_snapshot(api, db, steward, approver, analyst)
    body = {
        "dataset_snapshot_id": snapshot_id,
        "module_version_id": module_version_id(api, analyst),
        "reporting_date": REPORTING.isoformat(),
    }
    r = start_run(api, analyst, body)
    assert r.status_code == 201, r.text
    old = no_float(r.text)
    assert pinned(old, MATURITY_BUCKETS)["version"] == 1 and count(db, "rule_set") == 1
    old_rows = run_rows_sha256(db, old["run_id"])

    proposal = {
        "parameters_patch": {"buckets": NEW_BUCKETS},
        "change_summary": "Coarser buckets for the review",
    }
    r = api.post(route, json=proposal, headers=analyst)
    assert r.status_code == 201, r.text
    new = no_float(r.text)
    version = f"{route}/{new['version']}"
    decide = {"decision": "APPROVE", "comment": "Reviewed against the policy"}
    r = api.post(f"{version}/submit", json={"comment": "Ready"}, headers=analyst)
    assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW", r.text
    # The stale DRAFT (proposed from v1) goes to review too; it is decided below.
    r = api.post(f"{route}/{stale['version']}/submit", json={}, headers=analyst)
    assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW", r.text

    # --- 3. Activation, and the switch (BE-16.2) -----------------------------------------------
    off = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, RULE_CHANGE_ENABLED=False
    )
    with TestClient(create_app(off)) as frozen:
        before = versions_state(db)
        writes: tuple[tuple[str, str, dict[str, Any], dict[str, str]], ...] = (
            ("rule.propose", route, proposal, analyst),
            ("rule.submit", f"{RULES}/{integer_rule}/versions/2/submit", {}, analyst),
            ("rule.approve", f"{version}/approval", decide, approver),
        )
        for action, path, payload, headers in writes:
            c = cid()
            r = frozen.post(path, json=payload, headers={**headers, "X-Correlation-ID": c})
            assert (r.status_code, r.json().get("code")) == (409, "rule_change_disabled"), r.text
            assert_denied(db, c, "rule_change_disabled", action)
        assert versions_state(db) == before  # nothing written
        shown = frozen.get(f"{RULES}/{buckets_id}", headers=analyst)
        assert shown.status_code == 200 and shown.json()["change_enabled"] is False
        r = start_run(frozen, analyst, body)  # runs keep working, on the frozen versions
        assert r.status_code == 201, r.text
        frozen_run = r.json()
        assert frozen_run["rule_set_id"] == old["rule_set_id"]
        assert frozen_run["result_sha256"] == old["result_sha256"]

    c = cid()
    r = api.post(f"{version}/approval", json=decide, headers={**approver, "X-Correlation-ID": c})
    assert r.status_code == 200, r.text
    approved = no_float(r.text)
    assert (approved["status"], approved["decision_comment"]) == ("ACTIVE", decide["comment"])
    assert approved["approved_by"] and approved["approved_at"]
    trail = [(e["action"], e["outcome"]) for e in events(db, c)]
    assert ("rule.approve", "SUCCESS") in trail and all(o == "SUCCESS" for _, o in trail), trail
    statuses = sql(
        db,
        "SELECT v.id, v.version, v.status, v.xmin::text AS tx FROM rule_version v"
        " WHERE v.rule_definition_id = $1 ORDER BY v.version",
        uuid.UUID(buckets_id),
    )
    by_version = {s["version"]: s for s in statuses}
    assert [s["version"] for s in statuses if s["status"] == "ACTIVE"] == [new["version"]]
    assert by_version[1]["status"] == "SUPERSEDED"
    # Activation and supersession were written by one transaction.
    assert by_version[1]["tx"] == by_version[new["version"]]["tx"]
    assert (
        sql(
            db,
            "SELECT count(*) AS n FROM (SELECT rule_definition_id FROM rule_version"
            " WHERE status = 'ACTIVE' GROUP BY rule_definition_id HAVING count(*) > 1) x",
        )[0]["n"]
        == 0
    )
    # The DRAFT proposed from v1 is stale now: approving it would drop the new buckets.
    before = versions_state(db)
    c = cid()
    r = api.post(
        f"{route}/{stale['version']}/approval",
        json=decide,
        headers={**approver, "X-Correlation-ID": c},
    )
    assert (r.status_code, r.json().get("code")) == (409, "stale_version"), r.text
    assert_denied(db, c, "stale_version", "rule.approve")
    assert versions_state(db) == before

    # --- 4. Pinning across the change (BE-16.3) ------------------------------------------------
    r = start_run(api, analyst, body)
    assert r.status_code == 201, r.text
    fresh = no_float(r.text)
    member = pinned(fresh, MATURITY_BUCKETS)
    assert (member["rule_version_id"], member["version"]) == (
        new["rule_version_id"], new["version"]
    )  # fmt: skip
    assert member["parameters"] == {"buckets": NEW_BUCKETS}
    assert fresh["rule_set_id"] != old["rule_set_id"] and count(db, "rule_set") == 2
    assert fresh["result_sha256"] != old["result_sha256"]
    assert fresh["manifest_sha256"] != old["manifest_sha256"]
    ladder = api.get(f"{RUNS}/{fresh['run_id']}/maturity-ladder", headers=approver).json()
    assert [b["code"] for b in ladder["buckets"]] == [b["code"] for b in NEW_BUCKETS]
    assert run_rows_sha256(db, old["run_id"]) == old_rows  # the old run is untouched
    assert api.get(f"{RUNS}/{old['run_id']}", headers=analyst).json() == old

    c = cid()
    r = start_run(api, analyst, {"rerun_of": old["run_id"]}, **{"X-Correlation-ID": c})
    assert r.status_code == 201, r.text
    rerun = no_float(r.text)
    assert rerun["run_id"] != old["run_id"] and rerun["rerun_of_run_id"] == old["run_id"]
    for key in ("manifest_sha256", "result_sha256", "rule_set_id", "assumption_set_id",
                "dataset_snapshot_id", "reporting_date", "status"):  # fmt: skip
        assert rerun[key] == old[key], key
    assert pinned(rerun, MATURITY_BUCKETS)["version"] == 1  # SUPERSEDED, still pinned
    assert run_rows_sha256(db, old["run_id"]) == old_rows
    r = start_run(api, analyst, {"rerun_of": str(uuid.uuid4())})
    assert (r.status_code, r.json().get("code")) == (404, "not_found"), r.text

    # GET /rules/{rule_id}: each version lists the runs that pin it.
    detail = no_float(api.get(f"{RULES}/{buckets_id}", headers=auditor).text)
    shown = {v["version"]: v for v in detail["versions"]}
    assert detail["active_version"] == new["version"]
    assert detail["active_rule_version_id"] == new["rule_version_id"]
    assert shown[1]["status"] == "SUPERSEDED" and shown[1]["superseded_at"]
    assert {run["run_id"] for run in shown[1]["runs"]} == {
        old["run_id"], frozen_run["run_id"], rerun["run_id"]
    }  # fmt: skip
    assert shown[1]["run_count"] == 3
    assert [run["run_id"] for run in shown[new["version"]]["runs"]] == [fresh["run_id"]]
    assert shown[new["version"]]["approved_by"] == approved["approved_by"]
    assert shown[stale["version"]]["status"] == "IN_REVIEW"


# --- 5. Stress scenarios (Phase 24, BE-24.1 - BE-24.4) -------------------------------------------


@pytest.mark.ws3
def test_stress_scenarios_follow_the_governed_change_pattern(
    api: TestClient, db: str, artifact_dir: Path
) -> None:
    """Templates and static validation, exploration confirmation, maker-checker promotion,
    the 0026 triggers and the SCENARIOS_ENABLED switch (app/tests/scenario_kit.py)."""
    tokens = {role: dev_token(api, role) for role in ROLES}
    # The selectors name the ladder fixture's instruments (R-LOAN, R-DEP): project them first.
    ladder_snapshot(api, db, tokens["data_steward"], tokens["approver"], tokens["risk_analyst"])
    check_scenarios(api, db, artifact_dir, tokens, both_roles(db))
