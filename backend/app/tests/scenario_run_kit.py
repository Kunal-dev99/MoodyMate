"""Phase 25 (BE-25.1 - BE-25.4; D-45, D-46): the deterministic scenario engine and the
baseline comparison, folded into
`test_calculations.py::test_registry_snapshot_run_and_maturity_ladder`
(D-26, the Phase 25 test budget: 49 -> 49).

- `check_overlay_pure` (part 2, BE-25.2): the overlay golden loop over
  `app/calculations/golden/scenario_overlay_v1_engineering_cases.json`, the user's §5 worked
  example spelt out by hand, part ids stable across two runs, the no-float grep and the
  no-`app.ai` grep over `app/calculations/` and `app/orchestration/`.
- `check_scenario_runs` (part 5c, BE-25.3 and BE-25.4): runs through the API against a baseline
  `maturity_ladder` run of an INR fixture (the §5 flows plus a bucket that breaks only when
  the shocks are combined), the comparison and the drill-down. Returns the ids part 6 needs.
- `check_scenario_guards` (part 6, BE-25.1): the run-output guards on the three new tables,
  `scenario_run` append-only, and the 0027 downgrade refusal.

The overlay contract this kit assumes (D-45 §2), for the implementer to match or flag:

    scenario_overlay.run_overlay(inputs, directions, buckets, shocks, subset) -> Overlay
    Overlay.inputs: ModuleInputs                  # the stressed copy (one CashFlowInput per part)
    Overlay.parts: tuple[Part, ...]               # every input row, in stressed input order
    Part: part_id (the event id for the root, else uuid5), cashflow_event_id, path,
          shock_sequences (1-based), change, days_before, days_after, amount, flow
    Overlay.counts: tuple[ShockCount, ...]        # per shock: matched_flows, parts,
          direction_mismatches, skipped_past_due, skipped_incomplete
The engine must call `scenario_overlay.run_overlay` through the module attribute, so the
tampered-part test double (part 5c) is what the run uses.

The golden file schema (one engineering file, several cases):

    {"name", "status": GOLDEN_STATUS, "module": {"code": "scenario_overlay", "version": 1},
     "description", "cases": [{
        "name", "reporting_date", "buckets": [{code, label, lower_exclusive, upper_inclusive}],
        "directions": {instrument_type: INFLOW|OUTFLOW},
        "instruments": [{"instrument_id", "instrument_type"}],
        "cash_flows": [{"cashflow_id", "instrument_id", "days_after_reporting_date" | null,
                        "amount" | null, "currency", "origin"}],
        "shocks": [... the D-44 shock bodies ...],
        "expected": {
          "parts": [{"cashflow_id", "days_after_reporting_date", "amount", "shock_sequences"}],
          "counts": [{"matched_flows", "parts", "direction_mismatches", "skipped_past_due",
                      "skipped_incomplete"}],
          "ladder": {currency: {bucket_code: {"inflows", "outflows", "net_gap",
                                              "cumulative_gap"}}},
          "totals": {currency: {"inflows", "outflows"}}}}]}
"""

import dataclasses
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.calculations.modules import maturity_ladder
from app.calculations.modules.base import (
    CashFlowInput,
    InstrumentInput,
    ModuleInputs,
    PinnedRule,
)
from app.core.config import Settings
from app.core.decimals import exact_context
from app.main import create_app
from app.rules.constants import (
    CASHFLOW_DIRECTION,
    MATURITY_BUCKETS,
    SEEDED_RULES,
)
from app.rules.service import canonical_sha256
from app.tests.conftest import sql
from app.tests.helpers import cid, events, make_snapshot, project, start_run
from app.tests.scenario_kit import (
    SCENARIOS,
    assert_labelled,
    create_scenario,
    no_float,
    version_path,
)

APP = Path(__file__).resolve().parents[1]
GOLDEN_STATUS = "ENGINEERING — awaiting domain-expert approval"
OVERLAY_GOLDEN = APP / "calculations" / "golden" / "scenario_overlay_v1_engineering_cases.json"
RUNS = "/api/v1/calculation-runs"
SCENARIO_RUNS = "/api/v1/scenario-runs"
SCENARIO_RUN_TABLES = ("scenario_run",)
SCENARIO_OUTPUT_TABLES = (
    "calculation_scenario_pin",
    "scenario_contribution",
    "scenario_shock_application",
)
ALL_SCENARIO_RUN_TABLES = (*SCENARIO_RUN_TABLES, *SCENARIO_OUTPUT_TABLES)
MASKED = {"masked": True, "classification": "confidential"}
IMMUTABLE = asyncpg.InsufficientPrivilegeError
ROLES = ("platform_admin", "auditor", "data_steward", "approver", "risk_analyst")
# Feature 03 manifest and result-document key sets (calculations/service.py), unchanged by
# Feature 05 for non-stressed runs (D-45 §4).
F03_MANIFEST_KEYS = {
    "manifest_version",
    "engine_version",
    "reporting_date",
    "module",
    "dataset_snapshot",
    "rule_set",
    "assumption_set",
    "labels",
}
F03_RESULT_KEYS = {"status", "liquidity_gap", "contributions", "warnings"}
FIGURES = ("inflows", "outflows", "net_gap", "cumulative_gap")


def _no_fraction(text: str) -> Any:
    raise AssertionError(f"a JSON fraction in the golden pack: {text}")


def is_masked(value: Any) -> bool:
    return isinstance(value, dict) and value.get("masked") is True


# --- part 2: the pure overlay (BE-25.2) --------------------------------------------------------

# The user's §5 worked example, fixture buckets (0,7], (7,30], (30,90], (90,inf), INR.
CASE1_BUCKETS = [
    {"code": "K1", "label": "(0,7]", "lower_exclusive": 0, "upper_inclusive": 7},
    {"code": "K2", "label": "(7,30]", "lower_exclusive": 7, "upper_inclusive": 30},
    {"code": "K3", "label": "(30,90]", "lower_exclusive": 30, "upper_inclusive": 90},
    {"code": "K4", "label": "(90,inf)", "lower_exclusive": 90, "upper_inclusive": None},
]
DIRECTIONS = {"LOAN": "INFLOW", "BOND": "INFLOW", "DEPOSIT": "OUTFLOW"}
# (cashflow key, instrument key, type, day, amount)
CASE1_FLOWS = (
    ("L1", "I-L1", "LOAN", 3, "30"),
    ("L2", "I-L2", "LOAN", 5, "20"),
    ("B1", "I-B1", "BOND", 2, "50"),
    ("D1", "I-D1", "DEPOSIT", 4, "80"),
    ("B2", "I-B2", "BOND", 20, "60"),
    ("D2", "I-D2", "DEPOSIT", 25, "70"),
    ("B3", "I-B3", "BOND", 60, "90"),
    ("D3", "I-D3", "DEPOSIT", 50, "40"),
)
CASE1_SHOCK: dict[str, Any] = {
    "template": "DELAYED_INFLOWS",
    "parameters": {
        "cashflow_type": "LOAN",
        "affected_fraction": "0.2000",
        "delay_days": 14,
        "delay_calendar": "CALENDAR_DAYS",
    },
}
# Baseline and stressed ladder: (inflows, net, cumulative) per bucket K1..K3.
CASE1_BASELINE = {"K1": ("100", "20", "20"), "K2": ("60", "-10", "10"), "K3": ("90", "50", "60")}
CASE1_STRESSED = {"K1": ("90", "10", "10"), "K2": ("70", "0", "10"), "K3": ("90", "50", "60")}


def _pins(buckets: list[dict[str, Any]], directions: Mapping[str, str]) -> dict[str, PinnedRule]:
    pins = {code: PinnedRule(uuid.uuid4(), seed[3]) for code, seed in SEEDED_RULES.items()}
    pins[MATURITY_BUCKETS] = PinnedRule(uuid.uuid4(), {"buckets": buckets})
    pins[CASHFLOW_DIRECTION] = PinnedRule(uuid.uuid4(), {"map": dict(directions)})
    return pins


def _inputs(
    reporting: date,
    flows: Sequence[tuple[str, str, str, int | None, str | None]],
    currency: str = "INR",
) -> tuple[ModuleInputs, dict[uuid.UUID, str]]:
    """Inputs with deterministic ids (uuid5 of the key) so two builds are equal."""
    ns = uuid.UUID("25252525-0000-0000-0000-000000000025")
    names: dict[uuid.UUID, str] = {}
    cash, held = [], {}
    for key, instrument, kind, day, amount in flows:
        fid = uuid.uuid5(ns, key)
        names[fid] = key
        cash.append(
            CashFlowInput(
                fid,
                instrument,
                None if day is None else reporting + timedelta(days=day),
                None if amount is None else Decimal(amount),
                currency,
                "CONTRACTUAL",
            )
        )
        held[instrument] = InstrumentInput(
            uuid.uuid5(ns, f"v:{instrument}"), uuid.uuid5(ns, f"i:{instrument}"), instrument, kind
        )
    return ModuleInputs(reporting, tuple(cash), tuple(held.values())), names


def _ladder(
    inputs: ModuleInputs, buckets: list[dict[str, Any]], directions: Mapping[str, str]
) -> dict[str, dict[str, dict[str, Decimal]]]:
    with localcontext(exact_context()):
        result = maturity_ladder.MODULE.run(inputs, _pins(buckets, directions))
    assert not result.blocked, result.warnings
    out: dict[str, dict[str, dict[str, Decimal]]] = {}
    for row in result.liquidity_gap:
        out.setdefault(row.currency, {})[row.bucket.code] = {f: getattr(row, f) for f in FIGURES}
    return out


def _apply(
    overlay_module: Any,
    inputs: ModuleInputs,
    directions: Mapping[str, str],
    shocks: Sequence[Mapping[str, Any]],
    buckets: list[dict[str, Any]] = CASE1_BUCKETS,
) -> Any:
    from app.calculations.modules.base import Bucket
    from app.rules.constants import CashflowDirection

    return overlay_module.run_overlay(
        inputs,
        {k: CashflowDirection(v) for k, v in directions.items()},
        [
            Bucket(n, b["code"], b["label"], b["lower_exclusive"], b["upper_inclusive"])
            for n, b in enumerate(buckets, start=1)
        ],
        shocks,
        list(range(1, len(shocks) + 1)),
    )


def _part_rows(overlay: Any, names: Mapping[uuid.UUID, str], reporting: date) -> list[Any]:
    return sorted(
        (
            names[p.cashflow_event_id],
            (p.flow.due_date - reporting).days if p.flow.due_date else None,
            p.flow.amount,
            tuple(p.shock_sequences),
        )
        for p in overlay.parts
    )


def check_overlay_pure() -> None:
    from app.calculations import scenario_overlay  # BE-25.2; imported here so collection works

    # The no-float and no-AI greps cover the overlay and the orchestration module.
    path = APP / "calculations" / "scenario_overlay.py"
    assert "float" not in path.read_text(), path
    for root in (APP / "calculations", APP / "orchestration"):
        for source in sorted(root.rglob("*.py")):
            text = source.read_text()
            assert not re.search(r"^\s*(from|import)\s+app\.ai\b", text, re.M), source
            assert 'import_module("app.ai' not in text, source

    # Case 1, the user's §5 worked example, by hand.
    reporting = date(2026, 12, 31)
    inputs, names = _inputs(reporting, [(k, i, t, d, a) for k, i, t, d, a in CASE1_FLOWS])
    base = _ladder(inputs, CASE1_BUCKETS, DIRECTIONS)["INR"]
    for code, (inflows, net, cumulative) in CASE1_BASELINE.items():
        assert (base[code]["inflows"], base[code]["net_gap"], base[code]["cumulative_gap"]) == (
            Decimal(inflows),
            Decimal(net),
            Decimal(cumulative),
        ), code
    overlay = _apply(scenario_overlay, inputs, DIRECTIONS, [CASE1_SHOCK])
    rows = _part_rows(overlay, names, reporting)
    loans = [r for r in rows if r[0] in ("L1", "L2")]
    assert loans == sorted(
        [
            ("L1", 3, Decimal("24.0000"), (1,)),
            ("L1", 17, Decimal("6.0000"), (1,)),
            ("L2", 5, Decimal("16.0000"), (1,)),
            ("L2", 19, Decimal("4.0000"), (1,)),
        ]
    ) or [(k, d, a.normalize(), s) for k, d, a, s in loans] == sorted(
        [
            ("L1", 3, Decimal("24"), (1,)),
            ("L1", 17, Decimal("6"), (1,)),
            ("L2", 5, Decimal("16"), (1,)),
            ("L2", 19, Decimal("4"), (1,)),
        ]
    ), loans
    assert {r[0] for r in rows if r[0] not in ("L1", "L2")} == {"B1", "D1", "B2", "D2", "B3", "D3"}
    assert len(overlay.inputs.cash_flows) == len(overlay.parts) == 10
    ids = [cf.cashflow_event_id for cf in overlay.inputs.cash_flows]
    assert len(set(ids)) == len(ids)  # unique part ids: the COVERAGE post-check sees each once
    stressed = _ladder(overlay.inputs, CASE1_BUCKETS, DIRECTIONS)["INR"]
    for code, (inflows, net, cumulative) in CASE1_STRESSED.items():
        assert (
            stressed[code]["inflows"],
            stressed[code]["net_gap"],
            stressed[code]["cumulative_gap"],
        ) == (Decimal(inflows), Decimal(net), Decimal(cumulative)), code
    assert sum(r["inflows"] for r in stressed.values()) == Decimal("250")  # conservation
    [count] = overlay.counts
    assert (count.matched_flows, count.parts, count.direction_mismatches) == (2, 4, 0)
    # Part ids are stable across two runs (uuid5 of the event id and the part path).
    again = _apply(scenario_overlay, _inputs(reporting, list(CASE1_FLOWS))[0], DIRECTIONS,
                   [CASE1_SHOCK])  # fmt: skip
    assert [(p.part_id, p.path) for p in again.parts] == [
        (p.part_id, p.path) for p in overlay.parts
    ]
    assert [p.part_id for p in overlay.parts] == [
        scenario_overlay.part_id(p.cashflow_event_id, p.path) for p in overlay.parts
    ]
    assert [p.flow.cashflow_event_id for p in overlay.parts] == ids  # the module sees part ids

    # The golden loop: every hand-computed engineering case of the pack file.
    pack = json.loads(OVERLAY_GOLDEN.read_text(encoding="utf-8"), parse_float=_no_fraction)
    assert pack["status"] == GOLDEN_STATUS
    assert pack["module"] == {"code": "scenario_overlay", "version": 1}
    covered: set[str] = set()
    for case in pack["cases"]:
        reporting = date.fromisoformat(case["reporting_date"])
        kinds = {i["instrument_id"]: i["instrument_type"] for i in case["instruments"]}
        flows = [
            (
                f["cashflow_id"],
                f["instrument_id"],
                kinds.get(f["instrument_id"], "UNKNOWN"),
                f["days_after_reporting_date"],
                f["amount"],
            )
            for f in case["cash_flows"]
        ]
        currencies = {f["currency"] for f in case["cash_flows"]}
        inputs, names = (
            _inputs(reporting, flows, currencies.pop())
            if len(currencies) == 1
            else _mixed_inputs(reporting, case)
        )
        overlay = _apply(
            scenario_overlay, inputs, case["directions"], case["shocks"], case["buckets"]
        )
        expected = case["expected"]
        got = [(k, d, a, s) for k, d, a, s in _part_rows(overlay, names, reporting)]
        want = sorted(
            (
                p["cashflow_id"],
                p["days_after_reporting_date"],
                None if p["amount"] is None else Decimal(p["amount"]),
                tuple(p["shock_sequences"]),
            )
            for p in expected["parts"]
        )
        assert got == want, case["name"]
        assert [
            {
                k: getattr(c, k)
                for k in (
                    "matched_flows",
                    "parts",
                    "direction_mismatches",
                    "skipped_past_due",
                    "skipped_incomplete",
                )
            }
            for c in overlay.counts
        ] == expected["counts"], case["name"]
        ladder = _ladder(overlay.inputs, case["buckets"], case["directions"])
        for currency, buckets in expected["ladder"].items():
            for code, figures in buckets.items():
                assert {f: ladder[currency][code][f] for f in figures} == {
                    f: Decimal(v) for f, v in figures.items()
                }, (case["name"], currency, code)
        for currency, totals in expected.get("totals", {}).items():
            for f, v in totals.items():
                assert sum(r[f] for r in ladder[currency].values()) == Decimal(v), case["name"]
        covered.update(s["template"] for s in case["shocks"])
    assert covered == {"DELAYED_INFLOWS", "DEPOSIT_WITHDRAWAL", "FUNDING_UNAVAILABLE"}
    assert len(pack["cases"]) >= 9  # case 1 and the plan's other cases


def _mixed_inputs(reporting: date, case: Mapping[str, Any]) -> tuple[ModuleInputs, dict[Any, str]]:
    ns = uuid.UUID("25252525-0000-0000-0000-000000000025")
    kinds = {i["instrument_id"]: i["instrument_type"] for i in case["instruments"]}
    names: dict[uuid.UUID, str] = {}
    cash = []
    for f in case["cash_flows"]:
        fid = uuid.uuid5(ns, f["cashflow_id"])
        names[fid] = f["cashflow_id"]
        day = f["days_after_reporting_date"]
        cash.append(
            CashFlowInput(
                fid,
                f["instrument_id"],
                None if day is None else reporting + timedelta(days=day),
                None if f["amount"] is None else Decimal(f["amount"]),
                f["currency"],
                f["origin"],
            )
        )
    held = tuple(
        InstrumentInput(uuid.uuid5(ns, f"v:{k}"), uuid.uuid5(ns, f"i:{k}"), k, t)
        for k, t in kinds.items()
    )
    return ModuleInputs(reporting, tuple(cash), held), names


# --- part 5c: runs, comparison and drill-down through the API (BE-25.3, BE-25.4) -------------

# The §5 flows on the seeded buckets (B02 (1,7], B03 (7,30], B04 (30,90]) plus a B05 (90,180]
# bucket that breaks only when the shocks are combined: LOAN 30 at day 170 (20% moves to day
# 184, B06), BOND 20 at day 110, DEPOSIT 43 at day 120 (+15% = 6.45).
API_FLOWS = (
    ("SC-CF-L1", "SC-LOAN-1", "LOAN", 3, "30.00"),
    ("SC-CF-L2", "SC-LOAN-2", "LOAN", 5, "20.00"),
    ("SC-CF-B1", "SC-BOND-1", "BOND", 2, "50.00"),
    ("SC-CF-D1", "SC-DEP-1", "DEPOSIT", 4, "80.00"),
    ("SC-CF-B2", "SC-BOND-2", "BOND", 20, "60.00"),
    ("SC-CF-D2", "SC-DEP-2", "DEPOSIT", 25, "70.00"),
    ("SC-CF-B3", "SC-BOND-3", "BOND", 60, "90.00"),
    ("SC-CF-D3", "SC-DEP-3", "DEPOSIT", 50, "40.00"),
    ("SC-CF-L3", "SC-LOAN-3", "LOAN", 170, "30.00"),
    ("SC-CF-B4", "SC-BOND-4", "BOND", 110, "20.00"),
    ("SC-CF-D4", "SC-DEP-4", "DEPOSIT", 120, "43.00"),
)
DEPOSIT_SHOCK: dict[str, Any] = {
    "template": "DEPOSIT_WITHDRAWAL",
    "parameters": {"cashflow_type": "DEPOSIT", "increase_fraction": "0.15"},
}
FUNDING_SHOCK: dict[str, Any] = {
    "template": "FUNDING_UNAVAILABLE",
    "parameters": {"unavailable_days": 7, "affected_fraction": "1"},
    "selector": {"instrument_type": "LOAN", "instrument_keys": ["SC-LOAN-2"]},
}
NOTHING_SHOCK: dict[str, Any] = {
    **FUNDING_SHOCK,
    # A valid selector that matches nothing: the fixture has no LOAN flow due on day 1 (its
    # loans are due on days 3, 5 and 170). An unknown key would be refused 422 at creation.
    "selector": {"instrument_type": "LOAN", "due_to_day": 1},
}
# Hand-computed (seeded buckets), net gap per bucket: baseline, each shock alone, combined.
API_NET = {
    "B02": ("20.00", "10", "8", "0", "-18"),
    "B03": ("-10.00", "0", "-20.5", "10", "5.5"),
    "B04": ("50.00", "50", "44", "50", "44"),
    "B05": ("7.00", "1", "0.55", "7", "-5.45"),
    "B06": ("0", "6", "0", "0", "6"),
}
API_CUMULATIVE = {
    "B02": ("20.00", "10", "8", "0", "-18"),
    "B03": ("10.00", "10", "-12.5", "10", "-12.5"),
    "B04": ("60.00", "60", "31.5", "60", "31.5"),
    "B05": ("67.00", "61", "32.05", "67", "26.05"),
    "B06": ("67.00", "67", "32.05", "67", "32.05"),
}
SUBSETS = ([1], [2], [3], [1, 2, 3])


def scenario_run_counts(db: str) -> dict[str, int]:
    return {
        t: int(sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"])  # noqa: S608
        for t in ALL_SCENARIO_RUN_TABLES
    }


def _ready(
    api: TestClient,
    analyst: dict[str, str],
    approver: dict[str, str],
    name: str,
    shocks: list[dict[str, Any]],
    state: str,
) -> dict[str, Any]:
    """A scenario whose version 1 is in `state` (DRAFT, CONFIRMED_FOR_EXPLORATION,
    IN_REVIEW, APPROVED_FOR_TEST or REJECTED)."""
    scenario = create_scenario(api, analyst, name, shocks)
    path = version_path(scenario, 1)
    steps: list[tuple[str, dict[str, Any], dict[str, str]]] = []
    if state != "DRAFT":
        steps.append(("confirm", {}, analyst))
    if state in ("IN_REVIEW", "APPROVED_FOR_TEST", "REJECTED"):
        steps.append(("submit", {"comment": "For the shared library"}, analyst))
    if state in ("APPROVED_FOR_TEST", "REJECTED"):
        decision = "APPROVE" if state == "APPROVED_FOR_TEST" else "REJECT"
        steps.append(("approval", {"decision": decision, "comment": "Reviewed"}, approver))
    for step, payload, headers in steps:
        r = api.post(f"{path}/{step}", json=payload, headers=headers)
        assert r.status_code == 200, (step, r.text)
    detail = api.get(f"{SCENARIOS}/{scenario['scenario_id']}", headers=analyst).json()
    [v1] = [v for v in detail["versions"] if v["version"] == 1]
    assert v1["status"] == state, v1
    return scenario


def run_scenario(
    api: TestClient,
    headers: dict[str, str],
    scenario: Mapping[str, Any],
    baseline_run_id: str,
    key: str | None = None,
    version: int = 1,
) -> tuple[Any, str]:
    c = cid()
    r = api.post(
        f"{SCENARIOS}/{scenario['scenario_id']}/run",
        json={"version": version, "baseline_run_id": baseline_run_id},
        headers={**headers, "Idempotency-Key": key or f"sr-{uuid.uuid4()}", "X-Correlation-ID": c},
    )
    return r, c


def _cells(
    comparison: Mapping[str, Any], currency: str = "INR"
) -> dict[tuple[str, tuple[int, ...]], Any]:
    [cur] = [c for c in comparison["currencies"] if c["currency"] == currency]
    return {
        (row["bucket_code"], tuple(cell["shock_subset"])): {**cell, "_row": row}
        for row in cur["rows"]
        for cell in row["cells"]
    }


def check_scenario_runs(
    api: TestClient,
    db: str,
    artifact_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    load: Callable[..., dict[str, Any]],
    instrument_source: Mapping[str, Any],
    cash_source: Mapping[str, Any],
    instrument_csv: Callable[[Sequence[tuple[str, ...]]], bytes],
    cashflow_csv: Callable[[Sequence[tuple[Any, ...]], date], bytes],
    module_id: str,
    calc_counts: Callable[[str], dict[str, int]],
    feature03_run_id: str,
) -> dict[str, str]:
    from app.calculations import scenario_overlay
    from app.calculations.constants import ENGINE_VERSION, MANIFEST_VERSION  # noqa: I001

    tokens = {role: api_token(api, role) for role in ROLES}
    analyst, steward, approver = tokens["risk_analyst"], tokens["data_steward"], tokens["approver"]
    assert ENGINE_VERSION == "1" and MANIFEST_VERSION == 1  # D-45 §4 (the D-41 §3 reasoning)

    # The INR fixture, a snapshot and a non-stressed baseline run.
    reporting = date(2027, 3, 31)
    instruments = load(
        instrument_source,
        instrument_csv([(i, t, "INR", a) for _, i, t, _, a in API_FLOWS]),
    )
    flows = load(
        cash_source,
        cashflow_csv(
            [(k, i, d, a, "INR", "PRINCIPAL", "CONTRACTUAL") for k, i, _, d, a in API_FLOWS],
            reporting,
        ),
    )
    for imported in (instruments, flows):  # a snapshot takes projected imports only
        assert project(api, steward, imported["id"]).status_code == 201
    snapshot = make_snapshot(api, analyst, [instruments["id"], flows["id"]]).json()
    body = {
        "dataset_snapshot_id": snapshot["snapshot_id"],
        "module_version_id": module_id,
        "reporting_date": reporting.isoformat(),
    }
    before = scenario_run_counts(db)
    baseline = no_float(start_run(api, analyst, body).text)
    assert baseline["status"] == "SUCCEEDED", baseline
    baseline_id = baseline["run_id"]
    # A non-stressed run: exactly the Feature 03 key sets, and no scenario_* rows.
    assert scenario_run_counts(db) == before
    detail = no_float(api.get(f"{RUNS}/{baseline_id}", headers=steward).text)
    assert set(detail["manifest"]) == F03_MANIFEST_KEYS
    f03 = no_float(api.get(f"{RUNS}/{feature03_run_id}", headers=steward).text)
    assert set(f03["manifest"]) == F03_MANIFEST_KEYS
    from app.calculations.constants import CalculationRunStatus
    from app.calculations.service import result_document

    plain = result_document(CalculationRunStatus.SUCCEEDED, (), (), ())
    assert set(plain) == F03_RESULT_KEYS  # a non-stressed result document: Feature 03 keys

    # --- the §5 case end to end: one shock, one run, the BE-25.2 figures ----------------------
    single = _ready(api, analyst, approver, "Section 5 loan delay", [CASE1_SHOCK],
                    "CONFIRMED_FOR_EXPLORATION")  # fmt: skip
    vpath = version_path(single, 1)
    for role, headers in tokens.items():  # validation: calc:read, 200 for every role
        r = api.get(f"{vpath}/validation", params={"baseline_run_id": baseline_id}, headers=headers)
        assert r.status_code == 200, (role, r.text)
    dry = no_float(r.text)
    assert dry["runnable"] and dry["blocking_reasons"] == [] and dry["run_count"] == 1
    [counted] = dry["shocks"]
    assert (counted["matched_flows"], counted["parts"], counted["direction_mismatches"]) == (
        3,
        6,
        0,
    )
    assert dry["approval_state"] == "EXPLORATION"
    assert scenario_run_counts(db) == before  # the dry run writes nothing
    for role, headers in tokens.items():  # the run: calc:run, risk_analyst only (D-47 §2)
        if role == "risk_analyst":
            continue
        r, _ = run_scenario(api, headers, single, baseline_id)
        assert (r.status_code, r.json()["code"]) == (403, "forbidden"), role
    key = "p25-single"
    r, c = run_scenario(api, analyst, single, baseline_id, key=key)
    assert r.status_code == 201, r.text
    one = no_float(r.text)
    assert_labelled(one)
    trail = [(e["action"], e["outcome"]) for e in events(db, c)]
    assert ("scenario.run", "SUCCESS") in trail and ("calculation_run.create", "SUCCESS") in trail
    assert (one["approval_state"], one["approval_label"]) == (
        "EXPLORATION",
        "Exploration, not approved",
    )
    [member] = one["members"]
    assert member["shock_subset"] == [1] and member["status"] == "SUCCEEDED", member
    stressed_id = member["calculation_run_id"]
    # An Idempotency-Key replay returns the same scenario_run and writes nothing.
    counts = scenario_run_counts(db)
    r, _ = run_scenario(api, analyst, single, baseline_id, key=key)
    assert r.status_code == 200 and no_float(r.text) == one and scenario_run_counts(db) == counts
    # The stressed manifest equals the baseline's in every section except `scenario`.
    stressed = no_float(api.get(f"{RUNS}/{stressed_id}", headers=steward).text)
    manifest = stressed["manifest"]
    assert set(manifest) == F03_MANIFEST_KEYS | {"scenario"}
    assert {k: v for k, v in manifest.items() if k != "scenario"} == detail["manifest"]
    section = manifest["scenario"]
    assert section["approval_state"] == "EXPLORATION" and section["shock_subset"] == [1]
    assert section["baseline_run_id"] == baseline_id
    assert section["shocks_sha256"] == canonical_sha256(section["shocks"])
    assert stressed["manifest_sha256"] == canonical_sha256(manifest)
    assert all(p["passed"] for p in stressed["outcome"]["post_checks"])
    checks = {p["check"] for p in stressed["outcome"]["post_checks"]}
    assert {"SCENARIO_OVERLAY", "SCENARIO_CONSERVATION"} <= checks
    ladder = no_float(api.get(f"{RUNS}/{stressed_id}/maturity-ladder", headers=steward).text)
    [inr] = ladder["currencies"]
    got = {row["bucket_code"]: row for row in inr["rows"]}
    for code, (inflows, net, cumulative) in zip(
        ("B02", "B03", "B04"), CASE1_STRESSED.values(), strict=True
    ):
        assert (
            Decimal(got[code]["inflows"]),
            Decimal(got[code]["net_gap"]),
            Decimal(got[code]["cumulative_gap"]),
        ) == (Decimal(inflows), Decimal(net), Decimal(cumulative)), code
    total = sum(Decimal(row["inflows"]) for row in inr["rows"])
    assert total == Decimal("300.00")  # 250 of the §5 example + 50 in B05: conserved
    # The results read serves the stressed run from scenario_contribution.
    results = no_float(
        api.get(f"{RUNS}/{stressed_id}/results", params={"limit": 1000}, headers=steward).text
    )
    stored_parts = sql(
        db,
        "SELECT count(*) AS n FROM scenario_contribution WHERE calculation_run_id = $1",
        uuid.UUID(stressed_id),
    )[0]["n"]
    assert results["total"] == stored_parts == len(API_FLOWS) + 3
    assert int(sql(db, "SELECT count(*) AS n FROM intermediate_value WHERE calculation_run_id = $1",
                   uuid.UUID(stressed_id))[0]["n"]) == 0  # fmt: skip

    # --- three shocks: four runs, {1}, {2}, {3} and {1,2,3} -----------------------------------
    combined = _ready(api, analyst, approver, "Combined stress",
                      [CASE1_SHOCK, DEPOSIT_SHOCK, FUNDING_SHOCK], "APPROVED_FOR_TEST")  # fmt: skip
    r, c = run_scenario(api, analyst, combined, baseline_id)
    assert r.status_code == 201, r.text
    three = no_float(r.text)
    assert (three["approval_state"], three["approval_label"]) == (
        "APPROVED",
        "Approved scenario (illustrative)",
    )
    assert [m["shock_subset"] for m in three["members"]] == list(SUBSETS)
    assert [m["combined"] for m in three["members"]] == [False, False, False, True]
    assert len([e for e in events(db, c) if e["action"] == "calculation_run.create"]) == 4
    timing_only = {(1,), (3,)}
    for m in three["members"]:
        run = no_float(api.get(f"{RUNS}/{m['calculation_run_id']}", headers=steward).text)
        assert run["status"] == "SUCCEEDED", run
        post_checks = {p["check"]: p["passed"] for p in run["outcome"]["post_checks"]}
        assert post_checks["SCENARIO_CONSERVATION"] and post_checks["SCENARIO_OVERLAY"], m
        if tuple(m["shock_subset"]) in timing_only:  # timing only: totals unchanged
            page = api.get(f"{RUNS}/{m['calculation_run_id']}/maturity-ladder", headers=steward)
            [cur] = page.json()["currencies"]
            assert sum(Decimal(row["inflows"]) for row in cur["rows"]) == Decimal("300.00")
    listed = api.get(SCENARIO_RUNS, params={"scenario_id": combined["scenario_id"]},
                     headers=tokens["auditor"])  # fmt: skip
    assert listed.status_code == 200
    assert [i["scenario_run_id"] for i in listed.json()["items"]] == [three["scenario_run_id"]]
    for role, headers in tokens.items():
        assert api.get(SCENARIO_RUNS, headers=headers).status_code == 200, role
    # GET /calculation-runs?stressed=: the baseline picker's filter (stressed=false).
    for flag, has, lacks in (
        ("false", baseline_id, stressed_id),
        ("true", stressed_id, baseline_id),
    ):
        page = api.get(RUNS, params={"stressed": flag, "limit": 200}, headers=analyst).json()
        found = {i["run_id"] for i in page["items"]}
        assert has in found and lacks not in found, flag
        assert all((i["scenario"] is None) is (flag == "false") for i in page["items"]), flag
    # D-33: the run detail is a second read path of the pinned shocks. A picked instrument
    # key the caller is not cleared for reads as a MaskedValue there, as on the scenario
    # detail, while shocks_sha256 stays the pin over the stored keys. The seeded schema has
    # instrument_id internal (clear for every role), so a double raises SC-LOAN-2 to
    # confidential: clear for data_steward, masked for risk_analyst.
    from app.domain.service import DomainService

    real_classes = DomainService.instrument_key_classes

    async def confidential(self: Any, keys: Any, instrument_type: Any = None) -> dict[str, str]:
        found = await real_classes(self, keys, instrument_type)
        return {k: "confidential" if k == "SC-LOAN-2" else c for k, c in found.items()}

    monkeypatch.setattr(DomainService, "instrument_key_classes", confidential)
    [funding] = [m for m in three["members"] if m["shock_subset"] == [3]]
    [stored] = sql(db, "SELECT manifest->'scenario' AS s FROM calculation_run WHERE id = $1",
                   uuid.UUID(funding["calculation_run_id"]))  # fmt: skip
    pinned = no_float(stored["s"])
    assert pinned["shocks"][2]["selector"]["instrument_keys"] == ["SC-LOAN-2"]  # stored clear
    assert pinned["shocks_sha256"] == canonical_sha256(pinned["shocks"])
    for headers, keys in ((steward, ["SC-LOAN-2"]), (analyst, [MASKED])):
        r = api.get(f"{RUNS}/{funding['calculation_run_id']}", headers=headers)
        assert r.status_code == 200 and ("SC-LOAN-2" in r.text) is (keys != [MASKED]), r.text
        section = no_float(r.text)["manifest"]["scenario"]
        assert section["shocks"][2]["selector"]["instrument_keys"] == keys, section
        assert section["shocks_sha256"] == pinned["shocks_sha256"]
        seen = api.get(f"{SCENARIOS}/{combined['scenario_id']}", headers=headers).json()
        assert seen["versions"][0]["shocks"][2]["selector"]["instrument_keys"] == keys
    monkeypatch.setattr(DomainService, "instrument_key_classes", real_classes)

    # --- comparison (BE-25.4) ------------------------------------------------------------------
    cpath = f"{SCENARIOS}/{combined['scenario_id']}/comparison"
    params = {"scenario_run_id": three["scenario_run_id"]}
    views = {}
    for role, headers in tokens.items():
        r = api.get(cpath, params=params, headers=headers)
        assert r.status_code == 200, (role, r.text)
        views[role] = no_float(r.text)
        r = api.get(f"{cpath}/contributions", params=params, headers=headers)
        assert r.status_code == 200, (role, r.text)
    clear, hidden = views["data_steward"], views["risk_analyst"]
    assert_labelled(clear)
    assert clear["approval_label"] == "Approved scenario (illustrative)" and clear["illustrative"]
    assert clear["min_aggregate_count"] == hidden["min_aggregate_count"] == 3
    assert [c["currency"] for c in clear["currencies"]] == ["INR"]  # no cross-currency total
    cells = _cells(clear)
    for cell_key, cell in cells.items():
        for figure in FIGURES:  # every delta is stressed minus baseline, exactly
            f = cell[figure]
            assert Decimal(f["delta"]) == Decimal(f["stressed"]) - Decimal(f["baseline"]), (
                cell_key,
                figure,
            )
    for code, values in API_NET.items():
        for subset, want in zip(SUBSETS, values[1:], strict=True):
            cell = cells[(code, tuple(subset))]
            assert Decimal(cell["net_gap"]["baseline"]) == Decimal(values[0]), code
            assert Decimal(cell["net_gap"]["stressed"]) == Decimal(want), (code, subset)
            assert Decimal(cell["cumulative_gap"]["stressed"]) == Decimal(
                API_CUMULATIVE[code][SUBSETS.index(subset) + 1]
            ), (code, subset)
    # Turns negative (baseline >= 0, stressed < 0), hand-computed.
    turns_net = {k for k, c in cells.items() if c["turns_negative_net"]}
    turns_cum = {k for k, c in cells.items() if c["turns_negative_cumulative"]}
    assert turns_net == {("B02", (1, 2, 3)), ("B05", (1, 2, 3))}, turns_net
    assert turns_cum == {
        ("B03", (2,)),
        ("B02", (1, 2, 3)),
        ("B03", (1, 2, 3)),
    }, turns_cum
    # Breaks only when combined: B02 and B05; never B03 (negative at baseline) or B04.
    [inr_clear] = clear["currencies"]
    flagged = {row["bucket_code"]: row["breaks_only_combined"] for row in inr_clear["rows"]}
    assert {k for k, v in flagged.items() if v} == {"B02", "B05"}, flagged
    assert flagged["B03"] is False and flagged["B04"] is False
    # k = 1: no single-shock run is compared, so the flag is null (not assessable) everywhere.
    lone = api.get(f"{SCENARIOS}/{single['scenario_id']}/comparison",
                   params={"scenario_run_id": one["scenario_run_id"]}, headers=steward)  # fmt: skip
    assert {r["breaks_only_combined"] for c in lone.json()["currencies"] for r in c["rows"]} == {
        None
    }
    # Largest changes: ranked by absolute net delta, clear deltas only.
    _check_largest(clear)
    _check_largest(hidden)
    # D-46 §3: B05 combined changed by 2 distinct flows (4 parts), fewer than the minimum: the
    # delta field is masked for risk_analyst and clear for data_steward, and every later
    # cumulative delta of INR stays masked. This masks the delta field only: the same cell's
    # baseline and stressed figures stay clear (each side is its own ladder, with enough
    # flows), so the delta is still derivable as stressed - baseline (D-46 §5 residual risk,
    # an open item; not protected by this assertion).
    hcells = _cells(hidden)
    b05 = hcells[("B05", (1, 2, 3))]
    assert b05["changed_flow_count"] == 2 and b05["net_gap"]["delta"] == MASKED
    assert not is_masked(cells[("B05", (1, 2, 3))]["net_gap"]["delta"])
    for code in ("B05", "B06"):  # the combined cumulative deltas from the masked net onward
        assert hcells[(code, (1, 2, 3))]["cumulative_gap"]["delta"] == MASKED, code
        assert not is_masked(cells[(code, (1, 2, 3))]["cumulative_gap"]["delta"]), code
    contributions = api.get(
        f"{cpath}/contributions",
        params={**params, "shock_subset": [1, 2, 3], "limit": 500},
        headers=steward,
    ).json()["items"]
    b05_parts = [
        i for i in contributions if "B05" in (i["bucket_code_before"], i["bucket_code_after"])
    ]
    assert len(b05_parts) >= 3 and len({i["cashflow_event_id"] for i in b05_parts}) == 2
    [inr_hidden] = hidden["currencies"]
    order = [row["bucket_code"] for row in inr_hidden["rows"]]
    for subset in SUBSETS:
        masked_from = None
        for code in order:
            delta = hcells[(code, tuple(subset))]["cumulative_gap"]["delta"]
            if masked_from is not None:
                assert delta == MASKED, (code, subset)
            elif is_masked(delta):
                masked_from = code
    assert hcells[("B06", (1, 2, 3))]["cumulative_gap"]["delta"] == MASKED
    # Flags are null when they read a masked figure; otherwise equal to the clear view's.
    for k, cell in hcells.items():
        for flag, figure in (("turns_negative_net", "net_gap"),
                             ("turns_negative_cumulative", "cumulative_gap")):  # fmt: skip
            read = (cell[figure]["baseline"], cell[figure]["stressed"])
            if any(is_masked(v) for v in read):
                assert cell[flag] is None, (k, flag)
            else:
                assert cell[flag] == cells[k][flag], (k, flag)
    # The templated summary cites only clear figures.
    secret = {
        cells[k][f]["delta"]
        for k, cell in hcells.items()
        for f in FIGURES
        if is_masked(cell[f]["delta"]) and not is_masked(cells[k][f]["delta"])
    }
    shown = {
        v
        for cell in hcells.values()
        for f in FIGURES
        for v in cell[f].values()
        if isinstance(v, str)
    }
    text = " ".join(hidden["summary"]["sentences"])
    for value in secret - shown:
        if "." in value:
            assert value not in text, value
    assert hidden["summary"]["template_version"] and clear["summary"]["sentences"]

    # --- drill-down (D-46 §4) ------------------------------------------------------------------
    for headers, masked in ((steward, False), (analyst, True)):
        page = no_float(
            api.get(
                f"{cpath}/contributions",
                params={**params, "shock_subset": [1, 2, 3], "limit": 500},
                headers=headers,
            ).text
        )
        assert_labelled(page)
        assert page["items"] and page["total"] == len(page["items"])
        for item in page["items"]:
            assert (item["amount"] == MASKED) is masked, item
            assert isinstance(item["days_before"], int) and isinstance(item["days_after"], int)
            assert item["bucket_code_before"] and item["bucket_code_after"] and item["templates"]
            assert item["change"] in ("MOVED", "STAYED", "ADDED")
            [row] = sql(
                db,
                "SELECT calculation_run_id, cashflow_event_id, part_path, canonical_record_id,"
                " source_record_id FROM scenario_contribution WHERE id = $1",
                uuid.UUID(item["scenario_contribution_id"]),
            )
            assert (str(row["calculation_run_id"]), str(row["cashflow_event_id"]),
                    row["part_path"]) == (item["calculation_run_id"], item["cashflow_event_id"],
                                          item["part_path"])  # fmt: skip
            lineage = item["lineage"]
            [chain] = sql(
                db,
                "SELECT b.import_job_id, s.id, s.ordinal FROM canonical_record c"
                " JOIN record_lineage l ON l.canonical_record_id = c.id"
                " JOIN source_record s ON s.id = l.source_record_id"
                " JOIN import_batch b ON b.id = s.batch_id WHERE c.id = $1",
                uuid.UUID(lineage["canonical_record_id"]),
            )
            assert (str(chain["import_job_id"]), str(chain["id"]), chain["ordinal"]) == (
                lineage["import_id"],
                lineage["source_record_id"],
                lineage["source_ordinal"],
            )
            assert lineage["import_id"] == flows["id"]

    # --- the tampered part: SCENARIO_CONSERVATION fails the run -------------------------------
    real = scenario_overlay.run_overlay

    def tampered(*args: Any, **kwargs: Any) -> Any:
        """A test double: the real overlay with one moved part one unit larger."""
        out = real(*args, **kwargs)
        i = next((n for n, p in enumerate(out.parts) if p.path != scenario_overlay.ROOT), 0)
        part = out.parts[i]
        assert part.flow.amount is not None
        larger = part.flow.amount + 1
        bad = dataclasses.replace(part.flow, amount=larger)
        parts = (
            *out.parts[:i],
            dataclasses.replace(part, flow=bad, amount=larger),
            *out.parts[i + 1 :],
        )
        cash = out.inputs.cash_flows
        stressed = dataclasses.replace(out.inputs, cash_flows=(*cash[:i], bad, *cash[i + 1 :]))
        return dataclasses.replace(out, parts=parts, inputs=stressed)

    monkeypatch.setattr(scenario_overlay, "run_overlay", tampered)
    r, _ = run_scenario(api, analyst, single, baseline_id)
    monkeypatch.setattr(scenario_overlay, "run_overlay", real)
    assert r.status_code == 201, r.text
    [failed] = r.json()["members"]
    assert failed["status"] == "FAILED", failed
    warnings = no_float(api.get(f"{RUNS}/{failed['calculation_run_id']}", headers=steward).text)[
        "warnings"
    ]
    assert [(w["code"], w["details"].get("check")) for w in warnings] == [
        ("POST_CHECK_FAILED", "SCENARIO_CONSERVATION")
    ], warnings

    # --- refusals: 409, audited DENIED, nothing written ---------------------------------------
    refusals: list[tuple[Mapping[str, Any], str, str]] = []
    for state in ("DRAFT", "IN_REVIEW", "REJECTED"):
        s = _ready(api, analyst, approver, f"Not runnable {state}", [CASE1_SHOCK], state)
        refusals.append((s, baseline_id, "scenario_not_runnable"))
    refusals.append((single, stressed_id, "invalid_baseline"))
    nothing = _ready(api, analyst, approver, "Matches nothing", [NOTHING_SHOCK],
                     "CONFIRMED_FOR_EXPLORATION")  # fmt: skip
    refusals.append((nothing, baseline_id, "no_matching_cashflows"))
    switched_off: tuple[tuple[str, dict[str, Any]], ...] = (
        ("scenarios_disabled", {"SCENARIOS_ENABLED": False}),
        ("calculations_disabled", {"CALCULATIONS_ENABLED": False}),
    )
    before_all = (calc_counts(db), scenario_run_counts(db))
    for scenario, base_id, code in refusals:
        r, c = run_scenario(api, analyst, scenario, base_id)
        assert (r.status_code, r.json()["code"]) == (409, code), r.text
        [event] = [e for e in events(db, c) if e["action"] == "scenario.run"]
        assert event["outcome"] == "DENIED" and json.loads(event["details"])["reason"] == code
    for code, flags in switched_off:
        settings = Settings(ENV="test", DATABASE_URL=db, ARTIFACT_STORE_DIR=artifact_dir, **flags)
        with TestClient(create_app(settings)) as off:
            r, c = run_scenario(off, analyst, single, baseline_id)
            assert (r.status_code, r.json()["code"]) == (409, code), r.text
            [event] = [e for e in events(db, c) if e["action"] == "scenario.run"]
            assert event["outcome"] == "DENIED" and json.loads(event["details"])["reason"] == code
            # existing stressed runs stay readable
            assert off.get(cpath, params=params, headers=analyst).status_code == 200
    assert (calc_counts(db), scenario_run_counts(db)) == before_all

    # --- after CLOSED, re-running each member reproduces both hashes (D-45 §8) ----------------
    r = api.post(f"{version_path(combined, 1)}/close", json={}, headers=approver)
    assert r.status_code == 200, r.text
    for m in three["members"]:
        again = no_float(start_run(api, analyst, {"rerun_of": m["calculation_run_id"]}).text)
        assert again["status"] == "SUCCEEDED", again
        assert (again["manifest_sha256"], again["result_sha256"]) == (
            m["manifest_sha256"],
            m["result_sha256"],
        ), m["shock_subset"]
        assert again["manifest"]["scenario"]["shock_subset"] == m["shock_subset"]
    return {
        "stressed_run_id": stressed_id,
        "scenario_run_id": one["scenario_run_id"],
        "scenario_id": single["scenario_id"],
    }


def check_lcr_baseline(
    api: TestClient,
    db: str,
    scenario_id: str,
    lcr_run_id: str,
    calc_counts: Callable[[str], dict[str, int]],
) -> None:
    """invalid_baseline for an lcr_hqla run (part 5b, where the LCR run exists)."""
    before = (calc_counts(db), scenario_run_counts(db))
    r, c = run_scenario(api, api_token(api, "risk_analyst"), {"scenario_id": scenario_id},
                        lcr_run_id)  # fmt: skip
    assert (r.status_code, r.json()["code"]) == (409, "invalid_baseline"), r.text
    [event] = [e for e in events(db, c) if e["action"] == "scenario.run"]
    assert event["outcome"] == "DENIED" and json.loads(event["details"])["reason"] == (
        "invalid_baseline"
    )
    assert (calc_counts(db), scenario_run_counts(db)) == before


def clear_scenario_runs(db: str) -> None:
    """Remove the four Phase 25 tables' rows and the Phase 24 scenarios this kit created
    (their user triggers lifted for one transaction each), so the downgrade below 0027 can be
    exercised on the older guards (0026 refuses while a scenario exists)."""
    from app.tests.conftest import _SCENARIO_CLEANUP, _SCENARIO_RUN_CLEANUP, script

    script(db, _SCENARIO_RUN_CLEANUP)
    script(db, _SCENARIO_CLEANUP)


def api_token(api: TestClient, role: str) -> dict[str, str]:
    from app.tests.conftest import dev_token

    return dev_token(api, role)


def _check_largest(view: Mapping[str, Any], currency: str = "INR") -> None:
    """Largest changes are ranked per currency: the rank restarts at 1 in each."""
    cells = _cells(view, currency)
    largest = [x for x in view["largest_changes"] if x["currency"] == currency]
    assert [x["rank"] for x in largest] == list(range(1, len(largest) + 1))
    sizes = [abs(Decimal(x["net_gap_delta"])) for x in largest]
    assert sizes == sorted(sizes, reverse=True)
    for x in largest:
        cell = cells[(x["bucket_code"], tuple(x["shock_subset"]))]
        delta = cell["net_gap"]["delta"]
        assert not is_masked(delta) and delta == x["net_gap_delta"], x
        assert cell["calculation_run_id"] == x["calculation_run_id"]
    clear = sorted(
        (abs(Decimal(c["net_gap"]["delta"])) for c in cells.values()
         if not is_masked(c["net_gap"]["delta"]) and Decimal(c["net_gap"]["delta"]) != 0),
        reverse=True,
    )  # fmt: skip
    assert sizes == clear[: len(sizes)]


# --- part 6: guards (BE-25.1) -------------------------------------------------------------------


def intermediate_value_shape(db: str) -> list[Any]:
    """intermediate_value's columns and constraints, as the catalog states them."""
    columns = sql(
        db,
        "SELECT column_name, data_type, is_nullable, column_default, numeric_precision,"
        " numeric_scale FROM information_schema.columns WHERE table_name = 'intermediate_value'"
        " ORDER BY ordinal_position",
    )
    constraints = sql(
        db,
        "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint"
        " WHERE conrelid = 'intermediate_value'::regclass ORDER BY conname",
    )
    triggers = sql(
        db,
        "SELECT tgname, pg_get_triggerdef(oid) AS d FROM pg_trigger"
        " WHERE tgrelid = 'intermediate_value'::regclass AND NOT tgisinternal ORDER BY tgname",
    )
    return [dict(r) for r in (*columns, *constraints, *triggers)]


def check_scenario_append_only(db: str) -> None:
    """scenario_run refuses UPDATE, DELETE and TRUNCATE."""
    [row] = sql(db, "SELECT * FROM scenario_run ORDER BY id LIMIT 1")
    for statement in (
        "UPDATE scenario_run SET created_by = 'tampered' WHERE id = $1",
        "DELETE FROM scenario_run WHERE id = $1",
    ):
        with pytest.raises(IMMUTABLE):
            sql(db, statement, row["id"])
    with pytest.raises(IMMUTABLE, match="TRUNCATE"):
        sql(db, "TRUNCATE scenario_run CASCADE")
    assert sql(db, "SELECT * FROM scenario_run WHERE id = $1", row["id"]) == [row]
