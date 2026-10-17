"""The `lcr_hqla` v1 engineering golden case (BE-17.2) and its CSV builders, for
test_calculations.py. Hand-computed against the D-35 ILLUSTRATIVE seed:

- BOND is Level 1 with a 0 haircut; any other type is not HQLA;
- DEPOSIT has a 0.10 run-off;
- LOAN cash flows due within 30 days after the reporting date flow in at 0.50, capped at
  0.75 x outflows;
- ratio = HQLA / (outflows - inflows after the cap), 4 dp ROUND_HALF_EVEN.

ENGINEERING — awaiting domain-expert approval (D-35 §4), never an "approved golden case".
"""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

POSITION_COLUMNS = [
    "position_id",
    "instrument_id",
    "account_id",
    "legal_entity_id",
    "as_of_date",
    "balance",
    "currency",
]

REPORTING_DATE = date(2026, 12, 31)

LCR_GOLDEN: dict[str, Any] = {
    "name": "lcr_hqla v1 engineering cases",
    "status": "ENGINEERING — awaiting domain-expert approval",
    "reporting_date": REPORTING_DATE,
    # instrument_id, instrument_type, currency, notional
    "instruments": [
        ("LQ-BOND-E1", "BOND", "EUR", "1.00"),
        ("LQ-BOND-E2", "BOND", "EUR", "1.00"),
        ("LQ-BOND-E3", "BOND", "EUR", "1.00"),
        ("LQ-LOAN-E", "LOAN", "EUR", "1.00"),
        ("LQ-DEP-E1", "DEPOSIT", "EUR", "1.00"),
        ("LQ-DEP-E2", "DEPOSIT", "EUR", "1.00"),
        ("LQ-DEP-E3", "DEPOSIT", "EUR", "1.00"),
        ("LQ-BOND-U", "BOND", "USD", "1.00"),
        ("LQ-LOAN-U", "LOAN", "USD", "1.00"),
        ("LQ-BOND-G", "BOND", "GBP", "1.00"),
        ("LQ-LOAN-G", "LOAN", "GBP", "1.00"),
        ("LQ-DEP-G", "DEPOSIT", "GBP", "1.00"),
    ],
    # position_id, instrument_id, balance, currency
    "positions": [
        ("LP-E1", "LQ-BOND-E1", "200.00", "EUR"),  # L1
        ("LP-E2", "LQ-BOND-E2", "40.00", "EUR"),  # L1
        ("LP-E3", "LQ-BOND-E3", "6.81", "EUR"),  # L1
        ("LP-E4", "LQ-LOAN-E", "5000.00", "EUR"),  # an ineligible asset (LOAN is not HQLA)
        ("LP-E5", "LQ-DEP-E1", "5000.00", "EUR"),  # run-off 500.0000
        ("LP-E6", "LQ-DEP-E2", "2000.00", "EUR"),  # run-off 200.0000
        ("LP-E7", "LQ-DEP-E3", "1000.00", "EUR"),  # run-off 100.0000
        ("LP-U1", "LQ-BOND-U", "300.00", "USD"),  # L1; USD has no outflows
        ("LP-G1", "LQ-BOND-G", "700.00", "GBP"),  # L1
        ("LP-G2", "LQ-DEP-G", "1000.00", "GBP"),  # run-off 100.0000
    ],
    # cashflow_id, instrument_id, days after the reporting date, amount, currency, type, origin
    "cash_flows": [
        ("LCF-E1", "LQ-LOAN-E", 1, "1000.00", "EUR", "PRINCIPAL", "CONTRACTUAL"),
        ("LCF-E2", "LQ-LOAN-E", 15, "300.00", "EUR", "PRINCIPAL", "CONTRACTUAL"),
        ("LCF-E3", "LQ-LOAN-E", 30, "200.00", "EUR", "INTEREST", "CONTRACTUAL"),  # horizon edge
        ("LCF-E4", "LQ-LOAN-E", 31, "999.00", "EUR", "PRINCIPAL", "CONTRACTUAL"),  # beyond it
        ("LCF-U1", "LQ-LOAN-U", 5, "400.00", "USD", "PRINCIPAL", "CONTRACTUAL"),
        ("LCF-G1", "LQ-LOAN-G", 10, "60.00", "GBP", "PRINCIPAL", "CONTRACTUAL"),
    ],
    # Per currency, hand-computed (D-35 seed):
    # EUR: HQLA 200.00 + 40.00 + 6.81 = 246.81 (LP-E4 excluded, not eligible);
    #      outflows (5000 + 2000 + 1000) x 0.10 = 800; inflows (1000 + 300 + 200) x 0.50 = 750
    #      before the cap, 0.75 x 800 = 600 after it (the cap binds); LCF-E4 is past the
    #      horizon; ratio 246.81 / (800 - 600) = 1.23405 -> 1.2340 (HALF_EVEN; HALF_UP 1.2341).
    # USD: HQLA 300.00; no outflows, so the cap is 0: inflows 200 before, 0 after; the
    #      denominator is 0: NULL ratio and LCR_DENOMINATOR_ZERO.
    # GBP: HQLA 700.00; outflows 100; inflows 30 (the cap, 75, does not bind); ratio
    #      700 / 70 = 10.0000.
    # Per currency: the figures of GET /calculation-runs/{id}/liquidity (compared as Decimal;
    # the ratio also as exact text), then the counts and the LCR warnings.
    "lcr": {
        "EUR": {
            "hqla_total": "246.81",
            "levels": {"L1": "246.81", "L2A": "0", "L2B": "0"},
            "outflow_balance": "8000.00",
            "outflows": "800",
            "inflows_gross": "1500.00",
            "inflows_before_cap": "750",
            "inflow_cap_rate": "0.75",
            "inflow_cap_amount": "600",
            "inflows_after_cap": "600",
            "inflow_cap_applied": True,
            "net_outflows": "200",
            "ratio": "1.2340",
            "counts": {"excluded_asset_count": 1, "outflow_count": 3, "inflow_count": 3},
            "warnings": [],
        },
        "GBP": {
            "hqla_total": "700.00",
            "levels": {"L1": "700.00", "L2A": "0", "L2B": "0"},
            "outflow_balance": "1000.00",
            "outflows": "100",
            "inflows_gross": "60.00",
            "inflows_before_cap": "30",
            "inflow_cap_rate": "0.75",
            "inflow_cap_amount": "75",
            "inflows_after_cap": "30",
            "inflow_cap_applied": False,
            "net_outflows": "70",
            "ratio": "10.0000",
            "counts": {"excluded_asset_count": 0, "outflow_count": 1, "inflow_count": 1},
            "warnings": [],
        },
        "USD": {
            "hqla_total": "300.00",
            "levels": {"L1": "300.00", "L2A": "0", "L2B": "0"},
            "outflow_balance": "0",
            "outflows": "0",
            "inflows_gross": "400.00",
            "inflows_before_cap": "200",
            "inflow_cap_rate": "0.75",
            "inflow_cap_amount": "0",
            "inflows_after_cap": "0",
            "inflow_cap_applied": True,
            "net_outflows": "0",
            "ratio": None,
            "counts": {"excluded_asset_count": 0, "outflow_count": 0, "inflow_count": 1},
            "warnings": ["LCR_DENOMINATOR_ZERO"],
        },
    },
    # Inputs used: 6 asset rows, 4 outflow positions, 5 inflows; LCF-E4 is out of scope.
    "outcome": {"in_scope": 16, "bucketed": 15, "excluded": 0, "out_of_scope": 1},
    "included_assets": {
        "EUR": {"LP-E1", "LP-E2", "LP-E3"},
        "USD": {"LP-U1"},
        "GBP": {"LP-G1"},
    },
    "excluded_assets": {"LP-E4"},
    # D-33 minimum aggregation (CALC_MIN_AGGREGATE_COUNT 3): EUR's figures each rest on 3
    # contributions, so they are clear for risk_analyst. USD's and GBP's non-zero figures rest
    # on 1 and are masked, with everything derived from them; zeros and a NULL ratio stay
    # clear. Asset rows are masked (balance, value) whatever the count.
    "masked_for_internal": {
        "EUR": set(),
        "USD": {"hqla_total", "L1", "inflows_gross", "inflows_before_cap", "inflow_cap_applied"},
        "GBP": {
            "hqla_total",
            "L1",
            "outflow_balance",
            "outflows",
            "inflows_gross",
            "inflows_before_cap",
            "inflow_cap_amount",
            "inflows_after_cap",
            "inflow_cap_applied",
            "net_outflows",
            "ratio",
        },
    },
}


def position_csv(rows: Sequence[tuple[str, ...]], as_of: date) -> bytes:
    lines = [",".join(POSITION_COLUMNS)]
    for key, instrument, balance, currency in rows:
        lines.append(
            ",".join([key, instrument, f"ACC-{key}", "LE-1", as_of.isoformat(), balance, currency])
        )
    return ("\n".join(lines) + "\n").encode()


def due(reporting: date, days: int) -> str:
    return (reporting + timedelta(days=days)).isoformat()


LCR_OUTPUT_TABLES = ("hqla_asset_result", "lcr_result")
