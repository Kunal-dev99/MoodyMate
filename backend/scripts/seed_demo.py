"""Demo seed for the Agentic Stress-Test Explorer (Feature 05): ILLUSTRATIVE / SYNTHETIC only.

Populates a running LOCAL backend so the explorer is not empty when a demo starts:

1. Projected data: the synthetic instrument, position and cash-flow samples, each through its
   own source and ACTIVE mapping (data_steward maps, approver approves), validated and
   projected into the domain. Skipped when the tenant already has instruments.
2. "Two-week loan repayment delay" (DEMO_LOAN_DELAY): DELAYED_INFLOWS on LOAN, 0.2000, 14
   calendar days, by risk_analyst, CONFIRMED_FOR_EXPLORATION ("My explorations").
3. "Combined funding stress" (DEMO_COMBINED_STRESS): the loan delay, DEPOSIT outflows +15%
   and one projected BOND funding source unavailable for 7 days, by risk_analyst, submitted,
   and APPROVED_FOR_TEST by approver (the Shared library).

Every step goes through the public API as the seeded dev users (no SQL), so permissions,
maker-checker and audit apply exactly as in the UI. Re-running is safe: finished steps are
skipped and a half-finished scenario is moved on from where it stopped.

Refused unless the backend config is ENV=local with synthetic data (D-13, D-14): dev sessions
exist only in local/test, and test databases are disposable (D-03).

Usage (from backend/, with the backend running):
    python -m scripts.seed_demo [--api http://localhost:8000]
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "synthetic"
PREFIX = "demo"

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

    def post(self, role: str, path: str, body: Any = None, *ok: int) -> Any:
        r = self.http.post(path, headers=self.auth(role), json=body if body is not None else {})
        return self.check(r, *(ok or (200, 201))).json()


# --- 1. projected synthetic data -------------------------------------------------------------


def _upload(api: Api, source_id: str, file: str, mime: str) -> dict[str, Any]:
    headers = {**api.auth("data_steward"), "Idempotency-Key": f"{PREFIX}-{uuid.uuid4().hex}"}
    with (SAMPLES / file).open("rb") as fh:
        r = api.http.post(
            "/api/v1/imports",
            headers=headers,
            data={"source_id": source_id, "synthetic_attested": "true"},
            files={"file": (file, fh, mime)},
        )
    upload: dict[str, Any] = api.check(r, 201).json()
    return upload


def _activate_mapping(
    api: Api, contract_id: str, import_id: str, code: str, rules: list[Any]
) -> None:
    created = api.post(
        "data_steward",
        "/api/v1/mappings",
        {"code": code, "name": f"{code} (demo)", "source_contract_id": contract_id, "rules": rules},
        201,
    )
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


def _rules_for(api: Api, upload: dict[str, Any], columns: list[str]) -> list[dict[str, Any]]:
    """Every column to the same-named field of the schema version the import pins."""
    schemas = api.get("data_steward", "/api/v1/schemas")["items"]
    schema = next(s for s in schemas if s["code"] == upload["schema_code"])
    versions = api.get("data_steward", f"/api/v1/schemas/{schema['id']}/versions")["items"]
    pinned = next(v for v in versions if v["id"] == upload["schema_version_id"])
    types = {f["name"]: f["data_type"] for f in pinned["fields"]}
    rules = []
    for column in columns:
        transform: dict[str, Any] = {"code": "COPY", "params": {}}
        if types.get(column) == "DECIMAL":
            transform = {"code": "PARSE_DECIMAL"}
        elif types.get(column) == "DATE":
            transform = {"code": "PARSE_DATE", "params": {"format": "YYYY-MM-DD"}}
        rules.append(
            {
                "source_column": column,
                "target_field": column,
                "column_policy": "MAP",
                "null_policy": "ALLOW_NULL",
                "transform": transform,
            }
        )
    return rules


def _validate_and_project(api: Api, import_id: str) -> int:
    job = api.post("data_steward", f"/api/v1/imports/{import_id}/validate", {}, 200)
    if job["status"] not in ("COMPLETED", "COMPLETED_WITH_WARNINGS"):
        raise SeedError(f"import {import_id} validated as {job['status']}")
    projection = api.post("data_steward", f"/api/v1/imports/{import_id}/projection", {}, 201)
    return int(projection["projected_count"])


def _instrument_rules() -> list[dict[str, Any]]:
    def rule(src: str, target: str, code: str = "COPY", **params: Any) -> dict[str, Any]:
        return {
            "source_column": src,
            "target_field": target,
            "transform": {"code": code, "params": params},
        }

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


def seed_projected_data(api: Api, stamp: str) -> None:
    if api.get("risk_analyst", "/api/v1/instruments", limit=1)["total"] > 0:
        print("  projected data: already present, skipped")
        return

    sources = api.get("data_steward", "/api/v1/sources", limit=200)["items"]
    core = next((s for s in sources if s["code"] == "core_banking"), None)
    if not core or not core.get("current_contract"):
        raise SeedError("the seeded core_banking source is missing: run `alembic upgrade head`")
    contract, profile = core["current_contract"], core["profile"]
    keep = ("format", "encoding", "delimiter", "owner", "default_classification")
    source = api.post(
        "data_steward",
        "/api/v1/sources",
        {
            "code": f"{PREFIX}_instr_{stamp}",
            "name": "Instruments (demo seed)",
            "kind": "FILE_DROP",
            "profile": {k: profile[k] for k in keep if k in profile},
            "contract": {
                k: contract[k]
                for k in (
                    "schema_id",
                    "expected_columns",
                    "business_key_columns",
                    "control_total_columns",
                )
            },
        },
        201,
    )
    upload = _upload(api, source["id"], "instrument.csv", "text/csv")
    _activate_mapping(
        api,
        source["current_contract"]["id"],
        upload["id"],
        f"{PREFIX}_instr_{stamp}",
        _instrument_rules(),
    )
    print(f"  instruments projected: {_validate_and_project(api, upload['id'])}")

    position_cols = [
        "position_id", "instrument_id", "account_id", "legal_entity_id", "as_of_date",
        "quantity", "balance", "currency", "valuation_date",
    ]  # fmt: skip
    cashflow_cols = list(json.loads((SAMPLES / "cashflow_event.json").read_text())["records"][0])
    for code, file, fmt, mime, key, columns in (
        ("position", "position.xlsx", "XLSX",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "position_id", position_cols),
        ("cashflow_event", "cashflow_event.json", "JSON", "application/json",
         "cashflow_id", cashflow_cols),
    ):  # fmt: skip
        schemas = api.get("data_steward", "/api/v1/schemas")["items"]
        schema = next(s for s in schemas if s["code"] == code)
        src = api.post(
            "data_steward",
            "/api/v1/sources",
            {
                "code": f"{PREFIX}_{code}_{stamp}",
                "name": f"{code} (demo seed)",
                "kind": "FILE_DROP",
                "profile": {
                    "format": fmt,
                    "encoding": "UTF-8",
                    "owner": "Treasury data team (demo)",
                    "default_classification": "confidential",
                },
                "contract": {
                    "schema_id": schema["id"],
                    "expected_columns": [{"name": c, "required": True} for c in columns],
                    "business_key_columns": [key],
                    "control_total_columns": [],
                },
            },
            201,
        )
        upload = _upload(api, src["id"], file, mime)
        _activate_mapping(
            api,
            src["current_contract"]["id"],
            upload["id"],
            f"{PREFIX}_{code}_{stamp}",
            _rules_for(api, upload, columns),
        )
        print(f"  {code} projected: {_validate_and_project(api, upload['id'])}")


# --- 2-3. scenarios ----------------------------------------------------------------------------

STATUSES = (
    "DRAFT",
    "CONFIRMED_FOR_EXPLORATION",
    "IN_REVIEW",
    "APPROVED_FOR_TEST",
    "REJECTED",
    "CLOSED",
)


def _find(api: Api, code: str) -> dict[str, Any] | None:
    page = api.get(
        "risk_analyst", "/api/v1/scenarios", status=list(STATUSES), mine="true", limit=200
    )
    hit = next((s for s in page["items"] if s["code"] == code), None)
    return api.get("risk_analyst", f"/api/v1/scenarios/{hit['scenario_id']}") if hit else None


def _version1_status(detail: dict[str, Any]) -> str:
    return str(next(v["status"] for v in detail["versions"] if v["version"] == 1))


def seed_scenario(
    api: Api, code: str, name: str, description: str, shocks: list[Any], approve: bool
) -> None:
    detail = _find(api, code)
    if detail is None:
        detail = api.post(
            "risk_analyst",
            "/api/v1/scenarios",
            {
                "code": code,
                "name": name,
                "description": description,
                "shocks": shocks,
                "change_summary": "Demo seed (illustrative, synthetic data).",
            },
            201,
        )
    base = f"/api/v1/scenarios/{detail['scenario_id']}/versions/1"
    status = _version1_status(detail)
    if status == "DRAFT":
        status = api.post("risk_analyst", f"{base}/confirm", {}, 200)["status"]
    if approve and status == "CONFIRMED_FOR_EXPLORATION":
        comment = "Demo seed: promote to the shared library."
        status = api.post("risk_analyst", f"{base}/submit", {"comment": comment}, 200)["status"]
    if approve and status == "IN_REVIEW":
        decision = {
            "decision": "APPROVE",
            "comment": "Demo seed: approved for illustrative testing.",
        }
        status = api.post("approver", f"{base}/approval", decision, 200)["status"]
    print(f"  {code}: {name!r} -> {status}")


def funding_source_key(api: Api) -> str | None:
    """A projected BOND (an INFLOW type) to mark as the unavailable funding source."""
    page = api.get("risk_analyst", "/api/v1/instruments", instrument_type="BOND", limit=1)
    key = page["items"][0]["business_key"] if page["items"] else None
    return key if isinstance(key, str) else None  # a masked key cannot be written back


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://localhost:8000", help="The running backend.")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.ENV != "local" or settings.data_class != "synthetic":
        print(
            f"Refused: the demo seed runs only with ENV=local and synthetic data "
            f"(ENV={settings.ENV}, DATA_CLASS={settings.data_class}).",
            file=sys.stderr,
        )
        return 2

    api = Api(args.api)
    try:
        api.check(api.http.get("/api/v1/health"), 200)
    except (httpx.HTTPError, SeedError) as exc:
        print(
            f"The backend is not reachable at {args.api} ({exc}). Start it first.", file=sys.stderr
        )
        return 1

    stamp = uuid.uuid4().hex[:8]
    try:
        print("Projected synthetic data:")
        seed_projected_data(api, stamp)
        print("Scenarios:")
        seed_scenario(
            api,
            "DEMO_LOAN_DELAY",
            "Two-week loan repayment delay",
            "20% of LOAN inflows arrive 14 calendar days late (illustrative).",
            [LOAN_DELAY],
            approve=False,
        )
        funding: dict[str, Any] = {
            "template": "FUNDING_UNAVAILABLE",
            "parameters": {
                "unavailable_days": 7,
                "affected_fraction": "1.0000",
                "delay_calendar": "CALENDAR_DAYS",
            },
            "selector": {"instrument_type": "BOND"},
        }
        if key := funding_source_key(api):
            funding["selector"]["instrument_keys"] = [key]
        seed_scenario(
            api,
            "DEMO_COMBINED_STRESS",
            "Combined funding stress",
            "Loan repayments 14 days late, deposit outflows +15% and one funding source "
            "unavailable for 7 days (illustrative).",
            [LOAN_DELAY, DEPOSIT_RUN, funding],
            approve=True,
        )
    except SeedError as exc:
        print(f"Demo seed failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Done. Sign in as Risk analyst ('My explorations') or Approver ('Shared library') and "
        "open Advanced > Agentic Stress-Test Explorer."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
