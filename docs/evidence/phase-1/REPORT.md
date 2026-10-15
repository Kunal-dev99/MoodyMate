# Phase 1 — engineer report (BE-1.1…1.8, FE-1.1…1.4)

Built in worktree `../MoodyMate-wt/ws1-phase1` (branch `feat/ws1-phase1`) against DB copy
`moodymate_ws1_p1`; tests used `moodymate_test`. The live dev DB `moodymate_dev` was not
touched beyond being the template for the copy (D-03).

Raw command output: [`checks.txt`](checks.txt). Browser verification:
[`browser-verify.mjs`](browser-verify.mjs) + screenshots in this folder.

| Task | Done-when (verbatim from plan) | Evidence |
|---|---|---|
| BE-1.1 | `ruff check`, `mypy app` and `pytest` all pass on the empty skeleton. | checks.txt: ruff "All checks passed!", mypy "no issues found in 115 source files", pytest "25 passed". Module set enforced by `test_every_module_has_the_standard_file_set`. |
| BE-1.2 | a test proves the app refuses to start if `ENV=production` and `AUTH_MODE=dev`. | `test_config.py::test_app_refuses_to_start_with_dev_auth_in_production` PASSED (env vars → `create_app()` raises). |
| BE-1.3 | tests assert the envelope shape for a 404, a 422 and a forced 500, and that no stack trace leaks in the body. | `test_errors.py::test_404_envelope`, `test_422_envelope_does_not_echo_input`, `test_500_envelope_without_stack_trace` PASSED. |
| BE-1.4 | a test sends an ID and gets the same ID back in the header and the error body. | `test_correlation.py::test_inbound_id_is_echoed` PASSED; also live: `curl -H 'X-Correlation-ID: manual-check-1' /api/v1/nope` → header and body `manual-check-1`. |
| BE-1.5 | a test logs a payload containing a bearer token and a secret field, and the captured log contains neither. | `test_logging.py::test_tokens_and_secrets_never_reach_logs` PASSED. |
| BE-1.6 | `alembic upgrade head && alembic downgrade base && alembic upgrade head` succeeds against the compose DB. | checks.txt round trip on `moodymate_ws1_p1` → `0001 (head)`; `test_round_trip_upgrade_downgrade_upgrade` PASSED. **Deviation:** run against local PostgreSQL 16.15, not the compose container. The Docker daemon was unavailable in the build environment. `docker compose config` validated the file. |
| BE-1.7 | the health test passes, and the exported `openapi.json` validates as OpenAPI 3.1. | `test_health.py` 3 PASSED; `test_exported_contract_is_valid_openapi_31` PASSED (openapi 3.1.0); drift check "up to date". |
| BE-1.8 | the workflow runs green on the Phase 1 PR. | **Not yet evidenced.** `.github/workflows/ci.yml` written and parses; every step it runs was run locally and passed (checks.txt). Needs a PR to run on GitHub. |
| FE-1.1 | `npm run build` and `tsc --noEmit` pass, and the bundle's "Verify the install" checklist is confirmed with screenshots attached to the report. | checks.txt build "Compiled successfully", tsc 0 errors; bundle files byte-identical except the two allowed edits. Checklist below. |
| FE-1.2 | breadcrumbs render the platform labels, and no `fp_reporting` route label remains (grep proves it). | Browser: `/schemas` breadcrumb "Source & Schemas", `/dashboard` "Overview"; grep "none". |
| FE-1.3 | the health page renders backend health through the generated types, and no hand-written payload types exist in `src/` (grep). | Browser against live backend: "Operational / Reachable / local / 0.1.0" (01-dashboard-light.png); grep hits are aliases of generated types only. |
| FE-1.4 | a forced backend 500 shows a toast with the correlation ID from the response. | 04-forced-500-toast.png: toast "…Correlation ID: forced-500-corr-id". **Note:** the 500 was injected at the browser network layer (Playwright `route.fulfill`) using the backend's exact envelope shape, because no backend route can fail on demand yet. The backend's own 500 envelope is proven by `test_500_envelope_without_stack_trace`. |

## Bundle "Verify the install" checklist (measured in Chromium)

| Check | Result |
|---|---|
| Warm off-white canvas, not neutral grey | body `rgb(250, 250, 249)`. This is the bundle's verbatim token `60 11% 98%`; the bundle docs quote it as `#FAFAF7` |
| Navy primary | `bg-primary` (active rail) `rgb(27, 62, 105)` |
| No flash on reload in dark mode | `dark` class present at first commit, before hydration (`dark_before_hydration: true`) |
| Sidebar active item: 3px navy rail | width `3px`, colour `rgb(27, 62, 105)` |
| Status pills low-opacity fills | `success` badge `rgba(45, 123, 51, 0.1)` |
| Logo plate dark in both themes | `rgb(21, 23, 26)` light and dark |

Browser console errors during the run: none.

## Not done / deferred inside Phase 1
- The CI green run (BE-1.8) waits on a PR.
- The compose-container round trip waits on a machine with Docker running.
- The sidebar collapse doesn't shift `main` (fixed `ml-64`), matching the bundle example. A
  shared store would be a later FE task if wanted.
