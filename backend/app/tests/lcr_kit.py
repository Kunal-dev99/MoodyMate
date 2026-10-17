"""CSV builders for the `lcr_hqla` v1 engineering golden case (BE-17.2), for
test_calculations.py. The case itself lives in the domain validation pack (BE-18.2):
app/calculations/golden/lcr_hqla_v1_engineering_cases.json, ENGINEERING — awaiting
domain-expert approval (D-35 §4), never an "approved golden case".
"""

from collections.abc import Sequence
from datetime import date, timedelta

POSITION_COLUMNS = [
    "position_id",
    "instrument_id",
    "account_id",
    "legal_entity_id",
    "as_of_date",
    "balance",
    "currency",
]


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
