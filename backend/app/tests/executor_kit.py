"""Phase 33 (BE-33.1 - BE-33.3, D-59 §1-§5): background execution, folded into
test_calculations part 5 (+0 tests, D-26).

The test modules here are module-level functions so a process-pool worker can unpickle them
by reference: the executor must ship the registered module's `function` to the worker (or
fork after the registry is patched). A crash module exits its process, so it refuses to run
in the API (test) process itself: an inline fallback fails the test instead of killing it.
"""

import dataclasses
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.calculations.constants import FINISH_RUN
from app.calculations.modules import REGISTRY, maturity_ladder
from app.calculations.modules.base import RegisteredModule
from app.core.config import Settings
from app.main import create_app
from app.tests.conftest import response, sql
from app.tests.helpers import RUNS, cid, events, start_run

# The API (test) process: a spawned or forked worker inherits the variable, not the pid.
os.environ.setdefault("EXECUTOR_KIT_PARENT_PID", str(os.getpid()))
SLOW_SECONDS = 1.5
HANG_SECONDS = 30.0
HEALTH_BUDGET_SECONDS = 0.2
ASYNC = {"Prefer": "respond-async"}


def in_worker() -> bool:
    return os.getpid() != int(os.environ["EXECUTOR_KIT_PARENT_PID"])


def slow_ladder(inputs: Any, parameters: Any) -> Any:
    """The real maturity ladder, after a pause (same result, so the same result_sha256)."""
    time.sleep(SLOW_SECONDS)
    return maturity_ladder.MODULE.function(inputs, parameters)


def hanging_ladder(inputs: Any, parameters: Any) -> Any:
    """Outlives CALC_RUN_TIMEOUT_SECONDS; in the API process it would block the event loop."""
    if not in_worker():
        raise RuntimeError("executor_kit: hanging_ladder ran in the API process, not a worker")
    time.sleep(HANG_SECONDS)
    return maturity_ladder.MODULE.function(inputs, parameters)


def crashing_ladder(inputs: Any, parameters: Any) -> Any:
    """A hard worker crash (no exception to catch, the process is gone)."""
    if not in_worker():
        raise RuntimeError("executor_kit: crashing_ladder ran in the API process, not a worker")
    os._exit(13)


def ladder_with(function: Callable[[Any, Any], Any]) -> RegisteredModule:
    return dataclasses.replace(maturity_ladder.MODULE, function=function)


def pool_client(db: str, artifact_dir: Path, **extra: Any) -> TestClient:
    # Q1 ruling: the kit posts more runs per app than the default burst (5); the limiter
    # stays on (RATE_LIMIT_ENABLED default).
    extra.setdefault("RATE_LIMIT_CALCULATION_RUN_BURST", 20)
    settings = Settings(
        ENV="test",
        DATABASE_URL=db,
        ARTIFACT_STORE_DIR=artifact_dir,
        CALC_EXECUTION_MODE="pool",
        **extra,
    )
    # extra="ignore" on Settings: prove the Phase 33 settings exist and took effect.
    assert settings.CALC_EXECUTION_MODE == "pool"
    for name, value in extra.items():
        assert getattr(settings, name) == value, name
    return TestClient(create_app(settings))


def admitted(client: TestClient) -> int:
    """Runs the app's executor holds admitted (executing, queued or being pinned)."""
    count: int = client.app.state.run_executor.admitted  # type: ignore[attr-defined]
    return count


def poll(api: TestClient, headers: dict[str, str], url: str, deadline: float = 30.0) -> Any:
    """GET the run until it leaves RUNNING (backoff up to 1 s)."""
    end, wait = time.monotonic() + deadline, 0.05
    while True:
        r = api.get(url, headers=headers)
        assert r.status_code == 200, r.text
        if r.json()["status"] != "RUNNING":
            return r.json()
        assert time.monotonic() < end, f"run still RUNNING after {deadline}s: {url}"
        time.sleep(wait)
        wait = min(wait * 2, 1.0)


def warnings_of(run: Any) -> list[tuple[str, Any]]:
    return [(w["code"], w["details"]) for w in run["warnings"]]


def terminal_moves(db: str) -> dict[str, int]:
    """run id -> its calculation_run.finish audit events (a run is finished exactly once)."""
    rows = sql(
        db,
        "SELECT resource_id, count(*) AS n FROM audit_event WHERE action = $1"
        " GROUP BY resource_id",
        FINISH_RUN,
    )
    return {r["resource_id"]: r["n"] for r in rows}


def run_row(db: str, run_id: str) -> dict[str, Any]:
    [row] = sql(
        db,
        "SELECT status, result_sha256, manifest_sha256, ended_at, outcome::text AS outcome,"
        " warning_count FROM calculation_run WHERE id = $1",
        uuid.UUID(run_id),
    )
    return dict(row)


def check_background_execution(
    api: TestClient,
    db: str,
    artifact_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    analyst: dict[str, str],
    body: dict[str, Any],
    reference: dict[str, Any],
) -> None:
    """BE-33.1 - BE-33.3 against `reference`, a terminal inline run of `body` (the golden run).
    Every run it writes is a maturity_ladder run of `body`, with the reference's hashes."""
    ladder_key = (maturity_ladder.SPEC.code, maturity_ladder.SPEC.version)
    assert REGISTRY[ladder_key] is maturity_ladder.MODULE
    same = ("manifest_sha256", "result_sha256", "status", "rule_set_id", "assumption_set_id")

    def matches(run: Any, label: str) -> None:
        for key in same:
            assert run[key] == reference[key], (label, key, run[key], reference[key])

    # --- BE-33.2: without Prefer the contract is Phase 32's; with it, 202 + polling ------------
    plain = start_run(api, analyst, body)
    assert plain.status_code == 201, plain.text
    assert set(plain.json()) == set(reference) and "Preference-Applied" not in plain.headers
    matches(plain.json(), "plain")
    c = cid()
    accepted = start_run(api, analyst, body, key="async-run", **ASYNC, **{"X-Correlation-ID": c})
    assert accepted.status_code == 202, accepted.text
    started = accepted.json()
    assert set(started) == set(reference) and started["status"] == "RUNNING", started
    assert started["result_sha256"] is None
    location = f"{RUNS}/{started['run_id']}"
    assert accepted.headers["Location"].endswith(location), accepted.headers.get("Location")
    assert accepted.headers["Preference-Applied"] == "respond-async"
    finished = poll(api, analyst, location)
    assert finished["run_id"] == started["run_id"]
    matches(finished, "async")
    assert [(e["action"], e["outcome"]) for e in events(db, c)] == [
        ("calculation_run.create", "SUCCESS"),
        ("calculation_run.finish", "SUCCESS"),
    ]
    for headers in (ASYNC, {}):  # a replay is the same run, whatever the preference
        replay = start_run(api, analyst, body, key="async-run", **headers)
        assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json()["run_id"] == started["run_id"], replay.text
    # Prefer without respond-async (RFC 7240: other preferences) is the plain contract.
    other = start_run(api, analyst, body, Prefer="return=minimal")
    assert other.status_code == 201 and "Preference-Applied" not in other.headers, other.text

    # --- BE-33.1: the pool gives the same hashes, keeps the loop free, refuses a full queue ----
    with pool_client(db, artifact_dir, CALC_WORKERS=1, CALC_WORKER_QUEUE_MAX=1) as pool:
        pooled = start_run(pool, analyst, body)
        assert pooled.status_code == 201, pooled.text
        matches(pooled.json(), "pool")
        assert pooled.json()["run_id"] != reference["run_id"]

        # A slow module runs in the worker while the health check answers within 200 ms; the
        # waiting request is the plain (synchronous) contract.
        monkeypatch.setitem(REGISTRY, ladder_key, ladder_with(slow_ladder))
        out: dict[str, Any] = {}
        waiting = threading.Thread(
            target=lambda: out.setdefault("r", start_run(pool, analyst, body, key="slow-sync"))
        )
        waiting.start()
        time.sleep(0.3)  # the run is in the worker now
        assert waiting.is_alive(), "the slow run finished before the health probe"
        timings = []
        for _ in range(5):
            t0 = time.perf_counter()
            health = pool.get("/api/v1/health")
            timings.append(time.perf_counter() - t0)
            assert health.status_code == 200, health.text
        assert max(timings) < HEALTH_BUDGET_SECONDS, timings
        # The run waits on its worker without a connection idle in transaction: its read-only
        # transaction ended before the hand-off (the pool stays free for other requests).
        idle = sql(
            db,
            "SELECT count(*) AS n FROM pg_stat_activity"
            " WHERE datname = current_database() AND state = 'idle in transaction'",
        )
        assert idle[0]["n"] == 0, idle
        assert waiting.is_alive(), "the health probes outlasted the slow run"
        waiting.join(timeout=30)
        slow = response(out["r"])
        assert slow.status_code == 201, slow.text
        matches(slow.json(), "slow pool")

        # A full queue: 503 workers_busy, retryable with Retry-After, audited DENIED, no run.
        accepted_ids, busy = [], None
        for n in range(4):  # 1 worker + 1 queued slot: a refusal by the third request
            c = cid()
            r = start_run(pool, analyst, body, key=f"busy-{n}", **ASYNC, **{"X-Correlation-ID": c})
            if r.status_code == 503:
                busy = (r, c, f"busy-{n}")
                break
            assert r.status_code == 202, r.text
            accepted_ids.append(r.json()["run_id"])
        assert busy is not None and len(accepted_ids) <= 2, accepted_ids
        r, c, key = busy
        assert (r.json()["code"], r.json()["retryable"]) == ("workers_busy", True), r.text
        assert int(r.headers["Retry-After"]) >= 1
        [event] = events(db, c)
        assert (event["action"], event["outcome"]) == ("calculation_run.create", "DENIED")
        assert "workers_busy" in str(event["details"])
        assert sql(db, "SELECT id FROM calculation_run WHERE idempotency_key = $1", key) == []
        for run_id in accepted_ids:  # the queued runs still finish, with the same hashes
            matches(poll(pool, analyst, f"{RUNS}/{run_id}"), "queued")

        # A worker crash ends the run FAILED ENGINE_ERROR; the pool recovers.
        monkeypatch.setitem(REGISTRY, ladder_key, ladder_with(crashing_ladder))
        crashed = start_run(pool, analyst, body, key="crashed-run")
        assert crashed.status_code == 201, crashed.text
        assert crashed.json()["status"] == "FAILED", crashed.text
        assert [code for code, _ in warnings_of(crashed.json())] == ["ENGINE_ERROR"]
        monkeypatch.setitem(REGISTRY, ladder_key, maturity_ladder.MODULE)
        matches(start_run(pool, analyst, body).json(), "after crash")
        assert admitted(pool) == 0  # every admission was released, the failed ones too
    monkeypatch.setitem(REGISTRY, ladder_key, maturity_ladder.MODULE)

    # --- BE-33.3: the run timeout, the retry and the sweeper -----------------------------------
    timeouts = {"CALC_RUN_TIMEOUT_SECONDS": 1}
    with pool_client(db, artifact_dir, CALC_WORKERS=1, CALC_WORKER_QUEUE_MAX=1, **timeouts) as pool:
        monkeypatch.setitem(REGISTRY, ladder_key, ladder_with(hanging_ladder))
        t0 = time.monotonic()
        timed_out = start_run(pool, analyst, body, key="timed-out-run")
        assert time.monotonic() - t0 < HANG_SECONDS / 3, "the timeout did not end the run"
        assert timed_out.status_code == 201, timed_out.text
        failed = timed_out.json()
        assert failed["status"] == "FAILED" and failed["result_sha256"], failed
        assert [code for code, _ in warnings_of(failed)] == ["ENGINE_ERROR"]
        before = run_row(db, failed["run_id"])
        monkeypatch.setitem(REGISTRY, ladder_key, maturity_ladder.MODULE)
        # The worker was replaced: a retry (a new key) is a new run with the expected hashes.
        retry = start_run(pool, analyst, body, key="timed-out-retry")
        assert retry.status_code == 201, retry.text
        assert retry.json()["run_id"] != failed["run_id"]
        matches(retry.json(), "retry")
        # The old key's replay returns the FAILED run, never mutated.
        old = start_run(pool, analyst, body, key="timed-out-run")
        assert old.status_code == 200 and old.json()["run_id"] == failed["run_id"], old.text
        assert old.json()["status"] == "FAILED"
        assert run_row(db, failed["run_id"]) == before
    monkeypatch.setitem(REGISTRY, ladder_key, maturity_ladder.MODULE)

    # The sweeper closes a stale RUNNING run without any request touching it.
    sql(
        db,
        "INSERT INTO calculation_run SELECT * FROM (SELECT (jsonb_populate_record("
        "NULL::calculation_run, to_jsonb(r) || jsonb_build_object('id', gen_random_uuid(),"
        " 'status', 'RUNNING', 'result_sha256', NULL, 'ended_at', NULL, 'outcome', NULL,"
        " 'warning_count', 0, 'idempotency_key', 'swept-run',"
        " 'started_at', clock_timestamp() - interval '1 hour'))).*"
        " FROM calculation_run r WHERE r.id = $1) x",
        uuid.UUID(reference["run_id"]),
    )
    [swept] = sql(db, "SELECT id FROM calculation_run WHERE idempotency_key = 'swept-run'")
    sweeper = {
        "CALC_RUN_TIMEOUT_SECONDS": 1,
        "CALC_RUN_STALE_SECONDS": 2,
        "CALC_SWEEP_INTERVAL_SECONDS": 1,
    }
    with pool_client(db, artifact_dir, **sweeper) as pool:
        end = time.monotonic() + 15
        while (
            sql(db, "SELECT status FROM calculation_run WHERE id = $1", swept["id"])[0]["status"]
            == "RUNNING"
        ):
            assert time.monotonic() < end, "the sweeper never closed the stale run"
            time.sleep(0.25)
        closed = pool.get(f"{RUNS}/{swept['id']}", headers=analyst).json()
        assert closed["status"] == "FAILED", closed
        assert warnings_of(closed) == [("ENGINE_ERROR", {"error": "stale_run"})]
        # Q9 ruling: the sweeper's cross-tenant read ends the run as the system, no user.
        [finish] = sql(
            db,
            "SELECT actor, actor_user_id FROM audit_event WHERE action = $1 AND resource_id = $2",
            FINISH_RUN,
            str(swept["id"]),
        )
        assert (finish["actor"], finish["actor_user_id"]) == ("system:run-sweeper", None)

    # No run got two terminal moves (the 0018 guards and the single terminal move).
    moves = terminal_moves(db)
    assert all(n == 1 for n in moves.values()), {k: n for k, n in moves.items() if n != 1}


def check_pool_scenario_run(
    db: str,
    artifact_dir: Path,
    analyst: dict[str, str],
    scenario_id: str,
    baseline_run_id: str,
    inline_members: list[dict[str, Any]],
) -> None:
    """BE-33.1 (Q11 ruling), folded into scenario_run_kit (+0): a scenario's k + 1 member runs
    in the pool. Each stressed member ships the overlay's inputs to a worker and makes a second
    (unstressed) executor call for conservation, and the members share one admission. Every
    member's status and hashes equal its inline twin's, and the admission is released."""
    with pool_client(db, artifact_dir, CALC_WORKERS=1, CALC_WORKER_QUEUE_MAX=1) as pool:
        r = pool.post(
            f"/api/v1/scenarios/{scenario_id}/run",
            json={"version": 1, "baseline_run_id": baseline_run_id},
            headers={**analyst, "Idempotency-Key": f"pool-scenario-{uuid.uuid4()}"},
        )
        assert r.status_code == 201, r.text
        pooled = {tuple(m["shock_subset"]): m for m in r.json()["members"]}
        inline = {tuple(m["shock_subset"]): m for m in inline_members}
        assert set(pooled) == set(inline) and len(inline) > 1, sorted(pooled)
        for subset, twin in inline.items():
            member = pooled[subset]
            assert member["calculation_run_id"] != twin["calculation_run_id"], subset
            for key in ("status", "manifest_sha256", "result_sha256"):
                assert member[key] == twin[key], (subset, key, member[key], twin[key])
        assert admitted(pool) == 0
