"""Phase 30 (BE-30.2 - BE-30.4; D-56): validation status, the result `context` block and the
API boundary cases, folded into existing test functions (D-26, the Phase 30 test budget:
49 -> 49).

- `check_module_validation` (rules fold, `test_rules.py::test_governed_rule_changes_and_pinning`,
  BE-30.2): GET /calculation-modules shows ILLUSTRATIVE on every module version, PLANNED
  included, for every role; `MODULE_VALIDATION` covers every module_version row; a
  non-ILLUSTRATIVE `ValidationRecord` without an approver, `approved_on` and `evidence_ref`
  raises.
- `check_context` (calculations fold, `test_calculations.py::
  test_registry_snapshot_run_and_maturity_ladder`, BE-30.3): the `context` block of a result
  read (run detail, results, maturity ladder, LCR, comparison) equals the run's pins, with the
  `context.units` strings frozen per module; the manifest stays the Feature 03 key set and
  carries neither `validation_status` nor `context` (BE-30.2).
- `check_stored_hashes` (calculations fold, BE-30.2): the Feature 03 run's stored
  manifest_sha256 and result_sha256 equal the canonical hashes recomputed from its stored
  manifest and output rows; neither document carries `validation_status` or `context`.
- `check_lcr_boundaries` and `check_ladder_boundaries` (calculations fold, BE-30.4): the five
  boundary cases through the API, with hand-computed values labelled
  ENGINEERING — awaiting domain-expert approval (D-35 §4, user plan §16). If the code
  disagrees, the assertion reports it; no formula is changed to pass.
"""

import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.calculations.constants import CalculationRunStatus
from app.calculations.service import result_document
from app.core.canonical import canonical_sha256
from app.rules.constants import (
    ILLUSTRATIVE_STATEMENT,
    MATURITY_LADDER,
    MODULE_VALIDATION,
    ValidationRecord,
    ValidationStatus,
    validation_status_of,
)
from app.tests.conftest import dev_token, sql
from app.tests.helpers import IMPORTS, MODULES, RUNS, make_snapshot, project, start_run, upload
from app.tests.lcr_kit import position_csv
from app.tests.scenario_kit import no_float

ENGINEERING = "ENGINEERING — awaiting domain-expert approval"
ROLES = ("platform_admin", "auditor", "data_steward", "risk_analyst", "approver")
ILLUSTRATIVE = ValidationStatus.ILLUSTRATIVE.value
# Feature 03 manifest keys (as scenario_run_kit.F03_MANIFEST_KEYS; D-56 §4: unchanged).
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
# Phase 32 (BE-32.4, D-61 §6): `consolidation` is BLOCKED on every result read.
CONTEXT_KEYS = {
    "units", "currency", "as_of_date", "methodology", "validation_status", "statement",
    "consolidation",
}  # fmt: skip
# Frozen per module code: the exact `context.units` strings, quoted verbatim in the units
# section of docs/methodology/{maturity_ladder,lcr_hqla,scenario_overlay}.md (a comparison
# reads its baseline run's module, maturity_ladder).
_AMOUNT_UNIT = "Exact decimal, in the ISO 4217 currency of its row (no FX conversion)"
_DAYS_UNIT = "Calendar days after the reporting date"
CONTEXT_UNITS: dict[str, dict[str, str | None]] = {
    "maturity_ladder": {"amount": _AMOUNT_UNIT, "ratio": None, "days": _DAYS_UNIT},
    "lcr_hqla": {
        "amount": _AMOUNT_UNIT,
        "ratio": "Dimensionless fraction, 4 dp ROUND_HALF_EVEN",
        "days": _DAYS_UNIT,
    },
}
# Plain decimal text: digits, an optional fraction, never an exponent.
PLAIN_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
# Feature 03 result-document keys (a maturity_ladder run; D-56 §4: unchanged).
F03_RESULT_KEYS = {"status", "liquidity_gap", "contributions", "warnings"}


# --- BE-30.2: the rules fold ---------------------------------------------------------------------


def check_module_validation(api: TestClient, db: str) -> None:
    """ILLUSTRATIVE on every module version, PLANNED included; an unapproved validated record
    raises (D-56 §1-3)."""
    for role in ROLES:
        r = api.get(MODULES, headers=dev_token(api, role))
        assert r.status_code == 200, (role, r.text)
        items = r.json()["items"]
        statuses = {m["implementation_status"] for m in items}
        assert {"AVAILABLE", "PLANNED"} <= statuses, statuses
        for m in items:
            assert m["validation_status"] == ILLUSTRATIVE, (role, m["code"], m["version"])
            assert m["illustrative"] is True, (role, m["code"])
    listed = {(m["code"], m["version"]) for m in items}
    rows = sql(
        db,
        "SELECT m.code, v.version FROM module_version v"
        " JOIN calculation_module m ON m.id = v.module_id",
    )
    stored = {(r["code"], r["version"]) for r in rows}
    assert listed == stored, (listed, stored)
    # The code constant names every module version, each ILLUSTRATIVE (no claim by default).
    assert set(MODULE_VALIDATION) == stored, set(MODULE_VALIDATION) ^ stored
    assert all(isinstance(r, ValidationRecord) for r in MODULE_VALIDATION.values())
    assert {r.status for r in MODULE_VALIDATION.values()} == {ValidationStatus.ILLUSTRATIVE}
    assert all(validation_status_of(c, v) is ValidationStatus.ILLUSTRATIVE for c, v in stored)
    assert validation_status_of("no_such_module", 99) is ValidationStatus.ILLUSTRATIVE
    # D-56 §3: a non-ILLUSTRATIVE record without all of approver, approved_on and evidence_ref
    # fails at construction (so at import of app/rules/constants.py).
    full: dict[str, Any] = {
        "approver": "Named approver (test)",
        "approved_on": date(2026, 10, 24),
        "evidence_ref": "methodology/maturity_ladder.md@v1 + golden pack (test)",
    }
    for status in (ValidationStatus.INTERNAL_VALIDATED, ValidationStatus.REGULATORY_VALIDATED):
        with pytest.raises(ValueError):
            ValidationRecord(status)
        for missing in full:
            partial = {k: v for k, v in full.items() if k != missing}
            with pytest.raises(ValueError):
                ValidationRecord(status, **partial)
            with pytest.raises(ValueError):
                ValidationRecord(status, **{**partial, missing: ""})  # empty is not named
        assert ValidationRecord(status, **full).status is status  # complete: constructs
    assert ValidationRecord(ValidationStatus.ILLUSTRATIVE).approver is None


# --- BE-30.3: the context block, from the pins ---------------------------------------------------


def expected_context(detail: Mapping[str, Any]) -> dict[str, Any]:
    """The context a run's reads must carry, from its pins only (manifest and outcome)."""
    manifest = detail["manifest"]
    module = manifest["module"]
    assert module["engine_version"] == manifest["engine_version"], module
    return {
        "currency": {
            "basis": "PER_CURRENCY_NO_FX",
            "codes": sorted(detail["outcome"]["currencies"]),
        },
        "as_of_date": manifest["reporting_date"],
        "methodology": {
            "module_code": module["code"],
            "module_version": module["version"],
            "engine_version": manifest["engine_version"],
            "rule_versions": sorted(
                (
                    {
                        "rule_code": m["rule_code"],
                        "rule_version_id": m["rule_version_id"],
                        "version": m["version"],
                    }
                    for m in manifest["rule_set"]["members"]
                ),
                key=lambda m: m["rule_code"],
            ),
        },
        "validation_status": ILLUSTRATIVE,
        "statement": ILLUSTRATIVE_STATEMENT,
        "consolidation": "BLOCKED",
    }


def check_context(read: Mapping[str, Any], detail: Mapping[str, Any], what: str) -> None:
    """`read["context"]` equals the pins of the run `detail` describes; the wrapper's
    validation_status is ILLUSTRATIVE; the hashed manifest is the Feature 03 key set."""
    assert read["validation_status"] == ILLUSTRATIVE, what
    context = read["context"]
    assert set(context) == CONTEXT_KEYS, (what, set(context))
    want = expected_context(detail)
    assert {k: context[k] for k in want} == want, (what, context, want)
    units = context["units"]
    code = detail["manifest"]["module"]["code"]
    assert units == CONTEXT_UNITS[code], (what, code, units)  # literal, per module
    is_ladder = code == MATURITY_LADDER
    assert (units["ratio"] is None) is is_ladder, (what, units)  # only lcr_hqla has a ratio
    manifest = detail["manifest"]
    if "scenario" not in manifest:
        assert set(manifest) == F03_MANIFEST_KEYS, (what, set(manifest))
    text = json.dumps(manifest)
    assert "validation_status" not in text and '"context"' not in text, what


def check_stored_hashes(db: str, run_id: str, detail: Mapping[str, Any]) -> None:
    """BE-30.2: the stored manifest_sha256 and result_sha256 of a run made before the
    response wrapper changed (the Feature 03 maturity_ladder run) still equal the canonical
    hashes of its stored documents, recomputed here from the stored rows (result_document's
    shape, D-31 §4). Neither document carries `validation_status` or `context`."""
    rid = uuid.UUID(run_id)
    [run] = sql(
        db,
        "SELECT manifest::text AS m, manifest_sha256, result_sha256, status"
        " FROM calculation_run WHERE id = $1",
        rid,
    )
    manifest = json.loads(run["m"])
    assert canonical_sha256(manifest) == run["manifest_sha256"] == detail["manifest_sha256"]
    gap = sql(
        db,
        "SELECT currency, bucket_code, bucket_sequence, inflows::text, outflows::text,"
        " net_gap::text, cumulative_gap::text, inflows_contractual::text,"
        " inflows_behavioural::text, outflows_contractual::text, outflows_behavioural::text,"
        " contribution_count FROM liquidity_gap_result WHERE calculation_run_id = $1"
        " ORDER BY currency, bucket_sequence",
        rid,
    )
    contributions = sql(
        db,
        "SELECT v.cashflow_event_id::text, v.instrument_version_id::text, g.currency,"
        " v.bucket_code, v.direction, v.origin, v.value::text, v.days_after_reporting_date,"
        " v.rule_version_ids::text FROM intermediate_value v"
        " JOIN liquidity_gap_result g ON g.id = v.liquidity_gap_result_id"
        " WHERE v.calculation_run_id = $1 ORDER BY v.sequence",
        rid,
    )
    warnings = sql(
        db,
        "SELECT code, severity, entity_type, entity_id::text, details::text"
        " FROM calculation_warning WHERE calculation_run_id = $1 ORDER BY sequence",
        rid,
    )
    document = {
        "status": run["status"],
        "liquidity_gap": [dict(g) for g in gap],
        "contributions": [
            {**dict(c), "rule_version_ids": json.loads(c["rule_version_ids"])}
            for c in contributions
        ],
        "warnings": [{**dict(w), "details": json.loads(w["details"])} for w in warnings],
    }
    # The recomputed document has result_document's shape: its keys, and each row's.
    shape = result_document(CalculationRunStatus(run["status"]), [], [], [])
    assert set(document) == set(shape) == F03_RESULT_KEYS, set(document)
    assert canonical_sha256(document) == run["result_sha256"] == detail["result_sha256"]
    for text in (json.dumps(manifest), json.dumps(document)):
        assert '"validation_status"' not in text and '"context"' not in text


# --- BE-30.4: API boundary cases -----------------------------------------------------------------

# lcr_hqla v1 under the 0020 seed (BOND L1 haircut 0, DEPOSIT run-off 0.10, LOAN inflow 0.50
# within (0, 30], cap 0.75), ratio = HQLA / net outflows at 4 dp ROUND_HALF_EVEN.
# currency -> (bond balance, deposit balance or None, LOAN cash flow at day 10 or None,
#              the hand-computed figures)
LCR_BOUNDARY: dict[str, dict[str, Any]] = {
    # 20001.00 / (200000.00 x 0.10) = 1.00005: a tie, HALF_EVEN rounds down to the even
    # 1.0000 (HALF_UP would give 1.0001).
    "EUR": {
        "status": ENGINEERING,
        "bond": "20001.00",
        "deposit": "200000.00",
        "loan_flow": None,
        "expected": {
            "hqla_total": "20001.00",
            "outflows": "20000",
            "inflows_before_cap": "0",
            "inflow_cap_amount": "15000",
            "inflows_after_cap": "0",
            "net_outflows": "20000",
            "ratio": "1.0000",
            "inflow_cap_applied": False,
            "warnings": [],
        },
    },
    # 20003.00 / 20000 = 1.00015: a tie, HALF_EVEN rounds up to the even 1.0002 (HALF_DOWN
    # would give 1.0001).
    "USD": {
        "status": ENGINEERING,
        "bond": "20003.00",
        "deposit": "200000.00",
        "loan_flow": None,
        "expected": {
            "hqla_total": "20003.00",
            "outflows": "20000",
            "inflows_before_cap": "0",
            "inflow_cap_amount": "15000",
            "inflows_after_cap": "0",
            "net_outflows": "20000",
            "ratio": "1.0002",
            "inflow_cap_applied": False,
            "warnings": [],
        },
    },
    # Inflows exactly at the cap: 150.00 x 0.50 = 75.00 = 0.75 x (1000.00 x 0.10). The cap
    # does not bind (inflows are not above it): 100.00 / (100 - 75) = 4.0000.
    "GBP": {
        "status": ENGINEERING,
        "bond": "100.00",
        "deposit": "1000.00",
        "loan_flow": "150.00",
        "expected": {
            "hqla_total": "100.00",
            "outflows": "100",
            "inflows_gross": "150.00",
            "inflows_before_cap": "75",
            "inflow_cap_amount": "75",
            "inflows_after_cap": "75",
            "net_outflows": "25",
            "ratio": "4.0000",
            "inflow_cap_applied": False,
            "warnings": [],
        },
    },
    # One minor unit above the cap: 150.02 x 0.50 = 75.01 > 75.00, capped to 75.00.
    "CHF": {
        "status": ENGINEERING,
        "bond": "100.00",
        "deposit": "1000.00",
        "loan_flow": "150.02",
        "expected": {
            "hqla_total": "100.00",
            "outflows": "100",
            "inflows_gross": "150.02",
            "inflows_before_cap": "75.01",
            "inflow_cap_amount": "75",
            "inflows_after_cap": "75",
            "net_outflows": "25",
            "ratio": "4.0000",
            "inflow_cap_applied": True,
            "warnings": [],
        },
    },
    # Zero net outflows (HQLA and no outflow): JSON ratio null with LCR_DENOMINATOR_ZERO.
    "SEK": {
        "status": ENGINEERING,
        "bond": "100.00",
        "deposit": None,
        "loan_flow": None,
        "expected": {
            "hqla_total": "100.00",
            "outflows": "0",
            "inflows_before_cap": "0",
            "inflow_cap_amount": "0",
            "inflows_after_cap": "0",
            "net_outflows": "0",
            "ratio": None,
            "inflow_cap_applied": False,
            "warnings": ["LCR_DENOMINATOR_ZERO"],
        },
    },
}
DECIMAL_FIGURES = (
    "hqla_total",
    "outflows",
    "inflows_gross",
    "inflows_before_cap",
    "inflow_cap_amount",
    "inflows_after_cap",
    "net_outflows",
)
# The ladder boundary (maturity_ladder v1): the largest amount NUMERIC(20,2) holds.
BIG_AMOUNT = "999999999999999999.99"
THREE_DP_AMOUNT = "1.235"  # a non-zero third decimal: over scale 2, held, never rounded
TRAILING_ZERO_AMOUNT = "1.230"  # quantizes exactly to scale 2: accepted as 1.23


def check_lcr_boundaries(
    api: TestClient,
    *,
    load: Callable[..., dict[str, Any]],
    instrument_source: Mapping[str, Any],
    position_source: Mapping[str, Any],
    cash_source: Mapping[str, Any],
    instrument_csv: Callable[[Sequence[tuple[str, ...]]], bytes],
    cashflow_csv: Callable[[Sequence[tuple[Any, ...]], date], bytes],
    lcr_module_id: str,
    reporting: date,
) -> None:
    """The LCR boundary cases of BE-30.4 in one lcr_hqla run, one currency per case."""
    analyst, approver = dev_token(api, "risk_analyst"), dev_token(api, "approver")
    instruments: list[tuple[str, ...]] = []
    positions: list[tuple[str, ...]] = []
    flows: list[tuple[Any, ...]] = []
    for cur, case in LCR_BOUNDARY.items():
        assert case["status"] == ENGINEERING, cur
        instruments.append((f"BX-BOND-{cur}", "BOND", cur, "1.00"))
        positions.append((f"BXP-B-{cur}", f"BX-BOND-{cur}", case["bond"], cur))
        if case["deposit"] is not None:
            instruments.append((f"BX-DEP-{cur}", "DEPOSIT", cur, "1.00"))
            positions.append((f"BXP-D-{cur}", f"BX-DEP-{cur}", case["deposit"], cur))
        if case["loan_flow"] is not None:
            instruments.append((f"BX-LOAN-{cur}", "LOAN", cur, "1.00"))
            flows.append(
                (
                    f"BXF-{cur}",
                    f"BX-LOAN-{cur}",
                    10,
                    case["loan_flow"],
                    cur,
                    "PRINCIPAL",
                    "CONTRACTUAL",
                )  # fmt: skip
            )
    imports = [
        load(instrument_source, instrument_csv(instruments)),
        load(position_source, position_csv(positions, reporting)),
        load(cash_source, cashflow_csv(flows, reporting)),
    ]
    steward = dev_token(api, "data_steward")
    for imported in imports:
        assert project(api, steward, imported["id"]).status_code == 201
    r = make_snapshot(api, analyst, [i["id"] for i in imports])
    assert r.status_code == 201, r.text
    body = {
        "dataset_snapshot_id": r.json()["snapshot_id"],
        "module_version_id": lcr_module_id,
        "reporting_date": reporting.isoformat(),
    }
    r = start_run(api, analyst, body)
    assert r.status_code == 201, r.text
    run = no_float(r.text)
    assert run["status"] == "SUCCEEDED_WITH_WARNINGS", run  # SEK: LCR_DENOMINATOR_ZERO
    r = api.get(f"{RUNS}/{run['run_id']}/liquidity", headers=approver)
    assert r.status_code == 200, r.text
    liquidity = no_float(r.text)
    check_context(liquidity, run, "liquidity (boundary run)")
    got = {c["currency"]: c for c in liquidity["currencies"]}
    assert set(got) == set(LCR_BOUNDARY), set(got)
    for cur, case in LCR_BOUNDARY.items():
        want, row = case["expected"], got[cur]
        for figure in DECIMAL_FIGURES:
            if figure in want:
                # Decimal equality: figures keep the product's scale (e.g. 75.0000000000).
                assert Decimal(row[figure]) == Decimal(want[figure]), (ENGINEERING, cur, figure)
                assert PLAIN_DECIMAL.match(row[figure]), (cur, figure, row[figure])
        assert row["ratio"] == want["ratio"], (ENGINEERING, cur, row["ratio"], want["ratio"])
        assert row["inflow_cap_applied"] is want["inflow_cap_applied"], (ENGINEERING, cur)
        assert row["warnings"] == want["warnings"], (ENGINEERING, cur, row["warnings"])
    # Zero net outflows: the JSON value is null, not "0" and not absent.
    [sek] = [c for c in json.loads(r.text)["currencies"] if c["currency"] == "SEK"]
    assert "ratio" in sek and sek["ratio"] is None


def check_ladder_boundaries(
    api: TestClient,
    db: str,
    *,
    load: Callable[..., dict[str, Any]],
    instrument_source: Mapping[str, Any],
    cash_source: Mapping[str, Any],
    instrument_csv: Callable[[Sequence[tuple[str, ...]]], bytes],
    cashflow_csv: Callable[[Sequence[tuple[Any, ...]], date], bytes],
    module_id: str,
) -> None:
    """BE-30.4: 999999999999999999.99 stays exact through the ladder; a 3-dp amount is held
    at validation and never rounded."""
    analyst, approver = dev_token(api, "risk_analyst"), dev_token(api, "approver")
    steward = dev_token(api, "data_steward")
    reporting = date(2027, 6, 30)
    instruments = load(instrument_source, instrument_csv([("BIG-LOAN", "LOAN", "NOK", "1.00")]))
    big = load(
        cash_source,
        cashflow_csv(
            [("BIG-CF", "BIG-LOAN", 10, BIG_AMOUNT, "NOK", "PRINCIPAL", "CONTRACTUAL")], reporting
        ),
    )
    for imported in (instruments, big):
        assert project(api, steward, imported["id"]).status_code == 201
    snapshot = make_snapshot(api, analyst, [instruments["id"], big["id"]])
    assert snapshot.status_code == 201, snapshot.text
    body = {
        "dataset_snapshot_id": snapshot.json()["snapshot_id"],
        "module_version_id": module_id,
        "reporting_date": reporting.isoformat(),
    }
    r = start_run(api, analyst, body)
    assert r.status_code == 201, r.text
    run = no_float(r.text)
    assert run["status"] == "SUCCEEDED", run
    r = api.get(f"{RUNS}/{run['run_id']}/maturity-ladder", headers=approver)
    assert r.status_code == 200, r.text
    ladder = no_float(r.text)
    check_context(ladder, run, "maturity-ladder (boundary run)")
    [nok] = ladder["currencies"]
    assert nok["currency"] == "NOK"
    [placed] = [row for row in nok["rows"] if row["contribution_count"] == 1]
    # ENGINEERING — awaiting domain-expert approval: one inflow, no outflow, exact text.
    for figure in ("inflows", "inflows_contractual", "net_gap", "cumulative_gap"):
        assert placed[figure] == BIG_AMOUNT, (ENGINEERING, figure, placed[figure])
    assert Decimal(placed["outflows"]) == 0
    after = nok["rows"][nok["rows"].index(placed) :]
    assert all(row["cumulative_gap"] == BIG_AMOUNT for row in after), after
    results = no_float(api.get(f"{RUNS}/{run['run_id']}/results", headers=approver).text)
    check_context(results, run, "results (boundary run)")
    assert [i["value"] for i in results["items"]] == [BIG_AMOUNT]
    stored = sql(
        db,
        "SELECT inflows::text AS i, cumulative_gap::text AS c FROM liquidity_gap_result"
        " WHERE calculation_run_id = $1 AND inflows <> 0",
        uuid.UUID(run["run_id"]),
    )
    assert [(s["i"], s["c"]) for s in stored] == [(BIG_AMOUNT, BIG_AMOUNT)]

    # A 3-dp amount with a non-zero third decimal: held at validation (SCALE_EXCEEDED,
    # QUARANTINED), never rounded. A 3-dp amount whose third decimal is 0 quantizes exactly
    # and is accepted at scale 2. The cash flow source's mapping is ACTIVE already (load), so
    # upload and validate directly.
    held_csv = cashflow_csv(
        [
            ("TDP-1", "BIG-LOAN", 5, THREE_DP_AMOUNT, "NOK", "PRINCIPAL", "CONTRACTUAL"),
            ("TDP-2", "BIG-LOAN", 6, TRAILING_ZERO_AMOUNT, "NOK", "PRINCIPAL", "CONTRACTUAL"),
        ],
        reporting,
    )
    r = upload(api, steward, cash_source["id"], held_csv, filename="three_dp.csv")
    assert r.status_code == 201, r.text
    held_id = r.json()["id"]
    r = api.post(f"{IMPORTS}/{held_id}/validate", headers=steward)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "QUARANTINED", r.json()
    issues = api.get(f"{IMPORTS}/{held_id}/issues", headers=steward).json()["items"]
    assert [(i["code"], i["field"]) for i in issues] == [("SCALE_EXCEEDED", "amount")], issues
    assert THREE_DP_AMOUNT not in json.dumps(issues)  # messages are value-free
    raw = sql(
        db,
        "SELECT s.ordinal, s.raw_values->>'amount' AS amount FROM source_record s"
        " JOIN import_batch b ON b.id = s.batch_id WHERE b.import_job_id = $1 ORDER BY s.ordinal",
        uuid.UUID(held_id),
    )
    assert [x["amount"] for x in raw] == [THREE_DP_AMOUNT, TRAILING_ZERO_AMOUNT], raw  # exact
    canonical = sql(
        db,
        "SELECT ordinal, payload->>'amount' AS amount FROM canonical_record"
        " WHERE import_job_id = $1",
        uuid.UUID(held_id),
    )
    # Only the trailing-zero row is canonical, as 1.23; the held 1.235 row is never stored,
    # rounded or otherwise (HALF_EVEN would give 1.24).
    held_ordinal, zero_ordinal = (x["ordinal"] for x in raw)
    assert {c["ordinal"]: c["amount"] for c in canonical} == {zero_ordinal: "1.23"}, (
        held_ordinal,
        canonical,
    )
