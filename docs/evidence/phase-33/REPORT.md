I verified each task against the code and the reported test lines. BE-33.1 to BE-33.6 and FE-33.1 are ticked; BE-33.7 is not, because its two evidence files are missing. Below is the report text for the coordinator to save as `/home/user/MoodyMate-wt/f06-p33/docs/evidence/phase-33/REPORT.md`.

# Phase 33: Background execution and data scale (checks)

**Scope.** This report covers BE-33.1 to BE-33.7 and FE-33.1 (Workstream E; RO-01, RO-07, FC-04; D-59).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f06-p33` by:
  - a backend-engineer and a test writer;
  - one frontend-engineer, who owns FE-33.1 and `features.ts` (`navigation.ts` did not change: no new routes);
  - one review agent and one e2e run (inline and pool);
  - a backend and a frontend fix round.
- **Commits.** Nothing is committed yet.
- **No migration this phase.** No table, column or CHECK changed. QUEUED and CANCELLED stay reserved (D-59 §3), so the CHECK hard stop was not reached. The head stays 0030.
- **Test budget (D-26, D-60 §1):** 49 → 49. Every new check is folded into an existing test function.

## What was built

- **BE-33.1 Process pool** (`backend/app/calculations/executor.py`).
  - **Workers.** `CALC_WORKERS` workers, each its own single-process pool, so a crashed or overrunning worker is replaced on its own. They start with `forkserver` (`spawn` where that is missing).
  - **Admission.** At most `CALC_WORKERS + CALC_WORKER_QUEUE_MAX` runs are admitted. The next one gets 503 `workers_busy` before anything is written: retryable, `Retry-After: 5`, audited DENIED, no run row. A scenario run takes one admission for all its member runs.
  - **What goes to the worker.** Only the module function, its Decimal context, the inputs and the parameters. Pins, DB work, post-checks and hashes stay in the API process.
  - **Switch.** `CALC_EXECUTION_MODE` defaults to `inline` (Q4).
  - **Fix round.**
    - In pool mode, the run's read-only transaction now ends before the hand-off (`service.py:2009-2010`), so no connection sits idle in transaction while the run waits.
    - Startup refuses pool mode unless `CALC_WORKERS + CALC_WORKER_QUEUE_MAX` is below `DB_POOL_SIZE + DB_MAX_OVERFLOW` (`config.py:361-367`).
- **BE-33.2 `Prefer: respond-async`.**
  - The header is parsed as RFC 7240 tokens. With it: 202 with the run RUNNING, `Location: /api/v1/calculation-runs/{id}` and `Preference-Applied: respond-async`.
  - The run then executes in a background task with its own session, which shutdown waits for.
  - A replay gives 200 with the same run. Without the header, nothing changes.
  - CORS exposes `Location` and `Preference-Applied` (`main.py:98-104`).
- **BE-33.3 Timeout, sweeper, retry.**
  - **New settings.** `CALC_RUN_TIMEOUT_SECONDS` (default 600, must be below `CALC_RUN_STALE_SECONDS`) and `CALC_SWEEP_INTERVAL_SECONDS` (default 60).
  - **Overrun or crash.** In pool mode, a run that overruns has its worker killed and replaced and ends FAILED `ENGINE_ERROR` (`WorkerTimeout`; a crash gives `WorkerCrashed`).
  - **Sweeper.** It ends stale RUNNING runs with `stale_run`, as `system:run-sweeper` (`service.py:2838-2874`).
  - **Lost races.** `RunNotRunning` makes a lost race a no-op, so each run makes exactly one terminal move.
- **BE-33.4 DB pool.**
  - **New settings.** `DB_POOL_SIZE` 5, `DB_MAX_OVERFLOW` 10 (0 allowed), `DB_POOL_TIMEOUT_SECONDS` 30 (SQLAlchemy's defaults), plus `DB_POOL_RECYCLE_SECONDS` and `DB_STATEMENT_TIMEOUT_MS` (both unset by default).
  - The statement timeout is set on every connection.
  - A pool timeout gives 503 `db_pool_exhausted`, retryable, with `Retry-After: 1`. The handler is app-wide, and the shared `5XX` description now says so (Q10, `errors.py:149-152`).
- **BE-33.5 Cursors** (`backend/app/core/pagination.py`).
  - **Format.** The cursor is `<payload>.<mac>`, about 62 characters. The payload is `{o, v}` (offset and version). The mac is an HMAC-SHA256 over the version, tenant, operationId, canonical filters and offset. Nothing is used before the mac verifies (:145-166).
  - **Signing key.** `CURSOR_SIGNING_KEY`: SecretStr, at least 32 characters, required outside local/test, and not the development key there (Q6).
  - **Contract base.** `backend/openapi.base.json` is the pre-phase contract (df7b41a, identical to origin/master's), used by the additive-only check (Q15).
  - **Inventory of capped reads: 20 routes.** Every GET with `cursor`, and every router `limit` site with `le=` calls `page_offset` (grep):
    - calculations: `listDatasetSnapshots`, `listCalculationRuns`, `getCalculationRunResults` (le=1000, default 200);
    - ingestion: `listSources`, `listImports`, `listImportIssues` and `getQuarantine` (both le=500, default 100);
    - mapping: `listMappings`;
    - domain: `listInstruments`, `listCounterparties`, `listCreditExposures`, `listObligorRatings`, `listLegalEntities`, `listIdentityLinks`;
    - governance: `listUsers`, `listAuditEvents`, `listProposals`;
    - orchestration: `listScenarios`, `listScenarioRuns`, `listScenarioComparisonContributions` (le=`MAX_CONTRIBUTIONS_PAGE`, default 100).
    - Every other list is le=200, default 50.
    - `listCalculationModules`, `listRules`, `listScenarioTemplates`, `listSchemas`, `listSchemaVersions`, `listCreditVocabularies` and `getImportDrift` take no `limit`.
  - **Internal caps that are not list limits** (engineer's list):
    - comparison largest changes, top 10 per currency (`orchestration/service.py:1587`);
    - profile top values (`ingestion/service.py:1584`);
    - profile values truncated to 100 characters (:1606);
    - issue messages truncated to 500 characters (:2049);
    - revert title truncated to 200 characters (`governance/service.py:782`);
    - the rate limiter keeps 10,000 buckets;
    - `ingestion/repository.py:299` is a batch reader that walks every row, not a truncation.
- **BE-33.6 S3 store.**
  - `S3ArtifactStore` in `backend/app/core/artifacts.py`, chosen by `ARTIFACT_STORE_KIND` (default `filesystem`).
  - **Write-once and verified.** It checks for the object first (an existing one is re-verified, never rewritten), then writes with `IfNoneMatch="*"`. Every read is verified, and it never deletes.
  - **Settings.** `ARTIFACT_S3_BUCKET`, `_PREFIX`, `_REGION`, `_ENDPOINT_URL`, and the access-key pair as SecretStr (`config.py:283-329`).
  - **Library.** boto3, as the optional `s3` extra (`pyproject.toml:39-42`, `boto3>=1.35.2,<2`). It is imported only for s3, and startup refuses a botocore without `IfNoneMatch`. `.env.example` has commented placeholders.
- **BE-33.7 Perf.**
  - `backend/scripts/perf_run.py` runs deterministic synthetic sets (10k, 100k and the 5,000-flow fold), one fresh subprocess per measurement. It stops if inline and pool hashes differ, and refuses `moodymate_dev` by name (:253-254).
  - The fold is written (`test_db_check.py:27-46`, called at :105).
  - **The two evidence files are not in `docs/`** (see Open items).
- **FE-33.1.** Three switches in `features.ts`; each set to `off` restores the Phase 32 UI:
  - `NEXT_PUBLIC_LIST_PAGING_UI`;
  - `NEXT_PUBLIC_BACKGROUND_RUNS_UI` (only active when the calculations UI is on);
  - `NEXT_PUBLIC_RETRY_AFTER_UI`.
  - **Lists.**
    - The helper is `src/lib/paging.ts` and the pager is `ListPager.tsx`. It shows "Showing n of total" (plus the row range once a list is truncated), and Next appears only while the server gives `has_more` and a `next_cursor`.
    - The cursor replaces `offset` and resets when the list's filters change. The pager is wired into 19 lists.
    - The audit timeline keeps its `before` keyset paging (Q17).
    - Lookups the server cut show a truncation note (Q18).
    - A 400 `invalid_cursor` offers "Back to first page" and never retries by itself (Q19).
  - **Background runs.**
    - The poll loop (`polling.ts`) waits 0.5 s, doubling up to 5 s. A busy refusal waits at least its `Retry-After`. A 403 or 404, or an answer for a different run, stops polling for good.
    - The New-run panel follows a 202 RUNNING run and opens the run page only in a terminal status.
    - The run page polls a RUNNING run. Its ladder and liquidity panels read nothing until the run ends, and RunView keys them on run ID plus status (`RunView.tsx:309`).
  - **Retry-After.** `ApiError.retryAfterSeconds` (`parseRetryAfter`). Refusals say "Retry in N s." and have titles for `workers_busy`, `rate_limited` and `db_pool_exhausted`.
  - **e2e.** `background-runs.spec.ts` is new. `calc-support.ts` gains `settledRun` and `expectedRunStatus()` (Q20), used by `calculations.spec.ts:163`, `liquidity.spec.ts:87` and `ws3-smoke.spec.ts:214`.

**Shared and existing files changed:**
- **Backend:**
  - `app/core/{config,pagination,errors,artifacts}.py`, `app/db/session.py`, `app/main.py`;
  - `app/calculations/{executor,service,repository,router}.py`, `app/orchestration/{service,router}.py`, and every router with a capped list;
  - `pyproject.toml`, `.env.example`, `openapi.json` (re-exported), `openapi.base.json` (new), `scripts/perf_run.py` (new).
- **Backend tests:**
  - new: `executor_kit.py` and `pagination_kit.py`;
  - changed: `test_calculations.py` (:1055-1060, :1245-1268), `scenario_run_kit.py` (:992-996), `test_config.py` (:34, :101-111, :148-198, :320-337), `test_artifacts.py` (`FakeS3Client`, the two-store loop), `test_db_check.py` (:60-105) and `test_committed_artifacts.py` (:35-47, :75-133).
- **Frontend:** the files the FE fix report lists (paging, calculations, ingestion, domain/credit, advanced, audit and pages), `src/api/client.ts`, `src/lib/retry.ts`, and tests in `paging.test.ts`, `polling.test.ts`, `calculations.test.ts`, `audit/__tests__/paging.test.ts`, `scenario-run.test.ts`, `comparison.test.ts` and `identity.test.ts`.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration round trip (CLI, scratch `moodymate_scratch_p33`; no new revision) | `alembic upgrade head -> 0030 (head)` / `Running downgrade 0030 -> 0027` / `Running upgrade 0027 -> 0030` / `0030 (head)`; "dropped" |
| Backend engineer, settings + artifacts + db_check (`moodymate_test_p33`) | `5 passed, 1 warning in 6.48s` |
| Backend engineer, `test_calculations`, no workaround | first `assert 'float' not in text` on `executor.py` (fixed with integer-nanosecond deadlines); then `executor_kit.py:227 ... KeyError 'manifest_sha256'`, body `{'code': 'rate_limited', ...}` (Q1) |
| Same, with the rate limit raised through the environment | `FAILED at pagination_kit.py:64: ('listAuditEvents', '{"code":"forbidden",...,"required_permission":"audit:read"}')` (Q2) |
| Same, with a scratch plugin (403 lists skipped) | `SKIP-403 listAuditEvents` / `SKIP-403 listUsers` / `COVERED ['getCalculationRunResults', 'listCalculationRuns', 'listDatasetSnapshots', 'listIdentityLinks', 'listImports', 'listInstruments', 'listScenarioComparisonContributions', 'listScenarioRuns', 'listSources']` / `1 passed in 56.21s` |
| Direct cursor check (python, ENV=test) | `cursor eyJvIjoyLCJ2IjoxfQ.XDqdnzm3sq1IfTAg_bqRK_Do1uWlKSqa4G2oCwsm-vU 62`; `own -> 2`; `foreign tenant`, `other list`, `other filters`, `garbage`, `offset forged`, `other signing key` each `-> 400 invalid_cursor` |
| committed-artifacts + imports + auth + WS1 | `14 passed, 1 warning in 70.54s (0:01:10)` |
| Test writer, partial (`moodymate_test_p33b`) | `3 failed, 3 passed, 1 warning in 9.72s` (`ImportError ... S3ArtifactStore`; `assert '0' == '4321ms'`; `'float' is contained here: _seconds: float = 600.0`). These were implementation gaps at the time and are closed. |
| Fix round baseline, `test_calculations` | `POST http://testserver/api/v1/calculation-runs "HTTP/1.1 429 Too Many Requests"` / `1 failed, 1 warning in 21.46s` |
| Fix round, first re-run | `E AssertionError: listAuditEvents` / `pagination_kit.py:98` (every audit read grew the list; fixed with `action=scenario.run`) |
| Fix round, `test_calculations` (`moodymate_test_p33fix`) | `1 passed, 1 warning in 58.40s` |
| Mutation: the commit before the hand-off removed (restored, `cmp` -> `restored-identical`) | `E AssertionError: [<Record n=1>]` / `app/tests/executor_kit.py:209` / `1 failed, 2 warnings in 316.75s (0:05:16)` |
| Fix round, settings + artifacts + committed-artifacts | `6 passed, 1 warning in 5.42s` |
| Fix round, `test_db_check` as committed | `E FileNotFoundError: ... '/home/user/MoodyMate-wt/f06-p33/docs/evidence/phase-33/perf.json'` / `1 failed, 1 warning in 6.77s` |
| Same, pointed at the scratch copies | `1 passed, 1 warning in 11.86s`; all four files `7 passed, 1 warning in 17.19s` |
| Additive-only OpenAPI (by hand) | `forward []` / `reverse 124 [...has_more: removed, ...]` |
| Collected (D-26, D-60 §1) | `49 tests collected in 0.51s` (49 -> 49) |
| ruff / format / mypy | build round: `All checks passed!` / `180 files already formatted` / 3 errors in `pagination_kit.py:90-92` (since fixed); fix round: `All checks passed!` / `180 files already formatted` / `Success: no issues found in 180 source files` |
| OpenAPI drift | `openapi.json is up to date` (after the Q10 re-export) |
| botocore and the client build | `botocore-1.35.0 ... False` / `botocore-1.35.1 ... False` / `botocore-1.35.2-py3-none-any.whl True header If-None-Match`; `boto3 1.35.1 botocore 1.35.1: refused: ARTIFACT_STORE_KIND=s3 needs botocore >= 1.35.2 (PutObject IfNoneMatch) ...` / `boto3 1.35.2 botocore 1.35.2: client built (S3)` |
| perf_run, build round (scratch) | `10000 inline 0.197s api 144.5 MiB ... health p95 200.7 ms (n=1)`; `10000 pool 0.461s api 151.2 MiB worker 48.2 MiB health p95 49.4 ms (n=3)`; `100000 inline 2.329s api 340.2 MiB ... health p95 2333.1 ms (n=1)`; `100000 pool 5.631s api 429.5 MiB worker 215.6 MiB health p95 362.2 ms (n=42)` |
| perf_run, fix round (`moodymate_f06_p33_perf`, scratch output) | `10000 inline 0.307s api 145.3 MiB ... health p95 311.5 ms (n=1)`; `10000 pool 0.597s api 152.0 MiB worker 48.4 MiB health p95 324.0 ms (n=4)`; `100000 inline 2.388s api 341.0 MiB ... health p95 2392.3 ms (n=1)`; `100000 pool 5.859s api 435.6 MiB worker 215.8 MiB health p95 158.6 ms (n=46)`; 5,000-flow inline 0.29 s / 134.6 MiB, pool 0.34 s / 138.4 MiB (worker 41.4 MiB); same `result_sha256` `cbdc1dd7...`; `exit=0`. **Not the committed evidence** (Q7). |
| Frontend gen:api / tsc / lint (build) | two runs, identical `schema.d.ts` (`SCHEMA_UNCHANGED`) / no errors / `✔ No ESLint warnings or errors` |
| vitest | build: `Test Files 45 passed (45)` / `Tests 760 passed (760)`; FE fix round, targeted: `Test Files 7 passed (7)` / `Tests 164 passed (164)`; full: `Test Files 45 passed (45)` / `Tests 764 passed (764)` |
| FE mutation (RunView status key removed, then restored) | `× ... RunView keys them on the status` / `Test Files 1 failed (1)` |
| `npm run build` | e2e run (`NEXT_DIST_DIR=.next-p33fe`) succeeded; FE fix round (`.next-p33fefix`) `✓ Compiled successfully in 7.4s` / `✓ Generating static pages (35/35)` |
| Playwright, e2e run (`moodymate_test_p33fe`, ports 8033/3033) | background-runs alone `1 passed (12.2s)`; inline, 4 specs `9 passed (52.7s)`; `CALC_EXECUTION_MODE=pool`, 4 specs `9 passed (56.5s)` |
| Playwright, FE fix round (`moodymate_e2e_p33fefix`, ports 8733/3733) | `9 passed (50.4s)` |
| Full backend suite | **not yet run** (the coordinator runs it once) |
| Full Playwright | CI |
| `npm run gen:api` after the Q10 re-export | **not run** in the frontend (see Open items) |

Every run used a per-phase database: `moodymate_scratch_p33` (dropped), `moodymate_test_p33`, `_p33b`, `_p33fe`, `_p33fix`, `moodymate_f06_p33_perf` and `moodymate_e2e_p33fefix`. The agents report that `moodymate_dev` was never touched, and `perf_run` refuses it by name.

## Plan ticks

The architect checked each Done-when against the code, the test files and the reported lines on 2026-10-24. Each tick has an evidence line in DELIVERY_PLAN.md; the open box has a "Not yet ticked" line.

- **BE-33.1 ticked.**
  - `executor_kit.py`: pool hashes :179-183, :214, :234-235, :244; health under 200 ms :185-201; 503 `workers_busy` :216-233; crash :237-245.
  - Scenario k + 1: `check_pool_scenario_run` (:315-342) at `scenario_run_kit.py:994-996`, on 4 members. `1 passed`.
- **BE-33.2 ticked.**
  - `executor_kit.py:149-176`. Polling ends in the sync reference's terminal status, SUCCEEDED_WITH_WARNINGS for this fixture, with both hashes.
- **BE-33.3 ticked.**
  - `executor_kit.py`: timeout :249-258, retry :260-265, FAILED replay with an unchanged row :266-270, sweeper with system actor :273-308, one terminal move each :310-312.
- **BE-33.4 ticked.** The first bullet is reworded per Q12.
  - `test_config.py:148-175`; `test_db_check.py:60-103`.
  - Note: the function stays red until BE-33.7's files exist, because :105 reads them. The BE-33.4 lines run before that and passed in both runs.
- **BE-33.5 ticked.**
  - `pagination_kit.py:65-134`, called at `test_calculations.py:1247-1268`. At least the run list, run results, imports and audit events must reach the cursor step.
  - The foreign tenant is tested through the API.
  - Additive-only: `test_committed_artifacts.py:35-47` against `openapi.base.json`.
- **BE-33.6 ticked.**
  - `test_artifacts.py:91-134`; `test_config.py:320-337` and :193-198.
  - The library and the fake are recorded in today's D-59 amendment.
- **BE-33.7 not ticked.**
  - **Missing:** `docs/evidence/phase-33/perf.json` and `docs/PERFORMANCE.md` (glob: none). The fold fails at `test_db_check.py:32`.
- **FE-33.1 ticked.** The audit-timeline wording was added per Q17.
  - `polling.test.ts:43-139`; `src/lib/__tests__/paging.test.ts:38-206`; `e2e/background-runs.spec.ts:29`, `9 passed`.
  - Not checked in a browser: opening a run page while the run is still RUNNING (vitest only).

## Review outcome

- **MEDIUM 1 (fixed, backend): connections held during pool runs.**
  - The fix: `service.py:2009-2010`, the startup guard at `config.py:361-367` (test at `test_config.py:178-184`) and the idle-in-transaction assertion at `executor_kit.py:202-209`.
  - The mutation check fails the test.
- **MEDIUM 2 (fixed in code; two coordinator items open): S3 extra and D-59 record.**
  - Done: `pyproject.toml:39-42`, the botocore `IfNoneMatch` refusal, the `.env.example` placeholders, and today's D-59 amendment (boto3, `FakeS3Client`, `CURSOR_SIGNING_KEY`).
  - Open: `ci.yml:43` still installs `.[dev]`; `backend/uv.lock` has no boto3 (grep).
- **MEDIUM 3 (fixed, backend): scenario k + 1 in pool mode.** `check_pool_scenario_run`.
- **MEDIUM 4 (fixed, frontend): empty ladder after a background run ends.** `RunView.tsx:309` keys both panels on run ID and status, and the panels do not read while the run is pending. A vitest covers it, and the mutation check fails that test.
- **MEDIUM 5 (open, docs): BE-33.7 evidence.** The fold is written; the two files are missing. **Merge is blocked until they are in** (Open item 1).
- **LOW findings:**
  1. **Error on an already-ended run** (`service.py:1683-1706`, `_record_failure`). When a run was already ended elsewhere and its engine's output inserts then trip the 0018 trigger, `_fail` raises `RunNotRunning` as `again`, and the original error is re-raised. The synchronous POST then answers 500 instead of the FAILED run. Data stays correct. The early return at :1693-1695 only covers `exc` itself being `RunNotRunning`. **Open.** Fix: treat `isinstance(again, RunNotRunning)` as already ended and return.
  2. **One failing run stops the sweeper** (`service.py:2850-2856`). An error on one run that is not `RunNotRunning` aborts the whole pass. Runs are ordered oldest first, so a run that keeps failing blocks every later stale run. **Open.** Fix: a per-run try/except that rolls back, logs `calculation_sweep_run_error` (the type only) and continues.
  3. **`respond-async` in inline mode** (`router.py:378,399`; `service.py:1652`). The backend default is inline, and the UI sends the header by default. Inline, the 202 can be delayed by the whole computation, and inline admissions are bounded only by the rate limiter. **Open.** Either ignore the preference when the executor is not pooled (RFC 7240 allows it), or keep the Q4 position that deployed environments run `pool`.
  4. **(Q8) Hashing and post-checks run in the API process,** which costs event-loop time at large sizes. **Deferred:** added to the plan's Deferred list. PERFORMANCE.md must name it as the cause of any health p95 over budget.
  5. **(Architect) `listAuditEvents` with `before` over-reports `has_more`.** `total` is counted before the `before` filter (`governance/repository.py:190-193`), and `has_more` is `offset + returned < total` (`pagination.py:194`). A keyset page can therefore claim one more page, which comes back empty. The UI does not rely on it: it uses a one-row probe (`audit/paging.ts:15-33`). This is safer than Q17's "server's has_more" wording, so the plan text says "only while an older event exists". **Open.** Backend follow-up: compute `has_more` for `before` pages from a limit + 1 read, and leave `total` unchanged.
  6. **(Q9 part 2) Private attribute.** The executor reads the private `ProcessPoolExecutor._processes` and logs `calculation_worker_kill_unavailable` when it is missing (`executor.py:131-135`). **Accepted.** Move to the public kill API when the Python pin moves to 3.14.
  7. **(Architect) Perf fold may be flaky in CI.** The ceilings are 3× a single local measurement of about 0.3 s. Two local runs already differed: 10k pool health p95 49.4 vs 324.0 ms, duration 0.461 vs 0.597 s. **Watch the first CI run.** If it flakes, send it to the decision-resolver; do not loosen it silently.
  8. **BE-33.5 coverage.** Lists with fewer than 3 rows in `test_calculations` get only the key and last-page checks; the build round's run printed 9 lists that reached the cursor step. **Follow-up:** seed rows or assert more lists.

## Rulings (auto-resolved)

The decision-resolver gave each ruling. "Report" means this note is the ruling's record. The DECISIONS.md amendments are dated 2026-10-24, Phase 33 build, auto-resolved.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | Raise the calculation-run burst in `pool_client` and keep the limiter on, so a 429 can never hide the 503 check. Done: `executor_kit.py:68-70`. | Report |
| Q2 | (a) A role per permission, and a 403 fails the test. (b) Required page keys are {items, total, has_more, next_cursor}; limit/offset are checked only where the schema declares them. (c) Variables renamed. (d) The foreign-tenant cursor is tested through the API. Done: `pagination_kit.py:17-20`, :65-125; `test_calculations.py:1247-1268`. The audit list is pinned with `action=scenario.run` so its size stays fixed. | Report |
| Q3 | boto3 as the optional `s3` extra, with a floor that supports `IfNoneMatch`. Done: `pyproject.toml:39-42` (`>=1.35.2`, the measured first version). **Open:** `ci.yml:43` should install `.[dev,s3]`; the coordinator re-locks `uv.lock` additively if it is tracked, otherwise BE-34.1 locks it. | D-59 amendment |
| Q4 | The code default stays `inline`; `pool` is recommended for deployed environments; startup does not refuse inline. BE-34.2 now sets `pool` and the PERFORMANCE.md worker/queue values in the example. | D-59 amendment; plan BE-34.2 |
| Q5 | S3 settings confirmed as built (`config.py:283-329`). Per-tenant key prefixes are not adopted and become an open item before a shared multi-tenant bucket. | D-59 amendment |
| Q6 | `CURSOR_SIGNING_KEY` confirmed; outside local/test it may not equal the development key. Done: `config.py:283`; `test_config.py:108-111`. Rotation gives 400 `invalid_cursor`. | D-59 amendment |
| Q7 | The coordinator re-runs `perf_run` into `docs/evidence/phase-33/perf.json` against a phase DB copy and writes `PERFORMANCE.md` from that file. Required content: the size labelled PROVISIONAL with the machine named; admission is per process; the multi-instance needs; any health p95 over budget, and why; "the supported size is for the client to confirm (D-59 §7)". **Open.** | Report |
| Q8 | Hashing and post-checks stay in the API process this phase. Logged as LOW 4 and added to Deferred. | Report; plan Deferred |
| Q9 | (1) The sweeper's cross-tenant read is a narrow, named D-04 exception under conditions; the system actor and null user are asserted at `executor_kit.py:301-308`. (2) The private `_processes` access is accepted, with a value-free warning when it is missing (`executor.py:131-135`). | D-04 amendment (1); Report (2) |
| Q10 | The global `5XX` description names `db_pool_exhausted`. Done: `errors.py:149-152`; `openapi.json` re-exported (90 hits). **Open:** the frontend `schema.d.ts` is not regenerated (0 hits). | Report |
| Q11 | A pool-mode scenario run is compared member by member with the inline one. Done (BE-33.1 evidence). | Report |
| Q12 | `CALC_WORKER_QUEUE_MAX=0` and `DB_MAX_OVERFLOW=0` are valid; BE-33.4's Done-when is reworded to the exact settings. | Report; plan BE-33.4 |
| Q13 | The `S3ArtifactStore` interface matches the test loop; the library, client protocol and fake are recorded. | D-59 amendment |
| Q14 | The executor ships `module.function` by reference, and the worker imports `app.tests.executor_kit`, so monkeypatched test modules reach the worker. No action. | Report |
| Q15 | The additive-only check is folded into `test_committed_artifacts` against the committed `backend/openapi.base.json`. Done: :35-47, :75-133. | Report |
| Q16 | CORS already exposes `Location` and `Preference-Applied` (`main.py:98-104`). No action. | Report |
| Q17 | The audit timeline keeps `before` keyset paging, with an Older control. Built with a one-row probe (see LOW 5). | D-59 amendment; plan FE-33.1 |
| Q18 | Truncation notes on capped lookups, behind `LIST_PAGING_UI`. Done (`truncationOf`, `TruncationNote`; vitest `paging.test.ts:123`). | Report |
| Q19 | No automatic reset; a "Back to first page" action keeps the filters. Done (vitest `paging.test.ts:144`; 19 lists). | Report |
| Q20 | The specs expect exactly one status through `expectedRunStatus()`, and a vitest proves the off path. Done (`calc-support.ts:58`; the three specs). | Report |

## Open items

**Blocking merge:**
1. **BE-33.7 evidence (Q7).**
   - Re-run the script into the default path, against a phase DB copy:
     `cd /home/user/MoodyMate-wt/f06-p33/backend && python -m scripts.perf_run --database-url postgresql+asyncpg://moodymate:moodymate@localhost:5432/<phase copy>`
   - Pass `--database-url` explicitly: without it the script falls back to `backend/.env` and refuses `moodymate_dev`.
   - Then write `docs/PERFORMANCE.md` from that `perf.json`, under Q7's conditions, the Q4 pool recommendation and the Q8 cause. The doc must contain "Supported size", "Concurrency", "rate-limit" and "S3", which `test_db_check.py:34-35` asserts.
   - The engineer's draft is at `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/perf/PERFORMANCE.md`. Its figures must be replaced from the committed file.
   - Re-run `test_db_check`, then tick BE-33.7.
2. **Frontend types.** Run `npm run gen:api` in `/home/user/MoodyMate-wt/f06-p33/frontend`, because the Q10 description is not in `schema.d.ts`. Otherwise CI's `git diff --exit-code src/api/schema.d.ts` (`ci.yml:78`) fails.
3. **Q3.** Change `ci.yml:43` to `pip install -e ".[dev,s3]"` so pip-audit sees boto3. If `backend/uv.lock` is tracked, re-lock it additively (`uv lock`, no `--upgrade`) and check that the diff only adds the boto3 set.
4. **The full backend suite**, run once by the coordinator.
5. **The full Playwright run in CI.**

**Not blocking (LOW findings and follow-ups):**
- LOW 1: `_record_failure` returns 500 for a run already ended elsewhere.
- LOW 2: one failing run stops a sweeper pass.
- LOW 3: `respond-async` in inline mode.
- LOW 5: audit `has_more` with `before`.
- LOW 7: watch the perf fold's ceilings in the first CI run.
- LOW 8: cursor-step coverage of lists with fewer than 3 rows.
- **ARCHITECTURE.md** needs entries, after merge, for:
  - the executor and both execution modes;
  - the async 202 contract;
  - the sweeper;
  - the DB pool settings;
  - signed cursors;
  - the S3 store;
  - today's D-59 and D-04 amendments.
- **Databases to drop:** `moodymate_test_p33`, `_p33b`, `_p33fe`, `_p33fix`, `moodymate_f06_p33_perf` and `moodymate_e2e_p33fefix`.
- **Build folders to delete:** `frontend/.next-p33fe` and `frontend/.next-p33fefix` (git-ignored).

**For the client before production (not blocking the build):**
- **Supported size and concurrency** (D-59 §7). The worker and queue values stay PROVISIONAL.
- **Multi-instance needs:**
  - a shared rate-limit store (D-53 §5, deferred);
  - `ARTIFACT_STORE_KIND=s3`;
  - the same `CURSOR_SIGNING_KEY` on every instance;
  - a total connection budget across instances;
  - per-instance admission (effective capacity is N × (`CALC_WORKERS` + `CALC_WORKER_QUEUE_MAX`)).
- **Per-tenant S3 key prefixes** (Phase 8 D7) are not adopted. Revisit before any shared multi-tenant production bucket (Q5).
- **The S3-compatible storage host and bucket** (none is chosen here), and provisioning `CURSOR_SIGNING_KEY` and the S3 credentials in deployed environments.
- **D-59** is still at status "proposed".
- **Illustrative content.** The perf sets and samples are synthetic. No regulatory certification or vendor parity is claimed.

**For the user's attention (not a hard stop):** the D-04 amendment (Q9). The sweeper's cross-tenant read is a named exception for a system job that no request can reach. It changes no role, permission or approval, but it is an exception to a core isolation decision, and a new decision can reverse it.

No new hard stop arose in this phase:
- no migration, and no CHECK widened;
- no role or permission change (the routes and permissions are unchanged; `workers_busy` is audited DENIED);
- no real client data;
- no regulatory or vendor-parity claim;
- the only deletions were per-agent test databases and the scratch DB. The S3 store never deletes.

**Not checked locally:**
- the full backend suite and the full Playwright run;
- opening a RUNNING run's page in a browser (vitest only);
- the Q17, Q18 and Q19 UI in a browser (vitest only).

---

Files edited by the architect:
- `/home/user/MoodyMate-wt/f06-p33/docs/DELIVERY_PLAN.md`:
  - BE-33.1 to BE-33.6 and FE-33.1 ticked, each with an evidence line; BE-33.7 left unticked with a "Not yet ticked" line;
  - BE-33.4's first Done-when bullet reworded (Q12);
  - FE-33.1 gains the audit-timeline wording (Q17);
  - BE-34.2 gains the pool setting in the example (Q4);
  - a Deferred entry for hashing in the worker (Q8).
- `/home/user/MoodyMate-wt/f06-p33/docs/DECISIONS.md`: a D-59 amendment (Q3, Q4, Q5, Q6, Q13, Q17) and a D-04 amendment (Q9 part 1), both dated 2026-10-24 and marked auto-resolved.

## Coordinator merge (2026-10-24)

- **Blocking items 1-4 done.**
  1. `perf.json` was re-run on a phase DB copy (`moodymate_f06_p33_perf`, migrated to 0031; never `moodymate_dev`):
     - 10k: inline 0.174 s, pool 0.653 s;
     - 100k: inline 2.311 s, pool 6.731 s, with pool health p95 244.2 ms (over the 200 ms budget; Q8);
     - the same `result_sha256` in both modes at every size.
     `docs/PERFORMANCE.md` was written from it under Q7's conditions, and BE-33.7 is ticked.
  2. `schema.d.ts` was regenerated.
  3. Q3, on top of Phase 34's uv-based CI: the backend job installs `--extra dev --extra s3`, and pip-audit reads the `uv export --locked --extra dev --extra s3` output (`No known vulnerabilities found`). The backend image installs the `s3` extra too, so it can serve `ARTIFACT_STORE_KIND=s3`. `uv lock` was re-run additively; the diff adds only boto3, botocore, jmespath, python-dateutil and s3transfer.
  4. The full suite on the merged tree: `49 passed, 1 warning in 334.38s`.
- **Phase 34 merge note (Q3 e), done:**
  - `RunExecutor.ready()` answers the readiness `workers` check. It returns false only while shutting down; a full queue is answered per request with 503 `workers_busy`.
  - `app.state.workers` is set in pool mode.
  - The worker-queue gauge follows the waiters.
  - Runs record the BE-34.4 run metrics on the inline path and on the background (`respond-async`) path.
- **Phase 34 interplay:**
  - `S3ArtifactStore.ready()` (HeadBucket) satisfies the store's readiness contract, folded into the store test.
  - `.env.production.example` sets `CURSOR_SIGNING_KEY` (placeholder) and `CALC_EXECUTION_MODE=pool` with the default worker and queue sizes (Q4). The compose `prod` check fills the key with a random value.
- **Additive-contract base:** `backend/openapi.base.json` is now master's `openapi.json`, the actual merge base, so the check proves Phase 33 only adds to the merged contract.
- **Checks:**
  - frontend: vitest `783 passed (783)`, tsc clean, `No ESLint warnings or errors`;
  - backend: ruff and mypy (`187 source files`) clean, `openapi.json is up to date`.
