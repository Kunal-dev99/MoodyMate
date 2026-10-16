# Phase 6 — engineer report (BE-6.1…6.3, FE-6.1…6.2)

Built in worktree `../MoodyMate-wt/ws1-phase6` (branch `feat/ws1-phase6`, from `master` c8e0c56). Backend tests ran on `moodymate_test`, the browser and e2e runs on fresh copies of `moodymate_dev` (D-03).

Evidence:
- `checks.txt`: the backend in CI order, the WS1 suite with its printed matrix, the environment guard, the frontend, and Playwright.
- `browser-phase6.json`, `browser-phase6.mjs` and screenshots 01–02: the banner in local dev.

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| BE-6.1 | tests cover each ENV × DATA_CLASS combination. | `test_environment_guard.py::test_env_by_data_class` covers all 8 combinations: per D-14, only production accepts client data, and local, test and staging accept synthetic only. `test_every_combination_is_covered` asserts the matrix is complete. Every response carries `X-Data-Class`, error envelopes included (200, 401, 404 tests), and the header is exposed to the browser through CORS. `/auth/config` reports `environment`. |
| BE-6.2 | the document exists and is referenced from ARCHITECTURE.md. | `docs/RETENTION.md` sets out retention, deletion and backup assumptions per artifact class: audit, schema registry, proposals, identity, classification, seed data, logs and local DBs. Every row names who must confirm it, and there is no numeric RPO/RTO (Arch §11). It is referenced from `docs/ARCHITECTURE.md` under "Environments, retention and backup". |
| BE-6.3 | the suite runs in CI and the matrix table is printed in the test report. | `app/tests/test_ws1_verification.py` (marker `ws1`) covers:<br>• the route × role matrix for all 15 protected routes and 5 roles: 403 exactly where the role lacks the permission, no 5xx, and 401 without a token;<br>• cross-tenant isolation for lists, reads by id, writes and creation;<br>• breaking-change approval: 409 without analysis, 403 for self-approval, ACTIVE after a second person approves;<br>• audit completeness: every mutating route is listed, and each one emits its audit action under its correlation ID;<br>• a base → head migration round trip.<br>CI runs it as its own step, `pytest -m ws1 -v`, and a `pytest_terminal_summary` hook prints the matrix table (checks.txt). |
| FE-6.1 | the banner reads "LOCAL · SYNTHETIC DATA" in local dev. | browser-phase6.json, with the backend in `ENV=local`: `login_banner` and `app_banner` are both "LOCAL · SYNTHETIC DATA", and the API's `X-Data-Class` is `synthetic`. Screenshots 01 (sign-in) and 02 (app shell). Unit tests: the label, client data in staging, and no banner in production. Playwright also checks the banner on every role's sign-in. |
| FE-6.2 | the Playwright report passes in CI. | `frontend/e2e/smoke.spec.ts` covers sign-in for each of the 5 roles, the 403 page, schema compare (all 5 fixture badges) and the proposal flow (steward proposes, Submit disabled until analysis, BREAKING, approver approves, APPLIED). It passes locally, 8 of 8 (checks.txt). The new CI job `e2e` migrates a fresh Postgres, starts the backend (ENV=test) and the production frontend build through Playwright's `webServer`, runs the suite, and uploads `playwright-report`. **CI status is confirmed when the PR runs.** |

## Design notes

- **ENV × DATA_CLASS (decided: D-14, production only).** The plan text ("synthetic is required outside production", "refuse client unless staging or production") reads two ways. The first build allowed staging; the user decided on production only. The banner shows client data in red and synthetic data in amber.
- **Matrix semantics.**
  - "Allowed" means the route's declared permission is held, or the route is open to any signed-in user.
  - An allowed role may still get 404, 409 or 422, because the matrix uses random ids and empty bodies.
  - A denied role must get 403 `forbidden`, which proves the permission check runs before any lookup.
- **Audit completeness is enforced going forward.** `test_every_mutating_route_is_listed` fails as soon as a mutating route is added without an audit expectation.
- **Playwright** is pinned to 1.56.1 to match the preinstalled browser build here, and CI installs its own Chromium. `PW_CHROMIUM_PATH` and `E2E_UVICORN` keep the config usable both in this sandbox and in CI.

## Verification follow-ups

The independent review ticked BE-6.1, BE-6.2 and FE-6.1. It left BE-6.3 and FE-6.2 pending until CI runs, as expected. The fixes below are recorded in checks.txt, in the "Verification follow-ups" section.

| # | Finding | Resolution |
|---|---|---|
| 1 | `DATA_CLASS` defaulted to `synthetic` everywhere, so a staging or production deployment that forgot it would falsely claim synthetic data. | `DATA_CLASS` is now required when ENV is staging or production (startup refuses otherwise, D-13). local and test still default to synthetic (tests). |
| 2 | Whether staging may hold client data. | **Decided by the user: production only (D-14).** `DATA_CLASS=client` is now refused in staging. The test matrix has staging-client refused, and ARCHITECTURE.md and RETENTION.md are updated. |
| 3 | The audit test could be bypassed by listing a route without exercising it. | The emit test records every route it drives and asserts that the set equals `AUDITED`. |
| 4 | The matrix didn't prove the "2xx" side. | The matrix now uses real ids, so allowed roles get 200 on every GET. An allowed role passes only on 2xx, 404, 409 or 422, so any 401 or 403 fails. A denial must be `forbidden` and name the route's declared permission. Every role must reach at least one 2xx. The seeded roles must equal the roles tested. |
| 5 | The banner overlapped the header on scroll. | The banner and header now sit in one sticky block (no overlap after scrolling; screenshot 03). The background is opaque: a tint over the surface colour. |
| 6 | The banner had low contrast and the wrong role. | The text uses the body colour (about 15:1); the colour bar only adds emphasis. It is now `role="region"` with `aria-label="Environment"`. |
| 7 | Not every response carried the header. | The header middleware now runs outermost, so CORS preflights carry `X-Data-Class`. The 500 path is tested. |
| 8 | The cross-tenant audit check was weak. | Every audit item an outsider sees is checked to belong to their tenant. The reverse direction is tested too (this tenant cannot list or read the outsider's schema or users). |
| 9 | RETENTION.md was incomplete against Arch §11. | Added a `tenant` row, a deletion-evidence section covering every class, and a restore procedure outline. The outline includes verifying the triggers exist and running `pytest -m ws1` against the restored database. |
| 10 | No banner when `/auth/config` fails. | Sign-in shows "ENVIRONMENT UNKNOWN · TREAT DATA AS REAL" (fail closed). `/auth/callback` is a transient redirect page and is left without a banner. |

## Not done / follow-ups

- The decisions raised in Phases 2 and 5 are still open:
  - the ENV=test dev-login default;
  - regression evidence for BREAKING proposals;
  - a withdraw action for proposers.
- `RETENTION.md` open items 1–5 wait on client owners.
