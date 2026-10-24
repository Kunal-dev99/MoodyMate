"""Phase 15 (BE-15.1 – BE-15.5): the rule registry, dataset snapshots, a pinned run and the
maturity ladder end to end, in one essential test (D-26), in six parts:

1. Registry seed (BE-15.1): the frozen copies in migration 0018 equal the code (enums,
   transitions, seed, module contracts, hash function); module `maturity_ladder` v1 is
   AVAILABLE and registered in code; the rule versions are ACTIVE, tagged, PROVISIONAL and
   ILLUSTRATIVE, approved by the seed tag, with recomputable hashes; the buckets are
   materialised; the baseline assumption set is ACTIVE and empty; GET /calculation-modules
   shows what a run would pin, with the D-35 statement. The PLANNED extension points of
   migration 0021 (BE-18.1) are listed with their contracts, never runnable, and refused
   409 module_not_available with a DENIED audit (in part 4).
2. Module purity (BE-15.4): no float and no AI import anywhere in app/calculations/ (source
   grep, as BE-11.2); the exact context traps rounding; the past-due alternative and an
   invalid bucket rule, through the pure function.
3. Snapshots (BE-15.3): each refusal (not_found, not_projected, ambiguous_business_key,
   duplicate_schema, reconciliation_broken, calculations_disabled) with its DENIED audit and
   nothing written; the same members (in any order) give the same snapshot; content_sha256
   equals the hash recomputed from the member manifests.
4. Runs (BE-15.4): the golden loop (the BE-18.2 pack files, D-35 §4) checks the
   ladder, the status and the warning codes; an Idempotency-Key replay returns the original
   run and a changed body is a conflict; a second run on the same pins has a new id and the
   same manifest_sha256 and result_sha256; module_not_available, a missing snapshot, a bad
   body, a role without calc:run, a BLOCKED run, and CALCULATIONS_ENABLED=false (409,
   audited, nothing written, reads still work).
5. Reads (BE-15.5): the run list filters (and GET /imports' projected and schema_code
   filters, for the new-run panel, in part 3); the manifest holds every pinned id and version and
   its hash recomputes; every bucket total equals the sum of its contributions, and each
   contribution resolves to a source row of a member import; contributions are masked for
   risk_analyst, and so is every ladder figure built from fewer than CALC_MIN_AGGREGATE_COUNT
   contributions (D-33 minimum aggregation: a reporting date that isolates one cash flow);
   no JSON float anywhere. Also in part 4: a stale RUNNING run is ended FAILED on a replay of
   its key, and an engine failure (even a cancellation-like exit) ends the run FAILED.
6. Guards (BE-15.1, BE-15.2): rule and module versions outside DRAFT refuse UPDATE, DELETE
   and TRUNCATE; a DRAFT is editable until it leaves DRAFT; at most one ACTIVE version per
   rule (DB); snapshots and rule sets are append-only; a finished run refuses any change and
   any new output row; the downgrade refuses while a run or a non-seed registry row exists,
   and without them 0018 -> 0017 -> head removes and restores the tagged seed.

Phase 17 (BE-17.1 – BE-17.3), folded in (+0): part 1 checks the 0020 frozen seed and the
second module (`lcr_hqla` v1); part 2 runs it purely (levels, haircuts, uncapped L2 flagged,
a zero denominator with outflows, a JSON-number parameter refused); part 5b runs the
hand-computed LCR golden case (the lcr_hqla pack file, BE-18.2) and reads GET …/liquidity
(HQLA total = its included asset rows, D-33 masking, a rerun's hashes, 404 for a run of
another module); part 6
loops the output guards over hqla_asset_result and lcr_result, and 0020 refuses its downgrade
while LCR results exist.

Phase 25 (BE-25.1 - BE-25.4; D-45, D-46), folded in (+0, scenario_run_kit): part 2 runs the
scenario overlay golden loop (the §5 worked example and the other engineering cases, stable
part ids, the no-float and no-`app.ai` greps); part 5c runs scenarios against a baseline
through the API (Feature 03 key sets for non-stressed runs, the §5 figures end to end, k + 1
runs, conservation and a tampered part, approval labels, refusals, Idempotency-Key replay,
re-runs after CLOSED) and reads the comparison and drill-down (exact deltas, turns-negative,
breaks only combined, largest changes, distinct-flow masking, the summary, lineage); part 5b
refuses an LCR baseline; part 6 loops the output guards over the three new run-output tables,
proves scenario_run append-only, intermediate_value unchanged and the 0027 downgrade refusal.

Phase 30 (BE-30.2 - BE-30.4; D-56), folded in (+0, validation_kit): the `context` block of the
run detail, the results page, the maturity ladder (part 4), the LCR read (5b) and the
comparison (5c) equals the run's pins (units frozen per module), with validation_status
ILLUSTRATIVE in the run list and every read; the Feature 03 run's stored manifest_sha256 and
result_sha256 are recomputed from its stored rows, both documents keep the Feature 03 key sets
and the re-run reproduces both hashes and its context; the boundary cases (ENGINEERING — awaiting
domain-expert approval): LCR ratio ties on each side of ROUND_HALF_EVEN, inflows at the 75% cap
and one minor unit above, zero net outflows (JSON ratio null, LCR_DENOMINATOR_ZERO) in 5b;
999999999999999999.99 exact through the ladder, a 3-dp amount (1.235) held at validation and a
trailing-zero 3-dp amount (1.230) accepted as 1.23 in 5c.
"""

import dataclasses
import hashlib
import importlib.util
import json
import re
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.calculations.constants import (
    ENGINE_VERSION,
    TERMINAL_STATUSES,
    CalculationRunStatus,
    CalculationWarningCode,
    InputRefType,
    IntermediateCode,
    LcrWarningCode,
    PostCheck,
    WarningSeverity,
)
from app.calculations.models import LcrResult
from app.calculations.modules import REGISTRY, lcr_hqla, maturity_ladder
from app.calculations.modules.base import (
    CashFlowInput,
    InstrumentInput,
    ModuleInputs,
    PinnedRule,
    PositionInput,
    RegisteredModule,
)
from app.calculations.service import (
    ladder_classes,
    liquidity_classes,
    reproduce,
    result_document,
)
from app.core.canonical import canonical_sha256
from app.core.config import Settings
from app.core.decimals import exact_context
from app.domain.constants import CashflowOrigin, DomainSchemaCode
from app.main import create_app
from app.rules.constants import (
    BASELINE_ASSUMPTION_SET,
    HQLA_HAIRCUT,
    HQLA_LEVEL,
    ILLUSTRATIVE_STATEMENT,
    LCR_HQLA,
    LCR_INFLOW_CAP,
    LCR_INFLOW_RATE,
    LCR_SEED_TAG,
    LCR_SEEDED_RULES,
    MATURITY_BUCKETS,
    MATURITY_LADDER,
    PAST_DUE_TREATMENT,
    RULE_VERSION_TRANSITIONS,
    RULES_SEED_TAG,
    SEEDED_ASSUMPTION_SET_NAME,
    SEEDED_BUCKETS,
    SEEDED_RULE_OWNER,
    SEEDED_RULES,
    CashflowDirection,
    ImplementationStatus,
    ParameterKind,
    RuleCategory,
    RuleVersionStatus,
)
from app.rules.service import check_parameters, check_schema, exact_json
from app.tests.canonical_kit import check_overlay_properties
from app.tests.conftest import (
    _IDENTITY_CLEANUP,
    alembic_config,
    clean,
    dev_token,
    insert_tenant,
    script,
    sql,
)
from app.tests.executor_kit import check_background_execution
from app.tests.helpers import (
    IMPORTS,
    MAPPINGS,
    MODULES,
    RULES,
    RUNS,
    SNAPSHOTS,
    SOURCES,
    activate_version,
    cid,
    contract_body,
    events,
    make_snapshot,
    module_version_id,
    mrule,
    project,
    rule_id,
    sample_rules,
    source_body,
    start_run,
    token_for,
    upload,
    validate_import,
    without_rate_limits,
)
from app.tests.lcr_kit import (
    LCR_OUTPUT_TABLES,
    POSITION_COLUMNS,
    position_csv,
)
from app.tests.observability_kit import check_metrics_after_a_run_and_a_failed_import
from app.tests.pagination_kit import check_cursor_pages, check_foreign_cursor
from app.tests.scenario_run_kit import (
    OVERLAY_GOLDEN,
    SCENARIO_OUTPUT_TABLES,
    check_lcr_baseline,
    check_overlay_pure,
    check_scenario_append_only,
    check_scenario_runs,
    clear_scenario_runs,
    intermediate_value_shape,
)
from app.tests.validation_kit import (
    check_context,
    check_ladder_boundaries,
    check_lcr_boundaries,
    check_stored_hashes,
)

pytestmark = pytest.mark.db

APP = Path(__file__).resolve().parents[1]
MIGRATIONS = APP / "db" / "migrations" / "versions"
IMMUTABLE = asyncpg.InsufficientPrivilegeError
MASKED = {"masked": True, "classification": "confidential"}
DECIMAL_TEXT = re.compile(r"^-?\d+(\.\d+)?$")
REGISTRY_TABLES = (
    "calculation_module",
    "module_version",
    "rule_definition",
    "rule_version",
    "maturity_bucket_definition",
    "assumption_set",
)
SNAPSHOT_TABLES = ("dataset_snapshot", "dataset_snapshot_member", "rule_set", "rule_set_member")
OUTPUT_TABLES = (
    "calculation_input_ref",
    "liquidity_gap_result",
    "intermediate_value",
    "calculation_warning",
)
CALC_TABLES = (*SNAPSHOT_TABLES, "calculation_run", *OUTPUT_TABLES, *LCR_OUTPUT_TABLES)
ALL_RULES = {**SEEDED_RULES, **LCR_SEEDED_RULES}
SEED_TAGS = {
    **dict.fromkeys(SEEDED_RULES, RULES_SEED_TAG),
    **dict.fromkeys(LCR_SEEDED_RULES, LCR_SEED_TAG),
}
# GET /calculation-runs/{id}/liquidity: the decimal figures of a currency (BE-17.3).
LCR_FIGURES = (
    "hqla_total",
    "outflow_balance",
    "outflows",
    "inflows_gross",
    "inflows_before_cap",
    "inflow_cap_amount",
    "inflows_after_cap",
    "net_outflows",
)

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
INSTRUMENT_RULES = [
    mrule("instrument_id", "instrument_id"),
    mrule("instrument_type", "instrument_type"),
    mrule("currency", "currency"),
    mrule("notional", "notional", "PARSE_DECIMAL"),
]


AMOUNTS = (
    "inflows",
    "outflows",
    "net_gap",
    "cumulative_gap",
    "inflows_contractual",
    "inflows_behavioural",
    "outflows_contractual",
    "outflows_behavioural",
)
# The domain validation pack (BE-18.2, D-35 §4): one hand-computed engineering case per JSON
# file in app/calculations/golden/, ENGINEERING — awaiting domain-expert approval, not
# "approved golden cases" (see its README). Each file is exact per metric (tolerance "0").
GOLDEN_DIR = APP / "calculations" / "golden"
GOLDEN_STATUS = "ENGINEERING — awaiting domain-expert approval"
CASHFLOW_FIELDS = (
    "cashflow_id",
    "instrument_id",
    "days_after_reporting_date",
    "amount",
    "currency",
    "cashflow_type",
    "origin",
)
GOLDEN_POSITION_FIELDS = ("position_id", "instrument_id", "balance", "currency")


def no_fraction(text: str) -> Any:
    raise AssertionError(f"a JSON fraction in the golden pack: {text}")  # amounts are text


LCR_TOLERANCE = (
    *LCR_FIGURES,
    "ratio",
    "levels",
    "excluded_asset_count",
    "outflow_count",
    "inflow_count",
)


def golden_case(path: Path) -> dict[str, Any]:
    """A pack file in its module's loop shape. maturity_ladder v1: input rows as CSV tuples,
    ladder rows as (the AMOUNTS, the number of contributions). lcr_hqla v1: input rows as CSV
    tuples, the per-currency figures under "lcr", asset keys as sets."""
    case = json.loads(path.read_text(encoding="utf-8"), parse_float=no_fraction)
    assert case["status"] == GOLDEN_STATUS, path
    inputs, expected = case["inputs"], case["expected"]
    common = {
        **case,
        "reporting_date": date.fromisoformat(inputs["reporting_date"]),
        "instruments": [tuple(i[c] for c in INSTRUMENT_COLUMNS) for i in inputs["instruments"]],
        "cash_flows": [tuple(f[c] for c in CASHFLOW_FIELDS) for f in inputs["cash_flows"]],
    }
    if case["module"] == {"code": LCR_HQLA, "version": 1}:
        assert case["tolerance"] == dict.fromkeys(LCR_TOLERANCE, "0"), path
        return {
            **common,
            "positions": [tuple(p[c] for c in GOLDEN_POSITION_FIELDS) for p in inputs["positions"]],
            "lcr": expected["currencies"],
            "outcome": expected["outcome"],
            "included_assets": {k: set(v) for k, v in expected["included_assets"].items()},
            "excluded_assets": set(expected["excluded_assets"]),
            "masked_for_internal": {k: set(v) for k, v in expected["masked_for_internal"].items()},
        }
    assert case["module"] == {"code": MATURITY_LADDER, "version": 1}, path
    assert case["tolerance"] == dict.fromkeys((*AMOUNTS, "contribution_count"), "0"), path
    return {
        **common,
        **expected,
        "ladder": {
            currency: {
                code: (tuple(row[a] for a in AMOUNTS), row["contribution_count"])
                for code, row in rows.items()
            }
            for currency, rows in expected["ladder"].items()
        },
    }


# Phase 25: the scenario overlay pack file has its own loop (scenario_run_kit, part 2).
PACK = [golden_case(path) for path in sorted(GOLDEN_DIR.glob("*.json")) if path != OVERLAY_GOLDEN]
# Each file runs under its own module: the ladder loop (part 4) and the LCR part (5b).
GOLDEN = [g for g in PACK if g["module"]["code"] == MATURITY_LADDER]
LCR_GOLDEN_CASES = [g for g in PACK if g["module"]["code"] == LCR_HQLA]


def migration(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def no_float(text: str) -> Any:
    """Parse JSON, failing on any JSON number with a fraction or exponent."""

    def refuse(value: str) -> Any:
        raise AssertionError(f"a JSON float: {value}")

    return json.loads(text, parse_float=refuse)


def count(db: str, table: str) -> int:
    return int(sql(db, f"SELECT count(*) AS n FROM {table}")[0]["n"])


def calc_counts(db: str) -> dict[str, int]:
    return {t: count(db, t) for t in CALC_TABLES}


def denied(db: str, correlation: str, action: str, reason: str) -> None:
    [event] = events(db, correlation)
    assert (event["action"], event["outcome"]) == (action, "DENIED"), dict(event)
    assert json.loads(event["details"]) == {"reason": reason}


def csv_source(
    api: TestClient, db: str, steward: dict[str, str], code: str, schema: str, columns: list[str]
) -> dict[str, Any]:
    """A CSV source whose contract is these columns. The instrument key is (instrument_id,
    currency), so one instrument_id may come twice in one file (the ambiguous fixture)."""
    key = {"instrument": ["instrument_id", "currency"], "position": ["position_id"]}.get(
        schema, ["cashflow_id"]
    )
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


def instrument_csv(rows: Sequence[tuple[str, ...]]) -> bytes:
    lines = [",".join(INSTRUMENT_COLUMNS), *(",".join(r) for r in rows)]
    return ("\n".join(lines) + "\n").encode()


def cashflow_csv(rows: Sequence[tuple[Any, ...]], reporting: date) -> bytes:
    lines = [",".join(CASHFLOW_COLUMNS)]
    for key, instrument, days, amount, currency, kind, origin in rows:
        due = (reporting + timedelta(days=days)).isoformat()
        lines.append(",".join([key, instrument, due, amount, currency, kind, origin]))
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.ws3
def test_registry_snapshot_run_and_maturity_ladder(
    api: TestClient, db: str, artifact_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    without_rate_limits(api)
    # --- 1. The registry seed (BE-15.1) --------------------------------------------------------
    m18 = migration("0018_calculations")
    m21 = migration("0021_planned_modules")
    assert m21.down_revision == "0020"  # re-chained onto Phase 17's 0020_lcr_results
    spec = maturity_ladder.SPEC
    assert (m18.TAG, m18.STATEMENT) == (RULES_SEED_TAG, ILLUSTRATIVE_STATEMENT)
    assert m18.RULE_STATUSES == tuple(s.value for s in RuleVersionStatus)
    assert {k: set(v) for k, v in m18.RULE_TRANSITIONS.items()} == {
        k.value: {s.value for s in v} for k, v in RULE_VERSION_TRANSITIONS.items()
    }
    assert m18.RULE_CATEGORIES == tuple(c.value for c in RuleCategory)
    assert m18.PARAMETER_KINDS == tuple(k.value for k in ParameterKind)
    assert m18.IMPLEMENTATION_STATUSES == tuple(s.value for s in ImplementationStatus)
    assert m18.RUN_STATUSES == tuple(s.value for s in CalculationRunStatus)
    assert set(m18.TERMINAL) == {s.value for s in TERMINAL_STATUSES}
    assert m18.WARNING_CODES == tuple(c.value for c in CalculationWarningCode)
    assert m18.SEVERITIES == tuple(s.value for s in WarningSeverity)
    assert m18.INPUT_REF_TYPES == tuple(t.value for t in InputRefType)
    assert m18.INTERMEDIATE_CODES == tuple(c.value for c in IntermediateCode)
    assert m18.DIRECTIONS == tuple(d.value for d in CashflowDirection)
    assert m18.ORIGINS == tuple(o.value for o in CashflowOrigin)
    # BE-19.4: 0022 widened the member CHECK (D-38); 0018's copy is the codes before it.
    assert m18.SCHEMA_CODES + migration("0022_credit_domain").SCHEMA_CODES == tuple(
        c.value for c in DomainSchemaCode
    )
    assert m18.BUCKETS == SEEDED_BUCKETS
    assert m18.RULES == {
        code: (name, category.value, schema, params, description)
        for code, (name, category, schema, params, description) in SEEDED_RULES.items()
    }
    assert (m18.RULE_OWNER, m18.ASSUMPTION_SET["name"]) == (
        SEEDED_RULE_OWNER,
        SEEDED_ASSUMPTION_SET_NAME,
    )
    assert m18.ASSUMPTION_SET["code"] == BASELINE_ASSUMPTION_SET
    assert (m18.MODULE["code"], m18.MODULE["name"]) == (spec.code, spec.name)
    assert m18.MODULE_VERSION == {
        "version": spec.version,
        "implementation_status": ImplementationStatus.AVAILABLE.value,
        "engine_version": spec.engine_version,
        "description": spec.description,
        "input_contract": spec.input_contract,
        "output_contract": spec.output_contract,
    }
    assert spec.engine_version == ENGINE_VERSION
    probe = {"b": [1, None, "é"], "a": {"z": "0.10"}}
    assert m18.canonical_sha256(probe) == canonical_sha256(probe)
    m20 = migration("0020_lcr_results")  # BE-17.1: its frozen seed equals the code
    lspec = lcr_hqla.SPEC
    assert (m20.TAG, m20.STATEMENT, m20.down_revision) == (
        LCR_SEED_TAG,
        ILLUSTRATIVE_STATEMENT,
        "0018",
    )
    assert m20.RULES == {
        code: (name, category.value, schema, params, description)
        for code, (name, category, schema, params, description) in LCR_SEEDED_RULES.items()
    }
    assert m20.MODULE["code"] == lspec.code and m20.MODULE_VERSION["version"] == lspec.version
    assert m20.MODULE_VERSION["engine_version"] == lspec.engine_version
    assert set(m20.OUTPUT_TABLES) == set(LCR_OUTPUT_TABLES)
    assert m20.LCR_WARNING_CODES == tuple(c.value for c in LcrWarningCode)
    planned_ids = {m21._id("calculation_module", c) for c in m21.MODULES} | {
        m21._id("module_version", f"{c}:{m21.VERSION}") for c in m21.MODULES
    }
    assert planned_ids.isdisjoint({m20.MODULE_ID, m20.VERSION_ID})  # uuid5 seeds never collide

    modules = sql(
        db,
        "SELECT m.code, v.* FROM module_version v JOIN calculation_module m ON m.id = v.module_id"
        " WHERE v.implementation_status = 'AVAILABLE'",
    )
    assert {(r["code"], r["version"]) for r in modules} == set(REGISTRY)  # code == seed (D-31)
    for registered in (spec, lspec):
        [module_row] = [r for r in modules if r["code"] == registered.code]
        assert json.loads(module_row["input_contract"]) == registered.input_contract
        assert json.loads(module_row["output_contract"]) == registered.output_contract
        assert module_row["implementation_status"] == "AVAILABLE", registered.code
    assert module_row["seeded_by"] == LCR_SEED_TAG and lspec.code == LCR_HQLA
    for table in REGISTRY_TABLES:
        rows = sql(
            db,
            f"SELECT seeded_by, provisional, illustrative FROM {table}"  # noqa: S608
            " WHERE seeded_by IS DISTINCT FROM $1",
            m21.TAG,
        )
        assert rows, table
        assert all(
            r["seeded_by"] in (RULES_SEED_TAG, LCR_SEED_TAG)
            and r["provisional"]
            and r["illustrative"]
            for r in rows
        ), table
    versions = sql(
        db,
        "SELECT d.code, d.description AS rule_description, v.* FROM rule_version v"
        " JOIN rule_definition d ON d.id = v.rule_definition_id",
    )
    assert {v["code"] for v in versions} == set(ALL_RULES)
    assert (set(spec.rules), set(lspec.rules)) == (set(SEEDED_RULES), set(LCR_SEEDED_RULES))
    for v in versions:
        parameters = json.loads(v["parameters"])
        assert parameters == ALL_RULES[v["code"]][3]
        assert v["parameters_sha256"] == canonical_sha256(parameters)
        assert (v["status"], v["version"]) == ("ACTIVE", 1)
        tag = SEED_TAGS[v["code"]]
        assert v["authored_by"] == v["approved_by"] == v["seeded_by"] == tag and v["approved_at"]
        assert v["rule_description"].endswith(ILLUSTRATIVE_STATEMENT)
    all_versions = {v["code"]: str(v["id"]) for v in versions}
    seeded_versions = {code: all_versions[code] for code in spec.rules}
    lcr_versions = {code: all_versions[code] for code in lspec.rules}
    buckets = sql(
        db,
        "SELECT sequence, code, label, lower_exclusive_days AS lower,"
        " upper_inclusive_days AS upper FROM maturity_bucket_definition ORDER BY sequence",
    )
    assert [
        {"code": b["code"], "label": b["label"], "lower_exclusive": b["lower"],
         "upper_inclusive": b["upper"]}
        for b in buckets
    ] == SEEDED_BUCKETS  # fmt: skip
    [baseline] = sql(db, "SELECT * FROM assumption_set")
    assert (baseline["code"], baseline["status"], json.loads(baseline["assumptions"])) == (
        "baseline",
        "ACTIVE",
        {},
    )
    assert baseline["content_sha256"] == canonical_sha256({})
    [index] = sql(
        db, "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_rule_version_one_active'"
    )
    assert "UNIQUE" in index["indexdef"] and "WHERE" in index["indexdef"]

    steward, approver = dev_token(api, "data_steward"), dev_token(api, "approver")
    analyst = dev_token(api, "risk_analyst")
    catalogue = no_float(api.get(MODULES, headers=dev_token(api, "auditor")).text)
    listed_modules = {
        m["code"]: m for m in catalogue["items"] if m["implementation_status"] == "AVAILABLE"
    }
    assert set(listed_modules) == {"maturity_ladder", LCR_HQLA}
    lcr_module = listed_modules[LCR_HQLA]
    assert (lcr_module["version"], lcr_module["implementation_status"]) == (1, "AVAILABLE")
    assert lcr_module["runnable"] and lcr_module["illustrative"]
    assert lcr_module["statement"] == ILLUSTRATIVE_STATEMENT
    assert {r["code"]: r["active_version"]["rule_version_id"] for r in lcr_module["rules"]} == (
        lcr_versions
    )
    module = listed_modules["maturity_ladder"]
    # BE-18.1: the PLANNED extension points are listed, with their declared contracts, and
    # never runnable (part 4: refused 409 module_not_available, audited DENIED).
    planned = {m["code"]: m for m in catalogue["items"] if m["implementation_status"] == "PLANNED"}
    assert set(planned) == set(m21.MODULES) == {"alm_irrbb", "collateral", "capital"}
    for code, m in planned.items():
        assert not m["runnable"] and m["provisional"] and m["illustrative"], code
        assert m["input_contract"] == m21.MODULES[code]["input_contract"], code
        assert m["output_contract"] == m21.MODULES[code]["output_contract"], code
    assert (module["code"], module["version"], module["implementation_status"]) == (
        "maturity_ladder",
        1,
        "AVAILABLE",
    )
    assert module["runnable"] and module["provisional"] and module["illustrative"]
    assert module["statement"] == ILLUSTRATIVE_STATEMENT
    assert module["engine_version"] == ENGINE_VERSION
    assert [r["code"] for r in module["rules"]] == list(spec.rules)
    assert {r["code"]: r["active_version"]["rule_version_id"] for r in module["rules"]} == (
        seeded_versions
    )
    assert all(r["active_version"]["illustrative"] for r in module["rules"])
    assert module["assumption_set"]["assumption_set_id"] == str(baseline["id"])

    # --- 2. Module purity (BE-15.4, D-31 §1-2, §8) ---------------------------------------------
    sources = [*sorted((APP / "calculations").rglob("*.py")), APP / "core" / "decimals.py"]
    assert len(sources) > 8 and APP / "calculations" / "modules" / "lcr_hqla.py" in sources
    for path in sources:
        text = path.read_text()
        assert "float" not in text, path  # as BE-11.2: Decimal only
        assert "app.ai" not in text, path  # no AI on the calculation path
    with localcontext(exact_context()), pytest.raises((Inexact, Rounded)):
        _ = Decimal(1) / Decimal(3)
    check_overlay_pure()  # Phase 25 (BE-25.2): the overlay golden loop, §5 case, greps
    check_overlay_properties()  # Phase 29 (BE-29.4): hypothesis, conservation and part ids
    reporting = GOLDEN[0]["reporting_date"]
    flow_id, version_id = uuid.uuid4(), uuid.uuid4()
    pure_inputs = ModuleInputs(
        reporting,
        (
            CashFlowInput(
                flow_id, "P-1", reporting - timedelta(days=5), Decimal("7.00"), "EUR", "CONTRACTUAL"
            ),
        ),
        (InstrumentInput(version_id, uuid.uuid4(), "P-1", "LOAN"),),
    )
    pins = {code: PinnedRule(uuid.uuid4(), seed[3]) for code, seed in SEEDED_RULES.items()}
    pins[PAST_DUE_TREATMENT] = PinnedRule(uuid.uuid4(), {"value": "INCLUDE_IN_FIRST_BUCKET"})
    with localcontext(exact_context()):
        result = maturity_ladder.MODULE.run(pure_inputs, pins)
    [placed] = result.contributions
    assert (placed.bucket.code, placed.amount, placed.days_after_reporting_date) == (
        "B01",
        Decimal("7.00"),
        -5,
    )
    assert [w.code for w in result.warnings] == [CalculationWarningCode.PAST_DUE_IN_FIRST_BUCKET]
    open_last = {"code": "X", "label": "x", "lower_exclusive": 0, "upper_inclusive": 5}
    pins[MATURITY_BUCKETS] = PinnedRule(uuid.uuid4(), {"buckets": [open_last]})
    blocked = maturity_ladder.MODULE.run(pure_inputs, pins)
    assert blocked.blocked and [w.code for w in blocked.warnings] == ["PARAMETERS_INVALID"]
    # lcr_hqla v1 (BE-17.2), pure, under pins that are not the seed: three levels with their
    # haircuts (L2A/L2B included uncapped, flagged), a cap of 1 that makes the denominator 0
    # with outflows present, a flow due on the reporting date (out of the (0, 30] horizon).
    kinds = {"B": "BOND", "C": "COVERED", "Q": "EQUITY", "D": "DEPOSIT", "L": "LOAN"}
    held = {k: InstrumentInput(uuid.uuid4(), uuid.uuid4(), k, t) for k, t in kinds.items()}
    held_rows = [("B", "100.00"), ("C", "200.00"), ("Q", "50.00"), ("D", "1000.00")]
    lcr_inputs = ModuleInputs(
        reporting,
        tuple(
            CashFlowInput(
                uuid.uuid4(), "L", reporting + timedelta(days=d), Decimal(a), "EUR", "CONTRACTUAL"
            )
            for d, a in ((0, "999.00"), (10, "400.00"))
        ),
        tuple(held.values()),
        tuple(
            PositionInput(uuid.uuid4(), uuid.uuid4(), f"P-{k}", k, Decimal(b), "EUR")
            for k, b in held_rows
        ),
    )
    lcr_pins = {code: PinnedRule(uuid.uuid4(), seed[3]) for code, seed in LCR_SEEDED_RULES.items()}
    lcr_pins[HQLA_LEVEL] = PinnedRule(
        uuid.uuid4(), {"map": {"BOND": "L1", "COVERED": "L2A", "EQUITY": "L2B"}}
    )
    lcr_pins[HQLA_HAIRCUT] = PinnedRule(
        uuid.uuid4(), {"map": {"BOND": "0", "COVERED": "0.15", "EQUITY": "0.50"}}
    )
    lcr_pins[LCR_INFLOW_CAP] = PinnedRule(uuid.uuid4(), {"value": "1"})
    with localcontext(exact_context()):
        priced = lcr_hqla.MODULE.run(lcr_inputs, lcr_pins)
    [lcr_row] = priced.lcr
    assert (
        lcr_row.hqla_level1,
        lcr_row.hqla_level2a,
        lcr_row.hqla_level2b,
        lcr_row.hqla_total,
    ) == (Decimal("100.00"), Decimal("170.00"), Decimal("25.00"), Decimal("295.00"))
    assert [(a.level, a.haircut, a.hqla_value) for a in priced.hqla_assets] == [
        ("L1", Decimal("0"), Decimal("100.00")),
        ("L2A", Decimal("0.15"), Decimal("170.00")),
        ("L2B", Decimal("0.50"), Decimal("25.00")),
    ]
    assert (lcr_row.outflows, lcr_row.inflows_before_cap, lcr_row.inflows_after_cap) == (
        Decimal("100.00"),
        Decimal("200.00"),
        Decimal("100.00"),
    )
    assert lcr_row.inflow_cap_applied and lcr_row.net_outflows == 0 and lcr_row.ratio is None
    assert list(lcr_row.warnings) == ["LCR_DENOMINATOR_ZERO", "LCR_LEVEL_CAPS_NOT_APPLIED"]
    assert len(priced.out_of_scope) == 1 and not priced.warnings and not priced.blocked
    lcr_pins[LCR_INFLOW_CAP] = PinnedRule(uuid.uuid4(), {"value": 1})  # a JSON number
    refused_pins = lcr_hqla.MODULE.run(lcr_inputs, lcr_pins)
    assert refused_pins.blocked and [w.code for w in refused_pins.warnings] == [
        "PARAMETERS_INVALID"
    ]
    # One role per instrument type: HQLA and an inflow, or a run-off liability and an inflow,
    # would count one balance or cash flow twice (PARAMETERS_INVALID, BLOCKED).
    lcr_pins[LCR_INFLOW_CAP] = PinnedRule(uuid.uuid4(), {"value": "1"})
    for overlap in ({"LOAN": "0.5000", "BOND": "0.5000"}, {"LOAN": "0.5000", "DEPOSIT": "1"}):
        lcr_pins[LCR_INFLOW_RATE] = PinnedRule(uuid.uuid4(), {"map": overlap})
        refused_pins = lcr_hqla.MODULE.run(lcr_inputs, lcr_pins)
        assert refused_pins.blocked and [w.code for w in refused_pins.warnings] == [
            "PARAMETERS_INVALID"
        ], overlap
    # D-33: zero contributions do not count toward the minimum aggregation. One real deposit
    # and two dormant zero-balance ones: the outflows rest on one balance, so they are masked.
    lcr_pins[LCR_INFLOW_RATE] = PinnedRule(uuid.uuid4(), LCR_SEEDED_RULES[LCR_INFLOW_RATE][3])
    dormant = ModuleInputs(
        reporting,
        instruments=tuple(held.values()),
        positions=tuple(
            PositionInput(uuid.uuid4(), uuid.uuid4(), f"P-D{i}", "D", Decimal(b), "EUR")
            for i, b in enumerate(("1000.00", "0.00", "0"))
        ),
    )
    with localcontext(exact_context()):
        [dormant_row] = lcr_hqla.MODULE.run(dormant, lcr_pins).lcr
    assert (dormant_row.outflow_count, dormant_row.outflow_nonzero_count) == (3, 1)
    dormant_classes = liquidity_classes(
        LcrResult(
            **{
                f.name: getattr(dormant_row, f.name)
                for f in dataclasses.fields(dormant_row)
                if f.name not in ("warnings", "rule_version_ids")
            },
            classification="internal",
            contribution_classification="confidential",
        ),
        3,
    )
    assert dormant_classes["outflows"] == dormant_classes["outflow_balance"] == "confidential"

    # --- 3. Snapshots (BE-15.3) ----------------------------------------------------------------
    instrument_source = csv_source(
        api, db, steward, "calc_instruments", "instrument", INSTRUMENT_COLUMNS
    )
    cash_source = csv_source(
        api, db, steward, "calc_cash_flows", "cashflow_event", CASHFLOW_COLUMNS
    )
    mapped: set[str] = set()

    def load(source: dict[str, Any], data: bytes, **form: Any) -> dict[str, Any]:
        """Upload, map (once per source: the mapping is made ACTIVE against this import) and
        validate: COMPLETED."""
        r = upload(api, steward, source["id"], data, filename="fixture.csv", **form)
        assert r.status_code == 201, r.text
        imported = r.json()
        if source["id"] not in mapped:
            schema, columns = (
                ("position", POSITION_COLUMNS)
                if source["code"] == "calc_positions"
                else ("cashflow_event", CASHFLOW_COLUMNS)
            )
            rules = (
                INSTRUMENT_RULES
                if source is instrument_source
                else sample_rules(api, db, steward, schema, columns)
            )
            body = {
                "code": f"{source['code']}_map",
                "name": f"{source['code']} (test)",
                "source_contract_id": source["current_contract"]["id"],
                "rules": rules,
            }
            r = api.post(MAPPINGS, json=body, headers=steward)
            assert r.status_code == 201, r.text
            activate_version(api, steward, approver, r.json()["versions"][0], imported["id"])
            mapped.add(source["id"])
        r = validate_import(api, steward, imported["id"])
        assert r.status_code == 200 and r.json()["status"] == "COMPLETED", r.text
        return dict(r.json())

    def refused(import_ids: list[str], code: str, status: int = 409) -> None:
        c = cid()
        r = make_snapshot(api, analyst, import_ids, **{"X-Correlation-ID": c})
        assert (r.status_code, r.json()["code"]) == (status, code), r.text
        denied(db, c, "dataset_snapshot.create", code)

    case = GOLDEN[0]
    instruments = load(instrument_source, instrument_csv(case["instruments"]))
    cash = load(cash_source, cashflow_csv(case["cash_flows"], case["reporting_date"]))
    refused([str(uuid.uuid4())], "not_found", 404)
    refused([instruments["id"], cash["id"]], "not_projected")

    def imports_listed(**params: str) -> set[str]:
        """GET /imports with the new-run panel's filters (projected, schema_code)."""
        page = api.get(IMPORTS, params={**params, "limit": "200"}, headers=analyst).json()
        return {i["id"] for i in page["items"]}

    assert (imports_listed(projected="false"), imports_listed(projected="true")) == (
        {instruments["id"], cash["id"]},
        set(),
    )
    for imported in (instruments, cash):
        assert project(api, steward, imported["id"]).status_code == 201
    assert (
        imports_listed(projected="true", schema_code="instrument"),
        imports_listed(projected="true", schema_code="cashflow_event"),
        imports_listed(projected="false"),
        imports_listed(schema_code="position"),
    ) == ({instruments["id"]}, {cash["id"]}, set(), set())
    # One business key, two versions in one import (the key is instrument_id + currency).
    twice = [("AMB-1", "LOAN", "EUR", "1.00"), ("AMB-1", "BOND", "USD", "2.00")]
    ambiguous = load(instrument_source, instrument_csv(twice))
    assert project(api, steward, ambiguous["id"]).status_code == 201
    refused([ambiguous["id"], cash["id"]], "ambiguous_business_key")
    refused([instruments["id"], ambiguous["id"]], "duplicate_schema")
    # A declared row count that differs is a D-28 BROKEN reconciliation.
    broken = load(instrument_source, instrument_csv(case["instruments"]), expected_row_count="99")
    assert project(api, steward, broken["id"]).status_code == 201
    refused([broken["id"], cash["id"]], "reconciliation_broken")
    off = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, CALCULATIONS_ENABLED=False
    )
    with TestClient(create_app(off)) as switched:
        c = cid()
        r = make_snapshot(
            switched, analyst, [instruments["id"], cash["id"]], **{"X-Correlation-ID": c}
        )
        assert (r.status_code, r.json()["code"]) == (409, "calculations_disabled"), r.text
        denied(db, c, "dataset_snapshot.create", "calculations_disabled")
    assert calc_counts(db) == dict.fromkeys(CALC_TABLES, 0)

    c = cid()
    r = make_snapshot(api, analyst, [instruments["id"], cash["id"]], **{"X-Correlation-ID": c})
    assert r.status_code == 201, r.text
    snapshot = no_float(r.text)
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
        ("dataset_snapshot.create", "SUCCESS")
    ]
    before = calc_counts(db)
    c = cid()
    again = make_snapshot(api, analyst, [cash["id"], instruments["id"]], **{"X-Correlation-ID": c})
    assert again.status_code == 200 and again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == snapshot and calc_counts(db) == before and events(db, c) == []
    manifests = {
        str(r["import_job_id"]): r
        for r in sql(
            db,
            "SELECT * FROM import_manifest WHERE import_job_id = ANY($1::uuid[])",
            [uuid.UUID(instruments["id"]), uuid.UUID(cash["id"])],
        )
    }
    members = [
        {"schema_code": code, "import_id": i, "manifest_sha256": manifests[i]["manifest_sha256"]}
        for code, i in (("instrument", instruments["id"]), ("cashflow_event", cash["id"]))
    ]
    text = json.dumps(
        sorted(members, key=lambda m: m["schema_code"]), sort_keys=True, separators=(",", ":")
    )
    assert snapshot["content_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert [
        (m["schema_code"], m["import_id"], m["manifest_sha256"]) for m in snapshot["members"]
    ] == [
        (m["schema_code"], m["import_id"], m["manifest_sha256"])
        for m in sorted(members, key=lambda m: m["schema_code"])
    ]
    assert all(m["reconciliation_outcome"] == "MATCHED" for m in snapshot["members"])
    assert api.get(f"{SNAPSHOTS}/{snapshot['snapshot_id']}", headers=approver).json() == snapshot

    # --- 4. Runs (BE-15.4) ---------------------------------------------------------------------
    module_id = module_version_id(api, analyst)
    body = {
        "dataset_snapshot_id": snapshot["snapshot_id"],
        "module_version_id": module_id,
        "reporting_date": case["reporting_date"].isoformat(),
    }
    golden_runs = []
    for golden in GOLDEN:  # the golden loop, over the BE-18.2 pack files
        assert golden["status"].startswith("ENGINEERING")
        c = cid()
        r = start_run(
            api, analyst, body, key=f"golden-{GOLDEN.index(golden)}", **{"X-Correlation-ID": c}
        )
        assert r.status_code == 201, r.text
        run = no_float(r.text)
        assert run["status"] == golden["run_status"], run
        assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
            ("calculation_run.create", "SUCCESS"),
            ("calculation_run.finish", "SUCCESS"),
        ]
        keys = {
            str(row["id"]): row["business_key"]
            for row in sql(db, "SELECT id, business_key FROM cashflow_event")
        }
        assert {keys[w["entity_id"]]: w["code"] for w in run["warnings"]} == golden["excluded"]
        assert all(w["severity"] == "WARNING" for w in run["warnings"])
        outcome = run["outcome"]
        assert (outcome["in_scope"], outcome["bucketed"], outcome["excluded"]) == (
            len(golden["cash_flows"]),
            len(golden["cash_flows"]) - len(golden["excluded"]),
            len(golden["excluded"]),
        )
        assert all(p["passed"] for p in outcome["post_checks"]) and len(outcome["post_checks"]) == 4
        ladder = no_float(api.get(f"{RUNS}/{run['run_id']}/maturity-ladder", headers=approver).text)
        assert [b["code"] for b in ladder["buckets"]] == [b["code"] for b in SEEDED_BUCKETS]
        got = {
            cur["currency"]: {
                row["bucket_code"]: (
                    tuple(Decimal(row[a]) for a in AMOUNTS),
                    row["contribution_count"],
                )
                for row in cur["rows"]
            }
            for cur in ladder["currencies"]
        }
        expected = {
            currency: {
                code: (tuple(Decimal(v) for v in amounts), n) for code, (amounts, n) in rows.items()
            }
            for currency, rows in golden["ladder"].items()
        }
        assert got == expected
        for cur in ladder["currencies"]:  # exact text, all clear for approver (confidential)
            for row in cur["rows"]:
                assert all(DECIMAL_TEXT.match(row[a]) for a in AMOUNTS), row
        assert ladder["statement"] == ILLUSTRATIVE_STATEMENT and ladder["illustrative"]
        check_context(run, run, "run (POST)")  # Phase 30 (BE-30.3): the pins, never computed
        check_context(ladder, run, "maturity-ladder")
        # D-33: risk_analyst (internal) sees exactly the expected figures masked, the rest as
        # the approver does.
        seen = no_float(api.get(f"{RUNS}/{run['run_id']}/maturity-ladder", headers=analyst).text)
        assert seen["min_aggregate_count"] == 3
        for mine, theirs in zip(seen["currencies"], ladder["currencies"], strict=True):
            masked = golden["masked_for_internal"][mine["currency"]]
            for row, clear in zip(mine["rows"], theirs["rows"], strict=True):
                for a in AMOUNTS:
                    expect = MASKED if a in masked[row["bucket_code"]] else clear[a]
                    assert row[a] == expect, (mine["currency"], row["bucket_code"], a)
        golden_runs.append(run)
    run = golden_runs[0]
    run_id = run["run_id"]

    # An Idempotency-Key replay returns the original run and writes nothing.
    before = calc_counts(db)
    c = cid()
    replay = start_run(api, analyst, body, key="golden-0", **{"X-Correlation-ID": c})
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == run and calc_counts(db) == before and events(db, c) == []
    c = cid()
    other_date = {**body, "reporting_date": "2027-01-31"}
    r = start_run(api, analyst, other_date, key="golden-0", **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (409, "idempotency_conflict")
    denied(db, c, "calculation_run.create", "idempotency_conflict")
    # A second run on the same pins: a new run with the same manifest and result hashes.
    second = no_float(start_run(api, analyst, body).text)
    assert second["run_id"] != run_id
    for key in ("manifest_sha256", "result_sha256", "rule_set_id", "assumption_set_id", "status"):
        assert second[key] == run[key], key
    assert count(db, "rule_set") == 1  # content-addressed (D-32 §4)
    # Refused before any run is written, each audited (a bad body is a plain 422).
    before = calc_counts(db)
    for change, status, code in (
        ({"module_version_id": str(uuid.uuid4())}, 409, "module_not_available"),
        *(
            ({"module_version_id": m["module_version_id"]}, 409, "module_not_available")
            for m in planned.values()
        ),
        ({"dataset_snapshot_id": str(uuid.uuid4())}, 404, "not_found"),
    ):
        c = cid()
        r = start_run(api, analyst, {**body, **change}, **{"X-Correlation-ID": c})
        assert (r.status_code, r.json()["code"]) == (status, code), r.text
        denied(db, c, "calculation_run.create", code)
    for bad in (
        {**body, "reporting_date": "31/12/2026"},
        {**body, "unexpected": True},
        {k: v for k, v in body.items() if k != "module_version_id"},
    ):
        r = start_run(api, analyst, bad)
        assert (r.status_code, r.json()["code"]) == (422, "validation_error"), bad
    r = api.post(RUNS, json=body, headers=analyst)  # no Idempotency-Key
    assert r.status_code == 422
    r = start_run(api, approver, body)  # approver lacks calc:run
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    assert calc_counts(db) == before
    # The input contract not met: BLOCKED, with no result, still hashed and ended.
    cash_only = make_snapshot(api, analyst, [cash["id"]]).json()
    blocked_run = no_float(
        start_run(api, analyst, {**body, "dataset_snapshot_id": cash_only["snapshot_id"]}).text
    )
    assert blocked_run["status"] == "BLOCKED" and blocked_run["result_sha256"]
    assert [(w["code"], w["details"]) for w in blocked_run["warnings"]] == [
        ("INPUT_SCHEMA_MISSING", {"schema_code": "instrument"})
    ]
    blocked_ladder = api.get(f"{RUNS}/{blocked_run['run_id']}/maturity-ladder", headers=analyst)
    assert blocked_ladder.json()["currencies"] == []
    # CALCULATIONS_ENABLED=false: 409, audited, nothing written; runs stay readable.
    before = calc_counts(db)
    with TestClient(create_app(off)) as switched:
        c = cid()
        r = start_run(switched, analyst, body, **{"X-Correlation-ID": c})
        assert (r.status_code, r.json()["code"]) == (409, "calculations_disabled"), r.text
        denied(db, c, "calculation_run.create", "calculations_disabled")
        assert switched.get(f"{RUNS}/{run_id}", headers=analyst).json() == run
    assert calc_counts(db) == before
    # A run left RUNNING (its engine gone): a replay of its key after CALC_RUN_STALE_SECONDS
    # ends it FAILED with ENGINE_ERROR and returns it; a fresh RUNNING run is left alone.
    for key, age in (("stale-run", timedelta(hours=1)), ("fresh-run", timedelta(0))):
        sql(
            db,
            "INSERT INTO calculation_run SELECT * FROM (SELECT (jsonb_populate_record("
            "NULL::calculation_run, to_jsonb(r) || jsonb_build_object('id', gen_random_uuid(),"
            " 'status', 'RUNNING', 'result_sha256', NULL, 'ended_at', NULL, 'outcome', NULL,"
            " 'warning_count', 0, 'idempotency_key', $2::text,"
            " 'started_at', clock_timestamp() - $3::interval))).*"
            " FROM calculation_run r WHERE r.id = $1) x",
            uuid.UUID(run_id),
            key,
            age,
        )
    stale = start_run(api, analyst, body, key="stale-run")
    assert stale.status_code == 200 and stale.json()["status"] == "FAILED", stale.text
    assert [(w["code"], w["details"]) for w in stale.json()["warnings"]] == [
        ("ENGINE_ERROR", {"error": "stale_run"})
    ]
    assert start_run(api, analyst, body, key="fresh-run").json()["status"] == "RUNNING"
    # The engine's own failure ends the run FAILED (in a fresh session), never silently and
    # never with the error's text; a cancellation-like exit is re-raised after that.

    class Halt(BaseException):
        pass

    def failing(error: BaseException) -> RegisteredModule:
        def run_module(inputs: ModuleInputs, parameters: Any) -> Any:
            raise error

        return RegisteredModule(spec, exact_context, None, run_module)

    ladder_key = (spec.code, spec.version)
    monkeypatch.setitem(REGISTRY, ladder_key, failing(RuntimeError("SENTINEL-4242.42")))
    failed = start_run(api, analyst, body)
    assert failed.status_code == 201 and failed.json()["status"] == "FAILED", failed.text
    assert [(w["code"], w["details"]) for w in failed.json()["warnings"]] == [
        ("ENGINE_ERROR", {"error": "RuntimeError"})
    ]
    assert "SENTINEL" not in failed.text
    monkeypatch.setitem(REGISTRY, ladder_key, failing(Halt()))
    # Through its own client without a lifespan: a BaseException escaping the app ends the
    # client's per-request event-loop portal, which must not be the shared one.
    throwaway = TestClient(
        create_app(Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir))
    )
    with pytest.raises(Halt):
        start_run(throwaway, analyst, body, key="halted-run")
    [halted] = sql(db, "SELECT * FROM calculation_run WHERE idempotency_key = 'halted-run'")
    assert halted["status"] == "FAILED" and halted["result_sha256"]
    monkeypatch.setitem(REGISTRY, ladder_key, maturity_ladder.MODULE)
    audited = sql(db, "SELECT details::text AS d FROM audit_event WHERE action LIKE 'calc%'")
    assert not any("SENTINEL" in a["d"] for a in audited)

    # --- 5. Reads (BE-15.5) --------------------------------------------------------------------
    def listed(**params: str) -> set[str]:
        page = api.get(RUNS, params={**params, "limit": "200"}, headers=analyst).json()
        return {r["run_id"] for r in page["items"]}

    assert listed(module_code="maturity_ladder", status="SUCCEEDED_WITH_WARNINGS") == {
        run_id,
        second["run_id"],
    }
    assert listed(status="BLOCKED") == {blocked_run["run_id"]}
    assert listed(module_code="lcr_hqla") == set()
    # Phase 33 (BE-33.1 - BE-33.3, D-59; executor_kit): Prefer: respond-async, the pool's
    # equal hashes, a free event loop, workers_busy, a worker crash, the timeout, the retry
    # and the sweeper. After the list filters above, which count this part's runs.
    check_background_execution(
        api, db, artifact_dir, monkeypatch, analyst=analyst, body=body, reference=run
    )
    detail = no_float(api.get(f"{RUNS}/{run_id}", headers=dev_token(api, "auditor")).text)
    manifest = detail["manifest"]
    check_context(detail, detail, "run detail")  # Phase 30 (BE-30.2, BE-30.3)
    run_page = no_float(api.get(RUNS, params={"limit": "200"}, headers=analyst).text)
    assert run_page["items"] and {i["validation_status"] for i in run_page["items"]} == {
        "ILLUSTRATIVE"
    }
    assert detail["statement"] == ILLUSTRATIVE_STATEMENT and manifest["labels"]["illustrative"]
    [stored] = sql(db, "SELECT manifest FROM calculation_run WHERE id = $1", uuid.UUID(run_id))
    assert canonical_sha256(json.loads(stored["manifest"])) == detail["manifest_sha256"]
    assert canonical_sha256(manifest) == detail["manifest_sha256"]
    check_stored_hashes(db, run_id, detail)  # Phase 30 (BE-30.2): both hashes, recomputed
    assert (manifest["engine_version"], manifest["reporting_date"]) == (
        ENGINE_VERSION,
        case["reporting_date"].isoformat(),
    )
    assert manifest["module"]["module_version_id"] == module_id
    assert {m["rule_code"]: m["rule_version_id"] for m in manifest["rule_set"]["members"]} == (
        seeded_versions
    )
    rule_set_sha = canonical_sha256(sorted(seeded_versions.values()))
    assert manifest["rule_set"]["content_sha256"] == rule_set_sha
    assert manifest["rule_set"]["rule_set_id"] == detail["rule_set_id"]
    assert manifest["assumption_set"]["assumption_set_id"] == str(baseline["id"])
    pinned_members = manifest["dataset_snapshot"]["members"]
    assert manifest["dataset_snapshot"]["dataset_snapshot_id"] == snapshot["snapshot_id"]
    for pinned, member in zip(pinned_members, snapshot["members"], strict=True):
        row = manifests[member["import_id"]]
        assert pinned["import_id"] == member["import_id"]
        assert pinned["manifest_sha256"] == row["manifest_sha256"]
        assert pinned["mapping_version_id"] == str(row["mapping_version_id"])
        assert pinned["schema_version_id"] == str(row["schema_version_id"])
        assert (pinned["schema_version"], pinned["projector_version"]) == (1, 1)
        assert pinned["accepted_count"] == row["accepted_count"]
    refs = {(r["ref_type"], r["ref_id"]) for r in detail["input_refs"]}
    expected_refs = {
        ("DATASET_SNAPSHOT", snapshot["snapshot_id"]),
        ("MODULE_VERSION", module_id),
        ("RULE_SET", detail["rule_set_id"]),
        ("ASSUMPTION_SET", str(baseline["id"])),
        ("ENGINE", None),
        *(("RULE_VERSION", v) for v in seeded_versions.values()),
        *(("IMPORT_MANIFEST", str(manifests[m["import_id"]]["id"])) for m in snapshot["members"]),
        *(("DOMAIN_PROJECTION", m["projection_id"]) for m in snapshot["members"]),
        *(("SCHEMA_VERSION", m["schema_version_id"]) for m in snapshot["members"]),
        *(("MAPPING_VERSION", m["mapping_version_id"]) for m in snapshot["members"]),
    }
    assert refs == expected_refs

    results = no_float(
        api.get(f"{RUNS}/{run_id}/results", params={"limit": 1000}, headers=steward).text
    )
    bucketed = len(case["cash_flows"]) - len(case["excluded"])
    assert results["total"] == len(results["items"]) == bucketed
    check_context(results, detail, "results")  # Phase 30 (BE-30.2, BE-30.3)
    ladder = api.get(f"{RUNS}/{run_id}/maturity-ladder", headers=steward).json()
    for cur in ladder["currencies"]:
        for row in cur["rows"]:
            mine = [
                i
                for i in results["items"]
                if (i["currency"], i["bucket_code"]) == (cur["currency"], row["bucket_code"])
            ]
            for direction, total in (("INFLOW", "inflows"), ("OUTFLOW", "outflows")):
                values = [Decimal(i["value"]) for i in mine if i["direction"] == direction]
                assert sum(values, Decimal(0)) == Decimal(row[total]), (cur["currency"], row)
            assert len(mine) == row["contribution_count"]
            assert all(i["liquidity_gap_result_id"] == row["liquidity_gap_result_id"] for i in mine)
    # The drill-down filter returns exactly one bucket's contributions.
    b02 = api.get(
        f"{RUNS}/{run_id}/results",
        params={"currency": "EUR", "bucket_code": "B02"},
        headers=steward,
    ).json()
    assert sorted(i["lineage"]["cashflow_business_key"] for i in b02["items"]) == ["CF-02", "CF-12"]
    assert sum(Decimal(i["value"]) for i in b02["items"]) == Decimal("50.49")
    member_imports = {m["import_id"] for m in snapshot["members"]}
    for item in results["items"]:
        lineage = item["lineage"]
        [chain] = sql(
            db,
            "SELECT b.import_job_id, s.id, s.ordinal, s.raw_values::text AS raw"
            " FROM canonical_record c JOIN record_lineage l ON l.canonical_record_id = c.id"
            " JOIN source_record s ON s.id = l.source_record_id"
            " JOIN import_batch b ON b.id = s.batch_id WHERE c.id = $1",
            uuid.UUID(lineage["canonical_record_id"]),
        )
        assert str(chain["import_job_id"]) == lineage["import_id"] == cash["id"]
        assert lineage["import_id"] in member_imports
        assert (str(chain["id"]), chain["ordinal"]) == (
            lineage["source_record_id"],
            lineage["source_ordinal"],
        )
        raw = json.loads(chain["raw"])
        assert raw["cashflow_id"] == lineage["cashflow_business_key"]
        assert raw["amount"] == item["value"]  # exact text, never rounded
        assert item["classification"] == "confidential" and item["unit"] == item["currency"]
        assert item["rule_version_ids"] == sorted(seeded_versions.values())
    # D-33: contributions are masked for risk_analyst; aggregates are clear.
    hidden = no_float(
        api.get(f"{RUNS}/{run_id}/results", params={"limit": 1000}, headers=analyst).text
    )
    assert hidden["total"] == bucketed and all(i["value"] == MASKED for i in hidden["items"])
    assert all(isinstance(i["lineage"]["cashflow_business_key"], str) for i in hidden["items"])
    # D-33 minimum aggregation: a reporting date one day before a due date isolates that
    # cash flow (CF-09, USD 1000.00) in B01. Its figures are masked for risk_analyst and clear
    # for approver; the figures with no contribution stay clear.
    isolating = case["reporting_date"] + timedelta(days=730)
    alone = start_run(api, analyst, {**body, "reporting_date": isolating.isoformat()}).json()

    def first_row(headers: dict[str, str]) -> dict[str, Any]:
        page = api.get(f"{RUNS}/{alone['run_id']}/maturity-ladder", headers=headers).json()
        [usd] = page["currencies"]
        assert usd["currency"] == "USD"
        return dict(usd["rows"][0])

    hidden_row, clear_row = first_row(analyst), first_row(approver)
    assert hidden_row["contribution_count"] == clear_row["contribution_count"] == 1
    for figure in ("inflows", "inflows_contractual", "net_gap", "cumulative_gap"):
        assert clear_row[figure] == "1000.00" and hidden_row[figure] == MASKED, figure
    for figure in ("outflows", "inflows_behavioural", "outflows_contractual"):
        assert hidden_row[figure] == clear_row[figure] == "0", figure
    # With the rule off (CALC_MIN_AGGREGATE_COUNT=1) aggregates are clear for risk_analyst.
    unthresholded = Settings(
        ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, CALC_MIN_AGGREGATE_COUNT=1
    )
    with TestClient(create_app(unthresholded)) as plain:
        seen = plain.get(f"{RUNS}/{run_id}/maturity-ladder", headers=analyst).json()
        assert seen["currencies"] == ladder["currencies"] and seen["min_aggregate_count"] == 1
    # D-33, Phase 15 open item: a cumulative gap of exactly zero after a masked net gap stays
    # masked (masked wins over "zeros stay clear": a clear zero would reveal that the masked
    # amounts cancel). Only a figure with no contribution at all stays clear.
    rows = [
        SimpleNamespace(
            id=uuid.uuid4(), currency="EUR", bucket_sequence=i, classification="internal"
        )
        for i in (1, 2, 3)
    ]
    parts = [
        (rows[0].id, "INFLOW", "CONTRACTUAL", "confidential", 1),  # +100.00
        (rows[1].id, "OUTFLOW", "CONTRACTUAL", "confidential", 1),  # -100.00: cumulative 0
    ]
    classes = ladder_classes(rows, parts, 3)
    assert [classes[r.id]["cumulative_gap"] for r in rows] == ["confidential"] * 3
    assert classes[rows[2].id]["net_gap"] == classes[rows[2].id]["inflows"] == "internal"

    # --- 5c. Phase 25 (BE-25.3, BE-25.4): stressed runs and the comparison, on the seeded
    # buckets (before Phase 16 below changes them) ------------------------------------------------
    stressed_ids = check_scenario_runs(
        api,
        db,
        artifact_dir,
        monkeypatch,
        load=load,
        instrument_source=instrument_source,
        cash_source=cash_source,
        instrument_csv=instrument_csv,
        cashflow_csv=cashflow_csv,
        module_id=module_id,
        calc_counts=calc_counts,
        feature03_run_id=run_id,
    )
    # Phase 30 (BE-30.3): the comparison's context is its baseline run's pins.
    r = api.get(
        f"/api/v1/scenarios/{stressed_ids['scenario_id']}/comparison",
        params={"scenario_run_id": stressed_ids["scenario_run_id"]},
        headers=steward,
    )
    assert r.status_code == 200, r.text
    comparison = no_float(r.text)
    compared = no_float(api.get(f"{RUNS}/{comparison['baseline_run_id']}", headers=steward).text)
    check_context(comparison, compared, "comparison")
    # Phase 30 (BE-30.4): the ladder boundary cases (validation_kit), on the seeded buckets.
    check_ladder_boundaries(
        api,
        db,
        load=load,
        instrument_source=instrument_source,
        cash_source=cash_source,
        instrument_csv=instrument_csv,
        cashflow_csv=cashflow_csv,
        module_id=module_id,
    )

    # Phase 33 (BE-33.5, D-59 §6; pagination_kit): one loop over the capped-list inventory,
    # each list read by a role that holds its permission; a cursor is tenant-bound.
    paged = check_cursor_pages(
        api,
        {
            "*": steward,
            "listAuditEvents": dev_token(api, "auditor"),
            "listUsers": dev_token(api, "platform_admin"),
        },
        {
            "run_id": run_id,
            "scenario_id": stressed_ids["scenario_id"],
            "scenario_run_id": stressed_ids["scenario_run_id"],
        },
        {"listCalculationRuns": {"module_code": "lcr_hqla"}},
        # Each audit read appends an audit.read event: page a set the reads do not grow.
        fixed={"listAuditEvents": {"action": "scenario.run"}},
    )
    assert {"listCalculationRuns", "getCalculationRunResults", "listImports"} <= paged, paged
    assert "listAuditEvents" in paged, paged
    outsider = token_for(
        db, "dev:p33-outsider", ("data_steward",), tenant_id=insert_tenant(db, "p33-outsider")
    )
    check_foreign_cursor(api, steward, outsider, RUNS)

    # --- Phase 16 (BE-16.3): pinning across a rule change, and re-runs --------------------------
    def run_rows(run: str) -> str:
        """A hash of a run's row and every output row (to prove they never change)."""
        found = [sql(db, "SELECT * FROM calculation_run WHERE id = $1", uuid.UUID(run))]
        found += [
            sql(
                db,
                f"SELECT * FROM {t} WHERE calculation_run_id = $1 ORDER BY sequence",
                uuid.UUID(run),
            )
            for t in OUTPUT_TABLES
        ]
        return hashlib.sha256(repr(found).encode()).hexdigest()

    old_rows = run_rows(run_id)
    bucket_rule = f"{RULES}/{rule_id(db, MATURITY_BUCKETS)}"
    new_buckets = [
        {"code": "B01", "lower_exclusive": -1, "upper_inclusive": 7},  # day 0 included
        {"code": "B02", "lower_exclusive": 7, "upper_inclusive": None},
    ]
    r = api.post(
        f"{bucket_rule}/versions",
        json={"parameters_patch": {"buckets": new_buckets}, "change_summary": "Two buckets."},
        headers=analyst,
    )
    assert r.status_code == 201 and r.json()["status"] == "DRAFT", r.text
    proposed = r.json()
    assert proposed["parameters"]["buckets"][1]["label"] == "(7,inf)"  # derived from the bounds
    number = proposed["version"]
    r = api.post(f"{bucket_rule}/versions/{number}/submit", json={}, headers=analyst)
    assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW", r.text
    # D-32 §5: materialised in the submit transaction, while the version was still DRAFT.
    assert [
        (b["code"], b["lower_exclusive_days"], b["upper_inclusive_days"])
        for b in sql(
            db,
            "SELECT * FROM maturity_bucket_definition WHERE rule_version_id = $1 ORDER BY sequence",
            uuid.UUID(proposed["rule_version_id"]),
        )
    ] == [("B01", -1, 7), ("B02", 7, None)]
    r = api.post(
        f"{bucket_rule}/versions/{number}/approval",
        json={"decision": "APPROVE", "comment": "Engineering check."},
        headers=approver,
    )
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
    changed = no_float(start_run(api, analyst, body).text)
    assert changed["status"] == "SUCCEEDED_WITH_WARNINGS", changed
    assert changed["rule_set_id"] != run["rule_set_id"]
    assert changed["result_sha256"] != run["result_sha256"]
    pinned_buckets = [
        m["rule_version_id"]
        for m in changed["manifest"]["rule_set"]["members"]
        if m["rule_code"] == MATURITY_BUCKETS
    ]
    assert pinned_buckets == [proposed["rule_version_id"]]
    # A re-run of the old run pins its original (now SUPERSEDED) versions and reproduces it.
    c = cid()
    rerun = start_run(api, analyst, {"rerun_of": run_id}, **{"X-Correlation-ID": c})
    assert rerun.status_code == 201, rerun.text
    again = no_float(rerun.text)
    assert again["run_id"] not in (run_id, changed["run_id"])
    assert again["rerun_of_run_id"] == run_id
    assert {"check": "RERUN_REPRODUCED", "passed": True} in again["outcome"]["post_checks"]
    for key in ("manifest_sha256", "result_sha256", "rule_set_id", "assumption_set_id", "status"):
        assert again[key] == run[key], key
    # Phase 30 (BE-30.2): the re-run of a run made before the response wrapper changed
    # reproduces both hashes (above) and the same context; the hashed documents keep the
    # Feature 03 key sets (check_context checks the manifest's).
    assert again["context"] == run["context"] and again["validation_status"] == "ILLUSTRATIVE"
    check_context(again, again, "re-run")
    assert set(result_document(CalculationRunStatus.SUCCEEDED, [], [], [])) == {
        "status",
        "liquidity_gap",
        "contributions",
        "warnings",
    }
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
        ("calculation_run.create", "SUCCESS"),
        ("calculation_run.finish", "SUCCESS"),
    ]
    assert run_rows(run_id) == old_rows  # the old run's rows never change
    # A re-run that does not reproduce its original's result_sha256 ends FAILED with no
    # result (D-31 §5): the finish helper, with a tampered expected hash.
    ok = CalculationRunStatus.SUCCEEDED
    same = canonical_sha256(result_document(ok, [], [], []))
    assert reproduce(same, ok, (), (), [], {})[0] == ok
    verdict, gap, kept, found, checks, sha = reproduce("0" * 64, ok, (), (), [], {})
    assert (verdict, gap, kept, checks) == (
        CalculationRunStatus.FAILED,
        [],
        [],
        {PostCheck.RERUN_REPRODUCED: False},
    )
    assert [(w.code, dict(w.details)) for w in found] == [
        (CalculationWarningCode.POST_CHECK_FAILED, {"check": "RERUN_REPRODUCED"})
    ]
    assert sha == canonical_sha256(result_document(verdict, [], [], found)) != same
    c = cid()
    r = start_run(api, analyst, {"rerun_of": str(uuid.uuid4())}, **{"X-Correlation-ID": c})
    assert (r.status_code, r.json()["code"]) == (404, "not_found")
    denied(db, c, "calculation_run.create", "not_found")
    r = start_run(api, analyst, {"rerun_of": run_id, "reporting_date": "2026-12-31"})
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")
    # Carry-over (JSONB decimals): a JSON fraction never reaches a rule parameter. It is
    # refused on the way in (decimals are exact text) and none is stored.
    for patch in ({"buckets": [{"code": "B01", "lower_exclusive": 0.5, "upper_inclusive": None}]},):
        r = api.post(
            f"{bucket_rule}/versions",
            json={"parameters_patch": patch, "change_summary": "x"},
            headers=analyst,
        )
        assert (r.status_code, r.json()["code"]) == (422, "invalid_parameters"), r.text
        assert r.json()["details"]["problems"] == [
            {"path": "buckets[0].lower_exclusive", "code": "inexact_number"}
        ]
    decimal_schema = {"kind": "DECIMAL", "min": "0", "max": "1", "scale": 4}
    assert check_parameters(decimal_schema, {"value": 0.1}, frozenset())[1]
    assert check_parameters(decimal_schema, {"value": "0.1"}, frozenset()) == (
        {"value": "0.1000"},
        [],
    )
    assert not exact_json({"map": {"LOAN": 0.5}}) and exact_json({"value": "0.5"})
    for stored in sql(db, "SELECT parameters::text AS p FROM rule_version"):
        assert exact_json(no_float(stored["p"]))

    # --- 5b. LCR/HQLA prototype (BE-17.1 – BE-17.3) -------------------------------------------
    # The golden case (hand-computed, D-35 §4) over its own snapshot of three projected
    # imports: an ineligible asset, the inflow cap binding, a zero denominator, mixed
    # currencies, the horizon edge and a ratio tie that only ROUND_HALF_EVEN rounds down.
    [lcr] = LCR_GOLDEN_CASES  # the lcr_hqla v1 pack file (BE-18.2)
    assert lcr["status"].startswith("ENGINEERING")
    lcr_date = lcr["reporting_date"]
    position_source = csv_source(api, db, steward, "calc_positions", "position", POSITION_COLUMNS)
    lcr_imports = [
        load(instrument_source, instrument_csv(lcr["instruments"])),
        load(position_source, position_csv(lcr["positions"], lcr_date)),
        load(cash_source, cashflow_csv(lcr["cash_flows"], lcr_date)),
    ]
    for imported in lcr_imports:
        assert project(api, steward, imported["id"]).status_code == 201
    r = make_snapshot(api, analyst, [i["id"] for i in lcr_imports])
    assert r.status_code == 201, r.text
    lcr_body = {
        "dataset_snapshot_id": r.json()["snapshot_id"],
        "module_version_id": module_version_id(api, analyst, LCR_HQLA),
        "reporting_date": lcr_date.isoformat(),
    }
    c = cid()
    r = start_run(api, analyst, lcr_body, **{"X-Correlation-ID": c})
    assert r.status_code == 201, r.text
    lcr_run = no_float(r.text)
    lcr_id = lcr_run["run_id"]
    assert lcr_run["status"] == "SUCCEEDED_WITH_WARNINGS", lcr_run  # USD: LCR_DENOMINATOR_ZERO
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
        ("calculation_run.create", "SUCCESS"),
        ("calculation_run.finish", "SUCCESS"),
    ]
    assert lcr_run["warnings"] == []  # nothing excluded; the LCR warnings are per currency
    outcome = lcr_run["outcome"]
    assert {k: outcome[k] for k in lcr["outcome"]} == lcr["outcome"], outcome
    assert outcome["post_checks"] and all(p["passed"] for p in outcome["post_checks"])
    lcr_detail = no_float(api.get(f"{RUNS}/{lcr_id}", headers=analyst).text)
    lcr_members = lcr_detail["manifest"]["rule_set"]["members"]
    assert {m["rule_code"]: m["rule_version_id"] for m in lcr_members} == lcr_versions
    liquidity = no_float(api.get(f"{RUNS}/{lcr_id}/liquidity", headers=approver).text)
    check_context(liquidity, lcr_detail, "liquidity")  # Phase 30 (BE-30.3)
    assert (liquidity["run_id"], liquidity["status"]) == (lcr_id, lcr_run["status"])
    assert liquidity["prototype"] and liquidity["illustrative"] and liquidity["limitations"]
    assert liquidity["statement"] == ILLUSTRATIVE_STATEMENT
    assert liquidity["inflow_horizon_days"] == 30
    assert [cur["currency"] for cur in liquidity["currencies"]] == sorted(lcr["lcr"])
    position_import = lcr_imports[1]["id"]
    for cur in liquidity["currencies"]:
        want = lcr["lcr"][cur["currency"]]
        for figure in LCR_FIGURES:
            assert DECIMAL_TEXT.match(cur[figure]), (cur["currency"], figure)
            assert Decimal(cur[figure]) == Decimal(want[figure]), (cur["currency"], figure)
        assert cur["ratio"] == want["ratio"], cur  # exact text, 4 dp ROUND_HALF_EVEN, or None
        assert cur["inflow_cap_applied"] is want["inflow_cap_applied"]
        assert cur["inflow_cap_rate"] == want["inflow_cap_rate"]
        assert {k: cur[k] for k in want["counts"]} == want["counts"]
        assert cur["warnings"] == want["warnings"] and cur["classification"] == "internal"
        assert [lv["level"] for lv in cur["hqla_levels"]] == ["L1", "L2A", "L2B"]
        # BE-17.3: the HQLA total, and each level's, is the sum of its included asset rows.
        included = [a for a in cur["assets"] if a["included"]]
        hqla_sum = sum((Decimal(a["hqla_value"]) for a in included), Decimal(0))
        assert hqla_sum == Decimal(cur["hqla_total"]), cur["currency"]
        for lv in cur["hqla_levels"]:
            mine = [Decimal(a["hqla_value"]) for a in included if a["level"] == lv["level"]]
            assert (
                sum(mine, Decimal(0))
                == Decimal(lv["total"])
                == Decimal(want["levels"][lv["level"]])
            )
            assert lv["asset_count"] == len(mine)
        assert {a["lineage"]["position_business_key"] for a in included} == (
            lcr["included_assets"][cur["currency"]]
        )
        for a in cur["assets"]:
            assert a["lineage"]["import_id"] == position_import, a
            assert a["classification"] == "confidential", a
            if a["included"]:
                assert (a["level"], a["haircut"], a["exclusion_reason"]) == ("L1", "0.0000", None)
                assert Decimal(a["hqla_value"]) == Decimal(a["balance"])
            else:
                assert (a["level"], a["haircut"], Decimal(a["hqla_value"])) == (
                    None,
                    None,
                    Decimal(0),
                )
    excluded_assets = {
        a["lineage"]["position_business_key"]: a["exclusion_reason"]
        for cur in liquidity["currencies"]
        for a in cur["assets"]
        if not a["included"]
    }
    assert excluded_assets == dict.fromkeys(lcr["excluded_assets"], "NOT_HQLA_ELIGIBLE")
    # D-33: risk_analyst (internal) sees asset amounts masked, and an aggregate masked when it
    # rests on fewer than 3 contributions (with what derives from it); zeros and NULL clear.
    seen = no_float(api.get(f"{RUNS}/{lcr_id}/liquidity", headers=analyst).text)
    assert seen["min_aggregate_count"] == 3
    for mine, theirs in zip(seen["currencies"], liquidity["currencies"], strict=True):
        masked = lcr["masked_for_internal"][mine["currency"]]
        for figure in (*LCR_FIGURES, "ratio", "inflow_cap_applied"):
            expect = MASKED if figure in masked else theirs[figure]
            assert mine[figure] == expect, (mine["currency"], figure)
        for lv, clear in zip(mine["hqla_levels"], theirs["hqla_levels"], strict=True):
            assert lv["total"] == (MASKED if lv["level"] in masked else clear["total"]), lv
        for row, clear in zip(mine["assets"], theirs["assets"], strict=True):
            assert row["balance"] == MASKED, row  # an excluded row's 0 is by rule: clear
            assert row["hqla_value"] == (MASKED if row["included"] else clear["hqla_value"]), row
            for key in ("included", "level", "haircut", "exclusion_reason", "sequence"):
                assert row[key] == clear[key], key
    # A re-run on the same pins reproduces both hashes.
    lcr_again = no_float(start_run(api, analyst, lcr_body).text)
    assert lcr_again["run_id"] != lcr_id
    for key in ("manifest_sha256", "result_sha256", "rule_set_id", "status"):
        assert lcr_again[key] == lcr_run[key], key
    assert listed(module_code=LCR_HQLA) == {lcr_id, lcr_again["run_id"]}
    check_lcr_baseline(api, db, stressed_ids["scenario_id"], lcr_id, calc_counts)  # BE-25.3
    # Phase 30 (BE-30.4): the LCR boundary cases (validation_kit), on the 0020 seed.
    check_lcr_boundaries(
        api,
        load=load,
        instrument_source=instrument_source,
        position_source=position_source,
        cash_source=cash_source,
        instrument_csv=instrument_csv,
        cashflow_csv=cashflow_csv,
        lcr_module_id=lcr_body["module_version_id"],
        reporting=lcr_date,
    )
    # Each read answers 404 for a run of the other module, and for no run at all.
    for url, refusal in (
        (f"{RUNS}/{run_id}/liquidity", "liquidity_not_found"),
        (f"{RUNS}/{uuid.uuid4()}/liquidity", "not_found"),
        (f"{RUNS}/{lcr_id}/maturity-ladder", "maturity_ladder_not_found"),
    ):
        r = api.get(url, headers=approver)
        assert (r.status_code, r.json()["code"]) == (404, refusal), (url, r.text)
    # Phase 16 x 17: every seeded rule is in the validator's stored shape (its kind's exact
    # schema keys; decimals padded to the scale), so re-sending the seeded cap as "0.75" is
    # no change; and an LCR rule goes through propose, submit and approve, after which a
    # run pins the new version and still reproduces the golden figures.
    for code, (_, _, schema, params, _) in ALL_RULES.items():
        assert check_schema(schema) == [], code
        assert check_parameters(schema, params, frozenset({"BOND", "DEPOSIT", "LOAN"})) == (
            params,
            [],
        ), code
    cap_rule = f"{RULES}/{rule_id(db, LCR_INFLOW_CAP)}"
    r = api.post(
        f"{cap_rule}/versions",
        json={"parameters_patch": {"value": "0.75"}, "change_summary": "Same cap."},
        headers=analyst,
    )
    assert (r.status_code, r.json()["code"]) == (422, "no_change"), r.text
    # A haircut for LOAN, which is not HQLA: governed, pinned, and no figure changes.
    haircut_rule = f"{RULES}/{rule_id(db, HQLA_HAIRCUT)}"
    r = api.post(
        f"{haircut_rule}/versions",
        json={"parameters_patch": {"map": {"LOAN": "0.25"}}, "change_summary": "LOAN haircut."},
        headers=analyst,
    )
    assert r.status_code == 201, r.text
    lcr_change = r.json()
    assert lcr_change["parameters"] == {"map": {"BOND": "0.0000", "LOAN": "0.2500"}}
    lcr_rule_version = f"{haircut_rule}/versions/{lcr_change['version']}"
    r = api.post(f"{lcr_rule_version}/submit", json={}, headers=analyst)
    assert r.status_code == 200 and r.json()["status"] == "IN_REVIEW", r.text
    r = api.post(
        f"{lcr_rule_version}/approval",
        json={"decision": "APPROVE", "comment": "LOAN is not HQLA: no figure changes."},
        headers=approver,
    )
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE", r.text
    lcr_changed = no_float(start_run(api, analyst, lcr_body).text)
    assert lcr_changed["status"] == lcr_run["status"]
    assert lcr_changed["rule_set_id"] != lcr_run["rule_set_id"]
    assert lcr_change["rule_version_id"] in {
        m["rule_version_id"] for m in lcr_changed["manifest"]["rule_set"]["members"]
    }
    changed_liquidity = no_float(
        api.get(f"{RUNS}/{lcr_changed['run_id']}/liquidity", headers=approver).text
    )
    for cur in changed_liquidity["currencies"]:
        want = lcr["lcr"][cur["currency"]]
        assert {f: Decimal(cur[f]) for f in LCR_FIGURES} == {
            f: Decimal(want[f]) for f in LCR_FIGURES
        }, cur["currency"]
        assert (cur["ratio"], cur["warnings"]) == (want["ratio"], want["warnings"])

    # --- 5d. Phase 34 (BE-34.4): after these runs and one failed import, the internal metrics
    # listener carries every D-62 §2 series, the failure under its reason code, and no id label.
    failed = upload(api, steward, instrument_source["id"], b'instrument_id,b\n1,"2"x\n')
    assert (failed.status_code, failed.json()["code"]) == (422, "csv_malformed"), failed.text
    check_metrics_after_a_run_and_a_failed_import(api, "csv_malformed")

    # --- 6. Guards (BE-15.1, BE-15.2) ----------------------------------------------------------
    bucket_version = uuid.UUID(seeded_versions[MATURITY_BUCKETS])
    for statement, match in (
        ("UPDATE rule_version SET parameters = '{}'::jsonb WHERE id = $1", "leaves DRAFT"),
        ("UPDATE rule_version SET status = 'DRAFT' WHERE id = $1", "not allowed"),
        ("UPDATE rule_version SET approved_by = 'someone' WHERE id = $1", "out of IN_REVIEW"),
        ("DELETE FROM rule_version WHERE id = $1", "never deleted"),
    ):
        with pytest.raises(IMMUTABLE, match=match):
            sql(db, statement, bucket_version)
    with pytest.raises(IMMUTABLE, match="assumption_set is immutable"):
        sql(db, "UPDATE assumption_set SET assumptions = '{\"x\": 1}'::jsonb")
    for table in ("rule_version", "assumption_set"):
        with pytest.raises(IMMUTABLE, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
    for table in ("calculation_module", "module_version", "rule_definition",
                  "maturity_bucket_definition", *SNAPSHOT_TABLES):  # fmt: skip
        [row] = sql(db, f"SELECT * FROM {table} ORDER BY id LIMIT 1")
        for statement in (
            f"UPDATE {table} SET created_by = 'tampered' WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(IMMUTABLE, match="append-only"):
                sql(db, statement, row["id"])
        with pytest.raises(IMMUTABLE, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
        assert sql(db, f"SELECT * FROM {table} WHERE id = $1", row["id"]) == [row], table

    def copy(table: str, changes: str, where: str = "true") -> str:
        """INSERT a copy of one row of `table` with a new id and these jsonb changes."""
        return (
            f"INSERT INTO {table} SELECT * FROM (SELECT (jsonb_populate_record(NULL::{table},"
            f" to_jsonb(t) || jsonb_build_object('id', gen_random_uuid(), {changes}))).*"
            f" FROM {table} t WHERE {where} LIMIT 1) x RETURNING id"
        )

    # A new version is DRAFT; it is editable only while DRAFT; at most one ACTIVE (DB).
    with pytest.raises(IMMUTABLE, match="is DRAFT"):
        sql(db, copy("rule_version", "'version', 9, 'seeded_by', NULL", "id = $1"), bucket_version)
    draft = sql(
        db,
        copy(
            "rule_version",
            "'version', 9, 'status', 'DRAFT', 'seeded_by', NULL, 'approved_by', NULL,"
            " 'approved_at', NULL, 'decision_comment', NULL",
            "id = $1",
        ),
        bucket_version,
    )[0]["id"]
    # Buckets are materialised only on a DRAFT DAY_BUCKETS version; a ladder row may name
    # only a bucket of its run's pinned bucket version.
    bucket_copy = copy(
        "maturity_bucket_definition",
        "'rule_version_id', $1::uuid, 'seeded_by', NULL",
        "sequence = 1",
    )
    with pytest.raises(IMMUTABLE, match="DRAFT DAY_BUCKETS"):
        sql(db, bucket_copy, bucket_version)  # the seeded ACTIVE version
    other_bucket = sql(db, bucket_copy, draft)[0]["id"]
    with pytest.raises(IMMUTABLE, match="pinned bucket"):
        sql(
            db,
            copy(
                "liquidity_gap_result",
                "'bucket_definition_id', $1::uuid, 'sequence', 9999",
                "calculation_run_id = $2",
            ),
            other_bucket,
            uuid.UUID(run_id),
        )
    sql(db, "UPDATE rule_version SET parameters = '{\"buckets\": []}'::jsonb WHERE id = $1", draft)
    sql(db, "UPDATE rule_version SET status = 'IN_REVIEW' WHERE id = $1", draft)
    with pytest.raises(IMMUTABLE, match="DRAFT DAY_BUCKETS"):
        sql(db, copy("maturity_bucket_definition", "'rule_version_id', $1::uuid, 'sequence', 2,"
                     " 'code', 'B02', 'seeded_by', NULL", "sequence = 1"), draft)  # fmt: skip
    with pytest.raises(IMMUTABLE, match="leaves DRAFT"):
        sql(db, "UPDATE rule_version SET parameters = '{}'::jsonb WHERE id = $1", draft)
    with pytest.raises(asyncpg.UniqueViolationError, match="uq_rule_version_one_active"):
        sql(
            db,
            "UPDATE rule_version SET status = 'ACTIVE', approved_by = 'approver',"
            " approved_at = now() WHERE id = $1",
            draft,
        )
    actives = sql(
        db,
        "SELECT count(*) AS n FROM rule_version WHERE status = 'ACTIVE'"
        " GROUP BY rule_definition_id",
    )
    assert [a["n"] for a in actives] == [1] * len(ALL_RULES)
    # One ACTIVE bucket version: the approved one; the seeded one is SUPERSEDED, never deleted.
    assert (
        sql(db, "SELECT status FROM rule_version WHERE id = $1", bucket_version)[0]["status"]
        == "SUPERSEDED"
    )

    # A finished run refuses any change, and any new output row; nothing is ever deleted.
    run_uuid = uuid.UUID(run_id)
    before_run = sql(db, "SELECT * FROM calculation_run WHERE id = $1", run_uuid)
    for statement in (
        "UPDATE calculation_run SET status = 'FAILED' WHERE id = $1",
        "UPDATE calculation_run SET manifest = '{}'::jsonb WHERE id = $1",
        "UPDATE calculation_run SET result_sha256 = repeat('0', 64) WHERE id = $1",
    ):
        with pytest.raises(IMMUTABLE, match="finished calculation_run is immutable"):
            sql(db, statement, run_uuid)
    with pytest.raises(IMMUTABLE, match="never deleted"):
        sql(db, "DELETE FROM calculation_run WHERE id = $1", run_uuid)
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE calculation_run CASCADE")
    with pytest.raises(IMMUTABLE, match="written RUNNING"):
        sql(db, copy("calculation_run", "'idempotency_key', 'copy'", "id = $1"), run_uuid)
    assert sql(db, "SELECT * FROM calculation_run WHERE id = $1", run_uuid) == before_run
    lcr_uuid = uuid.UUID(lcr_id)
    stressed_uuid = uuid.UUID(stressed_ids["stressed_run_id"])
    for table, owner in (
        *((t, run_uuid) for t in OUTPUT_TABLES),
        *((t, lcr_uuid) for t in LCR_OUTPUT_TABLES),  # BE-17.1: the LCR outputs, alike
        *((t, stressed_uuid) for t in SCENARIO_OUTPUT_TABLES),  # BE-25.1: the stressed outputs
    ):
        with pytest.raises(IMMUTABLE, match="only while their run is RUNNING"):
            sql(db, copy(table, "'sequence', 9999", "calculation_run_id = $1"), owner)
        [row] = sql(db, f"SELECT * FROM {table} WHERE calculation_run_id = $1 LIMIT 1", owner)
        for statement in (
            f"UPDATE {table} SET calculation_run_id = calculation_run_id WHERE id = $1"
            if table in SCENARIO_OUTPUT_TABLES
            else f"UPDATE {table} SET created_by = 'tampered' WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(IMMUTABLE, match="append-only"):
                sql(db, statement, row["id"])
        with pytest.raises(IMMUTABLE, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
        assert sql(db, f"SELECT * FROM {table} WHERE id = $1", row["id"]) == [row], table

    # The downgrade refuses while a run (or a non-seed registry row) exists, in one
    # transaction: nothing changes; 0020 alone refuses while LCR results exist (BE-17.1).
    # Without them, head -> 0017 -> head.
    check_scenario_append_only(db)  # BE-25.1: scenario_run refuses UPDATE, DELETE, TRUNCATE
    head = str(ScriptDirectory.from_config(Config("alembic.ini")).get_current_head())
    # Phase 31: head is 0030 or later (0030's own refusal is tested in the WS1 round trip).
    assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == head
    # BE-25.1: 0027 refuses its downgrade while any of its four tables has a row (nothing
    # changes); without them 0027 -> 0026 -> 0027 round-trips, and intermediate_value (its
    # columns, constraints and triggers) is byte-for-byte the same on both sides (frozen copy).
    m27 = (
        migration("0027_scenario_runs") if (MIGRATIONS / "0027_scenario_runs.py").exists() else None
    )
    assert m27 is None or m27.down_revision == "0026"
    frozen = intermediate_value_shape(db)
    before = calc_counts(db)
    with pytest.raises(Exception, match="this downgrade is refused"):
        command.downgrade(alembic_config(db), "0026")
    assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == head
    assert calc_counts(db) == before
    clear_scenario_runs(db)
    script(db, _IDENTITY_CLEANUP)  # Phase 31: 0030 refuses its downgrade while links exist
    try:
        command.downgrade(alembic_config(db), "0026")
        assert intermediate_value_shape(db) == frozen
        assert sql(db, "SELECT to_regclass('scenario_run') AS t")[0]["t"] is None
    finally:
        command.upgrade(alembic_config(db), "head")
    assert intermediate_value_shape(db) == frozen
    before = calc_counts(db)
    try:
        for target in (m20.down_revision, "0017"):
            with pytest.raises(Exception, match="LCR results.*this downgrade is refused"):
                command.downgrade(alembic_config(db), target)
            assert sql(db, "SELECT version_num FROM alembic_version")[0]["version_num"] == head
            assert calc_counts(db) == before
        clean(db)
        sql(
            db,
            copy(
                "rule_version",
                "'version', 2, 'status', 'DRAFT', 'seeded_by', NULL, 'approved_by', NULL,"
                " 'approved_at', NULL, 'decision_comment', NULL",
                "id = $1",
            ),
            bucket_version,
        )
        with pytest.raises(Exception, match="this downgrade is refused"):
            command.downgrade(alembic_config(db), "0017")
        clean(db)
        command.downgrade(alembic_config(db), "0017")
        for table in (*REGISTRY_TABLES, *CALC_TABLES):
            assert sql(db, f"SELECT to_regclass('{table}') AS t")[0]["t"] is None, table
    finally:
        command.upgrade(alembic_config(db), "head")
    seeded = sql(
        db,
        "SELECT (SELECT count(*) FROM rule_version WHERE status = 'ACTIVE'"
        " AND seeded_by = $1) AS v,"
        " (SELECT count(*) FROM maturity_bucket_definition WHERE seeded_by = $1) AS b,"
        " (SELECT count(*) FROM module_version WHERE seeded_by = $1) AS m,"
        " (SELECT count(*) FROM assumption_set WHERE status = 'ACTIVE' AND seeded_by = $1) AS a",
        RULES_SEED_TAG,
    )[0]
    assert (seeded["v"], seeded["b"], seeded["m"], seeded["a"]) == (3, 8, 1, 1)
    lcr_seeded = sql(
        db,
        "SELECT (SELECT count(*) FROM rule_version WHERE status = 'ACTIVE'"
        " AND seeded_by = $1) AS v,"
        " (SELECT count(*) FROM module_version WHERE seeded_by = $1) AS m",
        LCR_SEED_TAG,
    )[0]
    assert (lcr_seeded["v"], lcr_seeded["m"]) == (len(LCR_SEEDED_RULES), 1)
    assert {str(r["id"]) for r in sql(db, "SELECT id FROM rule_version")} == set(
        all_versions.values()
    )  # the seeds' ids are stable across a round trip
