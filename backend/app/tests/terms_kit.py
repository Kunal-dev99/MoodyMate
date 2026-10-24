"""Phase 32 (BE-32.1 – BE-32.4) helpers for the folds in test_domain.py and
test_ws1_verification.py (D-26: no new test function, budget 49 -> 49).

- `check_rate_terms_contract`: INSTRUMENT_RATE_TERMS is the typed-maturity contract plus the
  eleven optional D-61 §1 fields, its hash frozen in code.
- `publish_rate_terms`: proposed through the governed path (ADDITIVE: only new optional fields)
  and approved by a second person (D-57 §3).
- `RATE_TERMS_CSV`: a fixed and a floating loan with every term, the three TERMS_INCONSISTENT
  cases, and one unknown vocabulary value; `LATER_CSV` re-imports the fixed loan later with an
  earlier `effective_from` (BE-32.3), and the floating loan with floor > cap (no term row).
- `check_term_seed`, `check_terms_append_only`: the 0031 seed and guards (conftest's
  `_TERMS_CLEANUP` removes the non-seed 0031 rows).

The Phase 32 names are read from `app.domain.constants` when called (not at import), so this
module and its callers still collect while the implementation lands.
"""

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.domain import constants as domain_constants
from app.governance.service import FieldSpec, contract_sha256
from app.tests.conftest import sql
from app.tests.helpers import INSTRUMENT_RULES, PROPOSALS, mrule, schema_id
from app.tests.identity_kit import KEEP

CURRENCIES = "/api/v1/currencies"
FX_STATUS = "/api/v1/fx/consolidation-status"
TERM_TABLES = (
    "instrument_term_version",
    "instrument_term_vocabulary",
    "currency",
    "fx_rate_observation",
)
SEEDED_TERM_TABLES = ("instrument_term_vocabulary", "currency")
# D-61 §1: the optional fields INSTRUMENT_RATE_TERMS adds, as (data type, precision, scale).
RATE_TERM_FIELDS: dict[str, tuple[str, int | None, int | None]] = {
    "rate_type": ("STRING", None, None),
    "reference_index": ("STRING", None, None),
    "spread": ("DECIMAL", 11, 6),
    "reset_frequency": ("STRING", None, None),
    "next_reset_date": ("DATE", None, None),
    "cap_rate": ("DECIMAL", 11, 6),
    "floor_rate": ("DECIMAL", 11, 6),
    "day_count": ("STRING", None, None),
    "payment_frequency": ("STRING", None, None),
    "effective_from": ("DATE", None, None),
    "lifecycle_state": ("STRING", None, None),
}
# D-61 §3: the seeded PROVISIONAL vocabularies; no reference index is seeded.
TERM_VOCABULARIES = {
    "RATE_TYPE": {"FIXED", "FLOATING"},
    "DAY_COUNT": {"ACT_360", "ACT_365F", "30_360", "ACT_ACT"},
    "FREQUENCY": {"P1M", "P3M", "P6M", "P1Y"},
    "LIFECYCLE_STATE": {"ACTIVE", "MATURED", "PREPAID", "RESTRUCTURED", "DEFAULTED"},
}
# The currencies the clean synthetic samples use (instrument, position, cash flow, credit).
SAMPLE_CURRENCIES = {"CHF", "EUR", "GBP", "USD"}

RATE_TERMS_RULES = [
    *INSTRUMENT_RULES,
    mrule("rate_type", "rate_type"),
    mrule("reference_index", "reference_index"),
    mrule("spread", "spread", "PARSE_DECIMAL"),
    mrule("reset_frequency", "reset_frequency"),
    mrule("next_reset_date", "next_reset_date", "PARSE_DATE", params={"format": "YYYY-MM-DD"}),
    mrule("cap_rate", "cap_rate", "PARSE_DECIMAL"),
    mrule("floor_rate", "floor_rate", "PARSE_DECIMAL"),
    mrule("day_count", "day_count"),
    mrule("payment_frequency", "payment_frequency"),
    mrule("effective_from", "effective_from", "PARSE_DATE", params={"format": "YYYY-MM-DD"}),
    mrule("lifecycle_state", "lifecycle_state"),
]
RATE_TERMS_COLUMNS = (
    "instrument_id",
    "instrument_type",
    "currency",
    "counterparty_id",
    "product_family",
    "notional",
    "interest_rate",
    "maturity_date",
    "book_code",
    "rate_type",
    "reference_index",
    "spread",
    "reset_frequency",
    "next_reset_date",
    "cap_rate",
    "floor_rate",
    "day_count",
    "payment_frequency",
    "effective_from",
    "lifecycle_state",
)
_HEAD = ",".join(RATE_TERMS_COLUMNS).encode() + b"\n"
_BASE = b"LOAN,EUR,CP-1,CORPORATE,1000.00"
# RT-FIX: FIXED 7.250000 ACT_365F P3M. RT-FLT: floating, every term. RT-IDX: FIXED with an
# index; RT-NRS: FLOATING with no reset; RT-FGC: floor > cap (all TERMS_INCONSISTENT). RT-UNK:
# a day count outside the vocabulary (kept as text, UNKNOWN_VALUE).
RATE_TERMS_CSV = _HEAD + b"".join(
    [
        b"RT-FIX," + _BASE + b",7.250000,2031-03-31,BK-1,FIXED,,,,,,,ACT_365F,P3M,2026-01-01,"
        b"ACTIVE\n",
        b"RT-FLT," + _BASE + b",3.100000,2031-03-31,BK-1,FLOATING,EURIBOR_3M,1.250000,P3M,"
        b"2026-04-01,6.000000,0.500000,ACT_360,P3M,2026-01-01,ACTIVE\n",
        b"RT-IDX," + _BASE + b",4.000000,2031-03-31,BK-1,FIXED,EURIBOR_3M,,,,,,ACT_365F,P3M,"
        b"2026-01-01,ACTIVE\n",
        b"RT-NRS," + _BASE + b",4.000000,2031-03-31,BK-1,FLOATING,EURIBOR_3M,1.000000,,,"
        b"6.000000,0.500000,ACT_360,P3M,2026-01-01,ACTIVE\n",
        b"RT-FGC," + _BASE + b",4.000000,2031-03-31,BK-1,FLOATING,EURIBOR_3M,1.000000,P3M,"
        b"2026-04-01,2.000000,3.000000,ACT_360,P3M,2026-01-01,ACTIVE\n",
        b"RT-UNK," + _BASE + b",5.000000,2031-03-31,BK-1,FIXED,,,,,,,ACT_364,P1Y,2026-01-01,"
        b"ACTIVE\n",
    ]
)
CONSISTENT = ("RT-FIX", "RT-FLT", "RT-UNK")
INCONSISTENT = ("RT-IDX", "RT-NRS", "RT-FGC")
# BE-32.3: imported after RATE_TERMS_CSV. RT-FIX is effective from an earlier business date;
# RT-FLT restates its terms from 2026-03-01 with floor > cap (TERMS_INCONSISTENT: no term row,
# so that date is not stored and the `?effective_on=` pick fails closed).
LATER_CSV = (
    _HEAD
    + b"RT-FIX,"
    + _BASE
    + b",7.000000,2031-03-31,BK-1,FIXED,,,,,,,ACT_365F,P3M,2025-06-01,ACTIVE\n"
    + b"RT-FLT,"
    + _BASE
    + b",3.100000,2031-03-31,BK-1,FLOATING,EURIBOR_3M,1.250000,P3M,2026-04-01,2.000000,"
    + b"3.000000,ACT_360,P3M,2026-03-01,ACTIVE\n"
)
# The expected terms as the clear read shows them (exact decimal and ISO date text).
_NO_TERMS = dict.fromkeys(
    (f for f in RATE_TERM_FIELDS if f != "effective_from"),
    None,
)
EXPECTED_TERMS: dict[str, dict[str, Any]] = {
    "RT-FIX": {
        **_NO_TERMS,
        "rate_type": "FIXED",
        "day_count": "ACT_365F",
        "payment_frequency": "P3M",
        "lifecycle_state": "ACTIVE",
    },
    "RT-FLT": {
        "rate_type": "FLOATING",
        "reference_index": "EURIBOR_3M",
        "spread": "1.250000",
        "reset_frequency": "P3M",
        "next_reset_date": "2026-04-01",
        "cap_rate": "6.000000",
        "floor_rate": "0.500000",
        "day_count": "ACT_360",
        "payment_frequency": "P3M",
        "lifecycle_state": "ACTIVE",
    },
    "RT-UNK": {
        **_NO_TERMS,
        "rate_type": "FIXED",
        "day_count": "ACT_364",
        "payment_frequency": "P1Y",
        "lifecycle_state": "ACTIVE",
    },
}


def c(name: str) -> Any:
    """A Phase 32 name of app.domain.constants (an AttributeError names a missing one)."""
    return getattr(domain_constants, name)


def check_rate_terms_contract() -> list[dict[str, Any]]:
    """D-61 §1: INSTRUMENT_RATE_TERMS is INSTRUMENT_TYPED_MATURITY (unchanged, in order) plus
    the eleven optional fields, typed as the plan says; its hash is frozen in code."""
    contract = [dict(f) for f in c("INSTRUMENT_RATE_TERMS")]
    typed = [{k: f[k] for k in KEEP} for f in domain_constants.INSTRUMENT_TYPED_MATURITY]
    n = len(typed)
    assert [{k: f[k] for k in KEEP} for f in contract[:n]] == typed
    added = {str(f["name"]): f for f in contract[n:]}
    assert set(added) == set(RATE_TERM_FIELDS), sorted(added)
    for name, (data_type, precision, scale) in RATE_TERM_FIELDS.items():
        f = added[name]
        assert (f["data_type"], f["precision"], f["scale"], f["required"]) == (
            data_type,
            precision,
            scale,
            False,
        ), name
    frozen = [FieldSpec(**{k: f[k] for k in KEEP}) for f in contract]
    assert contract_sha256(frozen) == c("INSTRUMENT_RATE_TERMS_SHA256")
    return contract


def publish_rate_terms(
    api: TestClient, steward: dict[str, str], approver: dict[str, str], db: str
) -> dict[str, Any]:
    """Propose INSTRUMENT_RATE_TERMS on the ACTIVE typed-maturity version: ADDITIVE (only new
    optional fields), submitted, approved by a second person. Returns the APPLIED proposal."""
    sid = schema_id(db, "instrument")
    body = {
        "schema_id": str(sid),
        "title": c("INSTRUMENT_RATE_TERMS_TITLE"),
        "fields": [dict(f) for f in c("INSTRUMENT_RATE_TERMS")],
    }
    r = api.post(PROPOSALS, json=body, headers=steward)
    assert r.status_code == 201, r.text
    base = f"{PROPOSALS}/{r.json()['id']}"
    impact = api.post(f"{base}/impact-analysis", headers=steward).json()["impact"]
    assert impact["overall"] == "ADDITIVE", impact
    assert {ch["field"] for ch in impact["changes"]} == set(RATE_TERM_FIELDS), impact["changes"]
    assert api.post(f"{base}/submit", json={}, headers=steward).status_code == 200
    r = api.post(f"{base}/approval", json={"decision": "APPROVE"}, headers=approver)
    assert r.status_code == 200 and r.json()["status"] == "APPLIED", r.text
    return dict(r.json())


def term_rows(db: str, import_id: str) -> dict[str, dict[str, Any]]:
    """business key -> its instrument_term_version row, for the versions of this import."""
    return {
        r["business_key"]: dict(r)
        for r in sql(
            db,
            "SELECT i.business_key, t.* FROM instrument_term_version t"
            " JOIN instrument_version v ON v.id = t.instrument_version_id"
            " JOIN instrument i ON i.id = v.instrument_id WHERE v.import_job_id = $1",
            uuid.UUID(import_id),
        )
    }


def check_stored_terms(rows: dict[str, dict[str, Any]]) -> None:
    """Every stored term is typed: NUMERIC as an exact Decimal, dates as DATE."""
    flt = rows["RT-FLT"]
    assert (flt["spread"], flt["cap_rate"], flt["floor_rate"]) == (
        Decimal("1.250000"),
        Decimal("6.000000"),
        Decimal("0.500000"),
    )
    assert str(flt["spread"]) == "1.250000"
    assert flt["next_reset_date"].isoformat() == "2026-04-01"
    for row in rows.values():
        for name, value in row.items():
            assert not isinstance(value, float), name


def check_term_seed(db: str) -> None:
    """BE-32.1: every seed row is tagged and PROVISIONAL; the vocabularies are D-61 §3's, with
    no reference index; the currencies are the samples'."""
    vocabulary = sql(db, "SELECT * FROM instrument_term_vocabulary")
    seeded: dict[str, set[str]] = {}
    for row in vocabulary:
        assert row["seeded_by"] == c("TERM_VOCABULARY_SEED_TAG"), dict(row)
        assert row["provisional"] is True, dict(row)
        seeded.setdefault(row["vocabulary"], set()).add(row["code"])
    assert seeded == TERM_VOCABULARIES
    assert {k.value for k in c("TermVocabularyKind")} == set(TERM_VOCABULARIES)
    currencies = sql(db, "SELECT * FROM currency")
    assert {r["code"] for r in currencies} == SAMPLE_CURRENCIES
    for row in currencies:
        assert row["seeded_by"] == c("CURRENCY_SEED_TAG") and row["provisional"] is True, dict(row)
    assert sql(db, "SELECT count(*) AS n FROM fx_rate_observation")[0]["n"] == 0


def check_terms_append_only(db: str, tables: tuple[str, ...]) -> None:
    """UPDATE, DELETE and TRUNCATE are refused on these 0031 tables (each has a row)."""
    for table in tables:
        [row] = sql(db, f"SELECT * FROM {table} ORDER BY id LIMIT 1")
        for statement in (
            f"UPDATE {table} SET created_by = 'tampered' WHERE id = $1",
            f"DELETE FROM {table} WHERE id = $1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
                sql(db, statement, row["id"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="TRUNCATE"):
            sql(db, f"TRUNCATE {table} CASCADE")
        assert sql(db, f"SELECT * FROM {table} WHERE id = $1", row["id"]) == [row], table


def term_counts(db: str) -> dict[str, int]:
    return {t: int(sql(db, f"SELECT count(*) AS n FROM {t}")[0]["n"]) for t in TERM_TABLES}


def calculations_have_no_fx_arithmetic() -> list[str]:
    """BE-32.4 (D-61 §6): the calculations package names no FX rate, observation or conversion
    (the grep). Returns the offending `file:line` entries."""
    root = Path(__file__).resolve().parents[1] / "calculations"
    banned = ("fx_rate", "FxRate", "exchange_rate", "convert_currency", "conversion_rate", "fx.")
    found = []
    for path in sorted(root.rglob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if any(b in line for b in banned):
                found.append(f"{path.name}:{n}")
    return found
