"""Demo seed for a live MoodyMate demo: ILLUSTRATIVE / SYNTHETIC data only.

Populates a running LOCAL backend so every page has something to show:

1. Projected data: the synthetic instrument, position and cash-flow samples, each through its
   own source and ACTIVE mapping (data_steward maps, approver approves), validated and
   projected into the domain.
2. Credit data: the synthetic counterparty and credit-exposure samples, the same way.
3. Obligor ratings: one proposed by risk_analyst and approved by approver (ACTIVE), and one
   left IN_REVIEW for the Ratings review queue.
4. Calculations: a dataset snapshot of the projected instrument, position and cash-flow
   imports, then a maturity ladder run and an LCR/HQLA run on it, by risk_analyst.
5. Scenarios for the Agentic Stress-Test Explorer:
   - "Two-week loan repayment delay" (DEMO_LOAN_DELAY): DELAYED_INFLOWS on LOAN, 0.2000, 14
     calendar days, CONFIRMED_FOR_EXPLORATION ("My explorations");
   - "Combined funding stress" (DEMO_COMBINED_STRESS): the loan delay, DEPOSIT outflows +15%
     and one projected BOND funding source unavailable for 7 days, APPROVED_FOR_TEST by
     approver (the Shared library).

Every step goes through the public API as the seeded dev users (no SQL), so permissions,
maker-checker and audit apply exactly as in the UI. Re-running is safe: finished steps are
skipped and a half-finished one is moved on from where it stopped.

Refused unless the backend config is ENV=local with synthetic data (D-13, D-14): dev sessions
exist only in local/test, and test databases are disposable (D-03).

Usage (from backend/, with the backend running):
    python -m scripts.seed_demo [--api http://localhost:8000]
"""

import argparse
import csv
import json
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "synthetic"
PREFIX = "demo"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Cash flows of the synthetic sample fall from 2026-12-28 on: this date fills the ladder's
# buckets and puts a cash flow inside the LCR's 30-day horizon (as e2e/liquidity.spec.ts).
REPORTING_DATE = "2026-12-20"
CALC_MODULES = ("maturity_ladder", "lcr_hqla")
SUCCEEDED = ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS")
SNAPSHOT_SCHEMAS = ("instrument", "position", "cashflow_event")

LOAN_DELAY = {
    "template": "DELAYED_INFLOWS",
    "parameters": {
        "cashflow_type": "LOAN",
        "affected_fraction": "0.2000",
        "delay_days": 14,
        "delay_calendar": "CALENDAR_DAYS",
    },
}
DEPOSIT_RUN = {
    "template": "DEPOSIT_WITHDRAWAL",
    "parameters": {"cashflow_type": "DEPOSIT", "increase_fraction": "0.1500"},
}


class SeedError(RuntimeError):
    pass


class Api:
    """The backend as the seeded dev users: one bearer token per role, checked responses."""

    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=60)
        self.tokens: dict[str, dict[str, str]] = {}

    def auth(self, role: str) -> dict[str, str]:
        if role not in self.tokens:
            r = self.http.post("/api/v1/dev/sessions", json={"role": role})
            self.check(r, 200, 201)
            self.tokens[role] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self.tokens[role]

    @staticmethod
    def check(r: httpx.Response, *ok: int) -> httpx.Response:
        if r.status_code not in ok:
            raise SeedError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text}")
        return r

    def get(self, role: str, path: str, **params: Any) -> Any:
        return self.check(self.http.get(path, headers=self.auth(role), params=params), 200).json()

    def items(self, role: str, path: str, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.get(role, path, **params)["items"]
        return items

    def post(self, role: str, path: str, body: Any = None, *ok: int, key: str | None = None) -> Any:
        headers = self.auth(role) | ({"Idempotency-Key": key} if key else {})
        r = self.http.post(path, headers=headers, json=body if body is not None else {})
        return self.check(r, *(ok or (200, 201))).json()


def _new_key() -> str:
    return f"{PREFIX}-{uuid.uuid4().hex}"


# --- imports, mappings and projection (shared by steps 1 and 2) -------------------------------


@dataclass(frozen=True)
class Sample:
    """A synthetic sample file and the schema it lands in."""

    code: str
    file: str
    fmt: str
    mime: str
    key: str
    columns: tuple[str, ...]
    owner: str


def _csv_columns(file: str) -> tuple[str, ...]:
    """The header row of a sample CSV (`#` lines are the D-24 synthetic marker)."""
    with (SAMPLES / file).open(encoding="utf-8", newline="") as fh:
        rows = csv.reader(line for line in fh if line.strip() and not line.startswith("#"))
        return tuple(c.strip() for c in next(rows))


def _json_columns(file: str) -> tuple[str, ...]:
    return tuple(json.loads((SAMPLES / file).read_text())["records"][0])


POSITION = Sample(
    "position", "position.xlsx", "XLSX", XLSX, "position_id",
    ("position_id", "instrument_id", "account_id", "legal_entity_id", "as_of_date",
     "quantity", "balance", "currency", "valuation_date"),
    "Treasury data team (demo)",
)  # fmt: skip
CASHFLOW = Sample(
    "cashflow_event", "cashflow_event.json", "JSON", "application/json", "cashflow_id",
    _json_columns("cashflow_event.json"), "Treasury data team (demo)",
)  # fmt: skip
COUNTERPARTY = Sample(
    "counterparty", "counterparty.csv", "CSV", "text/csv", "counterparty_id",
    _csv_columns("counterparty.csv"), "Credit data team (demo)",
)  # fmt: skip
CREDIT_EXPOSURE = Sample(
    "credit_exposure", "credit_exposure.csv", "CSV", "text/csv", "exposure_id",
    _csv_columns("credit_exposure.csv"), "Credit data team (demo)",
)  # fmt: skip


def _projected_import(api: Api, schema_code: str) -> str | None:
    """The newest projected import of a schema, if any."""
    found = api.items(
        "data_steward", "/api/v1/imports", schema_code=schema_code, projected="true", limit=1
    )
    return str(found[0]["id"]) if found else None


def _schema(api: Api, code: str) -> dict[str, Any]:
    schemas = api.items("data_steward", "/api/v1/schemas")
    schema = next((s for s in schemas if s["code"] == code), None)
    if schema is None:
        raise SeedError(f"schema {code} is not seeded: run `alembic upgrade head`")
    return schema


def _create_source(
    api: Api, code: str, name: str, profile: dict[str, Any], contract: dict[str, Any]
) -> dict[str, Any]:
    body = {"code": code, "name": name, "kind": "FILE_DROP", "profile": profile}
    source: dict[str, Any] = api.post(
        "data_steward", "/api/v1/sources", body | {"contract": contract}, 201
    )
    return source


def _upload(api: Api, source_id: str, file: str, mime: str) -> dict[str, Any]:
    headers = api.auth("data_steward") | {"Idempotency-Key": _new_key()}
    with (SAMPLES / file).open("rb") as fh:
        r = api.http.post(
            "/api/v1/imports",
            headers=headers,
            data={"source_id": source_id, "synthetic_attested": "true"},
            files={"file": (file, fh, mime)},
        )
    upload: dict[str, Any] = api.check(r, 201).json()
    return upload


def _same_name_rules(api: Api, upload: dict[str, Any], columns: tuple[str, ...]) -> list[Any]:
    """Every column to the same-named field of the schema version the import pins."""
    schema = _schema(api, upload["schema_code"])
    versions = api.items("data_steward", f"/api/v1/schemas/{schema['id']}/versions")
    pinned = next(v for v in versions if v["id"] == upload["schema_version_id"])
    types = {f["name"]: f["data_type"] for f in pinned["fields"]}
    transforms: dict[str, dict[str, Any]] = {
        "DECIMAL": {"code": "PARSE_DECIMAL"},
        "DATE": {"code": "PARSE_DATE", "params": {"format": "YYYY-MM-DD"}},
    }
    copy: dict[str, Any] = {"code": "COPY", "params": {}}
    return [
        {
            "source_column": column,
            "target_field": column,
            "column_policy": "MAP",
            "null_policy": "ALLOW_NULL",
            "transform": transforms.get(types.get(column, ""), copy),
        }
        for column in columns
    ]


def _instrument_rules() -> list[dict[str, Any]]:
    def rule(src: str, target: str, code: str = "COPY", **params: Any) -> dict[str, Any]:
        transform = {"code": code, "params": params}
        return {"source_column": src, "target_field": target, "transform": transform}

    return [
        rule("instrument_id", "instrument_id"),
        rule("instrument_type", "instrument_type"),
        rule("currency", "currency"),
        rule("counterparty_id", "counterparty_id"),
        rule("product_family", "product_family"),
        rule("notional", "notional", "PARSE_DECIMAL"),
        rule("interest_rate", "interest_rate", "PARSE_DECIMAL"),
        rule("maturity_date", "maturity_date", "PARSE_DATE", format="YYYY-MM-DD"),
        rule("book_code", "legacy_book_code"),
    ]


def _activate_mapping(
    api: Api, contract_id: str, import_id: str, code: str, rules: list[Any]
) -> None:
    """Create a mapping, dry-run it on the import, submit (steward) and approve (approver)."""
    body = {"code": code, "name": f"{code} (demo)", "source_contract_id": contract_id}
    created = api.post("data_steward", "/api/v1/mappings", body | {"rules": rules}, 201)
    version = created["versions"][0]
    dry = api.post(
        "data_steward",
        f"/api/v1/imports/{import_id}/mappings/validate",
        {"mapping_version_id": version["id"]},
        201,
    )
    if not dry["ok"]:
        raise SeedError(f"mapping {code} dry run failed: {json.dumps(dry)}")
    base = f"/api/v1/mappings/{version['mapping_id']}/versions/{version['version']}"
    api.post("data_steward", f"{base}/submit", {}, 200)
    api.post("approver", f"{base}/approval", {"decision": "APPROVE"}, 200)


def _validate_and_project(api: Api, import_id: str) -> int:
    job = api.post("data_steward", f"/api/v1/imports/{import_id}/validate", {}, 200)
    if job["status"] not in ("COMPLETED", "COMPLETED_WITH_WARNINGS"):
        raise SeedError(f"import {import_id} validated as {job['status']}")
    projection = api.post("data_steward", f"/api/v1/imports/{import_id}/projection", {}, 201)
    return int(projection["projected_count"])


def _project_instruments(api: Api, stamp: str) -> None:
    """instrument.csv on a source whose contract copies the seeded core_banking one."""
    if _projected_import(api, "instrument"):
        print("  instrument: already projected, skipped")
        return
    sources = api.items("data_steward", "/api/v1/sources", limit=200)
    core = next((s for s in sources if s["code"] == "core_banking"), None)
    if not core or not core.get("current_contract"):
        raise SeedError("the seeded core_banking source is missing: run `alembic upgrade head`")
    contract, profile = core["current_contract"], core["profile"]
    keep_profile = ("format", "encoding", "delimiter", "owner", "default_classification")
    keep_contract = (
        "schema_id", "expected_columns", "business_key_columns", "control_total_columns",
    )  # fmt: skip
    code = f"{PREFIX}_instr_{stamp}"
    source = _create_source(
        api,
        code,
        "Instruments (demo seed)",
        {k: profile[k] for k in keep_profile if k in profile},
        {k: contract[k] for k in keep_contract},
    )
    upload = _upload(api, source["id"], "instrument.csv", "text/csv")
    contract_id = source["current_contract"]["id"]
    _activate_mapping(api, contract_id, upload["id"], code, _instrument_rules())
    print(f"  instrument: {_validate_and_project(api, upload['id'])} projected")


def _project_sample(api: Api, sample: Sample, stamp: str) -> None:
    """A sample on its own source (contract = the file's columns) and same-name mapping."""
    if _projected_import(api, sample.code):
        print(f"  {sample.code}: already projected, skipped")
        return
    code = f"{PREFIX}_{sample.code}_{stamp}"
    source = _create_source(
        api,
        code,
        f"{sample.code} (demo seed)",
        {
            "format": sample.fmt,
            "encoding": "UTF-8",
            "owner": sample.owner,
            "default_classification": "confidential",
        },
        {
            "schema_id": _schema(api, sample.code)["id"],
            "expected_columns": [{"name": c, "required": True} for c in sample.columns],
            "business_key_columns": [sample.key],
            "control_total_columns": [],
        },
    )
    upload = _upload(api, source["id"], sample.file, sample.mime)
    if upload["schema_code"] != sample.code:
        raise SeedError(f"{sample.file} landed in {upload['schema_code']}, not {sample.code}")
    rules = _same_name_rules(api, upload, sample.columns)
    _activate_mapping(api, source["current_contract"]["id"], upload["id"], code, rules)
    print(f"  {sample.code}: {_validate_and_project(api, upload['id'])} projected")


# --- 1. projected market data ------------------------------------------------------------------


def seed_projected_data(api: Api, stamp: str) -> None:
    _project_instruments(api, stamp)
    _project_sample(api, POSITION, stamp)
    _project_sample(api, CASHFLOW, stamp)


# --- 2. credit data ---------------------------------------------------------------------------


def seed_credit_data(api: Api, stamp: str) -> None:
    if api.get("data_steward", "/api/v1/counterparties", limit=1)["total"] > 0:
        print("  counterparties already present, skipped")
        return
    _project_sample(api, COUNTERPARTY, stamp)
    _project_sample(api, CREDIT_EXPOSURE, stamp)


# --- 3. obligor ratings -----------------------------------------------------------------------

RATING_DATE = "2026-09-30"  # the as_of_date of the synthetic credit exposures
RATINGS = (
    # (counterparty key, grade, approve, rationale)
    ("CP-0001", "G03", True, "Demo seed: stable earnings and strong collateral cover."),
    ("CP-0003", "G05", False, "Demo seed: 35 days past due on a term loan; downgrade proposed."),
)


def _counterparty_ids(api: Api) -> dict[str, str]:
    """Counterparty ids by business key (a masked key is left out)."""
    found = api.items("data_steward", "/api/v1/counterparties", limit=200)
    return {
        c["business_key"]: str(c["counterparty_id"])
        for c in found
        if isinstance(c["business_key"], str)
    }


def _seed_rating(api: Api, cp_id: str, grade: str, approve: bool, rationale: str) -> str:
    """Propose (if the counterparty has no rating yet) and, if asked, approve it."""
    ratings = api.items("risk_analyst", "/api/v1/obligor-ratings", counterparty_id=cp_id)
    rating = ratings[0] if ratings else None  # newest proposal first
    before = rating["status"] if rating else None
    if rating is None:
        body = {"grade": grade, "effective_from": RATING_DATE, "rationale": rationale}
        rating = api.post("risk_analyst", f"/api/v1/counterparties/{cp_id}/ratings", body, 201)
    if approve and rating["status"] == "IN_REVIEW":
        decision = {"decision": "APPROVE", "comment": "Demo seed: approved (illustrative)."}
        path = f"/api/v1/obligor-ratings/{rating['rating_id']}/approval"
        rating = api.post("approver", path, decision, 200)
    done = f"{rating['grade']} from {rating['effective_from']} -> {rating['status']}"
    return f"{done} (already, skipped)" if before == rating["status"] else done


def seed_ratings(api: Api) -> None:
    ids = _counterparty_ids(api)
    for key, grade, approve, rationale in RATINGS:
        if key not in ids:
            raise SeedError(f"counterparty {key} is not projected (or its key is masked)")
        print(f"  {key}: {_seed_rating(api, ids[key], grade, approve, rationale)}")


# --- 4. calculations --------------------------------------------------------------------------


def _succeeded_run(api: Api, module_code: str) -> dict[str, Any] | None:
    for status in SUCCEEDED:
        path = "/api/v1/calculation-runs"
        runs = api.items("risk_analyst", path, module_code=module_code, status=status, limit=1)
        if runs:
            return runs[0]
    return None


def _snapshot(api: Api) -> str:
    """One snapshot of the newest projected import per schema (the server reuses a match)."""
    import_ids = []
    for code in SNAPSHOT_SCHEMAS:
        import_id = _projected_import(api, code)
        if import_id is None:
            raise SeedError(f"no projected {code} import to snapshot")
        import_ids.append(import_id)
    snapshot = api.post("risk_analyst", "/api/v1/dataset-snapshots", {"import_ids": import_ids})
    print(f"  snapshot {snapshot['snapshot_id']}: {len(snapshot['members'])} imports")
    return str(snapshot["snapshot_id"])


def _module_version(api: Api, code: str) -> str:
    modules = api.items("risk_analyst", "/api/v1/calculation-modules")
    hit = next(
        (m for m in modules if m["code"] == code and m["implementation_status"] == "AVAILABLE"),
        None,
    )
    if hit is None or not hit["runnable"]:
        raise SeedError(f"module {code} is not AVAILABLE and runnable")
    return str(hit["module_version_id"])


def _run_contents(api: Api, run: dict[str, Any]) -> str:
    """How full the run's view is: non-empty ladder buckets, or LCR currencies."""
    base = f"/api/v1/calculation-runs/{run['run_id']}"
    if run["module"]["code"] == "maturity_ladder":
        ladder = api.get("risk_analyst", f"{base}/maturity-ladder")
        rows = [r for c in ladder["currencies"] for r in c["rows"] if r["contribution_count"]]
        return f"{len(ladder['currencies'])} currencies, {len(rows)} non-empty buckets"
    view = api.get("risk_analyst", f"{base}/liquidity")
    return f"{len(view['currencies'])} currencies"


def seed_calculations(api: Api) -> None:
    snapshot_id: str | None = None
    for code in CALC_MODULES:
        run = _succeeded_run(api, code)
        if run is not None:
            print(f"  {code}: run {run['run_id']} already {run['status']}, skipped")
            continue
        snapshot_id = snapshot_id or _snapshot(api)
        body = {
            "dataset_snapshot_id": snapshot_id,
            "module_version_id": _module_version(api, code),
            "reporting_date": REPORTING_DATE,
        }
        run = api.post("risk_analyst", "/api/v1/calculation-runs", body, 201, key=_new_key())
        if run["status"] not in SUCCEEDED:
            raise SeedError(f"{code} run {run['run_id']} ended {run['status']}")
        print(f"  {code}: run {run['run_id']} {run['status']} ({_run_contents(api, run)})")


# --- 5. scenarios -----------------------------------------------------------------------------

SCENARIO_STATUSES = (
    "DRAFT",
    "CONFIRMED_FOR_EXPLORATION",
    "IN_REVIEW",
    "APPROVED_FOR_TEST",
    "REJECTED",
    "CLOSED",
)


def _find_scenario(api: Api, code: str) -> dict[str, Any] | None:
    found = api.items(
        "risk_analyst", "/api/v1/scenarios", status=list(SCENARIO_STATUSES), limit=200
    )
    hit = next((s for s in found if s["code"] == code), None)
    return api.get("risk_analyst", f"/api/v1/scenarios/{hit['scenario_id']}") if hit else None


def seed_scenario(
    api: Api, code: str, name: str, description: str, shocks: list[Any], approve: bool
) -> None:
    """Create (if absent) and move the newest version on: confirm, then submit and approve."""
    detail = _find_scenario(api, code)
    created = detail is None
    if detail is None:
        body = {
            "code": code,
            "name": name,
            "description": description,
            "shocks": shocks,
            "change_summary": "Demo seed (illustrative, synthetic data).",
        }
        detail = api.post("risk_analyst", "/api/v1/scenarios", body, 201)
    latest = max(detail["versions"], key=lambda v: int(v["version"]))
    base = f"/api/v1/scenarios/{detail['scenario_id']}/versions/{latest['version']}"
    status = before = latest["status"]
    if status == "DRAFT":
        status = api.post("risk_analyst", f"{base}/confirm", {}, 200)["status"]
    if approve and status == "CONFIRMED_FOR_EXPLORATION":
        comment = {"comment": "Demo seed: promote to the shared library."}
        status = api.post("risk_analyst", f"{base}/submit", comment, 200)["status"]
    if approve and status == "IN_REVIEW":
        decision = {"decision": "APPROVE", "comment": "Demo seed: approved for illustrative use."}
        status = api.post("approver", f"{base}/approval", decision, 200)["status"]
    skipped = " (already, skipped)" if not created and before == status else ""
    print(f"  {code} v{latest['version']}: {name!r} -> {status}{skipped}")


def _funding_unavailable(api: Api) -> dict[str, Any]:
    """One projected BOND (an INFLOW type) unavailable as a funding source for 7 days."""
    shock: dict[str, Any] = {
        "template": "FUNDING_UNAVAILABLE",
        "parameters": {
            "unavailable_days": 7,
            "affected_fraction": "1.0000",
            "delay_calendar": "CALENDAR_DAYS",
        },
        "selector": {"instrument_type": "BOND"},
    }
    bonds = api.items("risk_analyst", "/api/v1/instruments", instrument_type="BOND", limit=1)
    key = bonds[0]["business_key"] if bonds else None
    if isinstance(key, str):  # a masked key cannot be written back
        shock["selector"]["instrument_keys"] = [key]
    return shock


def seed_scenarios(api: Api) -> None:
    seed_scenario(
        api,
        "DEMO_LOAN_DELAY",
        "Two-week loan repayment delay",
        "20% of LOAN inflows arrive 14 calendar days late (illustrative).",
        [LOAN_DELAY],
        approve=False,
    )
    seed_scenario(
        api,
        "DEMO_COMBINED_STRESS",
        "Combined funding stress",
        "Loan repayments 14 days late, deposit outflows +15% and one funding source "
        "unavailable for 7 days (illustrative).",
        [LOAN_DELAY, DEPOSIT_RUN, _funding_unavailable(api)],
        approve=True,
    )


# --- entry point ------------------------------------------------------------------------------


def _refused() -> bool:
    settings = get_settings()
    if settings.ENV == "local" and settings.data_class == "synthetic":
        return False
    print(
        f"Refused: the demo seed runs only with ENV=local and synthetic data "
        f"(ENV={settings.ENV}, DATA_CLASS={settings.data_class}).",
        file=sys.stderr,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://localhost:8000", help="The running backend.")
    args = parser.parse_args(argv)
    if _refused():
        return 2

    api = Api(args.api)
    try:
        api.check(api.http.get("/api/v1/health"), 200)
    except (httpx.HTTPError, SeedError) as exc:
        print(f"The backend is not reachable at {args.api} ({exc}).", file=sys.stderr)
        return 1

    stamp = uuid.uuid4().hex[:8]
    steps: tuple[tuple[str, Callable[[], None]], ...] = (
        ("Projected synthetic data", lambda: seed_projected_data(api, stamp)),
        ("Credit data", lambda: seed_credit_data(api, stamp)),
        ("Obligor ratings", lambda: seed_ratings(api)),
        (f"Calculations (reporting date {REPORTING_DATE})", lambda: seed_calculations(api)),
        ("Scenarios", lambda: seed_scenarios(api)),
    )
    try:
        for title, step in steps:
            print(f"{title}:")
            step()
    except SeedError as exc:
        print(f"Demo seed failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Done. Sign in as Risk analyst or Approver: the Financial Domain, Counterparties, "
        "Ratings review, Calculations and Advanced > Agentic Stress-Test Explorer all show "
        "illustrative data."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
