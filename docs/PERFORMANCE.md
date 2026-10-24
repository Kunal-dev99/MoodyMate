# Performance and supported size (Phase 33, BE-33.7, D-59 §7)

ILLUSTRATIVE / SYNTHETIC: every figure below comes from synthetic cash flows on a development
machine. None of it is client data, and none of it is a production commitment. The client
confirms the target size before production (D-59 §7). Until then the figures here are
**provisional**.

The figures come from `docs/evidence/phase-33/perf.json`, written by
`cd backend && python -m scripts.perf_run` (deterministic synthetic sets, maturity_ladder v1
under the seeded rule parameters). The run was against a disposable database, never
`moodymate_dev` (D-03). Machine: 4 CPUs, Python 3.12.3, Linux x86_64.

## What was measured

The script measures the engine only: the module's pure `run()` plus the canonical result hash,
each size and mode in a fresh process, with one pool worker. It does not measure the database
part of a run: reading the snapshot's rows and writing the outputs.

| Cash flows | Mode   | Duration | API peak RSS | Worker peak RSS | Health p95 (max), samples |
|-----------:|--------|---------:|-------------:|----------------:|---------------------------|
|      5,000 | inline |   0.08 s |    143.7 MiB |               - | 87.6 ms (87.6), 1         |
|      5,000 | pool   |   0.28 s |    146.9 MiB |        46.8 MiB | 52.4 ms (57.7), 3         |
|     10,000 | inline |   0.17 s |    154.2 MiB |               - | 178.1 ms (178.1), 1       |
|     10,000 | pool   |   0.65 s |    160.1 MiB |        53.7 MiB | 65.6 ms (80.6), 5         |
|    100,000 | inline |   2.31 s |    348.1 MiB |               - | 2,315.5 ms (2,315.5), 1   |
|    100,000 | pool   |   6.73 s |    445.4 MiB |       221.0 MiB | 244.2 ms (2,444.6), 51    |

Measured 2026-10-24T08:48:51Z (`measured_at` in perf.json), after Phases 32 and 34 were merged
in.

Inline and pool give the same `result_sha256` at every size; the script fails if they do not.

Pool worker start: about 0.3 s (0.285 to 0.345 s), paid once per worker. It is not counted in the
durations above.

Health probe: `GET /api/v1/health` every 50 ms during the run, on the same event loop.

- **Inline:** the loop is blocked for the whole run, so a probe waits for all of it.
- **Pool:** the module runs off the loop. Three steps still run on the loop, in the API
  process: pickling the inputs, unpickling the result, and the result hash. At 100,000 flows
  they stall it for up to about 2.4 s (the max above), and the p95 is 244 ms, **over the
  200 ms health budget**. That is the cost of those three steps on the loop; moving the hash
  into the worker is a deferred item (Q8). At 10,000 flows the pool p95 is 66 ms.

## Supported size (PROVISIONAL, for the client to confirm)

- **Per run: up to 50,000 cash flows.** A snapshot holds one import per schema, and one
  import holds at most `IMPORT_MAX_ROWS` = 50,000 rows (the D-16 hard ceiling). The engine was
  measured at twice that size (100,000), so it is not the limit. The database reads and writes
  of a 50,000-flow run are not measured here; they are the open part of the estimate.
- **Execution mode:** use `CALC_EXECUTION_MODE=pool` in deployed environments (Q4;
  `.env.production.example` sets it). The code default stays `inline` (D-02 reversibility).
  Inline blocks the API's event loop for the whole run: 2.3 s at 100,000 flows, during which
  every other request on that process waits.
- **Memory:** budget per API process is roughly the API peak plus one worker peak per
  `CALC_WORKERS`. At 100,000 flows with the default 2 workers: about 445 + 2 x 221 MiB, near
  0.9 GiB. Expect about half that at 50,000.

## Concurrency (per API process)

- At most `CALC_WORKERS` runs execute at once (default 2). Up to `CALC_WORKER_QUEUE_MAX` more
  are admitted to wait (default 8). Beyond that, a run request gets 503 `workers_busy`
  (retryable, `Retry-After`, audited DENIED, no run written).
- Startup refuses pool mode unless `CALC_WORKERS + CALC_WORKER_QUEUE_MAX` is below
  `DB_POOL_SIZE + DB_MAX_OVERFLOW` (defaults 10 < 15). An admitted run therefore never takes
  the API's last connections. While a run waits for a worker, it holds no connection.
- `CALC_RUN_TIMEOUT_SECONDS` (default 600) ends a run whose worker overruns: the worker is
  replaced and the run ends FAILED `ENGINE_ERROR`. The sweeper ends any run left RUNNING
  past `CALC_RUN_STALE_SECONDS` (default 900).
- A request that finds no free DB connection within `DB_POOL_TIMEOUT_SECONDS` gets 503
  `db_pool_exhausted` (retryable).
- Rate limits (D-53) cap run requests per principal: `RATE_LIMIT_CALCULATION_RUN_*` and
  `RATE_LIMIT_SCENARIO_RUN_*`.

## Multi-instance needs

The settings above are per process. Running more than one API instance needs the following:

1. **A shared rate-limit store** (rate-limit buckets, D-53 §5, deferred). The rate-limit buckets are in-process (D-53), so N
   instances allow N times the configured rates. A global limit needs a shared store (for
   example Redis), which is not built.
2. **S3 for artifacts.** The filesystem store is local to one host. Every instance must read
   every raw artifact, so set `ARTIFACT_STORE_KIND=s3` with one shared bucket (install the
   backend's `s3` extra; the backend image includes it), or mount one shared filesystem.
3. **The same `CURSOR_SIGNING_KEY` everywhere.** A list cursor issued by one instance must
   verify on another.
4. **A connection budget.** Total DB connections are instances x
   (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`). This must fit the server's `max_connections`.
5. **Per-instance limits.** Worker admission (`workers_busy`) is counted per instance.
   Fleet-wide run concurrency is instances x (`CALC_WORKERS` + `CALC_WORKER_QUEUE_MAX`).
6. **The sweeper.** It runs in every instance. That is safe: its guarded update makes one
   terminal move per run.

## The fold (CI)

`test_db_check_reports_each_failure` measures the 5,000-flow set again, inline and in the pool.
It asserts the recorded result hash, and that duration and peak RSS (API, and the worker in
pool mode) stay under 3x the recorded values (`fold.ceilings` in perf.json). After an
intended engine change, re-run the script and commit the new perf.json.

The supported size is for the client to confirm (D-59 §7).
