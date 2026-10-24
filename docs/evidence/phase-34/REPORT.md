# Phase 34: Deployment and observability (checks)

**Scope.** This report covers BE-34.1 to BE-34.7 and FE-34.1 (Workstream G; DG-04, DG-06; D-62).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f06-p34` by:
  - a backend-engineer and a test writer, both scoped to `backend/`;
  - one frontend-engineer, who owns FE-34.1. `navigation.ts` and `features.ts` are unchanged, because no page uses the new routes;
  - one review agent and one e2e run;
  - a backend fix round.
- **Commits.** Nothing is committed yet.
- **No migration.** The head stays `0030 (head)`, and no revision number is reserved.
- **Not built here.** Nobody owns the files outside `backend/` and `frontend/` yet: `.github/workflows/ci.yml`, `docker-compose.yml`, `.env.production.example`, `.gitignore` and `docs/ops/*`. None of them has been written. Docker images were not built because no Docker daemon is running in this environment.

## What was built

- **BE-34.1 Lockfile (partly built).**
  - `prometheus-client>=0.21,<1` was added to `pyproject.toml` (:22-23) and `uv.lock` (0.26.0, :880-881).
  - `uv lock --check` passes in the worktree.
  - **Problem found:** `uv sync --frozen` installs a stale lock without complaint, so it can never meet the Done-when. Per Q1 the plan now says `--locked`. The backend image already uses `--locked`.
  - CI has not been changed.
- **BE-34.2 Images (files only).**
  - **`backend/Dockerfile`:**
    - multi-stage, installing with `uv sync --locked --no-dev --no-install-project` (:31);
    - runs as uid/gid 10001 (:34-37, :50) and leaves the source owned by root;
    - has an artifact volume;
    - sets `FORWARDED_ALLOW_IPS=127.0.0.1` (:48) and starts uvicorn with `--proxy-headers --no-server-header` (:55);
    - has a HEALTHCHECK on `/api/v1/health/live` (:53-54).
  - **`backend/.dockerignore`** keeps out `.env`, `.env.*`, `.venv`, caches and `app/tests`.
  - **`frontend/Dockerfile`** is owned by the frontend-engineer (Q9); see FE-34.1.
  - **Not done:** the compose `prod` profile, `.env.production.example` and the CI image job.
- **BE-34.3 Access log.**
  - `RequestObservabilityMiddleware` (`app/core/observability.py:44-90`) is the outermost middleware (`main.py:89-92`). It writes one `app.access` JSON line per request with these fields:
    - `correlation_id`, read from the response header (:57-64);
    - `method`;
    - `route`, the served template, or `unmatched`;
    - `status`;
    - `duration_ms`.
  - It never writes the raw path, the query, the body, a header or a token.
  - `ACCESS_LOG_ENABLED` (`config.py:162`) defaults to true. While it is on, uvicorn's own access line, which includes the query, is silenced. Setting it to false brings that line back.
- **BE-34.4 Metrics.**
  - **Listener.** `app/core/metrics.py` serves metrics only on an internal listener:
    - `METRICS_ENABLED` (default False), with `METRICS_HOST` (default `127.0.0.1`) and `METRICS_PORT` (default 9100);
    - an optional `METRICS_TOKEN` (`SecretStr`), compared in constant time (`hmac.compare_digest`, :211-216). A failed token gets 401 with `WWW-Authenticate: Bearer` (:244-246).
  - **Startup.** The port is bound at startup, and a port that cannot be bound stops startup (:278-288).
  - **Series:**
    - HTTP requests, errors and latency;
    - runs and run duration;
    - worker busy and queue depth;
    - import failures by stage and reason code;
    - DB pool size, in use, idle and overflow;
    - `process_*`.
  - **Labels.** Unknown HTTP methods are labelled `OTHER`, and unmatched paths `unmatched`.
  - **Hooks:** `calculations/service.py:1567` and :1575; `ingestion/service.py:211`, :623 (upload refusals) and :1067 (validation FAILED).
  - Counters are kept in memory whatever `METRICS_ENABLED` says. Only the listener depends on the setting (Q3 b).
- **BE-34.5 Health.**
  - **Routes.** `GET /api/v1/health/live` does no I/O. `GET /api/v1/health/ready` gives 200 or 503 with a `ReadinessResponse` body. That model is declared for the 503 in OpenAPI (`health.py:44-99`; Q3 a).
  - **Checks** (`app/core/readiness.py`):
    - the DB within 2 s (:30, :54);
    - the `alembic_version` rows equal the code's heads (:91);
    - the artifact store can be written to, though nothing is written;
    - the workers: always `ok` with inline execution (Q3 e).
  - Both routes are in `PUBLIC_PATHS` (`core/auth.py:34-43`). `/api/v1/health` is unchanged.
- **BE-34.6 Error tracker.**
  - `app/core/error_tracker.py`: `report_exception(exc, correlation_id)` passes only `(correlation_id, type(exc))` to the reporter. The default reporter does nothing, and an error raised by a reporter is swallowed.
  - `set_reporter` installs an adapter later. It is called from `CorrelationIdMiddleware` (`correlation.py:63-68`).
  - `ERROR_TRACKER_DSN` is a `SecretStr`. If it is set while no adapter is installed, startup logs one warning with no value in it.
  - No vendor SDK was added, because the vendor is a D-62 §4 hard stop.
- **BE-34.7 Ops docs (not started).**
  - `docs/ops/` does not exist.
  - The fix round wrote a TRIGGERS.md draft and its generator (paths under Open items). It covers 177 triggers in 17 migrations, with table, timing and function.
- **FE-34.1 Standalone output and image.**
  - **`next.config.mjs:21-23`** sets `output: 'standalone'` and `outputFileTracingRoot` to the frontend folder. `NEXT_OUTPUT_STANDALONE=off` removes both settings and restores the earlier config exactly (D-02).
  - **`frontend/Dockerfile`:**
    - `npm ci` from the lockfile;
    - `ARG NEXT_PUBLIC_API_BASE_URL` has no default, and the build stops with exit 64 before `npm run build` when it is missing (:23-29);
    - the runtime stage is standalone and runs as `USER 1000:1000` (:53);
    - only `.next/cache` is writable;
    - HEALTHCHECK on `/login`;
    - the only build args are `NEXT_PUBLIC_API_BASE_URL`, `OIDC_ISSUER` and `NODE_IMAGE`, all public.
  - **`frontend/.dockerignore`** keeps out `.env`, `.env.*`, `node_modules` and `.next`.
  - **`frontend/scripts/check-image-build-arg.sh`** is the CI step. The build without the arg must fail, and the log must contain the arg message. The build with the arg must exit 0, and the image user must not be root. The script prints both exit codes.
  - **`src/config/__tests__/imageBuild.test.ts`** has 5 cases.
  - **`schema.d.ts`** was regenerated (`LivenessResponse`, `ReadinessChecks`).

**Files changed:**
- **Backend:**
  - `app/api/v1/health.py`
  - `app/core/readiness.py` (new), `metrics.py` (new), `observability.py` (new), `error_tracker.py` (new)
  - `app/core/config.py`, `logging.py`, `correlation.py`, `artifacts.py`
  - `app/db/session.py`, `app/main.py`
  - `app/calculations/service.py`, `app/ingestion/service.py`
  - `pyproject.toml`, `uv.lock`, `openapi.json`
  - `Dockerfile` (new), `.dockerignore` (new)
- **Tests, folded into existing functions:**
  - `app/tests/observability_kit.py` (new)
  - `test_config.py`: :174-181 and :263
  - `test_auth.py`: :243-250
  - `test_ws1_verification.py`: :1064 and :1071
  - `test_calculations.py`: step 5d, :1542-1546
  - `test_committed_artifacts.py`: :35
- **Frontend:**
  - `next.config.mjs`
  - `Dockerfile` (new), `.dockerignore` (new)
  - `scripts/check-image-build-arg.sh` (new)
  - `src/config/__tests__/imageBuild.test.ts` (new)
  - `src/api/schema.d.ts`

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| ruff / format / mypy | `All checks passed!` / `180 files already formatted` / `Success: no issues found in 180 source files` (repeated in the fix round, exit 0 each) |
| OpenAPI drift | `openapi.json is up to date` (exit 0) |
| Collected (D-26, D-60 §1) | `49 tests collected in 0.81s` (49 -> 49); test writer `49 tests collected in 0.45s`; fix round `49 tests collected in 0.55s` |
| Backend engineer, targeted (`moodymate_test_p34`) | test_config, test_auth, test_artifacts, test_imports, test_import_no_leak, the calculations ladder test, WS1 migration test: `15 passed in 99.35s (0:01:39)`. test_committed_artifacts: `FAILED test_committed_openapi_and_samples_are_current: FileNotFoundError: [Errno 2] No such file or directory: '/home/user/MoodyMate-wt/f06-p34/docs/ops/TRIGGERS.md'` |
| Test writer (`moodymate_test_p34b`) | auth + config + committed-artifacts: `1 failed, 5 passed, 1 warning in 7.62s` (TRIGGERS.md). WS1 + calculations at 07:13: `1 failed, 1 passed, 1 warning in 43.29s` (`observability_kit.py:231 AssertionError: []`, no import-failure sample while the hook was still being edited). Calculations re-run at 07:15: `1 passed, 1 warning in 40.20s` |
| Access-log route, first run | `'route': '/imports/{import_id}/validate'` (missing `/api/v1`); fixed, and the later test_config runs passed |
| Fix round (`moodymate_test_p34fix`, fresh) | `1 failed, 6 passed, 1 warning in 62.58s (0:01:02)` with `FAILED app/tests/test_committed_artifacts.py::test_committed_openapi_and_samples_are_current`. The new `www-authenticate` assertion passed |
| Trigger parser vs the database | `177 rows; diff parsed.txt dbtrig.txt -> SAME` (pg_trigger `WHERE NOT tgisinternal` after `alembic upgrade head` on `moodymate_test_p34b`) |
| TRIGGERS.md draft | `documented_triggers(draft) == migration_triggers(): True`; `triggers without parsed details: []`; one row removed gives `False`; the failing test with the kit's REPO_ROOT on a scratch root holding the draft: `1 passed, 1 warning in 3.28s` |
| Migration round trip (scratch `moodymate_scratch_p34`) | `Running upgrade 0027 -> 0030 ... Running downgrade 0030 -> 0027 ... Running upgrade 0027 -> 0030 ... 0030 (head)`, exit 0, dropped. No migration changed in this phase |
| BE-34.1 lock mismatch (scratch copy with `six>=1.16` added) | `uv lock --check` -> `error: The lockfile at uv.lock needs to be updated, but --check was provided.` exit 1; `uv sync --locked --no-dev --no-install-project` -> `... but --locked was provided.` exit 1; `uv sync --frozen ...` exit 0, then `import six` -> `ModuleNotFoundError`. Worktree: `Resolved 90 packages in 1ms`, exit 0 |
| Image layout copy, without Docker (locked no-dev env + `app/` + `alembic.ini`, ENV=test) | `alembic current` -> `0030 (head)`; live `{"status":"ok"} 200`; ready `{"status":"ok","checks":{"database":"ok","migrations":"ok","artifact_store":"ok","workers":"ok"}} 200`; public `/metrics` 404; internal `:19134/metrics` without the token `unauthorized 401`, with it `moodymate_http_requests_total{method="GET",route="/api/v1/imports/{import_id}",status="401"} 1.0`; access line `{"logger": "app.access", "message": "http_request", ..., "extra": {"method": "GET", "route": "/api/v1/imports/{import_id}", "status": 401, "duration_ms": 50.445}}`; sentinel hits 0; `ACCESS_LOG_ENABLED=false` brings back `"GET /api/v1/health/live HTTP/1.1" 200` |
| `docker build` (either image) | **not run**: `Cannot connect to the Docker daemon at unix:///var/run/docker.sock` |
| Frontend gen:api / lint / tsc | `../backend/openapi.json → src/api/schema.d.ts [562.2ms]` / `✔ No ESLint warnings or errors` / tsc exit 0 |
| vitest | `src/config`: `Tests 16 passed (16)`; full: `Test Files 44 passed (44)`, `Tests 741 passed (741)` |
| Local `next build` | without the variable: `Error: NEXT_PUBLIC_API_BASE_URL is not set. ...` exit 1; with `https://api.example.test`: exit 0, `ls .next-fe34/standalone -> node_modules package.json server.js`; `bash -n` on the CI script: syntax-ok |
| Playwright, e2e run (`moodymate_test_p34fe`, ports 8034/3034) | `smoke.spec.ts` + `security-headers.spec.ts`: `11 passed (16.7s)`. Warning: `"next start" does not work with "output: standalone" configuration` (expected; Q12) |
| curl against the e2e backend | health `[200]`; live `{"status":"ok"} [200]`; ready all `ok` `[200]`; `/metrics` `{"code":"not_found",...} [404]` |
| Full backend suite | **not yet run** (the coordinator runs it once; it stays red until `docs/ops/TRIGGERS.md` exists) |
| Full Playwright run, image CI job, compose `prod` | CI. None exists yet |

Each run used its own per-phase database: `moodymate_scratch_p34` (dropped), `moodymate_test_p34`, `_p34b`, `_p34fe` and `_p34fix`. `backend/.env` points at the phase copy `moodymate_f06_p34`. `moodymate_dev` was never touched.

## Plan ticks

The architect checked each Done-when against the code and the test lines on 2026-10-24. Each ticked box has an evidence line in DELIVERY_PLAN.md, and each open box has a "Not yet ticked" line naming what is missing.

- **BE-34.3 ticked.**
  - `check_access_log` (`observability_kit.py:105-136`) is called at `test_config.py:263`:
    - exactly one line (:119), with the five fields;
    - the correlation id equals the response header (:122);
    - the full template (:123-127);
    - no sentinel, token, path id or header name (:129-131);
    - no line when the setting is off (:133-136).
  - Runs: `15 passed`; fix round `6 passed`.
- **BE-34.4 ticked.**
  - `test_config.py:181` -> kit :197-216:
    - the public app gives 404 for `/metrics`, `/api/v1/metrics` and `/api/metrics` with metrics on and off;
    - the setting defaults to off;
    - with a token set, three bad cases each give 401 with `WWW-Authenticate: Bearer`, and the right token gives 200.
  - `test_calculations.py:1542-1546` -> kit :219-238: all nine series families, no forbidden label, `csv_malformed` counted, runs counted, no UUID.
  - Runs: re-run `1 passed` at 07:15; fix round `6 passed`.
- **BE-34.5 ticked.**
  - `test_config.py:180` -> kit :149-170:
    - ready gives 503 with `database: fail` and exactly the four check names;
    - live gives 200 while the DB stub fails on any use;
    - no DSN, host, driver name or version in either body.
  - `test_ws1_verification.py:1064` (503 `migrations: fail`) and :1071 (200 back at head), with no revision string in the body.
  - `test_auth.py:243-250`: the public-path set equals D-62 §1's.
- **BE-34.6 ticked.**
  - `test_config.py:174-177` -> kit :244-277: one call with exactly the correlation id and the class; the vendor grep; the default does nothing.
  - Architect grep: no vendor import under `backend/`, and `pyproject.toml` adds only `prometheus-client`.
- **BE-34.1 not ticked.** Reworded per Q1.
  - The local lock evidence is above.
  - **Missing:**
    - `ci.yml` still runs `pip install -e ".[dev]"` (:43, :122) and `pip-audit --skip-editable` (:61);
    - there is no CI temp-copy mismatch step (Q7);
    - there is no CI log.
- **BE-34.2 not ticked.** The Done-when is reworded per Q11.
  - **Missing:**
    - no image has been built;
    - no CI image job, non-root check or `docker history` grep;
    - `docker-compose.yml` has no `prod` profile (grep `profiles|prod|migrate`: no match);
    - no `.env.production.example`, and `.gitignore` would ignore it (Q10).
- **BE-34.7 not ticked.**
  - **Missing:**
    - `docs/ops/` does not exist, so the committed-artifacts check fails;
    - the four other ops docs;
    - the restore exercise.
- **FE-34.1 not ticked.**
  - The files and local builds are evidenced.
  - **Missing:** the `ci.yml` step (grep `check-image-build-arg`: no match), and a CI log with both exit codes.

## Review outcome

- **MEDIUM (open, blocks merge).** `docs/ops/TRIGGERS.md` is missing, so `test_committed_openapi_and_samples_are_current` fails, and so will the full suite.
  - Also missing: the compose `prod` profile, `.env.production.example`, the CI changes (locked install, pip-audit on the lock, the image job, the FE-34.1 step) and the other four `docs/ops` files.
  - The fix round could not land them, because they are outside `backend/`. It prepared the verified TRIGGERS.md draft.
  - The coordinator assigns the owners (Q2). BE-34.1, 34.2, 34.7 and FE-34.1 stay unticked until these land.
- **Q6 (401 on the metrics listener): confirmed and asserted.** The code already used a constant-time compare and sent `www-authenticate: Bearer`. The fix round added the header assertion at `observability_kit.py:212`, and the suite stays at 49.
- **LOW findings:**
  1. `backend/app/core/config.py:166-169` (validation at :268-271): staging and production accept a non-loopback `METRICS_HOST` with `METRICS_ENABLED` on and no `METRICS_TOKEN`. Anyone on that network could then read route templates, error rates and pool state. **Open, not ruled.** The stricter option is to refuse that combination outside the local envs. Otherwise a resolver ruling must say the token is optional only on loopback. Q2 already limits `0.0.0.0` to an `internal: true` compose network.
  2. `backend/app/core/readiness.py:51-59` and `backend/app/db/session.py:41-49`: each `/health/ready` hit opens two pooled connections in turn and checks the filesystem. There is no cache and no rate limit, so a client looping on it competes with real requests for the pool. **Open.** Fix: run both queries on one connection and cache the result for 1-2 s.
  3. `backend/app/core/correlation.py:63-68`: `report_exception` only sees exceptions that escape to the middleware. A failure inside the calculation engine is caught by `_record_failure` (`calculations/service.py:1571`, :1578) and saved as a FAILED run, so the error tracker never sees it. **Open.** Fix: call the hook in `_record_failure` (class and correlation id only), or write in INCIDENT_RESPONSE.md that the hook covers request-level exceptions only.
  4. `backend/app/core/observability.py:32-41`: `served_route` scans every route on every request, preflights and probes included. **Open.** Fix: build a cached route-to-template mapping per app.
  5. (Q4) A `readiness_failed` WARNING is logged on every failing probe, with no rate limit. **Kept** under the safer-default ruling, because the line carries only check names. Logging only on a change of state is a later additive change. This overlaps LOW 2.
  6. (Q12) The Playwright webServer still runs `next start` against standalone output and only logs a warning. **Deferred:** later, move it to `node <distDir>/standalone/server.js`, so e2e runs the same artifact the image ships.
  7. (Architect) Q3 (b) names the "status class" as the label, but the HTTP series use the status code (`metrics.py:174-180`). The codes come from the app, not the client, so the set is still bounded. **To confirm:** keep the codes and amend the wording, or change the label to the class.
  8. (e2e) Building into a custom `NEXT_DIST_DIR` rewrites `frontend/tsconfig.json` and `next-env.d.ts`. Both were reverted, and tsc exits 0. **Re-check before commit.**

## Rulings (auto-resolved)

The decision-resolver gave each ruling. "Report" means this note is the ruling's record. The DECISIONS.md amendment is one D-62 entry, dated 2026-10-24, Phase 34 build, auto-resolved. D-62's status stays **proposed**.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | CI installs with `uv sync --locked --extra dev` (backend job :43 and e2e job :122), because `--frozen` cannot fail on a mismatch. pip-audit reads `uv export --locked --no-hashes --format requirements-txt -o "$RUNNER_TEMP/req.txt"`, run with `--no-deps --progress-spinner off`. | D-62 amendment (§5); plan BE-34.1 reworded |
| Q2 | One owner for `ci.yml`, `docker-compose.yml`, `.env.production.example` and `.gitignore`; `docs/ops/*` and the restore evidence go to the BE-34.7 docs author. CI: one image job from a clean checkout. It checks `docker run --rm --entrypoint id <img> -u` is non-zero, and fails if `docker history --no-trunc` matches any of `TOKEN=`, `DSN=`, `PASSWORD=`, `KEY=` or `BEGIN`. Compose `prod`: a one-shot `migrate` service runs `alembic upgrade head` from the backend image, and the API waits on `service_completed_successfully`. `METRICS_HOST=0.0.0.0` is used only on an `internal: true` network, and the metrics port is never published. `.env.production.example` holds placeholders only: an empty `ERROR_TRACKER_DSN` (§4), and a placeholder for `FORWARDED_ALLOW_IPS`, never `*`. Restore: `createdb -T` from the phase's own copy, restore into a second scratch DB, copy the artifact store to a scratch directory, re-hash, paste the commands and output here. Synthetic data only. | Report |
| Q3 | D-62 status is not changed: acceptance goes to the user. Sub-choices: (a) a 503 from ready uses `ReadinessResponse`, declared in OpenAPI; (b) counters record in memory, with bounded labels and `unmatched`; (c) a port that cannot be bound stops startup; (d) metrics are per process, and multiprocess mode is out of scope; (e) workers are `ok` inline, and Phase 33 sets `app.state.workers.ready()` and calls `metrics.set_worker_queue()`. | D-62 amendment (§1, §2); report (merge note) |
| Q4 | `readiness_failed` stays at WARNING on every failing probe, with no rate limit this phase. | Report (LOW 5) |
| Q5 | TRIGGERS.md format accepted: one table only whose first cell starts with a 4-digit revision; trigger names in cell 2, lowercase and in backticks; notes outside the table. `documented_triggers` is unchanged. | Report |
| Q6 | A token failure on the metrics listener is 401 with `WWW-Authenticate: Bearer`, identical for all failure modes, with a constant-time compare and the token never logged. Verified and asserted (kit :212). | D-62 amendment (§2) |
| Q7 | Evidence from CI logs and this report is accepted for BE-34.1, BE-34.2, the BE-34.7 restore and FE-34.1. BE-34.1 needs a CI step that edits a temp copy's pyproject and expects `uv lock --check` / `uv sync --locked` to fail. A broken lock is never committed. The report quotes the CI run URL and log lines. | Report |
| Q8 | The FE-34.1 script runs as one step in the BE-34.2 image job, with a placeholder URL. The script is committed as executable, or called with `bash`. Tick FE-34.1 only on both exit-code lines. | Report |
| Q9 | The frontend-engineer owns `frontend/Dockerfile`, and no second one is written. The compose `prod` frontend uses `build: { context: ./frontend, args: { NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:?required} } }`. | Report |
| Q10 | Add `!.env.production.example` after `!.env.example` (line 7) in the root `.gitignore`, and confirm with `git check-ignore -v .env.production.example` (no output). The CI secret grep also covers this file. | Report |
| Q11 | The compose check uses `curl -fsS -L -o /dev/null -w '%{http_code}'` on `/` and expects 200. The image HEALTHCHECK stays on `/login`. | Report; plan BE-34.2 Done-when reworded |
| Q12 | Standalone output by default, with `NEXT_OUTPUT_STANDALONE=off` to revert, is accepted. e2e on the standalone server is deferred. | Report (LOW 6) |

## Reversibility

- Metrics are off by default, and only the internal listener exposes them.
- The health routes are additive, and `/api/v1/health` is unchanged.
- `ACCESS_LOG_ENABLED=false` restores the previous logs, including uvicorn's access line.
- `NEXT_OUTPUT_STANDALONE=off` restores the pre-Phase-34 Next config exactly.
- The error-tracker hook does nothing by default.
- The lockfile and Docker files are build artifacts only.
- No migration was added.

## Open items

**Blocking merge:**
1. **`docs/ops/` (BE-34.7 docs author).**
   - Copy `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/TRIGGERS.md` unchanged to `/home/user/MoodyMate-wt/f06-p34/docs/ops/TRIGGERS.md`. It must be the only 4-digit-keyed table in the file (Q5).
   - If a migration changes first, rebuild it with `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/gen_triggers_doc.py`. `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/TRIGGERS_table.md` is the backend engineer's earlier version.
   - The scratchpad belongs to this session, so copy the files before it ends.
   - Write the other four docs:
     - `BACKUP_RESTORE.md`: DB plus artifact store, re-hash verification, RPO/RTO placeholders;
     - `MIGRATIONS.md`, including that metrics are per process (Q3 d);
     - `INCIDENT_RESPONSE.md`, including the hook's scope (LOW 3);
     - `API_COMPATIBILITY.md` (D-62 §6).
   - Retention enforcement points to `RETENTION.md`, which stays PROVISIONAL.
2. **`ci.yml` (single owner, Q2):**
   - `uv sync --locked --extra dev` at :43 and :122;
   - pip-audit on the `uv export --locked` output at :61 (Q1);
   - the temp-copy lock-mismatch step (Q7);
   - one image job: both images, the non-root `id -u` check, and the `docker history --no-trunc` secret grep, which also covers `.env.production.example`;
   - the FE-34.1 step with a placeholder URL (Q8).
3. **`docker-compose.yml` `prod` profile (Q2, Q9, Q11):**
   - the one-shot `migrate` service, with `service_completed_successfully`;
   - metrics only on an `internal: true` network and never published;
   - the frontend built with the `${NEXT_PUBLIC_API_BASE_URL:?required}` arg;
   - the `curl -fsSL` check on `/`.
4. **`.env.production.example`** with placeholders only (Q2), and the `.gitignore` re-include (Q10).
5. **Restore exercise (BE-34.7)** on scratch copies, per Q2. Paste the commands and output into this report.
6. **Full backend suite,** run once by the coordinator after item 1. Then the CI Playwright run and the image job. Quote the run URL and the log lines for BE-34.1, BE-34.2 and FE-34.1, then tick them.

**Not blocking:**
- LOW findings 1-8 above.
- **Phase 33 merge note (Q3 e):** when D-59 pool mode lands, set `app.state.workers` with a `ready()` and call `metrics.set_worker_queue()`. The `workers` readiness check then reads the pool.
- **ARCHITECTURE.md** needs, after merge, the D-62 surfaces with today's amendment: the live and ready probes and their `PUBLIC_PATHS` entries, the internal metrics listener, the access log, the error-tracker hook and the images.
- **Databases to drop when no longer needed:** `moodymate_test_p34`, `_p34b`, `_p34fe` and `_p34fix`.

**For the user at the end of the phase:**
- **D-62 acceptance.** It is still "proposed", and so are D-52 to D-61. Acceptance includes the two new unauthenticated `PUBLIC_PATHS` entries, `/api/v1/health/live` and `/api/v1/health/ready`. That is a change to which endpoints need credentials (D-04), which D-62 §1 records.
- **Error-tracker vendor (D-62 §4, hard stop).** `ERROR_TRACKER_DSN` stays empty, and no SDK was added.
- **Before production (client):**
  - the RPO/RTO targets (BACKUP_RESTORE placeholders);
  - the proxy addresses for `FORWARDED_ALLOW_IPS`;
  - the metrics scrape topology and the `METRICS_TOKEN` policy (LOW 1);
  - the retention periods (RETENTION.md stays PROVISIONAL).
- **Content.** Samples and test data are synthetic. Nothing claims regulatory certification or vendor parity.

No new hard stop arose in this phase:
- there is no migration;
- no CHECK was widened;
- no data was deleted outside per-agent test and scratch databases;
- no role or permission changed. The public-path entries are D-62 §1's and wait for D-62's acceptance.

**Not checked locally:** Docker image builds, compose `prod`, the restore exercise and the full Playwright run.

---

Files edited by the architect:
- `/home/user/MoodyMate-wt/f06-p34/docs/DELIVERY_PLAN.md`:
  - BE-34.3, BE-34.4, BE-34.5 and BE-34.6 ticked, each with an evidence line;
  - BE-34.1, BE-34.2, BE-34.7 and FE-34.1 left unticked, each with a "Not yet ticked" line;
  - BE-34.1 reworded (Q1) and BE-34.2's Done-when reworded (Q11), the old text struck through.
- `/home/user/MoodyMate-wt/f06-p34/docs/DECISIONS.md`: one D-62 amendment (2026-10-24, Phase 34 build, auto-resolved) covering Q1, Q3 and Q6. The status stays proposed.

On "how much time left?": I can't see a clock or the workflow's time budget, so I can't give a time. The backend engineer's own slot ended at 07:16 with about 23 minutes unused; that was its slot only, not the workflow's. What remains is blocking items 1-6 above.

## Restore exercise

BE-34.7 restore, per Q2, D-03 and D-14, run on 2026-10-24 by the owner of the files outside
`backend/`. It followed `docs/ops/BACKUP_RESTORE.md`: the database first, then the artifact
store; restore into a second scratch database and a scratch store directory; then verify.

- **Databases.** `moodymate_p34_restore_src` was created with `createdb -T moodymate_f06_p34`
  (the phase copy, only read as the template). The restore target was
  `moodymate_p34_restore_dst`. Both were dropped at the end. `moodymate_dev` was never read or
  written.
- **Data.** Synthetic only. The phase copy holds no `import_artifact` rows, so the scratch
  source was filled through the public API by `scripts/seed_demo.py`, against a local backend
  (`ENV=local`, dev auth) pointed at the scratch source and a scratch store. That gave 5 raw
  artifacts, plus the imports, runs and scenarios the seed creates.
- **Re-hash.** `scripts/verify-artifact-store.sh` (new) is the check BACKUP_RESTORE.md
  describes: every `import_artifact` row's object exists, with the right SHA-256 and size;
  objects without a row are listed.
- **One correction.** In the first run, the trigger-name comparison had a quoting error. Both
  queries failed, so the diff compared two empty outputs and its "SAME" proved nothing. The
  query was fixed and the whole exercise run again. The output below is that second run.
- **Not exercised:** the WS1 to WS3 suites on the restored copy (BACKUP_RESTORE.md step 6),
  and copying a compose `artifacts` volume.

Paths are abbreviated, and each abbreviation stands for exactly this:
- `$R` = `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/p34ops/restore`
- `$WT` = `/home/user/MoodyMate-wt/f06-p34` (the backend commands ran in `$WT/backend`)
- `$SRC_ENV` = `ENV=local AUTH_MODE=dev AI_ENABLED=false DATA_CLASS=synthetic LOG_LEVEL=WARNING RATE_LIMIT_ENABLED=false DATABASE_URL=postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_p34_restore_src ARTIFACT_STORE_DIR=$R/store-src`
- `$DST_ENV` = the same, with `moodymate_p34_restore_dst` and `$R/store-dst`
- libpq: `PGHOST=localhost PGUSER=moodymate PGPASSWORD=moodymate`

```text
== 0. Preconditions

$ psql -X -At -d postgres -c "SELECT count(*) AS other_sessions FROM pg_stat_activity WHERE datname = 'moodymate_f06_p34'"
0
(exit 0)

$ psql -X -At -d postgres -c "SELECT datname FROM pg_database WHERE datname IN ('moodymate_p34_restore_src', 'moodymate_p34_restore_dst')"
(exit 0)

== 1. Scratch source: a copy of the phase copy, then synthetic uploads through the API

$ createdb -T moodymate_f06_p34 moodymate_p34_restore_src
(exit 0)

$ mkdir -p $R/store-src $R/backup
(exit 0)

$ env $SRC_ENV .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18134 &
backend up (pid 8673)

$ env $SRC_ENV .venv/bin/python -m scripts.seed_demo --api http://127.0.0.1:18134 >$R/seed.log 2>&1; tail -n 4 $R/seed.log
Scenarios:
  DEMO_LOAN_DELAY v1: 'Two-week loan repayment delay' -> CONFIRMED_FOR_EXPLORATION
  DEMO_COMBINED_STRESS v1: 'Combined funding stress' -> APPROVED_FOR_TEST
Done. Sign in as Risk analyst or Approver: the Financial Domain, Counterparties, Ratings review, Calculations and Advanced > Agentic Stress-Test Explorer all show illustrative data.
(exit 0)
backend stopped

$ psql -X -At -d moodymate_p34_restore_src -c 'SELECT count(*) AS artifact_rows, sum(size_bytes) AS bytes FROM import_artifact'
5|21363
(exit 0)

$ find $R/store-src -type f | wc -l
5
(exit 0)

== 2. Backup: the database first, then the artifact store

$ pg_dump -Fc -f $R/backup/db.dump moodymate_p34_restore_src
(exit 0)

$ tar -C $R/store-src -cf $R/backup/artifacts.tar .
(exit 0)

$ (cd $R/backup && sha256sum db.dump artifacts.tar && ls -l db.dump artifacts.tar | awk '{print $5, $9}')
2b7ea1ddd16abe4dedd6cf526bc79e61778f9ea4ef24b395abc0a7c81447bd62  db.dump
475db1cd495cbe7405667b8e540899231f0c0b6c8a2aef9d34631987269a335d  artifacts.tar
30720 artifacts.tar
631451 db.dump
(exit 0)

== 3. Restore into a second scratch database and a scratch store directory

$ createdb moodymate_p34_restore_dst
(exit 0)

$ pg_restore --exit-on-error -d moodymate_p34_restore_dst $R/backup/db.dump
(exit 0)

$ mkdir -p $R/store-dst && tar -C $R/store-dst -xf $R/backup/artifacts.tar
(exit 0)
restore elapsed: 1.120284192 s (database and store)

== 4. Verify

$ env $DST_ENV .venv/bin/alembic current 2>/dev/null
0030 (head)
(exit 0)

$ psql -X -At -d moodymate_p34_restore_dst -c "SELECT count(*) AS triggers FROM pg_trigger WHERE NOT tgisinternal"
177
(exit 0)

$ psql -X -At -d moodymate_p34_restore_dst -c "SELECT count(*) AS disabled FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'D'"
0
(exit 0)

$ psql -X -At -d moodymate_p34_restore_src -c "SELECT tgrelid::regclass || '.' || tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1" >$R/triggers-src.txt && psql -X -At -d moodymate_p34_restore_dst -c "SELECT tgrelid::regclass || '.' || tgname FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1" >$R/triggers-dst.txt && wc -l < $R/triggers-src.txt && wc -l < $R/triggers-dst.txt && head -n 2 $R/triggers-dst.txt && diff $R/triggers-src.txt $R/triggers-dst.txt && echo 'trigger names: SAME'
177
177
"position".position_append_only
"position".position_checks
trigger names: SAME
(exit 0)

$ wc -l < $R/counts-src.txt; diff $R/counts-src.txt $R/counts-dst.txt && echo 'row counts per table: SAME'
77
row counts per table: SAME
(exit 0)

$ grep -E '^(audit_event|import_artifact|import_job|source_record|canonical_record|calculation_run|scenario_version)=' $R/counts-dst.txt
audit_event=74
calculation_run=2
canonical_record=97
import_artifact=5
import_job=5
scenario_version=2
source_record=97
(exit 0)

$ bash $WT/scripts/verify-artifact-store.sh moodymate_p34_restore_dst $R/store-dst
rows checked: 5; ok: 5; missing: 0; hash mismatch: 0; size mismatch: 0; objects without a row: 0
OK: every import_artifact row has its object, with the right SHA-256 and size
(exit 0)

== 5. The check catches a damaged store (a copy with one object changed and one removed)

$ cp -a $R/store-dst $R/store-bad
(exit 0)

$ chmod u+w $R/store-bad/sha256/19/19edfdf68d1f1b613f0f8faa1786e96e9646d66b1bed95c769727b44a6520d43 && printf 'X' | dd of=$R/store-bad/sha256/19/19edfdf68d1f1b613f0f8faa1786e96e9646d66b1bed95c769727b44a6520d43 bs=1 seek=0 conv=notrunc status=none
(exit 0)

$ rm -f $R/store-bad/sha256/65/6590f6d603ce0c0a499d59b68a1f66816c8cb80d005689efc049b68914526798
(exit 0)

$ bash $WT/scripts/verify-artifact-store.sh moodymate_p34_restore_dst $R/store-bad
hash mismatch: sha256/19/19edfdf68d1f1b613f0f8faa1786e96e9646d66b1bed95c769727b44a6520d43
missing: sha256/65/6590f6d603ce0c0a499d59b68a1f66816c8cb80d005689efc049b68914526798
rows checked: 5; ok: 3; missing: 1; hash mismatch: 1; size mismatch: 0; objects without a row: 0
FAIL: the artifact store does not match import_artifact
(exit 1)

== 6. The API on the restored database and store: readiness

$ env $DST_ENV .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 18135 &

$ curl -sS -w ' [%{http_code}]\n' http://127.0.0.1:18135/api/v1/health/ready
{"status":"ok","checks":{"database":"ok","migrations":"ok","artifact_store":"ok","workers":"ok"}} [200]
(exit 0)
backend stopped

== 7. Clean up: only the scratch objects this exercise created

$ dropdb moodymate_p34_restore_src && dropdb moodymate_p34_restore_dst
(exit 0)

$ rm -rf $R/store-src $R/store-dst $R/store-bad $R/backup
(exit 0)

$ psql -X -At -d postgres -c "SELECT count(*) AS left_over FROM pg_database WHERE datname IN ('moodymate_p34_restore_src', 'moodymate_p34_restore_dst')"
0
(exit 0)
exercise exit=0
```

**Result:**
- `alembic current` on the restored database gives `0030 (head)`.
- 177 triggers in both databases, with identical names, and none disabled. That is the same
  count as `docs/ops/TRIGGERS.md`.
- Row counts are identical in all 77 tables.
- Re-hash: `rows checked: 5; ok: 5`, exit 0. On a damaged copy (one object changed, one
  removed) the same check names both refs and exits 1.
- The API on the restored database and store: `/health/ready` 200, with every check `ok`.
- The restore step took 1.12 s. That is on a tiny synthetic database, so it is no RTO
  evidence (RPO/RTO stay client placeholders).
- Only the scratch objects this exercise created were removed. `left_over` is 0.

## Coordinator notes (2026-10-24)

- **Items 1-5 landed** by the one owner of the files outside `backend/` (Q2). They cover `docs/ops/` (TRIGGERS.md copied unchanged from the verified draft), `ci.yml`, the compose `prod` profile, `.env.production.example` with its `.gitignore` re-include, and the restore exercise above.
- **Choices that differ from the report text, and why:**
  - compose uses `${NEXT_PUBLIC_API_BASE_URL:-}`, because `:?required` broke the dev `docker compose up -d db`. The frontend Dockerfile's exit-64 guard still refuses an empty arg, and the compose check proves it.
  - metrics bind to the `api-metrics` alias on the `internal: true` network, not `0.0.0.0`, which would also listen on the shared default network. Port 9100 is never published.
  - the `docker history` grep exempts only the official python base image's public `GPG_KEY` (the CPython release key).
- **New LOW findings:**
  - LOW 9: an empty `ERROR_TRACKER_DSN=` parses as a set `SecretStr('')` and logs `error_tracker_dsn_set_without_adapter`. `.env.production.example` comments it out. Treating an empty value as unset is a later backend fix.
  - LOW 10: `/health/ready` needs the database at exactly the code's head, so after a migration the previous version reports 503 (`migrations: fail`) and is not a ready rollback target. This is documented in MIGRATIONS.md.
- **Checked locally with Docker:**
  - non-root: backend uid 10001, frontend uid 1000;
  - `docker history` secret grep: clean;
  - FE-34.1 missing build arg: exit 1, and 0 with the arg;
  - compose `prod`: migrate exit 0, ready 200, `/` 200, 9100 not published, metrics reachable only on the internal network;
  - lock: `uv lock --check` exit 0, and the lock-mismatch step fails under `bash -e` as intended.
  - The sandbox proxy needed local base images that trust its certificate. The Dockerfiles are unchanged, and CI uses the official images.
- **Still to tick from CI evidence:** BE-34.1, BE-34.2, BE-34.7 and FE-34.1. The new `images` job and the uv steps must pass on the PR first.
