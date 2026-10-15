# Phase 1 — engineer report (BE-1.1…1.8, FE-1.1…1.4)

Built in worktree `../MoodyMate-wt/ws1-phase1` (branch `feat/ws1-phase1`) against DB copy
`moodymate_ws1_p1`; tests used `moodymate_test`. The live dev DB `moodymate_dev` was not
touched beyond being the template for the copy (D-03).

Raw command output: [`checks.txt`](checks.txt) (re-run after review fixes),
[`compose-roundtrip.txt`](compose-roundtrip.txt). Browser verification:
[`browser-verify.mjs`](browser-verify.mjs) → [`browser-verify.json`](browser-verify.json);
[`verify-real-500.mjs`](verify-real-500.mjs) → [`browser-real-500.json`](browser-real-500.json); screenshots in this folder.

| Task | Done-when (verbatim from plan) | Evidence |
|---|---|---|
| BE-1.1 | `ruff check`, `mypy app` and `pytest` all pass on the empty skeleton. | checks.txt: ruff "All checks passed!", mypy "no issues found in 115 source files", pytest "25 passed". Module set enforced by `test_every_module_has_the_standard_file_set`. |
| BE-1.2 | a test proves the app refuses to start if `ENV=production` and `AUTH_MODE=dev`. | `test_config.py::test_app_refuses_to_start_with_dev_auth_in_production` PASSED (env vars → `create_app()` raises). |
| BE-1.3 | tests assert the envelope shape for a 404, a 422 and a forced 500, and that no stack trace leaks in the body. | `test_errors.py::test_404_envelope`, `test_422_envelope_does_not_echo_input`, `test_500_envelope_without_stack_trace` PASSED. |
| BE-1.4 | a test sends an ID and gets the same ID back in the header and the error body. | `test_correlation.py::test_inbound_id_is_echoed` PASSED; also live: `curl -H 'X-Correlation-ID: manual-check-1' /api/v1/nope` → header and body `manual-check-1`. |
| BE-1.5 | a test logs a payload containing a bearer token and a secret field, and the captured log contains neither. | `test_logging.py::test_tokens_and_secrets_never_reach_logs` PASSED. |
| BE-1.6 | `alembic upgrade head && alembic downgrade base && alembic upgrade head` succeeds against the compose DB. | compose-roundtrip.txt: `docker compose up -d --wait db` (postgres:16, healthy) → upgrade/downgrade/upgrade log lines → `0001 (head)`; `alembic_version` in the container = `0001`; `docker compose down -v`. Also on the DB copy (checks.txt) and in `test_round_trip_upgrade_downgrade_upgrade`. |
| BE-1.7 | the health test passes, and the exported `openapi.json` validates as OpenAPI 3.1. | `test_health.py` 3 PASSED; `test_exported_contract_is_valid_openapi_31` PASSED (openapi 3.1.0); drift check "up to date". |
| BE-1.8 | the workflow runs green on the Phase 1 PR. | **Not yet evidenced.** `.github/workflows/ci.yml` written and parses; every step it runs was run locally and passed (checks.txt). Needs a PR to run on GitHub. |
| FE-1.1 | `npm run build` and `tsc --noEmit` pass, and the bundle's "Verify the install" checklist is confirmed with screenshots attached to the report. | checks.txt build "Compiled successfully", tsc 0 errors; bundle files byte-identical except the two allowed edits. Checklist below. |
| FE-1.2 | breadcrumbs render the platform labels, and no `fp_reporting` route label remains (grep proves it). | Browser: `/schemas` breadcrumb "Source & Schemas", `/dashboard` "Overview"; grep "none". |
| FE-1.3 | the health page renders backend health through the generated types, and no hand-written payload types exist in `src/` (grep). | Browser against live backend: "Operational / Reachable / local / 0.1.0" (01-dashboard-light.png); grep hits are aliases of generated types only. |
| FE-1.4 | a forced backend 500 shows a toast with the correlation ID from the response. | **Real backend 500, no browser stub.** [`failing_backend.py`](failing_backend.py) runs the real `create_app()` with its DB ping replaced by one that raises, so `GET /api/v1/health` fails through the full middleware stack. browser-real-500.json: backend responded 500 with `x-correlation-id: b3b4a065-…`; the toast reads "…Correlation ID: b3b4a065-29f1-4404-a6aa-3b8d9b1bec7e"; `toast_cid_matches_backend_header: true`. The same ID is on the backend's `unhandled_exception` log line. 05-real-500-toast.png (toast element, full text), 06-real-500-page.png. |

## Bundle "Verify the install" checklist (measured in Chromium)

| Check | Result |
|---|---|
| Warm off-white canvas, not neutral grey | body `rgb(250, 250, 249)`. This is the bundle's verbatim token `60 11% 98%`; the bundle docs quote it as `#FAFAF7` |
| Buttons: navy primary, 6px radius, subtle shadow | default-variant `<Button>` classes measured: background `rgb(27, 62, 105)` (navy), white text, shadow `rgba(27, 37, 50, 0.04) 0 1px 2px` (subtle), radius **4px**. The bundle token `--radius` is `0.375rem` (6px); `button.tsx` uses `rounded-md` = `calc(var(--radius) - 2px)` = 4px. That's the bundle's verbatim behaviour; the INSTRUCTIONS.md "6px" refers to the token. |
| No flash on reload in dark mode | `dark` class present at first commit, before hydration (`dark_before_hydration: true`) |
| Sidebar active item: 3px navy rail | width `3px`, colour `rgb(27, 62, 105)` |
| Status pills low-opacity fills | `success` badge `rgba(45, 123, 51, 0.1)` |
| Logo plate dark in both themes | `rgb(21, 23, 26)` light and dark |

Browser console errors during the run: none.

## Independent review — response

The solution-architect review (recorded in DELIVERY_PLAN.md) found these defects. Responses:

| # | Finding | Response |
|---|---|---|
| 1 | Unhandled-500 response had no CORS headers (correlation middleware outside CORS) | **Fixed.** CORS is now outermost (`backend/app/main.py`). Regression test `test_500_envelope_carries_cors_headers`. Real-browser proof in browser-real-500.json. |
| 2 | Redaction missed plain `key`, `signing_key`, `encryption_key` | **Fixed.** The pattern now matches any key-suffixed field. Regression test `test_generic_key_fields_are_redacted`. |
| 3 | Planned workspaces are links to a "planned" page, not disabled items; no item literally named "Admin" | **Kept, deviation noted.** Disabling items would mean editing the verbatim `sidebar.tsx`. Each link opens an explicit "planned, not built" state with its target phase. The Administration section holds Audit and Access & Roles. Not part of FE-1.2's Done-when. |
| 4 | Sidebar "Calculations" vs breadcrumb "Calculation Operations" | **Fixed.** Both now read "Calculations". |
| 5 | Browser output not kept; gen:api drift "compared at commit time" | **Fixed.** JSON outputs saved; checks.txt now runs `git diff --exit-code` on `schema.d.ts` (exit 0). |
| 6 | Health docstring says "exposes no data" but returns `env` | **Fixed** the docstring. `env` stays because FE-6.1 needs it. |
| 7 | CI Node 20 vs local Node 22 | **Fixed.** CI uses Node 22. |

## Not done / deferred inside Phase 1
- The CI green run (BE-1.8) waits on a PR.
- The sidebar collapse doesn't shift `main` (fixed `ml-64`), matching the bundle example. A
  shared store would be a later FE task if wanted.
