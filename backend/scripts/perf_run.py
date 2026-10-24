"""scripts/perf_run.py (BE-33.7, D-59 §7): measure the calculation engine at scale.

Deterministic synthetic sets of cash flows (10,000 and 100,000 by default, ILLUSTRATIVE and
SYNTHETIC only) run through the maturity_ladder module under the seeded rule parameters, as
a run does: the module's pure run() (inline on the event loop, or in a pool worker) and then,
in the API process, the canonical result hash. For each size and mode it records:

- `duration_seconds`: module run plus result hash (wall clock);
- peak RSS in MiB: of the API process (`api_peak_rss_mib`) and, in pool mode, of the worker
  (`worker_peak_rss_mib`), each a fresh process (Linux VmHWM; ru_maxrss elsewhere, API only);
- `health_p95_ms` (and max, sample count): GET /api/v1/health through the app, every 50 ms
  while the run executes, on the same event loop. Inline, the loop is blocked for the run, so
  a probe waits for all of it (often the only sample).

Each measurement runs in its own subprocess (a clean peak RSS). Inline and pool must give the
same `result_sha256` for the same set; the script fails if they do not. A 5,000-flow
measurement is recorded too, with ceilings at 3x its values for the fold (BE-33.7).

    cd backend && python -m scripts.perf_run                 # writes the default --out
    python -m scripts.perf_run --sizes 5000 --out /tmp/p.json

The health probe needs a reachable database: `--database-url`, else DATABASE_URL (or
backend/.env). Never the live dev database (D-03).
"""

import argparse
import asyncio
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
DEFAULT_OUT = REPO / "docs" / "evidence" / "phase-33" / "perf.json"
# The supported size, concurrency and multi-instance needs, stated from DEFAULT_OUT (BE-33.7).
PERFORMANCE_DOC = REPO / "docs" / "PERFORMANCE.md"
SIZES = (10_000, 100_000)
MODES = ("inline", "pool")
FOLD_FLOWS = 5_000
CEILING_FACTOR = 3
PROBE_INTERVAL_SECONDS = 0.05
REPORTING_DATE = date(2026, 6, 30)
CURRENCIES = ("EUR", "USD", "GBP")
INSTRUMENT_TYPES = ("LOAN", "BOND", "DEPOSIT")
FLOWS_PER_INSTRUMENT = 10
LIVE_DEV_DATABASE = "moodymate_dev"


def _lcg(seed: int) -> Any:
    """A fixed 64-bit linear congruential sequence: the same set on every machine."""
    state = seed
    while True:
        state = (state * 6364136223846793005 + 1442695040888963407) % 2**64
        yield state >> 11


def synthetic_inputs(flows: int, seed: int = 33) -> Any:
    """`flows` synthetic cash flows over flows/10 instruments: due dates from 30 days past
    due to ~11 years out, amounts 0.01..99,999.99 with exactly two decimals, three
    currencies. Deterministic ids (uuid5), so the result hash is reproducible."""
    from app.calculations.modules.base import CashFlowInput, InstrumentInput, ModuleInputs

    ns = uuid.uuid5(uuid.NAMESPACE_URL, f"moodymate:perf:{seed}")
    rand = _lcg(seed)
    instruments = []
    for i in range(max(1, flows // FLOWS_PER_INSTRUMENT)):
        key = f"SYN-{i:07d}"
        instruments.append(
            InstrumentInput(
                uuid.uuid5(ns, f"iv:{i}"),
                uuid.uuid5(ns, f"i:{i}"),
                key,
                INSTRUMENT_TYPES[i % len(INSTRUMENT_TYPES)],
            )
        )
    cash_flows = []
    for n in range(flows):
        r = next(rand)
        cents = r % 9_999_999 + 1
        cash_flows.append(
            CashFlowInput(
                uuid.uuid5(ns, f"cf:{n}"),
                instruments[n % len(instruments)].business_key,
                REPORTING_DATE + timedelta(days=int((r >> 24) % 4_000) - 30),
                Decimal(f"{cents // 100}.{cents % 100:02d}"),
                CURRENCIES[(r >> 40) % len(CURRENCIES)],
                "CONTRACTUAL",
            )
        )
    return ModuleInputs(REPORTING_DATE, tuple(cash_flows), tuple(instruments))


def seeded_pins() -> Any:
    from app.calculations.modules.base import PinnedRule
    from app.rules.constants import SEEDED_RULES

    ns = uuid.uuid5(uuid.NAMESPACE_URL, "moodymate:perf:rules")
    return {code: PinnedRule(uuid.uuid5(ns, code), seed[3]) for code, seed in SEEDED_RULES.items()}


def _status_kib(field: str, pid: int | str = "self") -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(f"{field}:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _mib(kib: int | None) -> float | None:
    return round(kib / 1024, 1) if kib is not None else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[-1]


async def _measure(flows: int, mode: str, database_url: str) -> dict[str, Any]:
    import httpx

    from app.calculations.constants import CalculationRunStatus, WarningSeverity
    from app.calculations.executor import RunExecutor
    from app.calculations.modules import maturity_ladder
    from app.calculations.service import reproduce
    from app.core.config import Settings
    from app.main import create_app

    module = maturity_ladder.MODULE
    inputs, pins = synthetic_inputs(flows), seeded_pins()
    settings = Settings(
        ENV="test", DATABASE_URL=database_url, LOG_LEVEL="WARNING", RATE_LIMIT_ENABLED=False
    )
    app = create_app(settings)
    executor = RunExecutor(mode="pool" if mode == "pool" else "inline", timeout_seconds=3_600)
    transport = httpx.ASGITransport(app=app)
    worker_hwm: int | None = None
    async with httpx.AsyncClient(transport=transport, base_url="http://perf") as client:
        warm = await client.get("/api/v1/health")
        if warm.json().get("database") != "ok":
            raise SystemExit("perf_run: the health probe needs a reachable database")
        if executor.pooled:  # start the worker first: its startup is not the run
            started = time.perf_counter()
            await executor.run(module, synthetic_inputs(10), pins, module.context)
            worker_start = time.perf_counter() - started
        api_rss_before = _status_kib("VmRSS")
        latencies: list[float] = []
        done = asyncio.Event()

        async def probe() -> None:
            while not done.is_set():
                t0 = time.perf_counter()
                await client.get("/api/v1/health")
                latencies.append((time.perf_counter() - t0) * 1000)
                await asyncio.sleep(PROBE_INTERVAL_SECONDS)

        probing = asyncio.create_task(probe())
        await asyncio.sleep(0)  # the first probe is in flight before the run starts
        t0 = time.perf_counter()
        result = await executor.run(module, inputs, pins, module.context, executor.deadline())
        warned = any(w.severity != WarningSeverity.INFO for w in result.warnings)
        status = (
            CalculationRunStatus.SUCCEEDED_WITH_WARNINGS
            if warned
            else CalculationRunStatus.SUCCEEDED
        )
        *_, result_sha256 = reproduce(
            None, status, result.liquidity_gap, result.contributions, result.warnings, {}
        )
        duration = time.perf_counter() - t0
        done.set()
        await probing
        if executor.pooled:
            for slot in executor._slots:  # the worker's own high-water mark, before it stops
                pool = slot._pool
                for pid in getattr(pool, "_processes", None) or {}:
                    worker_hwm = _status_kib("VmHWM", pid)
    await executor.aclose()
    await app.state.db.dispose()
    api_hwm = _status_kib("VmHWM") or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    out: dict[str, Any] = {
        "flows": flows,
        "mode": mode,
        "status": status.value,
        "contributions": len(result.contributions),
        "warnings": len(result.warnings),
        "result_sha256": result_sha256,
        "duration_seconds": round(duration, 3),
        "api_rss_before_run_mib": _mib(api_rss_before),
        "api_peak_rss_mib": _mib(api_hwm),
        "worker_peak_rss_mib": _mib(worker_hwm),
        "health_samples": len(latencies),
        "health_p95_ms": round(p, 1) if (p := _p95(latencies)) is not None else None,
        "health_max_ms": round(max(latencies), 1) if latencies else None,
    }
    if executor.pooled:
        out["worker_start_seconds"] = round(worker_start, 3)
    return out


def measure(flows: int, mode: str, database_url: str) -> dict[str, Any]:
    """One measurement in a fresh subprocess (a clean peak RSS)."""
    with tempfile.TemporaryDirectory() as tmp:
        result_file = Path(tmp) / "result.json"
        env = {**os.environ, "DATABASE_URL": database_url, "ENV": "test"}
        done = subprocess.run(  # noqa: S603 - this interpreter, this script, fixed arguments
            [
                sys.executable,
                "-m",
                "scripts.perf_run",
                "--one",
                str(flows),
                mode,
                "--result-file",
                str(result_file),
                "--database-url",
                database_url,
            ],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0 or not result_file.exists():
            sys.stderr.write(done.stderr[-4000:])
            raise SystemExit(f"perf_run: the {mode} measurement of {flows} flows failed")
        result: dict[str, Any] = json.loads(result_file.read_text())
        return result


def _database_url(given: str | None) -> str:
    url = given or os.environ.get("DATABASE_URL")
    if url is None:
        from app.core.config import Settings

        url = Settings(ENV="test").DATABASE_URL  # backend/.env, if any
    if url.rsplit("/", 1)[-1].split("?")[0] == LIVE_DEV_DATABASE:
        raise SystemExit("perf_run: never the live dev database (D-03); use a copy")
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--database-url")
    parser.add_argument("--one", nargs=2, metavar=("FLOWS", "MODE"), help=argparse.SUPPRESS)
    parser.add_argument("--result-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    url = _database_url(args.database_url)
    if args.one:
        flows, mode = int(args.one[0]), args.one[1]
        result = asyncio.run(_measure(flows, mode, url))
        args.result_file.write_text(json.dumps(result))
        return

    runs = []
    for flows in args.sizes:
        pair = [measure(flows, mode, url) for mode in MODES]
        if pair[0]["result_sha256"] != pair[1]["result_sha256"]:
            raise SystemExit(f"perf_run: inline and pool hashes differ at {flows} flows")
        runs.extend(pair)
        for r in pair:
            print(
                f"{r['flows']:>7} {r['mode']:<6} {r['duration_seconds']:>8.3f}s "
                f"api {r['api_peak_rss_mib']} MiB worker {r['worker_peak_rss_mib']} MiB "
                f"health p95 {r['health_p95_ms']} ms (n={r['health_samples']})"
            )
    fold = {mode: measure(FOLD_FLOWS, mode, url) for mode in MODES}
    ceilings = {
        mode: {
            "duration_seconds": round(r["duration_seconds"] * CEILING_FACTOR, 3),
            "api_peak_rss_mib": round(r["api_peak_rss_mib"] * CEILING_FACTOR, 1),
        }
        for mode, r in fold.items()
    }
    document = {
        "statement": "ILLUSTRATIVE / SYNTHETIC: synthetic cash flows only; not client data.",
        "phase": 33,
        "task": "BE-33.7",
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpus": os.cpu_count(),
        },
        "method": {
            "module": "maturity_ladder v1 under the seeded (illustrative) rule parameters",
            "measured": "module run() plus the canonical result hash, per fresh process",
            "health_probe_interval_seconds": PROBE_INTERVAL_SECONDS,
            "pool": "one worker (CALC_WORKERS=1); worker start measured separately",
        },
        "runs": runs,
        "fold": {
            "flows": FOLD_FLOWS,
            "ceiling_factor": CEILING_FACTOR,
            "measured": fold,
            "ceilings": ceilings,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
