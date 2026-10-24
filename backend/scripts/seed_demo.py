"""Demo seed for a live MoodyMate demo: ILLUSTRATIVE / SYNTHETIC data only.

Populates a running LOCAL backend so every page has something to show:

1. Projected data: the synthetic instrument, position and cash-flow samples, each with an
   ACTIVE mapping (data_steward maps, approver approves), validated and projected into the
   domain. Instruments and cash flows share the source "Core banking instruments (demo)"
   (contract v1 instrument, v2 cashflow_event): an instrument lists only the cash flows of its
   own source (D-29 §3). Positions come from "Positions (demo)".
2. Credit data: the synthetic counterparty and credit-exposure samples, the same way, from
   "Counterparty master (demo)" and "Credit exposures (demo)".
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

6. Opt-in only (`--publish-typed-instrument`, Phase 31, D-57 §3): the typed-maturity
   instrument contract INSTRUMENT_TYPED_MATURITY (v1 with `maturity_date` DATE and
   `interest_rate` (11,6)) is proposed by data_steward through the governed path (impact
   analysis, submit) and approved by approver; it becomes the ACTIVE instrument version and
   v1 is DEPRECATED. The instrument sample is then imported again through
   "Core banking instruments (demo)" with a mapping for the new version
   (demo_instruments_typed) and projected by projector v2, so the instrument detail shows a
   typed maturity next to the v1 "Not stated". Never part of the default seed: publishing
   changes the contract new instrument imports are checked against (a client decision outside
   a local demo).

Every step goes through the public API as the seeded dev users (no SQL), so permissions,
maker-checker and audit apply exactly as in the UI. Sources and mappings have stable codes
(demo_instruments, demo_cashflows, ...). Re-running is safe: finished steps are skipped and a
half-finished one is moved on from where it stopped.

Refused unless the backend config is ENV=local with synthetic data (D-13, D-14): dev sessions
exist only in local/test, and test databases are disposable (D-03).

Usage (from backend/, with the backend running):
    python -m scripts.seed_demo [--api http://localhost:8000] [--publish-typed-instrument]
"""

import argparse
import csv
import io
import json
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.domain.constants import (
    INSTRUMENT_TYPED_MATURITY,
    INSTRUMENT_TYPED_MATURITY_SHA256,
    INSTRUMENT_TYPED_MATURITY_TITLE,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "synthetic"
PREFIX = "demo"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Cash flows of the synthetic sample fall from 2026-12-28 on: this date fills the ladder's
# buckets and puts a cash flow inside the LCR's 30-day horizon (as e2e/liquidity.spec.ts).
REPORTING_DATE = "2026-12-20"
CALC_MODULES = ("maturity_ladder", "lcr_hqla")
SUCCEEDED = ("SUCCEEDED", "SUCCEEDED_WITH_WARNINGS")

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
class Source:
    """A demo source, found again by its stable code on a re-run."""

    code: str
    name: str
    fmt: str
    mime: str
    owner: str


@dataclass(frozen=True)
class Feed:
    """A synthetic sample file, the schema it lands in and the mapping that maps it."""

    schema: str
    file: str
    key: str
    columns: tuple[str, ...]
    mapping_code: str
    mapping_name: str


def _csv_columns(file: str) -> tuple[str, ...]:
    """The header row of a sample CSV (`#` lines are the D-24 synthetic marker)."""
    with (SAMPLES / file).open(encoding="utf-8", newline="") as fh:
        rows = csv.reader(line for line in fh if line.strip() and not line.startswith("#"))
        return tuple(c.strip() for c in next(rows))


def _json_columns(file: str) -> tuple[str, ...]:
    return tuple(json.loads((SAMPLES / file).read_text())["records"][0])


CSV, TREASURY, CREDIT = "text/csv", "Treasury data team (demo)", "Credit data team (demo)"

# Instruments and their cash flows share one source: the instrument detail lists the cash flows
# of its own source only (a business key is unique per source, D-29 §3).
CORE = Source("demo_instruments", "Core banking instruments (demo)", "CSV", CSV, TREASURY)
POSITIONS = Source("demo_positions", "Positions (demo)", "XLSX", XLSX, TREASURY)
COUNTERPARTIES = Source("demo_counterparties", "Counterparty master (demo)", "CSV", CSV, CREDIT)
EXPOSURES = Source("demo_credit_exposures", "Credit exposures (demo)", "CSV", CSV, CREDIT)

INSTRUMENT = Feed(
    "instrument", "instrument.csv", "instrument_id", _csv_columns("instrument.csv"),
    "demo_instruments", "Core banking instruments (demo)",
)  # fmt: skip
CASHFLOW = Feed(
    "cashflow_event", "cashflow_event.json", "cashflow_id", _json_columns("cashflow_event.json"),
    "demo_cashflows", "Cash-flow schedule (demo)",
)  # fmt: skip
POSITION = Feed(
    "position", "position.xlsx", "position_id",
    ("position_id", "instrument_id", "account_id", "legal_entity_id", "as_of_date",
     "quantity", "balance", "currency", "valuation_date"),
    "demo_positions", "Positions (demo)",
)  # fmt: skip
COUNTERPARTY = Feed(
    "counterparty", "counterparty.csv", "counterparty_id", _csv_columns("counterparty.csv"),
    "demo_counterparties", "Counterparty master (demo)",
)  # fmt: skip
CREDIT_EXPOSURE = Feed(
    "credit_exposure", "credit_exposure.csv", "exposure_id", _csv_columns("credit_exposure.csv"),
    "demo_credit_exposures", "Credit exposures (demo)",
)  # fmt: skip

# Imports that can still be validated, or are validated and only wait for projection.
PENDING = ("RECEIVED", "MAPPING_REQUIRED", "COMPLETED", "COMPLETED_WITH_WARNINGS")
VALIDATED = ("COMPLETED", "COMPLETED_WITH_WARNINGS")


def _schema(api: Api, code: str) -> dict[str, Any]:
    schemas = api.items("data_steward", "/api/v1/schemas")
    schema = next((s for s in schemas if s["code"] == code), None)
    if schema is None:
        raise SeedError(f"schema {code} is not seeded: run `alembic upgrade head`")
    return schema


def _find_source(api: Api, code: str) -> dict[str, Any] | None:
    sources = api.items("data_steward", "/api/v1/sources", limit=200)
    return next((s for s in sources if s["code"] == code), None)


def _contract_for(api: Api, feed: Feed) -> dict[str, Any]:
    """The instrument contract copies the seeded core_banking one; the others list the
    sample's columns, all required."""
    if feed.schema == "instrument":
        core = _find_source(api, "core_banking")
        if not core or not core.get("current_contract"):
            raise SeedError("the seeded core_banking source is missing: run `alembic upgrade head`")
        keep = ("schema_id", "expected_columns", "business_key_columns", "control_total_columns")
        return {k: core["current_contract"][k] for k in keep}
    return {
        "schema_id": _schema(api, feed.schema)["id"],
        "expected_columns": [{"name": c, "required": True} for c in feed.columns],
        "business_key_columns": [feed.key],
        "control_total_columns": [],
    }


def _projected_import(api: Api, schema_code: str, source_id: str) -> str | None:
    """The newest projected import of a schema from a source, if any."""
    found = api.items(
        "data_steward",
        "/api/v1/imports",
        source_id=source_id,
        schema_code=schema_code,
        projected="true",
        limit=1,
    )
    return str(found[0]["id"]) if found else None


def _mapped_import(api: Api, feed: Feed, source_id: str) -> str | None:
    """The newest projected import of the feed's schema from a source that the feed's own
    mapping mapped, if any. Phase 31 (ruling Q7): after `--publish-typed-instrument` the
    newest instrument import of the core source is the typed one (its own mapping,
    demo_instruments_typed); the snapshot keeps the v1 import, so a re-run creates no new
    snapshot or run."""
    mappings = api.items("data_steward", "/api/v1/mappings", source_id=source_id, limit=200)
    hit = next((m for m in mappings if m["code"] == feed.mapping_code), None)
    if hit is None:
        return None
    detail = api.get("data_steward", f"/api/v1/mappings/{hit['id']}")
    versions = {v["id"] for v in detail["versions"]}
    found = api.items(
        "data_steward",
        "/api/v1/imports",
        source_id=source_id,
        schema_code=feed.schema,
        projected="true",
        limit=200,
    )
    return next((str(i["id"]) for i in found if i["mapping_version_id"] in versions), None)


def _ensure_source(api: Api, src: Source, feed: Feed) -> dict[str, Any]:
    """The source by its code, created (with this feed's contract) if absent."""
    found = _find_source(api, src.code)
    if found is not None:
        return found
    profile = {
        "format": src.fmt,
        "encoding": "UTF-8",
        "owner": src.owner,
        "default_classification": "confidential",
    }
    body = {"code": src.code, "name": src.name, "kind": "FILE_DROP", "profile": profile}
    created: dict[str, Any] = api.post(
        "data_steward", "/api/v1/sources", body | {"contract": _contract_for(api, feed)}, 201
    )
    print(f"  source {src.code} ({src.name!r}) created")
    return created


def _ensure_contract(api: Api, source: dict[str, Any], feed: Feed) -> dict[str, Any]:
    """The source's current contract when it targets the feed's schema, else a new contract
    version that does (the earlier version is SUPERSEDED, never edited)."""
    current: dict[str, Any] | None = source.get("current_contract")
    if current is not None and current["schema_code"] == feed.schema:
        return current
    body = _contract_for(api, feed) | {
        "change_summary": f"Demo seed: this source now also delivers {feed.schema} files."
    }
    added: dict[str, Any] = api.post(
        "data_steward", f"/api/v1/sources/{source['id']}/contracts", body, 201
    )
    print(f"  source {source['code']}: contract v{added['version']} for {feed.schema}")
    return added


def _file(feed: Feed, src: Source) -> tuple[str, bytes]:
    """The sample as the source's format delivers it. The JSON cash-flow sample goes to the
    CSV instrument source as the same records in CSV, under the D-24 marker line."""
    path = SAMPLES / feed.file
    if path.suffix.lstrip(".").upper() == src.fmt:
        return feed.file, path.read_bytes()
    if path.suffix != ".json" or src.fmt != "CSV":
        raise SeedError(f"no conversion of {feed.file} to {src.fmt}")
    sample = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    out = io.StringIO()
    out.write(f"# {sample['marker']}\n")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(feed.columns)
    writer.writerows([record[c] for c in feed.columns] for record in sample["records"])
    return f"{path.stem}.csv", out.getvalue().encode("utf-8")


def _upload(api: Api, source_id: str, name: str, data: bytes, mime: str) -> dict[str, Any]:
    headers = api.auth("data_steward") | {"Idempotency-Key": _new_key()}
    r = api.http.post(
        "/api/v1/imports",
        headers=headers,
        data={"source_id": source_id, "synthetic_attested": "true"},
        files={"file": (name, data, mime)},
    )
    upload: dict[str, Any] = api.check(r, 201).json()
    return upload


def _pending_import(api: Api, source_id: str, feed: Feed, contract_id: str) -> dict[str, Any]:
    """An unprojected import of this contract left by an interrupted run, if any."""
    found = api.items(
        "data_steward",
        "/api/v1/imports",
        source_id=source_id,
        schema_code=feed.schema,
        projected="false",
        limit=50,
    )
    return next(
        (i for i in found if i["source_contract_id"] == contract_id and i["status"] in PENDING),
        {},
    )


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


def _ensure_mapping(
    api: Api, source_id: str, contract_id: str, upload: dict[str, Any], feed: Feed
) -> None:
    """The feed's mapping by code, created if absent, then moved on to ACTIVE: dry run on the
    import and submit (steward), approve (approver)."""
    found = api.items("data_steward", "/api/v1/mappings", source_id=source_id, limit=200)
    hit = next((m for m in found if m["code"] == feed.mapping_code), None)
    if hit is None:
        rules = (
            _instrument_rules()
            if feed.schema == "instrument"
            else _same_name_rules(api, upload, feed.columns)
        )
        body = {
            "code": feed.mapping_code,
            "name": feed.mapping_name,
            "source_contract_id": contract_id,
            "rules": rules,
        }
        version = api.post("data_steward", "/api/v1/mappings", body, 201)["versions"][0]
        print(f"  mapping {feed.mapping_code} ({feed.mapping_name!r}) created")
    else:
        if hit["source_contract_id"] != contract_id:
            raise SeedError(
                f"mapping {feed.mapping_code} maps contract v{hit['contract_version']}, not the "
                "source's current one: reset the demo database and seed again"
            )
        if hit["active_version"] is not None:
            return
        detail = api.get("data_steward", f"/api/v1/mappings/{hit['id']}")
        version = max(detail["versions"], key=lambda v: int(v["version"]))
    base = f"/api/v1/mappings/{version['mapping_id']}/versions/{version['version']}"
    status = version["status"]
    if status == "DRAFT":
        dry = api.post(
            "data_steward",
            f"/api/v1/imports/{upload['id']}/mappings/validate",
            {"mapping_version_id": version["id"]},
            201,
        )
        if not dry["ok"]:
            raise SeedError(f"mapping {feed.mapping_code} dry run failed: {json.dumps(dry)}")
        status = api.post("data_steward", f"{base}/submit", {}, 200)["status"]
    if status == "IN_REVIEW":
        status = api.post("approver", f"{base}/approval", {"decision": "APPROVE"}, 200)["status"]
    if status != "ACTIVE":
        raise SeedError(f"mapping {feed.mapping_code} v{version['version']} is {status}")


def _validate_and_project(api: Api, upload: dict[str, Any]) -> int:
    import_id = upload["id"]
    if upload["status"] not in VALIDATED:
        job = api.post("data_steward", f"/api/v1/imports/{import_id}/validate", {}, 200)
        if job["status"] not in VALIDATED:
            raise SeedError(f"import {import_id} validated as {job['status']}")
    projection = api.post("data_steward", f"/api/v1/imports/{import_id}/projection", {}, 201)
    return int(projection["projected_count"])


def project(api: Api, src: Source, feed: Feed) -> None:
    """One sample through its source: contract, import, ACTIVE mapping, validate, project.
    A step already done is reused, so a re-run creates nothing."""
    source = _ensure_source(api, src, feed)
    if _projected_import(api, feed.schema, source["id"]):
        print(f"  {feed.schema} via {src.code}: already projected, skipped")
        return
    contract = _ensure_contract(api, source, feed)
    upload = _pending_import(api, source["id"], feed, contract["id"])
    if not upload:
        name, data = _file(feed, src)
        upload = _upload(api, source["id"], name, data, src.mime)
        if upload["schema_code"] != feed.schema:
            raise SeedError(f"{name} landed in {upload['schema_code']}, not {feed.schema}")
    _ensure_mapping(api, source["id"], contract["id"], upload, feed)
    count = _validate_and_project(api, upload)
    print(f"  {feed.schema} via {src.code}: {count} projected")


# --- 1. projected market data ------------------------------------------------------------------


def seed_projected_data(api: Api) -> None:
    project(api, CORE, INSTRUMENT)
    project(api, POSITIONS, POSITION)
    project(api, CORE, CASHFLOW)  # after the instruments: a new contract version on CORE


# --- 2. credit data ---------------------------------------------------------------------------


def seed_credit_data(api: Api) -> None:
    project(api, COUNTERPARTIES, COUNTERPARTY)
    project(api, EXPOSURES, CREDIT_EXPOSURE)


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


def _snapshot(api: Api) -> str:
    """One snapshot of the projected instrument, position and cash-flow imports of the demo
    sources (the server returns an equal existing snapshot instead of a new one)."""
    import_ids = []
    for src, feed in ((CORE, INSTRUMENT), (POSITIONS, POSITION), (CORE, CASHFLOW)):
        source = _find_source(api, src.code)
        import_id = _mapped_import(api, feed, source["id"]) if source else None
        if import_id is None:
            raise SeedError(f"no projected {feed.schema} import from {src.code} to snapshot")
        import_ids.append(import_id)
    snapshot = api.post("risk_analyst", "/api/v1/dataset-snapshots", {"import_ids": import_ids})
    print(f"  snapshot {snapshot['snapshot_id']}: {len(snapshot['members'])} imports")
    return str(snapshot["snapshot_id"])


def _succeeded_run(api: Api, module_code: str, snapshot_id: str) -> dict[str, Any] | None:
    """A finished run of the module on this snapshot and reporting date, if any."""
    for status in SUCCEEDED:
        path = "/api/v1/calculation-runs"
        runs = api.items("risk_analyst", path, module_code=module_code, status=status, limit=200)
        pins = (snapshot_id, REPORTING_DATE)
        hit = next(
            (r for r in runs if (r["dataset_snapshot_id"], r["reporting_date"]) == pins), None
        )
        if hit is not None:
            return hit
    return None


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
    snapshot_id = _snapshot(api)
    for code in CALC_MODULES:
        run = _succeeded_run(api, code, snapshot_id)
        if run is not None:
            print(f"  {code}: run {run['run_id']} already {run['status']}, skipped")
            continue
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


# --- 6. opt-in: the typed-maturity instrument contract (Phase 31, D-57) ------------------------

TYPED_INSTRUMENT = Feed(
    "instrument", "instrument.csv", "instrument_id", _csv_columns("instrument.csv"),
    "demo_instruments_typed", "Core banking instruments, typed maturity (demo)",
)  # fmt: skip
OPEN_PROPOSALS = ("DRAFT", "SUBMITTED")


def _typed_version(api: Api) -> dict[str, Any] | None:
    """The instrument schema version whose contract is INSTRUMENT_TYPED_MATURITY, if any."""
    schema = _schema(api, "instrument")
    versions = api.items("data_steward", f"/api/v1/schemas/{schema['id']}/versions")
    return next(
        (v for v in versions if v["content_sha256"] == INSTRUMENT_TYPED_MATURITY_SHA256), None
    )


def _typed_proposal(api: Api, version_id: str) -> dict[str, Any] | None:
    proposals = api.items("data_steward", "/api/v1/schema-change-proposals", limit=200)
    return next((p for p in proposals if p["proposed_version"]["id"] == version_id), None)


def publish_typed_contract(api: Api) -> dict[str, Any]:
    """Propose (data_steward), analyse, submit and approve (approver) the frozen contract,
    or move an earlier attempt on. Returns the ACTIVE version."""
    version = _typed_version(api)
    if version is not None and version["status"] == "ACTIVE":
        print(f"  instrument v{version['version']} (typed maturity) already ACTIVE, skipped")
        return version
    proposal = _typed_proposal(api, version["id"]) if version else None
    if proposal is None:
        body = {
            "schema_id": _schema(api, "instrument")["id"],
            "title": INSTRUMENT_TYPED_MATURITY_TITLE,
            "rationale": "Demo seed (opt-in): type the maturity date (D-57 option B).",
            "fields": list(INSTRUMENT_TYPED_MATURITY),
        }
        proposal = api.post("data_steward", "/api/v1/schema-change-proposals", body, 201)
    elif proposal["status"] not in OPEN_PROPOSALS:
        raise SeedError(f"the typed-maturity proposal is {proposal['status']}")
    base = f"/api/v1/schema-change-proposals/{proposal['id']}"
    if proposal["status"] == "DRAFT":
        if proposal.get("impact") is None:  # the impact analysis precedes the submission
            proposal = api.post("data_steward", f"{base}/impact-analysis", None, 200)
        proposal = api.post("data_steward", f"{base}/submit", {}, 200)
    if proposal["status"] == "SUBMITTED":
        proposal = api.post("approver", f"{base}/approval", {"decision": "APPROVE"}, 200)
    version = _typed_version(api)
    if version is None or version["status"] != "ACTIVE":
        raise SeedError(f"the typed-maturity proposal ended {proposal['status']}")
    print(f"  instrument v{version['version']} (typed maturity) ACTIVE; v1 DEPRECATED")
    return version


def seed_typed_instruments(api: Api) -> None:
    version = publish_typed_contract(api)
    source = _ensure_source(api, CORE, TYPED_INSTRUMENT)
    projected = api.items(
        "data_steward",
        "/api/v1/imports",
        source_id=source["id"],
        schema_code="instrument",
        projected="true",
        limit=200,
    )
    if any(i["schema_version_id"] == version["id"] for i in projected):
        print(f"  typed instruments via {CORE.code}: already projected, skipped")
        return
    contract = _ensure_contract(api, source, TYPED_INSTRUMENT)
    upload = _pending_import(api, source["id"], TYPED_INSTRUMENT, contract["id"])
    if not upload:
        name, data = _file(TYPED_INSTRUMENT, CORE)
        upload = _upload(api, source["id"], name, data, CORE.mime)
    if upload["schema_version_id"] != version["id"]:
        raise SeedError("the typed import does not pin the typed-maturity version")
    _ensure_mapping(api, source["id"], contract["id"], upload, TYPED_INSTRUMENT)
    count = _validate_and_project(api, upload)
    print(f"  typed instruments via {CORE.code}: {count} projected (projector v2)")


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
    parser.add_argument(
        "--publish-typed-instrument",
        action="store_true",
        help="Opt-in (D-57 §3): publish the typed-maturity instrument contract through the "
        "governed proposal path and project the instrument sample under it (projector v2).",
    )
    args = parser.parse_args(argv)
    if _refused():
        return 2

    api = Api(args.api)
    try:
        api.check(api.http.get("/api/v1/health"), 200)
    except (httpx.HTTPError, SeedError) as exc:
        print(f"The backend is not reachable at {args.api} ({exc}).", file=sys.stderr)
        return 1

    steps: tuple[tuple[str, Callable[[], None]], ...] = (
        ("Projected synthetic data", lambda: seed_projected_data(api)),
        ("Credit data", lambda: seed_credit_data(api)),
        ("Obligor ratings", lambda: seed_ratings(api)),
        (f"Calculations (reporting date {REPORTING_DATE})", lambda: seed_calculations(api)),
        ("Scenarios", lambda: seed_scenarios(api)),
    )
    if args.publish_typed_instrument:
        steps += (("Typed-maturity instruments (opt-in)", lambda: seed_typed_instruments(api)),)
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
