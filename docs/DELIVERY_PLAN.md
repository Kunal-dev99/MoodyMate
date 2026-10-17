# Delivery Plan — phases + tasks

Conventions (see proposed D-08):
- Phase numbers are global and sequential across the whole plan. Later features continue
  at Phase 7, 8, … so task IDs never collide.
- Task IDs `BE-<phase>.<n>` / `FE-<phase>.<n>` are permanent. Superseded tasks are
  struck through, never deleted or renumbered.
- `[ ]` open, `[x]` verified by the solution-architect, with the evidence cited.
- Every phase is built in its own worktree against a DB copy (D-03).

---

## Feature 01 — WS1 Data Governance, Security & Platform Foundation

Kickoff: `docs/prompts/kickoffs/01-ws1-foundation.md` · Status: **APPROVED 2026-10-15 — Phase 1 merged (PR #2); Phase 2 merged (PR #3); Phase 2 hardening merged (PR #4); PR #6 merged (Phase 2 hardening complete); Phase 3 merged (PR #7); Phase 4 merged (PR #8); Phase 5 merged (PR #9); Phase 6 merged (PR #10). All phases merged; FE-2.2, BE-2.15, BE-2.16 and FE-2.5 still await independent verification**

Constraining decisions: D-01…D-12 (all active).

Pre-plan findings:
- **Codebase:** no `backend/` or `frontend/` yet, so Phase 1 must create both skeletons.
- **Design bundle:** `fp_design_system_template/` exists. It needs its route labels and
  status pills adapted after copying (the bundle itself is never edited).
- **Contract conflicts:** five listed in `docs/ARCHITECTURE.md`. Those touching WS1 are
  settled by D-05…D-08.

### Phase 1 — Platform skeleton and contract baseline (G-P1, cross-cutting)

Goal: a running, empty, tested backend and frontend with every cross-cutting rule wired
in before any feature table exists.

- [x] **BE-1.1** Backend project: `backend/` with `pyproject.toml` (FastAPI, SQLAlchemy 2.0
  async, asyncpg, Alembic, pydantic-settings, pytest, ruff, mypy), app factory
  `app/main.py`, and the module layout from `docs/ARCHITECTURE.md` (empty modules carrying
  the standard 7-file set).
  - *Done-when:* `ruff check`, `mypy app` and `pytest` all pass on the empty skeleton. — ✔ verified: checks.txt (ruff "All checks passed!", `mypy app scripts` "no issues found", pytest "25 passed"; 25 PASSED lines match the 25 test functions in backend/app/tests; still holds after the review fixes: the re-run checks.txt shows ruff/mypy clean and "27 passed"); deps in backend/pyproject.toml:6-24; module layout matches docs/ARCHITECTURE.md:35-42 and is enforced by `test_structure.py::test_every_module_has_the_standard_file_set` (PASSED).
- [x] **BE-1.2** `core/config.py` settings: `ENV` (`local|test|staging|production`),
  `DATABASE_URL`, `AUTH_MODE`, `AI_ENABLED=false`, `DATA_CLASS=synthetic`.
  - *Done-when:* a test proves the app refuses to start if `ENV=production` and
    `AUTH_MODE=dev`. — ✔ verified: `test_config.py::test_app_refuses_to_start_with_dev_auth_in_production` (env vars set, `create_app()` raises ValidationError; PASSED in checks.txt), guard at backend/app/core/config.py:29-34, factory reads settings at backend/app/main.py:19.
- [x] **BE-1.3** `core/errors.py`: error envelope `{code, message, details, correlation_id,
  retryable}` (D-07) for HTTP, validation and unhandled errors.
  - *Done-when:* tests assert the envelope shape for a 404, a 422 and a forced 500, and
    that no stack trace leaks in the body. — ✔ verified: `test_errors.py::test_404_envelope`, `::test_422_envelope_does_not_echo_input`, `::test_500_envelope_without_stack_trace` (each asserts the exact 5-key set; the 500 test asserts no "Traceback"/"RuntimeError"/exception text); all PASSED in checks.txt. Impl: backend/app/core/errors.py:20-114, backend/app/core/correlation.py:61-70.
- [x] **BE-1.4** `core/correlation.py` middleware: accepts or generates `X-Correlation-ID`,
  echoes it in the response, binds it to logs.
  - *Done-when:* a test sends an ID and gets the same ID back in the header and the error
    body. — ✔ verified: `test_correlation.py::test_inbound_id_is_echoed` asserts header and error-body `correlation_id` both equal the sent ID (PASSED in checks.txt); middleware backend/app/core/correlation.py:41-72; log binding backend/app/core/logging.py:78.
- [x] **BE-1.5** `core/logging.py`: structured JSON logs with a redaction filter
  (Authorization headers, tokens, `password|secret|key` fields, values tagged sensitive).
  - *Done-when:* a test logs a payload containing a bearer token and a secret field, and
    the captured log contains neither. — ✔ verified: `test_logging.py::test_tokens_and_secrets_never_reach_logs` (bearer token in message and header, `client_secret`, `password`, nested `api_key`, JWT and `Sensitive(...)` all asserted absent; PASSED in checks.txt). Impl: backend/app/core/logging.py:22-85. Re-checked after the key-regex change (logging.py:22-26): the test is unchanged and still PASSED in the re-run checks.txt, and the new `test_generic_key_fields_are_redacted` also PASSED.
- [x] **BE-1.6** `db/`: async engine/session, declarative base with the common columns
  (`id` UUID, `tenant_id`, `created_at`, `updated_at`, `created_by`), Alembic env (async),
  an empty baseline revision, and `docker-compose.yml` with Postgres 16 (D-12).
  - *Done-when:* `alembic upgrade head && alembic downgrade base && alembic upgrade head`
    succeeds against the compose DB. — ✔ verified: docs/evidence/phase-1/compose-roundtrip.txt shows `docker compose config -q` valid, `up -d --wait db` reaching postgres:16 (healthy), then upgrade → downgrade → upgrade log lines, `0001 (head)`, `alembic_version` = `0001` read inside the container, and `down -v`. It is backed by `test_migrations.py::test_round_trip_upgrade_downgrade_upgrade` (PASSED, checks.txt). Deliverables: backend/app/db/session.py, base.py (common columns as `AuditColumns` + `TenantScoped` mixins), migrations/env.py (async), versions/0001_baseline.py, docker-compose.yml.
- [x] **BE-1.7** `GET /api/v1/health` (no auth; returns version and DB reachability) and
  `scripts/export_openapi.py` writing `backend/openapi.json`.
  - *Done-when:* the health test passes, and the exported `openapi.json` validates as
    OpenAPI 3.1. — ✔ verified: `test_health.py::test_health_degraded_when_db_unreachable`, `::test_health_ok_with_db`, `::test_health_needs_no_auth` and `test_openapi.py::test_exported_contract_is_valid_openapi_31` (openapi-spec-validator `validate`) plus `::test_committed_contract_matches_code`, all PASSED in checks.txt; backend/openapi.json:101 `"openapi": "3.1.0"`; drift check "up to date". Impl: backend/app/api/v1/health.py:18-36, backend/scripts/export_openapi.py (the script sits under `backend/scripts/`).
- [x] **BE-1.8** CI workflow `.github/workflows/ci.yml`: backend lint, type check, tests,
  migration round trip on a Postgres service container, OpenAPI drift check; frontend
  lint, type check, build.
  - *Done-when:* the workflow runs green on the Phase 1 PR. — ✔ verified: docs/evidence/phase-1/ci-run.md records PR https://github.com/Kunal-dev99/MoodyMate/pull/2 (head 54f0320), workflow run https://github.com/Kunal-dev99/MoodyMate/actions/runs/38741589741, and both jobs `backend` and `frontend` completed with conclusion **success**. .github/workflows/ci.yml has every step the task lists: lint/format :44-45, mypy :46-47, round trip on a postgres:16 service :22-31 and :48-49, pytest with REQUIRE_DB=1 :36 and :50-51, OpenAPI drift :52-53; frontend gen:api diff, lint, typecheck and build :69-76. The local git ref `feat/ws1-phase1` = 54f0320a, which is the "Phase 1 review fixes" commit on top of 5c63ee3, so the run covers the code verified above. Caveat: ci-run.md is a transcription of the GitHub API result. I could not open the run links under my Read/Grep/Glob/Edit-only constraints, so the "green" status rests on that record, corroborated by the matching SHA and commit timing (commit 13:18:11Z, jobs started 13:18:36Z).
- [x] **FE-1.1** `frontend/`: Next.js 14 App Router + TS + Tailwind 3 under `src/`,
  `@/*` alias, with `fp_design_system_template/` applied verbatim per its INSTRUCTIONS.md
  (Option A logo; `api/logo` route removed).
  - *Done-when:* `npm run build` and `tsc --noEmit` pass, and the bundle's "Verify the
    install" checklist is confirmed with screenshots attached to the report. — ✔ verified: `npm run build` "Compiled successfully" and `tsc --noEmit` exit 0 in the re-run checks.txt; the bundle copy is verbatim apart from the two allowed edits (checks.txt); Option A logo at public/brand/logo.png and no `src/app/api/` route. All six checklist items are measured in docs/evidence/phase-1/browser-verify.json (canvas, primary button, no-flash dark, 3px navy rail, low-opacity pill, dark logo plate in both themes), with screenshots 01–03. Judgement call on the Button item: it measures navy `rgb(27, 62, 105)` with a subtle shadow but a **4px** radius, not the checklist's literal 6px. That 4px comes from the bundle's own verbatim `rounded-md` = `calc(var(--radius) - 2px)` (frontend/tailwind.config.ts:82, button.tsx:7), and the `--radius` token itself is 6px (`0.375rem`). The install therefore matches the bundle, which INSTRUCTIONS.md names as the source of truth, and I accept it. The canvas (`rgb(250,250,249)` vs the documented `#FAFAF7`) is the same kind of token-vs-docs gap. Caveat: the button was measured on an injected element carrying button.tsx's default classes, not on a rendered `<Button>`. (Correction to my earlier note: `reports: 'Reports'` is an unchanged line of the bundle's own PageHeader.tsx:26, so the checks.txt diff was not stale.)
- [x] **FE-1.2** App shell: Sidebar and Header with this platform's nav (Admin, Schemas,
  Audit; later workspaces shown as disabled "planned" items), and `ROUTE_LABELS` replaced.
  - *Done-when:* breadcrumbs render the platform labels, and no `fp_reporting` route label
    remains (grep proves it). — ✔ verified: ROUTE_LABELS replaced at frontend/src/components/common/PageHeader.tsx:25-36; screenshots 01-dashboard-light.png (breadcrumb "Overview") and 03-planned-workspace.png ("Source & Schemas"); my own grep of frontend/src for the fp_reporting labels (Requirements, Matching Engine, SQL Builder, Catalogue, Configurations, …) finds none outside verbatim bundle comments/CSS (header.tsx:10, sidebar.tsx:14, globals.css:238,262). Nav: frontend/src/config/navigation.ts.
- [x] **FE-1.3** Generated API client: `openapi-typescript` + a thin fetch wrapper in
  `src/api/` that sends `X-Correlation-ID` and parses the error envelope. Includes an
  `npm run gen:api` script.
  - *Done-when:* the health page renders backend health through the generated types, and
    no hand-written payload types exist in `src/` (grep). — ✔ verified: frontend/src/app/(platform)/dashboard/PlatformHealth.tsx:13,34 uses `Schemas['HealthResponse']` from generated frontend/src/api/schema.d.ts:55; 01-dashboard-light.png shows Operational / Reachable / local / 0.1.0 from the unmocked backend (browser-verify.mjs step 1); `gen:api` at frontend/package.json:11; correlation header sent at frontend/src/api/client.ts:22-32 and envelope parsed at :65-78. My grep of type/interface declarations in src finds only aliases of generated types (client.ts:10-11, PlatformHealth.tsx:13) plus UI prop types.
- [x] **FE-1.4** Shared error and state components: error-envelope toast showing the
  correlation ID, empty state, loading skeleton.
  - *Done-when:* a forced backend 500 shows a toast with the correlation ID from the
    response. — ✔ verified: docs/evidence/phase-1/failing_backend.py runs the real `create_app()` with a raising DB ping, so `/api/v1/health` fails as an unhandled exception through the full middleware stack. verify-real-500.mjs only passes requests through (`route.continue()`). browser-real-500.json shows a backend 500 with `x-correlation-id: b3b4a065-29f1-4404-a6aa-3b8d9b1bec7e`, and the toast text carries the same ID (`toast_cid_matches_backend_header: true`). 05-real-500-toast.png shows the full ID and 06-real-500-page.png the page. Because the toast reads the ID from the response body, this also proves the CORS fix in the browser: CORS is now outermost (backend/app/main.py:39-50), with regression test `test_errors.py::test_500_envelope_carries_cors_headers` (PASSED, checks.txt). Components: frontend/src/components/common/api-error-toast.ts, EmptyState.tsx, LoadingBlock.tsx.

*Reversibility:* additive new folders and an empty baseline migration. Rollback is
reverting the merge; no data exists yet. The AI flag ships `false`.

### Phase 2 — Identity and authorization (DG-02)

Goal: every request has a principal; nothing is permitted unless a permission says so;
row scope is applied in repositories.

- [x] **BE-2.1** Migration + models: `tenant`, `user_account` (IdP subject, no password),
  `role`, `permission`, `user_role`, `role_permission`, with a tested downgrade.
  - *Done-when:* the migration round trip passes, and model tests create and read each
    entity. — ✔ verified: backend/app/db/migrations/versions/0002_identity_tables.py creates all 6 tables (:24-95) with a full downgrade (:99-110), and the upgrade has no DROP (`test_no_drop_in_forward_migrations` PASSED). The round trip passes: `test_migrations.py::test_round_trip_upgrade_downgrade_upgrade` is PASSED in checks.txt, and checks.txt:91-117 shows base↔head twice on the DB copy and on the postgres:16 compose container. `test_identity_models.py::test_create_and_read_each_identity_entity` creates all 6 entities and reads each one back, including the user→role→permission relationships (:51-62; PASSED). Models: backend/app/governance/models.py:18-95. Caveat: checks.txt was captured at 13:39:42Z on the uncommitted tree, 2 minutes before commit 9b361b8 (13:41:38Z per the worktree reflog). I had no shell, so I could not diff the two.
- [x] **BE-2.2** Seed data migration (D-11): one tenant; roles `platform_admin`,
  `data_steward`, `risk_analyst`, `approver`, `auditor`; WS1 permissions `schema:read`,
  `schema:propose`, `schema:approve`, `audit:read`, `admin:read`. Its downgrade deletes
  only seed rows tagged by the migration (documented exception under D-01: seed data,
  not user data).
  - *Done-when:* after upgrade, the role→permission matrix query matches the table in
    this plan; after downgrade, the tables are empty. — ✔ verified: I compared the plan's matrix table (below) by hand against the frozen `MATRIX` in backend/app/db/migrations/versions/0003_identity_seed.py:49-55 and against `ROLE_PERMISSIONS` in backend/app/governance/constants.py:40-48. All three are identical, and `platform_admin` has no `schema:approve`. `test_identity_seed.py::test_seeded_matrix_matches_plan` runs the SQL join and compares it with `ROLE_PERMISSIONS`. `::test_downgrade_removes_seed_and_upgrade_restores_it` asserts that all 6 identity tables count 0 at revision 0002. Both are PASSED in checks.txt. The container query at checks.txt:118-123 prints the same matrix. The downgrade deletes only rows tagged `seeded_by` (0003:138-141). Its FK refusal when user data references a seeded role is tested (`::test_seed_downgrade_refuses_to_delete_user_data`) and was also seen for real at checks.txt:85-87.
- [x] **BE-2.3** `core/auth.py` principal resolution behind `AUTH_MODE` (D-09):
  `oidc` validates a JWT against the issuer's JWKS; `dev` validates JWTs signed by a
  local dev key. No token or an invalid token returns 401. Unknown users are not
  auto-provisioned outside `ENV=local`.
  - *Done-when:* tests cover a valid token, an expired token, a wrong issuer, no token,
    and `dev` mode being rejected in production. — ✔ verified: test_auth.py `test_valid_oidc_token_resolves_principal`, `test_expired_token_is_401` (`token_expired`), `test_wrong_issuer_is_401`, `test_no_token_is_401_with_challenge` (401 + `WWW-Authenticate: Bearer`) and `test_dev_mode_refused_in_production` are all PASSED in checks.txt. The app-start refusal is in test_config.py `test_app_refuses_to_start_with_dev_auth_in_production`. Impl: backend/app/core/tokens.py:56-73 (exp/iss/sub/aud required, issuer and audience checked) and :133-142 (asymmetric algorithms only; HS256 confusion and `alg=none` are tested). auth.py:49-72 maps errors to 401. Unknown users get 403 outside local (governance/service.py:47-54, `test_unknown_user_is_not_provisioned_outside_local`). Caveat: the unit tests inject `key_resolver`, so the real JWKS discovery and fetch path (tokens.py:116-129) is exercised only by browser-oidc-mode.json, where the backend accepted an RS256 token from the mock IdP. This Done-when does not cover security defects; those are reported separately in the architect's Phase 2 security review (fail-open ENV/AUTH_MODE defaults, an http issuer and an unvalidated jwks_uri, JWKS refetch on an unknown kid).
- [x] **BE-2.4** `require_permission(...)` dependency, fail closed (D-04), plus a route
  registry test that fails if any `/api/v1` route other than `health` lacks a declared
  permission.
  - *Done-when:* the registry test fails when a deliberately unguarded test route is
    added, and passes otherwise. — ✔ verified: test_route_registry.py `test_registry_catches_an_unguarded_route` adds `GET /api/v1/_unguarded` and asserts that `undeclared_routes` reports exactly that route. `test_every_api_route_declares_a_permission` asserts `[]` for the real app with the dev routes mounted. Both are PASSED in checks.txt. Runtime fail-closed: an app-level dependency (backend/app/main.py:34) runs `enforce_declared_permission` (backend/app/core/auth.py:124-135), and `test_unguarded_route_is_denied_at_runtime` (PASSED) gets 403 with no data. `require_permission` is at auth.py:78-91, and `PUBLIC_PATHS` is pinned by `test_public_paths_are_exactly_the_reviewed_set`. Limit: only `APIRoute`s are checked; mounts and websocket routes are not (see the architect's Phase 2 security review).
- [x] **BE-2.5** `ScopedRepository` base: every query is filtered by
  `tenant_id = principal.tenant_id` (and further scopes when added); no repository
  method accepts a tenant from request input.
  - *Done-when:* a test with two tenants proves cross-tenant reads return nothing, even
    with a crafted ID in the URL. — ✔ verified: test_scoping.py `test_cross_tenant_reads_return_nothing_even_with_crafted_id` sets up a second tenant with a user. That user is absent from `GET /users`, and `GET /users/{foreign-id}` returns 404 with the same code as a random ID. `::test_tenant_parameters_in_the_request_are_ignored` sends the tenant as a query param and in `X-Tenant-ID`. `::test_repository_scope_filters_at_the_query` tests the repository directly. All are PASSED in checks.txt. Impl: backend/app/db/repository.py:32-46 filters every query by `scope.tenant_id`; the constructor takes only a `DataScope`, which comes from `Principal.scope` (backend/app/core/principal.py:20-23); governance/service.py:95-96 wires it.
- [x] **BE-2.6** `GET /api/v1/me` and `GET /api/v1/me/permissions`.
  - *Done-when:* tests for each seeded role return exactly that role's permissions. — ✔ verified: `test_me.py::test_me_and_permissions_per_role[platform_admin|data_steward|risk_analyst|approver|auditor]` (5× PASSED in checks.txt) asserts that `/me` roles == [role], that tenant == the default tenant, and that `/me/permissions` equals the sorted `ROLE_PERMISSIONS[role]` exactly, which I checked against the plan matrix. Both endpoints require `authenticated()` (backend/app/governance/router.py:82-97); `test_me_requires_authentication` (PASSED) gets 401. Permissions are derived server-side from the DB (governance/service.py:20-30).
- [x] **FE-2.1** Session handling for both `AUTH_MODE`s (an OIDC redirect flow, and a dev
  user picker shown only when the backend reports `AUTH_MODE=dev`).
  - *Done-when:* manual test notes show sign-in as each seeded role in dev mode; the
    picker doesn't render when the mode is `oidc`. — ✔ verified: docs/evidence/phase-2/p2-dev.mjs:8-14,30-33 signs in by clicking each of the 5 persona buttons on the real picker; it does not inject tokens. browser-dev-mode.json shows each role landing with `shown_matches_backend: true`, and screenshot 01 shows the 5 personas. In oidc mode, p2-oidc.mjs:13-16 records `dev_picker_rendered: 0`, and screenshot 06 shows only "Sign in with SSO". The full code+PKCE (S256) round trip reached /dashboard. The picker is gated on `config.mode === 'dev'` (frontend/src/app/login/SignIn.tsx:100) and the SSO button on `'oidc'` (:85). Token handling: frontend/src/lib/session.ts, src/lib/oidc.ts, src/app/auth/callback/page.tsx; 401 → sign-in at src/api/client.ts:39-43 (tampered-token run → /login?next=). The "manual test notes" are scripted Playwright runs with recorded output, which I accept as stronger than manual notes. Follow-ups are in the architect's Phase 2 security review: the `next` open-redirect bypass and token storage.
- [ ] **FE-2.2** Permission-aware nav and actions driven by `GET /me/permissions`; a 403
  page and an access-denied inline state.
  - *Done-when:* `auditor` sees no Propose button; a direct URL to a forbidden page shows
    the 403 state, and the backend also returns 403 (cosmetic hiding is not relied on). — ⚠ open: the second half is verified. Auditor → /access/users renders the 403 state (frontend/src/components/auth/Forbidden.tsx) with the backend's own 403 and correlation ID (browser-dev-mode.json `auditor_direct_url_users.backend_status_seen_by_browser: 403`, screenshot 04). The page always sends the request and renders on the server's 403 (src/app/(platform)/access/users/page.tsx:26-43). The nav hides items per permission (AppShell.tsx:19; `nav` per role in the JSON). The first half is not demonstrated. No Propose button exists anywhere in frontend/src (my grep finds `propose` only in the generated Permission enum). "Auditor sees no Propose button" is therefore true only because there is no button, and that proves nothing about action gating, which is what the criterion tests. The gating mechanism (`<RequirePermission fallback={null}>`) is shown only on the admin-only Users section. Missing: a Propose action gated on `schema:propose`, and evidence that auditor does not see it while data_steward does. Re-verify when FE-5.1 lands, or add that check to FE-5.1's Done-when.

**Phase 2 hardening.** These were added 2026-10-15 after the independent security review of PR #3 and approved by the user. Governed by D-13.

- [x] **BE-2.7** Fail-safe configuration: `ENV` has no default (startup fails if unset), and
  `AUTH_MODE=dev` is allowed only when `ENV` is `local` or `test` (D-13 §1).
  - *Done-when:* tests prove startup is refused with `ENV` unset, and with `AUTH_MODE=dev`
    in `staging` and in `production`. — ✔ verified: `ENV: Environment` has no default (backend/app/core/config.py:30), and the validator refuses dev mode unless `ENV` is in `LOCAL_ENVS` (:51-55). test_hardening.py `test_startup_refused_without_env` (removes `ENV` and passes `_env_file=None`) and `test_dev_auth_refused_outside_local_and_test[staging|production]` are PASSED in checks.txt, as is test_config.py `test_app_refuses_to_start_with_dev_auth_in_production` through `create_app()`. startup-refusals.txt shows real `uvicorn --factory` processes refusing to start. The suite ran under `env -u ENV`. CI sets `ENV: test` (.github/workflows/ci.yml:33), so only the local run shows that nothing depends on a default. Caveats: checks.txt was captured on the uncommitted tree (its header says so), and I had no shell to diff it against HEAD. backend/.env.example:13 still describes dev mode outside local/test, which D-13 forbids; that comment needs fixing. `AUTH_MODE` still defaults to `dev`, and `ENV=test` enables dev sessions with the public default secret, so a deployed environment must never be labelled `test`.
- [x] **BE-2.8** OIDC transport: `https://` is required for `OIDC_ISSUER`, `OIDC_JWKS_URL` and a
  discovered `jwks_uri` outside local/test (D-13 §2).
  - *Done-when:* tests prove an `http://` issuer is refused in staging, and an `http://`
    `jwks_uri` from discovery is rejected when insecure transport is not allowed. — ✔ verified: config.py:69-73 refuses non-https `OIDC_ISSUER`/`OIDC_JWKS_URL` outside local/test. `JwksKeys._checked` (backend/app/core/tokens.py:151-154) is applied to the discovery URL and to the JWKS URL (:161, :168). `build_verifier` passes `allow_http=settings.allow_insecure_oidc` (:238), which is local/test only (config.py:76-79). test_hardening.py `test_http_issuer_refused_in_staging`, `test_http_jwks_uri_from_discovery_rejected_when_insecure_not_allowed` (asserts only the https discovery URL was fetched, never the http JWKS) and `test_http_jwks_url_refused_in_production` are PASSED in checks.txt. Follow-up (availability, fails closed): tokens.py:166 caches the discovered `jwks_uri` before the https check at :168. One bad discovery document therefore leaves the backend returning 503 until restart, even after the provider fixes it.
- [x] **BE-2.9** Token-type confusion guard: startup is refused when
  `OIDC_AUDIENCE == OIDC_CLIENT_ID` (D-13 §3).
  - *Done-when:* a test proves the refusal. — ✔ verified: config.py:66-68 applies in every `ENV`, and `OIDC_CLIENT_ID` is now required for oidc (:59-65). test_hardening.py `test_audience_equal_to_client_id_refused` is PASSED in checks.txt, and startup-refusals.txt:10-11 shows a real process refusing. Audience checking is still enforced per token (tokens.py:61-67, test_auth.py `test_wrong_audience_is_401` PASSED). The check is an exact string comparison, so it relies on the operator configuring a distinct API audience at the identity provider.
- [x] **BE-2.10** JWKS resilience: an unknown `kid` refreshes keys at most once per interval;
  provider or discovery failures return 503 `auth_provider_unavailable` (retryable); the
  key loader is thread-safe.
  - *Done-when:* a test with N tokens carrying random `kid`s shows ≤ 1 key fetch per
    interval; a test with the provider unreachable shows 503 with the envelope. — ✔ verified: `JwksKeys.__call__` (tokens.py:181-202) fetches only when `_last_attempt` is older than `min_refresh_seconds`. `_last_attempt` is set before the network call (:157), so failures back off too. All state sits under a `threading.Lock` (:146, :186). test_hardening.py `test_unknown_kids_trigger_at_most_one_fetch_per_interval` sends 50 forged kids, sees `fetches` stay at 1, and sees exactly 1 more fetch after the interval. `test_provider_unavailable_is_503_envelope` asserts 503, `code=auth_provider_unavailable`, `retryable: true` and the correlation ID through the real app. `test_provider_outage_backs_off` and `test_key_rotation_is_picked_up_after_the_interval` also cover this. All are PASSED in checks.txt. The 503 mapping is in auth.py:64-70. kid-flood.txt shows 200 forged tokens returning 200 × 401 with no 500. Follow-up (availability): the lock is held across `urlopen` (5 s timeout per call, discovery plus JWKS), and cached-key lookups take the same lock. During a slow refresh, every authenticated request therefore waits, and the waiting requests occupy the request threadpool (verify runs via `run_in_threadpool`, auth.py:61). Thread-safety, as the task requires, is met.
- [x] **BE-2.11** Suspended tenants: users of a non-ACTIVE tenant are denied with 403
  `tenant_suspended`.
  - *Done-when:* a test proves an active user in a SUSPENDED tenant gets 403. — ✔ verified: backend/app/governance/service.py:68-69 denies any tenant status other than ACTIVE, including a missing tenant (`None`), on every principal resolution. The query is in governance/repository.py:66-70. test_hardening.py `test_user_of_suspended_tenant_is_denied` creates a tenant and an ACTIVE `platform_admin`, suspends the tenant, and asserts 403 `tenant_suspended` on `/me`. It is PASSED in checks.txt.
- [x] **BE-2.12** Guard coverage: the route registry reports any mount or websocket route under
  `/api/v1` as undeclared.
  - *Done-when:* a test adds a mount and a websocket route under `/api/v1` and the registry
    lists both. — ✔ verified: `undeclared_routes` (backend/app/core/auth.py:119-135) reports every non-`APIRoute` whose path starts with `/api/v1`. test_hardening.py `test_registry_reports_mounts_and_websockets` adds `Mount /api/v1/files` and `APIWebSocketRoute /api/v1/ws` and asserts that the result is exactly those two. It is PASSED in checks.txt, and test_route_registry.py still passes for the real app. Limits, as follow-ups: (a) the check is a path-prefix match on top-level routes, so a mount at a parent path (e.g. `app.mount("/api", sub_app)`) serves `/api/v1/*` without being reported; (b) at runtime, `enforce_declared_permission` (auth.py:142-147) still skips websockets and cannot see into mounts, so CI is the only guard for them.
- [x] **FE-2.3** Same-origin redirects only: `next` (sign-in page) and the OIDC callback state
  accept only same-origin paths.
  - *Done-when:* unit tests reject `//x`, `/\\x`, `/%5Cx`, absolute and `javascript:` URLs;
    a browser run with `next=/%5Cevil.example` lands on `/dashboard`. — (PR #4 verification, now closed by the follow-up below) ⚠ open: the literal criteria are met. frontend/src/lib/__tests__/redirect.test.ts rejects every listed form (14 tests PASSED in checks.txt). browser-redirects.json.txt shows `next=/%5Cevil.example` and 5 other payloads landing on `/dashboard` with no external requests. SignIn.tsx:25,48,58 and auth/callback/page.tsx:30 both use the check. The task is still not done, because "accept only same-origin paths" is bypassable. `safeRedirectPath` (frontend/src/lib/redirect.ts:17-23) checks the raw/decoded input for a leading `//`, but it returns the *normalised* `url.pathname`. Dot segments collapse during normalisation. So `next=/.//evil.example` (also `/..//evil.example` and `/%2e//evil.example`) passes both checks, and `new URL('/.//evil.example', origin)` is same-origin. The function then returns `//evil.example`, and `router.replace('//evil.example')` navigates off-site. Missing: reject a result whose path starts with `//` or `/\` *after* normalisation (or return `url.href` only when it is same-origin), unit cases for the dot-segment forms, and a browser run with `next=/.//evil.example`. — ✔ verified (follow-up, branch feat/ws1-phase2-hardening-2): `safeRedirectPath` now checks the value it returns. After `new URL(raw, origin)` and the origin check, it rejects a normalised `pathname+search+hash` that starts with `//` or `/\`, and the once-decoded pathname too (frontend/src/lib/redirect.ts:36-45). It then re-resolves the result against the origin (:46). I traced each form through the URL parsing standard. `/.//x`, `/..//x`, `/%2e//x`, `/%2e%2e//x` and `/a/..//x` all normalise to pathname `//x`. `/./\x` normalises the same way, because `\` is `/` for http(s). `/%2F%2Fx` and `/%5Cx` decode to `///x` and `/\x`. Every one is rejected. A normalised same-origin pathname cannot start with `\`, and tab and newline are removed during parsing, so I found no remaining normalisation bypass. redirect.test.ts has all of the Done-when's forms, 6 dot-segment cases, a check that the output always resolves to the same origin, and a bare-`%` keep case. All 21 are listed as passing in docs/evidence/phase-2-hardening-2/checks.txt:113-134 (26/26 overall). browser-redirects.json shows `next=/%5Cevil.example`, the 4 dot-segment payloads and 5 others landing on `/dashboard` with 0 external requests, and `/access` kept. prefix-bypass.txt reproduces the bypass on the merged code. Both callers still use the check (SignIn.tsx, auth/callback/page.tsx:30). Caveat: checks.txt was again captured on the uncommitted tree (its header says so); I had no shell to diff it against HEAD.
- [x] **FE-2.4** A 401 clears the session only when it answers the current token.
  - *Done-when:* a unit test proves a late 401 for an old token leaves a newer token in place,
    and a 401 for the current token clears it. — ✔ verified: `authMiddleware.onResponse` (frontend/src/api/client.ts:43-49) clears the session only when the request's `Authorization` header equals `Bearer <current token>`. frontend/src/api/__tests__/auth-middleware.test.ts has "keeps a newer token when a late 401 answers an old one" (stale → `renewed` kept) and "clears the session when the 401 answers the current token". Both are among the 5 PASSED in checks.txt, and CI runs `npm test` (.github/workflows/ci.yml:76). Redirect to sign-in still follows from the token being cleared (components/auth/SessionProvider.tsx:61-67).
- [x] **BE-2.13** JWKS refinements (verifier findings on BE-2.10): no network I/O while
  holding the lock (cached keys stay readable during a refresh); a discovered `jwks_uri` is
  checked before it is remembered.
  - *Done-when:* a test proves a cached-key lookup completes while a refresh is blocked; a
    test proves an unsafe discovered `jwks_uri` is not cached and the loader recovers when
    the provider fixes it. Both fail on the pre-fix code. — (first verification, now closed by Round 2 below) ⚠ open: the Done-when itself is met. `JwksKeys` (backend/app/core/tokens.py:189-236) reads cached keys without the lock (:195), and the lock covers only bookkeeping. One fetcher runs `_download` outside the lock (:218-227), and its `finally` clears `_inflight` and wakes waiters even if the fetch raises. The discovered `jwks_uri` is checked before it is stored (:173, stored at :222). test_hardening.py `test_cached_keys_served_while_a_refresh_is_in_flight`, `test_unsafe_discovered_jwks_uri_is_not_remembered` and `test_concurrent_first_requests_share_one_fetch` are PASSED (checks.txt:59-61). By reading the old code (lookup under a lock held across the fetch; `_jwks_url` set before the check), both named tests would fail there. The "FAILED on master" run itself is not in the evidence folder. Open because the rewrite introduces a race that the old code did not have. Between the lock-free miss (:195) and taking the lock (:199), another thread's fetch can finish. That thread clears `_inflight` and leaves `_last_attempt` recent, so this request sees no fetch in flight and fetching not allowed. It then raises `InvalidToken` (:213-216) without looking again, even though its key is now cached. Scenario: on a cold start or a key rotation, a valid token gets 401, and FE-2.4 then clears that user's session. Missing: repeat `_lookup(kid)` before raising at :213-216, plus a test that forces this interleaving. — ✔ verified (Round 2): the cache is now looked up again under the lock before any decision (backend/app/core/tokens.py:202-207). A fetch stores its keys under the lock (:233-235) before it clears `_inflight` (:237-238). So a request that takes the lock afterwards always sees the new keys, and the race is closed. Unknown kids no longer wait when keys are already cached (:210-213). Truncated provider responses now give 503 instead of 500 (:114). The 4 Round 2 tests are recorded as FAILED before the fix and PASSED after (docs/evidence/phase-2-hardening-2/checks.txt:142-178), and the full suite is 98 passed. The original Done-when tests still pass. Follow-up (availability, not a Done-when gap): the early `InvalidToken` at :210-213 returns 401 for a *valid* token whose new kid is being fetched right now. Scenario: right after the identity provider rotates keys, a user loads a page. `/me` starts the fetch, and the parallel `/me/permissions` (SessionProvider.tsx:71-75) gets 401. FE-2.4 then clears the session, so the first users after each rotation are signed out once. Separately, the 60 s interval (an existing BE-2.10 trade-off) means a new kid seen within 60 s of any other refresh gets 401 until the interval passes. Forged kids sent every 60 s make that window certain at each rotation, but it is bounded to ≤ 60 s. Suggested fix: raise `ProviderUnavailable` (503, retryable) instead of `InvalidToken` at :213 and :228 when keys are cached. The frontend does not clear the session on 503, and a forged kid gains nothing.
- [x] **BE-2.14** Registry coverage of parent-path mounts (verifier finding on BE-2.12).
  - *Done-when:* a test proves mounts at `/api` and `/` are reported, and an unrelated mount
    (`/static`) is not. — ✔ verified: `undeclared_routes` (backend/app/core/auth.py:134-139) now also reports any `Mount` whose path is a prefix of `/api/v1`. test_hardening.py `test_registry_reports_parent_path_mounts[/api]` and `[]` (a root mount, reported as `Mount /`) and `test_registry_ignores_unrelated_mounts` are PASSED (checks.txt:62-64). The last test asserts `[]` for the real app plus `/static`. That also shows FastAPI's own `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` are not flagged: they are plain `Route`s outside `/api/v1`. Low follow-ups: (a) the prefix test compares strings, not path segments, so a mount at `/a` or `/ap` is reported too (a false positive, which is safe); (b) a `Host(...)` route or a mount with a path parameter (`/{x}`) can still serve `/api/v1/*` without being reported. — Round 2: (a) and (b) are fixed. `_may_serve_api` (backend/app/core/auth.py:120-130) compares whole path segments (`""` or `/api`), reports mounts with path parameters and every `Host`, and is covered by `test_registry_segment_aware_and_catches_param_mounts_and_hosts` (PASSED, checks.txt:177). Remaining low gap: the `APIRoute` branch (:144-146) and the runtime guard check only `path.startswith("/api/v1")`. A catch-all route such as `@app.get("/{full_path:path}")` (an SPA fallback or proxy) without a permission therefore serves unmatched `/api/v1/*` paths, and neither check reports it. Every `Host` is reported, even for unrelated domains; that false positive is safe.
- [ ] **BE-2.15** Key rotation must not sign users out (verifier finding on BE-2.13): when keys
  are cached but a token's `kid` cannot be checked yet (a refresh is in flight, or the refresh
  interval is running), respond 503 `auth_provider_unavailable` (retryable), not 401. 401
  only after a refresh has run and the `kid` is still unknown.
  - *Done-when:* a test with a rotated key proves the request made during the in-flight
    refresh gets `ProviderUnavailable` (not `InvalidToken`) and the next one succeeds, failing
    on the pre-fix code; a test proves an unknown kid after a completed refresh is still 401.
- [ ] **BE-2.16** Catch-all routes: a route whose path template could match `/api/v1/*`
  (e.g. `/{full_path:path}`) must declare a permission. The runtime guard decides on the
  request path, not the route template.
  - *Done-when:* tests prove the registry reports an unguarded catch-all route, and that a
    request to `/api/v1/anything` served by it is denied at runtime; both fail on the
    pre-fix code.
- [ ] **FE-2.5** A retryable failure while loading the session (e.g. 503 during a key
  rotation) offers "Try again" and does not end the session.
  - *Done-when:* a browser run where `/api/v1/me` first returns a retryable 503 shows the
    Try again action, the token is still stored, and one click loads the app.

*Reversibility:* migration downgrades remove the identity tables (no user data yet).
`AUTH_MODE` switches the identity source without code change.

### Phase 3 — Audit trail and data protection (DG-03, DG-04)

Goal: privileged actions are recorded immutably; sensitive values are masked in API, UI
and logs.

- [x] **BE-3.1** `audit_event` table plus a DB trigger rejecting `UPDATE`/`DELETE`
  (D-10), with a tested downgrade.
  - *Done-when:* a test attempting `UPDATE` and `DELETE` on `audit_event` gets a DB error;
    the round trip passes. — ✔ verified: `test_audit.py::test_audit_rows_cannot_be_updated_or_deleted[UPDATE…]` and `[DELETE…]` expect `asyncpg.InsufficientPrivilegeError` "append-only" and re-read the row unchanged; `::test_audit_table_cannot_be_truncated`; all PASSED in checks.txt:10-12. Triggers at backend/app/db/migrations/versions/0004_audit_and_classification_tables.py:74-92. Round trip base→head→base→head completed in checks.txt:63-84 (second run, after removing E2E users). Caveat (not a Done-when gap): the 0004 downgrade drops `audit_event` with its rows unguarded (0004:95-105); flagged in the 2026-10-16 verification report.
- [x] **BE-3.2** `governance.service.audit(...)`: records actor, action, resource
  type/id, outcome, correlation ID, safe metadata and approval reference, in the same
  transaction as the action it records. Denied (403) requests are audited too.
  - *Done-when:* tests show a successful action and a denied action each produce exactly
    one event with the correct correlation ID. — ✔ verified: `test_audit.py::test_successful_action_produces_exactly_one_event` (sends `X-Correlation-ID`, asserts exactly one row with that ID: `user.list`/SUCCESS) and `::test_denied_action_produces_exactly_one_event` (risk_analyst, 403, exactly one DENIED row with that ID and `required_permission`), PASSED in checks.txt:13-14; same-transaction behaviour by `::test_audit_joins_the_callers_transaction` (checks.txt:15). Impl: backend/app/governance/service.py:31-85, denied path backend/app/core/auth.py:96-115. Caveat: only permission-check 403s are audited; `account_disabled`/`tenant_suspended`/`account_not_provisioned` 403s (service.py:126-160) are not.
- [x] **BE-3.3** `GET /api/v1/audit-events?resource_id=&resource_type=&actor=&from=&to=`,
  paginated, requiring `audit:read`. Reading audit is itself audited.
  - *Done-when:* tests cover filters, pagination, 403 for `risk_analyst`, and the
    self-audit event. — ✔ verified (re-check 2026-10-16, commit 9be31f5): the missing `resource_id` filter test now exists. `test_audit_hardening.py::test_resource_id_filter` (:155-162) calls `user.read` on one user, then reads `resource_type=user&resource_id=<id>` and asserts every row has that `resource_id` (unfiltered `user.list` rows with a null `resource_id` would fail it) and that a `user.read` row is present. Paging that survives self-audit: `::test_keyset_paging_does_not_repeat_rows_despite_self_audit` (:128-138, disjoint pages that strictly descend via `before=`), `::test_unknown_or_foreign_before_cursor_is_rejected` (:141-152, 422 for an unknown or other-tenant id) and `::test_filters_are_length_bounded` (:165-167). All PASSED (checks.txt:92-95). The full suite passed with 133 tests (checks.txt:101-102). Impl: filter at backend/app/governance/repository.py:153-154; tenant-checked cursor at repository.py:126-137 and :186-188; service at service.py:273-317; route at router.py:141-189. Remaining non-blocking caveats (`total` ignores `before`; `before` is not written to the `audit.read` details) are in the 2026-10-16 re-verification report. Original evidence follows. Pagination (`test_filters_and_pagination`, disjoint pages under `action=user.list`), 403 (`test_audit_read_requires_audit_read`), self-audit (`test_reading_audit_is_itself_audited`) and tenant scope are covered and PASSED (checks.txt:17-20), and resource_type/actor/action/outcome/from/to filters are exercised. (The earlier gaps are now closed: the `resource_id` filter test and stable unfiltered paging are both covered above.)
- [x] **BE-3.4** `data_classification` and `access_policy` tables. Seeded classifications
  are `public`, `internal`, `confidential`, `restricted`, marked PROVISIONAL pending the
  client (D-11).
  - *Done-when:* the round trip passes and seed classifications exist after upgrade. — ✔ verified: tables at 0004_audit_and_classification_tables.py:44-71; tagged seed at 0005_classification_seed.py:25-60, downgrade deletes only `seeded_by='0005_classification'` rows (0005:63-66, D-11). `test_audit.py::test_seed_classifications_exist_and_are_provisional` (codes and ranks 0–3, `provisional` and tag on every row) and `::test_seed_clearances_match_constants` PASSED (checks.txt:21-22). Round trip checks.txt:63-84 ends at `0005 (head)`. Note: the first round-trip attempt (checks.txt:33-46) failed with an unhandled FK violation in 0003, not a deliberate D-11 refusal as the report says.
- [x] **BE-3.5** Masking utility: response serialisation masks fields whose
  classification exceeds the principal's allowance, and the log filter (BE-1.5) uses the
  same classification.
  - *Done-when:* a test proves the same record returns masked to `risk_analyst` and clear
    to `data_steward` for a `restricted` field. — ✔ verified: `test_audit.py::test_same_record_masked_for_risk_analyst_clear_for_data_steward` PASSED (checks.txt:23). It asserts `{"masked": true, "classification": "restricted"}` and no "12345" for risk_analyst, and the clear value for data_steward. The record is served by a test-only route (test_audit.py:242-253), but everything the Done-when is about is production code: real dev tokens, `require_permission`, clearance from the seeded `access_policy` (repository.py:81-92), and `mask_model` (classification.py:60-71). No production endpoint has a restricted field before Phase 4, so a test record is the honest way to prove it. Log side: `::test_log_filter_uses_the_same_classifications` (checks.txt:27). Caveat: masking is opt-in per route (each handler must call `mask_model`) rather than enforced in serialisation. Phase 4 needs a guard (see verification report).
- [x] **FE-3.1** Audit timeline component (resource-scoped, filterable, paginated) and
  an Audit page.
  - *Done-when:* the timeline renders events created in BE-3.2 tests on the seeded
    environment, with the correlation ID visible. — ✔ verified: browser-phase3.mjs:14-40 replays the two BE-3.2 scenarios (platform_admin `user.list`, risk_analyst denied `GET /api/v1/users`) against the seeded DB copy and captures their `X-Correlation-ID` headers. browser-phase3.json has `timeline_shows_success_cid: true`, `timeline_shows_denied_cid: true`, and the denied row shows its correlation ID (screenshot 01). The ID is rendered at frontend/src/components/audit/AuditTimeline.tsx:55. Filters at AuditTimeline.tsx:140-177 (`filtered_all_denied: true`, screenshot 03), pager at :191-214, `resourceType`/`resourceId` props at :84-115, page at frontend/src/app/(platform)/audit/page.tsx:17-21. The browser exercised only one page.
- [x] **FE-3.2** `MaskedValue` component with an explicit "masked by policy" state.
  - *Done-when:* a masked API value renders as masked text with a tooltip, never as an
    empty string. — ✔ verified: frontend/src/components/common/MaskedValue.tsx:24-45 always renders the literal "masked" with a lock icon, an aria-label and a tooltip; `isMasked` (:10-17) needs `masked === true` and a string classification. browser-phase3.json: `auditor_email_masked_text: "masked"`, tooltip "Masked by policy: confidential data…", `auditor_sees_raw_email: false`, `admin_sees_clear_email: true` (screenshots 02, 04). Unit test frontend/src/components/common/__tests__/masked.test.ts covers the marker predicate (33 FE tests passed, checks.txt:51-53). The masked row was a hand-inserted fixture; the masking itself went through the real API.

*Reversibility:* tables are additive; masking ships behind setting `MASKING_ENABLED`
(default `true`), which, if set to `false`, restores unmasked output in `ENV=local` only.

### Phase 4 — Schema registry (DG-01)

Goal: versioned canonical schemas with owners, lifecycle and compatibility, which later
workstreams bind to.

- [x] **BE-4.1** Tables `schema_definition`, `schema_version` (immutable; content SHA-256;
  status per D-06), `schema_field` (type, required, unit, scale, classification,
  extension eligibility), `schema_dependency`, with a tested downgrade.
  - *Done-when:* the round trip passes; a test proves an UPDATE to an ACTIVE version's
    contract is rejected (DB trigger or service guard plus test). — ✔ verified (commit 9684d9c): tables at backend/app/db/migrations/versions/0006_schema_registry_tables.py:87-161. Row triggers at 0006:30-82, created at :163-172. They reject any change to `schema_id`/`version`/`content_sha256`, any return to DRAFT, and any DELETE of a non-DRAFT version, plus any field INSERT/UPDATE/DELETE on a non-DRAFT version (both the OLD and the NEW version are checked, so a field cannot be moved into or out of one). The seed-removal setting is NULL-safe (`coalesce`, :36, :65). `test_schema_registry.py::test_active_version_contract_cannot_change` (:34-67) runs 8 statements against seeded `instrument` v1 (ACTIVE). Each must raise `InsufficientPrivilegeError` "immutable" and leave the row unchanged, and all 8 PASSED (checks.txt:6-13). `test_status_can_still_move_forward` and `test_draft_version_fields_are_editable` PASSED (checks.txt:14-15). Round trip head→0005→head on the DB copy (checks.txt:48-53), and `test_migrations.py::test_round_trip_upgrade_downgrade_upgrade` is in the 172-pass suite (checks.txt:41). The downgrade refuses while `schema_definition` has rows (0006:177-181). That works online (a DO block in the migration transaction) and in the `--sql` script (checks.txt:54-55); the refusal itself has no test. ⚠ open follow-ups (beyond the Done-when; see the 2026-10-16 Phase 4 verification report): (a) there is no `BEFORE TRUNCATE` trigger, so `TRUNCATE schema_field` or `TRUNCATE schema_definition CASCADE` wipes ACTIVE contracts. Phase 3 blocked TRUNCATE on `audit_event` (0004:90). (b) `moodymate.schema_seed_removal` is a custom setting that any session may `SET`, so `SET moodymate.schema_seed_removal='on'` lets anyone delete the seeded ACTIVE versions. (c) the field trigger reads the version status without a row lock (0006:62,72). A field insert that races a DRAFT→ACTIVE update can therefore commit into an ACTIVE version. (d) on non-DRAFT versions, `effective_from`, `compatibility` and `seeded_by` are still mutable, and any non-DRAFT status can move to any other (REJECTED→ACTIVE). (e) there are no CHECK constraints on `status`, `data_type`, `compatibility` or `dependent_type`, and `classification` has no FK. (f) nothing checks that `content_sha256` matches the fields when a version leaves DRAFT.
- [x] **BE-4.2** Seed PROVISIONAL synthetic schemas `instrument`, `position`,
  `cashflow_event` from Data Model §4, explicitly labelled illustrative.
  - *Done-when:* `GET /schemas` lists three schemas, each with status and a
    `provisional: true` marker. — ✔ verified: the seed is at backend/app/db/migrations/versions/0007_schema_seed.py:58-142. Every definition is `provisional=true` and its description ends "ILLUSTRATIVE seed from Data Model §4; not client-approved." (:30, :175). Every row is tagged `0007_schema_seed`, and the downgrade deletes only tagged rows (:241-247, D-11). `test_schema_registry.py::test_list_schemas` (:134-153) calls `GET /api/v1/schemas` as `risk_analyst` and asserts exactly the 3 codes, `total == 3`, and for each `provisional is True`, status ACTIVE, effective v1, an owner and "ILLUSTRATIVE". It is PASSED (checks.txt:18). `::test_seed_is_tagged_and_provisional` is PASSED (checks.txt:17). Route at backend/app/governance/router.py:247-251, response model at schemas.py:122-140. The seed contents after upgrade are at checks.txt:56-60.
- [x] **BE-4.3** `GET /api/v1/schemas`, `GET /schemas/{id}/versions`,
  `GET /schemas/{id}/diff?from=&to=`, requiring `schema:read`. The diff classifies each
  change as `ADDITIVE | COMPATIBLE | BREAKING`.
  - *Done-when:* diff tests cover an added optional field (additive), a widened scale
    (compatible), and a removed field, a newly required field and a type change
    (breaking). — ✔ verified: all three routes declare `schema:read` (router.py:211, :247-300). Reads are tenant-scoped through `SchemaRepository` → `ScopedRepository.scoped/get` (governance/repository.py:198-206, db/repository.py:32-37). Versions are reachable only through a scoped definition, and an absent or foreign schema gets the same 404 (service.py:529-534). Rules are at service.py:408-505. `test_change_classification` (test_schema_registry.py:262-307) has the 5 required cases: optional add → ADDITIVE (case 0), widened scale 9,4→11,6 → COMPATIBLE (case 7), removed → BREAKING (case 2), newly required → BREAKING (case 3) and STRING→DATE → BREAKING (case 5). It also has 8 more. All 13 PASSED (checks.txt:24-36). The seeded fixture is checked end to end over HTTP by `::test_seeded_diff_fixture` (:200-223, checks.txt:22). 404s: `::test_unknown_and_foreign_schemas_are_not_found` and `::test_diff_rejects_unknown_versions` (checks.txt:21, :23). `SchemaVersionList.schema_` is published as `schema` in backend/openapi.json:837-843 (`export_openapi --check` passes, checks.txt:46). Caveats (not Done-when gaps): a DECIMAL that drops its precision/scale (10,2 → NULL,NULL) is reported BREAKING "narrowed" (service.py:413-436). Precision/scale changes on non-DECIMAL types and field reordering are not reported, so the diff says "Identical" while the content hashes differ. A classification raise counts as COMPATIBLE, which domain owners should confirm.
- [x] **FE-4.1** Schema catalogue: owner, status pill, effective version, dependents.
  - *Done-when:* the catalogue shows the three seeded schemas with platform status pills
    (not the bundle's draft/published set). — ✔ verified: frontend/src/app/(platform)/schemas/page.tsx:66-121 renders the owner, a `StatusPill` for the summary status and the latest version, the effective version with its date, and the dependents. `StatusPill` (components/schemas/StatusPill.tsx:5-16) emits `data-status` from the platform enum. `STATUS_STYLE` is a `Record<SchemaVersionStatus, …>` that covers all 7 D-06 statuses (components/schemas/registry.ts:12-20), and registry.test.ts:20-26 asserts the full set (40 FE tests passed, checks.txt:64-65). browser-phase4.json shows `catalogue_codes` = the 3 seeded codes, `status_values` ACTIVE/DRAFT only, `status_values_all_platform: true`, `provisional_badges: 3` and `nav_has_schemas: true`. Screenshot 01 shows Active/Draft pills on all 3 rows. Note: the pills reuse bundle CSS classes (`status-published`, `status-testing`) for styling, but the labels and values are the platform set. IN_REVIEW/DEPRECATED and DRAFT/RETIRED have the same colour.
- [x] **FE-4.2** Schema detail and version compare: side-by-side diff with a
  classification badge per change.
  - *Done-when:* comparing two seeded versions renders every change from the BE-4.3
    fixture with the correct badge. — ✔ verified: frontend/src/app/(platform)/schemas/[schemaId]/page.tsx:179-289 renders the compare selectors, a change list with one `CompatibilityBadge` per change (:228-248) and the side-by-side table (:251-285). browser-phase4.json `diff_changes` has the 5 changes of `test_seeded_diff_fixture` with these badges: legacy_book_code and product_family BREAKING, interest_rate COMPATIBLE, maturity_date BREAKING, rating_category ADDITIVE. It also shows `ui_matches_api_fixture: true`, `diff_overall: "BREAKING"`, `side_by_side_rows: 10` and `identical_when_same_version: true`. Screenshot 02 matches. ⚠ open follow-up (not a Done-when gap): the diff effect (:67-77) has no cancellation or response check. If the compare versions change quickly, a slow earlier response can land after a later one, and the page then shows the old diff's changes and overall badge under the new from/to columns. A failed diff also leaves the loading block shown indefinitely.

*Reversibility:* additive tables; seed downgrade removes only migration-tagged rows.
Nothing consumes the registry yet.

### Phase 5 — Controlled schema change (DG-05)

Goal: no schema change goes live without impact analysis and approval by someone other
than the proposer.

- [x] **BE-5.1** `schema_change_proposal` table (status per D-06; stores the diff, impact
  result, regression-evidence reference, approver and rollback reference), with a tested
  downgrade.
  - *Done-when:* the round trip passes. — ✔ verified (commit 66d6a59): table at backend/app/db/migrations/versions/0008_schema_change_proposals.py:27-59. It has a D-06 status CHECK (:49), base/proposed version FKs (:50-51, proposed unique :56), `impact` JSONB holding the diff and dependents (:36), `regression_evidence_ref` (:38), the decision columns `decided_by`/`decided_by_user_id`/`decided_at`/`decision_comment` (:40-43) and the rollback reference `reverts_proposal_id` (self-FK :44, :52). The model matches (governance/models.py:292-336). The 0007⇄head round trip on a fresh copy is at checks.txt:38-41, and `test_migrations.py::test_round_trip_upgrade_downgrade_upgrade` (base⇄head) is in the 209-pass suite (checks.txt:25). The downgrade refuses while proposals exist (0008:63-68), as shown on the DB copy (checks.txt:33-37). The refusal itself has no test. ⚠ open follow-up (not a Done-when gap): `schema_change_proposal` has no immutability trigger. Any session with DML can UPDATE an APPLIED proposal's status, decision or impact, or DELETE it, and deleting the rows also unlocks the downgrade refusal. Phase 3 and 4 protect `audit_event` and `schema_version` this way, but not this table.
- [x] **BE-5.2** `POST /schema-change-proposals`, `GET /schema-change-proposals/{id}`,
  `POST /{id}/impact-analysis` (diff + compatibility + `schema_dependency` walk listing
  affected mappings, rules, modules and reports).
  - *Done-when:* a test proposal that removes a field reports BREAKING and lists the
    seeded dependent. — ✔ verified: routes at governance/router.py:376-417. Create and impact-analysis need `schema:propose` and get need `schema:read` (:319-321, D-04). Writes also pass the `SCHEMA_CHANGE_ENABLED` guard (:324-332). Create (service.py:607-697) locks the schema row, needs a version in effect (409 `no_active_version`, so DRAFT-only schemas are covered), and validates duplicates, classifications and no-change. It then writes a DRAFT `schema_version` plus fields and the proposal, audited, in one commit. Analysis (:749-783) diffs base against proposed and stores both hashes. A tenant-scoped BFS over `schema_dependency` (:720-747, repository.py:282-292) builds the dependent list. `test_schema_change.py::test_removal_reports_breaking_and_lists_seeded_dependents` (:91-111) asserts overall BREAKING, exactly one change `counterparty_id` REMOVED/BREAKING, the seeded direct dependents (SCHEMA position, MAPPING core_banking_instrument), and the transitive MODULE liquidity_gap at depth 2 via instrument and REPORT maturity_ladder. It PASSED (checks.txt:8), and `::test_create_proposal_creates_a_draft_version`, `::test_create_validates_the_contract` and `::test_proposing_needs_schema_propose` also PASSED (checks.txt:7, :9-10). ⚠ open follow-ups (beyond the Done-when; see the 2026-10-16 Phase 5 verification report): (a) the walk lists a non-schema dependent once per path, so liquidity_gap appears twice (browser-phase5.json:21-22), and "6 dependents" are 5 distinct ones. (b) the API accepts DECIMAL without precision/scale, and `_size_change` (service.py:415-448) treats a missing bound as unbounded. So DECIMAL(18,6)→DECIMAL(18,null) and →(null,null) are classified COMPATIBLE, though NUMERIC(18) has scale 0. That conflicts with the fixed-precision domain rule, and it differs from the Phase 4 verification, which recorded (10,2)→(NULL,NULL) as BREAKING. (c) `extension_eligible` defaults to false when omitted (schemas.py:206), so an omitted key silently flips a true field (null is correctly 422).
- [x] **BE-5.3** `POST /{id}/approval` with maker-checker: the approver must hold
  `schema:approve` and must not be the proposer. BREAKING proposals require recorded
  impact analysis. Approval + activation creates a new ACTIVE `schema_version` and sets
  the previous one to DEPRECATED, in one transaction, audited.
  - *Done-when:* tests cover the proposer self-approving (403), approval without impact
    analysis (409), and a valid approval (new version active, old one deprecated, old
    contract unchanged, audit event present). — ✔ verified: the route needs `schema:approve` (router.py:321, :438-453). `decide` (service.py:811-886) locks the proposal row, then refuses the proposer (403 `self_approval`, :816-817) before any status check. Approval needs a recorded analysis (:823-826). It locks the schema row (:827), so concurrent approvals serialise; the loser re-reads its base and is superseded. The base must still be ACTIVE and in effect (:831-839). The recomputed, stored and analysed proposed hashes must all agree (:840-848; the `not in {…}` form is an equality test and is correct). Deprecate, activate, APPLIED and the three audit rows are then committed once (:850-885). Tests: `::test_proposer_cannot_approve_own_proposal` (:172-177) uses one user holding data_steward+approver, gets 403 `self_approval` and leaves the status SUBMITTED. `::test_approval_without_impact_analysis_is_409` (:147-158) gets 409 `impact_analysis_required`. Submit makes that state unreachable through the API, so the test forces it with SQL. `::test_valid_approval_activates_new_version` (:190-251): the new version is ACTIVE with `effective_from`, the base is DEPRECATED, the base hash and field count are unchanged, and `schema_change.approve`/`schema_version.activate`/`schema_version.deprecate` share the request's correlation ID. All PASSED (checks.txt:11-15), as did the rejection, stale-base, tampered-hash and switch tests (checks.txt:16-19). The lock order is always proposal→schema (create takes only the schema), and no reverse path was found, so there is no deadlock. ⚠ open follow-ups (beyond the Done-when): (a) the refusals that matter most for maker-checker are not audited: `self_approval` (:816-817), `not_proposer` (:787-788) and `schema_change_disabled` (router.py:324-329). `require_permission` audits its denials as DENIED (core/auth.py:131-146). (b) on a stale base, `_close` commits SUPERSEDED and a SUCCESS `schema_change.supersede` audit row under the approver (decided_by = approver), then returns 409 (:831-839, :906). The error response hides a committed change, and the approve attempt itself is not recorded. Stale siblings are only superseded when someone tries to approve them, and a stale DRAFT can still be analysed and submitted. (c) a proposer cannot withdraw their own proposal, because REJECT also hits `self_approval`. (d) regression evidence stays optional even for BREAKING (schemas.py:226).
- [x] **BE-5.4** Rollback path: `POST /{id}/revert` creates a new proposal reinstating
  the prior version's contract as a NEW version, not by editing history.
  - *Done-when:* after a revert is approved, the active version's contract hash equals
    the original hash, and the version count increased. — ✔ verified: `revert` (service.py:699-716) needs an APPLIED proposal whose version is still in effect (409 `not_applied`/`not_current`). It creates a NEW proposal through `create` with the base version's fields and `reverts_proposal_id` set. History is not edited: the revert is a new DRAFT version that goes through the same analyse/submit/maker-checker approval. Route at router.py:456-466 (`schema:propose` + switch). `::test_revert_reinstates_the_prior_contract_as_a_new_version` (test_schema_change.py:317-365) approves v1→v3, reverts, analyses (ADDITIVE), submits and approves. It asserts exactly one ACTIVE version with hash == v1's original hash, that it is v4, and that count_after == count_before + 1. It PASSED (checks.txt:20), and `::test_only_an_applied_proposal_can_be_reverted` PASSED (checks.txt:21). The browser run agrees: `active_hash_equals_original: true`, v1/v3 DEPRECATED and v4 ACTIVE (browser-phase5.json:38-44). Maker-checker on reverts: the revert's proposer is whoever calls `/revert`, and the approver must be someone else. With the seeded disjoint roles, the original approver cannot propose a revert. A user holding both roles could, but a second person must still approve, so this is not a bypass. ⚠ open follow-up: the `not_current` check reads an unlocked snapshot (:701-708) before `create` takes the schema lock (:615). If another change (v3→v5) is approved in between, the revert is created against base v5. It then also undoes v5 under the title "Revert: …", although the approver still sees the diff against v5. Repeat reverts of the same applied proposal are also allowed.
- [x] **FE-5.1** Proposal workflow: create proposal → run impact analysis → submit
  (blocked until analysis exists) → approver decision view.
  - *Done-when:* manual test notes show the full flow with two seeded users; the Submit
    button stays disabled before analysis, and the backend also returns 409. — ✔ verified: the manual test notes are at docs/evidence/phase-5/REPORT.md:19-29 (Data steward and Approver in two sessions: create → analyse → submit → approve → revert → approve). browser-phase5.json records `submit_disabled_before_analysis: true` (:3), `backend_submit_before_analysis` 409 `impact_analysis_required` (:5-14), `submit_enabled_after_analysis: true` (:25), proposer text "You cannot approve your own proposal" (:27), the approver queue row (:28-30) and `after_approval_status: "APPLIED"` (:31). Screenshots 02 (Submit disabled, "Submit unlocks once…"), 04 (queue filtered to Awaiting approval) and 05 (Applied; v1 Deprecated → v3 Active; decided by the approver) match. Code: Submit is disabled by `canSubmit` (components/schemas/proposals.ts:19-21; frontend/src/app/(platform)/schemas/proposals/[proposalId]/page.tsx:191-221). The decision panel is gated by `canDecide` (proposals.ts:24-26; page.tsx:223-278), so the proposer never sees it, and the server decides anyway. The new-proposal editor's `useSearchParams` is inside `<Suspense>` (new/page.tsx:228-230). Double-clicks are guarded by `saving`/`busy`. 46 FE tests, lint, tsc and build passed (checks.txt:43-51). ⚠ open follow-ups: (a) changing the schema in the editor (new/page.tsx:45-68) discards edits without asking, and the fetch has no cancellation. A slow response for schema A can land after schema B's, so the proposal is created for B with A's fields, and a failed load leaves the loading block shown indefinitely. (b) `toInt` (new/page.tsx:22) turns a non-numeric precision/scale into NaN, which JSON serialises as `null`. This silently drops the bound, and with BE-5.2 follow-up (b) the diff calls it COMPATIBLE. `draftProblems` does not check precision 1–38. (c) there is no server idempotency: retries or a second tab create duplicate proposals, each using a version number.
- [x] **FE-5.2** Impact analysis display: compatibility summary and dependent list.
  - *Done-when:* the BREAKING fixture renders a red badge with the dependents listed. — ✔ verified: `ImpactPanel` (frontend/src/components/schemas/ImpactPanel.tsx:9-72) renders the overall `CompatibilityBadge` (:14-16), the change counts, each change with its own badge (:26-41) and every dependent with its type, ref, "direct"/"via a → b" path and an illustrative marker (:45-68). The section accent turns to danger on BREAKING ([proposalId]/page.tsx:164). browser-phase5.json: `impact_overall: "BREAKING"` with class `bg-destructive` (:15-16), and all 6 dependent rows are listed (:17-24). Screenshot 03 shows the red "Breaking" badge, the removed `counterparty_id` and the 6 dependents. Caveat: one dependent (liquidity_gap) appears twice, once per path (see BE-5.2 follow-up (a)).

*Reversibility:* the proposal workflow sits behind setting `SCHEMA_CHANGE_ENABLED`; off
means the registry is read-only, which is the Phase 4 behaviour. Activated versions are
reverted only through new versions.

### Phase 6 — Environment controls and WS1 verification (DG-06, WS1 verification)

- [x] **BE-6.1** Environment guard: `DATA_CLASS=synthetic` is required outside
  production; startup refuses `DATA_CLASS=client` unless `ENV` is staging or production,
  and every response carries an `X-Data-Class` header.
  - *Done-when:* tests cover each ENV × DATA_CLASS combination. — ✔ verified (commit 670fed6): the guard is in the settings validator (backend/app/core/config.py:57-60), so startup fails before an app exists. `test_environment_guard.py::test_env_by_data_class` is parametrised over all 8 ENV × DATA_CLASS cells (:27-50): client is refused with the exact message in local and test, and every other cell builds. `::test_every_combination_is_covered` (:53-55) asserts the matrix is the full product. All 15 tests in the file PASSED (checks.txt:43-59). The header comes from a pure ASGI middleware (core/environment.py:14-31), added inside CORS and outside `CorrelationIdMiddleware` (main.py:52-61), so the 500 envelope that middleware builds (correlation.py:63-70) also gets it, and streaming responses are not buffered. The header is tested on 200, 401 and 404 (:65-79) and is in `expose_headers` (main.py:60, test :82-84). `/auth/config` reports `environment` (governance/router.py:84, schemas.py:32-41). ⚠ open follow-ups (beyond the Done-when; see the 2026-10-16 Phase 6 verification report): (a) the staging × client cell is accepted (config.py:59, test :33). That contradicts this task's first clause ("synthetic is required outside production"), and ARCHITECTURE.md:86 now records it as architecture. It needs a user decision (D-NN) before staging may hold client data. (b) `DATA_CLASS` defaults to `synthetic` in every ENV (config.py:34), so a staging or production deployment that omits it labels client data `X-Data-Class: synthetic` and shows a "SYNTHETIC DATA" banner. That is not fail-closed (D-13). (c) CORS preflight (`OPTIONS` with `Access-Control-Request-Method`) is answered by `CORSMiddleware`, which is outside the header middleware, so preflight responses do not carry `X-Data-Class`. (d) the 500 path has no test. — **Resolved 2026-10-16:** (a) the user decided on production only (D-14), and the staging × client cell is now refused, with tests; (b) `DATA_CLASS` is required in staging and production. The other review items are listed in docs/evidence/phase-6/REPORT.md under "Verification follow-ups".
- [x] **BE-6.2** `docs/RETENTION.md`: provisional retention, deletion and backup
  assumptions per artifact class (audit, schemas, proposals), each marked as needing
  client confirmation. No numeric RPO/RTO (Arch §11).
  - *Done-when:* the document exists and is referenced from ARCHITECTURE.md. — ✔ verified: docs/RETENTION.md exists. It is marked PROVISIONAL (:3) and has one row per artifact class: audit, schema registry, proposals, plus identity, classification, seed data, logs and local DBs (:25-34). Each row has a "Needs confirmation from" owner. No numeric period or RPO/RTO appears, and :5 and :40 say so. It is linked from docs/ARCHITECTURE.md:87 under "Environments, retention and backup" (:84). The code claims were checked and hold at this commit: the downgrade refusals are at 0004:96-112, 0006:256 and 0008:98-101; the `allow_audit_loss` opt-in is at 0004:99-100; and the TRUNCATE/DELETE/decided-row triggers are at 0004:90, 0006:88-91 and :246-248, and 0008:75-93. ⚠ gaps against Arch §11 (not Done-when gaps): §11 asks for a *documented restore procedure* at MVP, and RETENTION.md:36-40 states only assumptions. "Deletion evidence" is described only for audit events. The `tenant` table is not covered by any row.
- [x] **BE-6.3** WS1 verification suite: authorization matrix test (every route × every
  seeded role, expected 2xx/403), cross-tenant isolation, breaking-change approval,
  audit completeness (every mutating route emits an event), and migration round trip
  from base to head.
  - *Done-when:* the suite runs in CI and the matrix table is printed in the test report. — ✔ verified in CI 2026-10-16: PR #10 run 38884187927 on 3ed8538 (https://github.com/Kunal-dev99/MoodyMate/actions/runs/38884187927). The `backend` job succeeded, and it includes the step `WS1 verification suite (BE-6.3; prints the route x role matrix)` (`pytest -m ws1 -v`), where the `pytest_terminal_summary` hook prints the matrix. A job succeeds only if every step does. Earlier note: CI has not run on commit 670fed6, and this Done-when is "runs in CI". The step is wired correctly: .github/workflows/ci.yml:52-53 (`pytest -m ws1 -v`) runs after `-m "not ws1"` (:50-51). The `ws1` marker is registered (backend/pyproject.toml:64-67). Locally, the 7 tests PASSED and the hook printed the table: 15 routes × 5 roles (checks.txt:15-42; hook at app/tests/conftest.py:31-45). The round-trip test (test_ws1_verification.py:249-254) leaves the DB at head, and its `db` fixture cleans it first, so it does not conflict with the session `migrated_db` fixture. Tick when the CI log shows the step and the table. ⚠ defects to fix (beyond the Done-when): (a) audit completeness can be bypassed. `test_every_mutating_route_is_listed` (:184-192) only compares route keys with `AUDITED`, and `test_every_mutating_route_emits_an_audit_event` (:195-243) calls a hand-written list. Nothing asserts that every `AUDITED` key was called, so a new route added to `AUDITED` but not exercised passes. (b) the matrix proves the 403s but not the plan's "2xx" side. Any allowed response other than a 403 `forbidden` passes, including 401 or a 403 with another code (:76-79). 9 of the 15 routes return 404/422 for every allowed role (checks.txt:31-41), and the body's `required_permission` is not compared with the declared permission. (c) the cross-tenant audit check only looks for the proposal's `resource_id` among the outsider's items (:111-112). It does not assert the items belong to the outsider's tenant.
- [x] **FE-6.1** Environment banner showing ENV and data class (always visible outside
  production).
  - *Done-when:* the banner reads "LOCAL · SYNTHETIC DATA" in local dev. — ✔ verified: browser-phase6.json has `login_banner` and `app_banner` = "LOCAL · SYNTHETIC DATA" with the backend in ENV=local (browser-phase6.mjs:8-20) and `health_x_data_class: "synthetic"`. Screenshots 01 (sign-in) and 02 (app shell, on /schemas) show the banner. The label is built from the backend's `/auth/config` (frontend/src/components/layout/EnvironmentBanner.tsx:7-11). It is rendered in the shell (components/layout/AppShell.tsx:35) and on sign-in (app/login/SignIn.tsx:65). Unit tests are in components/layout/__tests__/environment.test.ts:5-14 (50 FE tests passed, checks.txt:65-66). ⚠ open follow-ups: (a) the banner and the header are both `sticky top-0` (EnvironmentBanner.tsx:26, header.tsx:45). Once a page scrolls, the banner (z-40) sits over the top ~25px of the 64px header, and its background is 12% opacity (:28-29), so the header and page content show through the label. Use an opaque background and offset the header (`top-[banner height]`) or wrap both in one sticky container. (b) amber `#B26A00` on its 12% tint is about 3.6:1 at 12px, which is below WCAG AA 4.5:1. `role="status"` is a live region; a landmark (`role="region"` with `aria-label="Environment"`) suits a permanent banner better. (c) there is no banner when `/auth/config` fails (SignIn.tsx:65) or on pages outside the shell (e.g. /auth/callback).
- [x] **FE-6.2** Frontend checks: Playwright smoke for sign-in per role, 403 page, schema
  compare and the proposal flow.
  - *Done-when:* the Playwright report passes in CI. — ✔ verified in CI 2026-10-16: PR #10 run 38884187927 on 3ed8538 (https://github.com/Kunal-dev99/MoodyMate/actions/runs/38884187927). The `e2e` job succeeded: it migrated a fresh Postgres, ran `npm run e2e` (8 Playwright tests) and uploaded the `playwright-report` artifact. Earlier note: CI has not run on commit 670fed6, and local evidence cannot tick this. Locally, 8 of 8 passed (checks.txt:70-79). Review of the job (.github/workflows/ci.yml:82-136) found no blocker. It migrates a fresh `moodymate_e2e` DB with `ENV`/`DATABASE_URL` set at job level (alembic env.py:32 reads `DATABASE_URL`), and dev sessions provision the persona users. `uvicorn` is on PATH from the setup-python `pip install` (`E2E_UVICORN=uvicorn`). `playwright install --with-deps` works because GitHub runners have passwordless sudo. `CI=true` disables `reuseExistingServer` (frontend/playwright.config.ts:46, :53). `next start -p 3000` is correct (package.json `start: next start`). The backend gets `ENV=test` and the DB URL through webServer `env`, which Playwright merges with `process.env`. The default `CORS_ORIGINS` and `NEXT_PUBLIC_API_BASE_URL` match ports 3000/8000. The spec is serial with 1 worker (smoke.spec.ts:13, config :19-20), and compare runs before the flow that changes `instrument`. The report is uploaded with `if: always()` (ci.yml:130-136). Tick when the `e2e` job is green and the `playwright-report` artifact exists. Note: the suite is not re-runnable against the same DB (a second run finds `counterparty_id` already removed), which matches the config's "disposable copy" comment.

*Reversibility:* guards are settings-driven; the verification suite only adds tests.

### Role → permission matrix (seed, BE-2.2)

| Role | schema:read | schema:propose | schema:approve | audit:read | admin:read |
|---|---|---|---|---|---|
| platform_admin | ✓ | | | ✓ | ✓ |
| data_steward | ✓ | ✓ | | | |
| risk_analyst | ✓ | | | | |
| approver | ✓ | | ✓ | | |
| auditor | ✓ | | | ✓ | |

`platform_admin` deliberately cannot approve schema changes (segregation of duties).
The final role model needs client approval (Arch §10).

### Deferred (out of scope for Feature 01)

- **Real enterprise IdP integration:** the client's IAM details are unknown. `AUTH_MODE=oidc`
  is built and tested against a local issuer only.
- **User and role administration UI** (assigning roles and editing permissions): read-only
  admin views only for now, pending the client role model.
- **Multi-tenant administration:** the model is tenant-aware, but only one tenant is seeded.
- **Object storage, encryption-at-rest, key management, backup automation:** these are
  infrastructure, and hosting is undecided.
- **Mapping, rule, module and report dependents:** WS2–WS4 own them. Phase 4–5 impact
  analysis uses seeded `schema_dependency` rows until those modules exist.
- **Retention deletion jobs:** the policy is documented only.
- **Penetration testing and production hardening:** pre-production gate (Arch §17).

---

## Feature 02 — WS2 Data Ingestion, Mapping & Data Quality

Kickoff: `docs/prompts/kickoffs/02-ws2-ingestion.md` · Status: **PROPOSED 2026-10-16. Awaiting user approval of the plan and of D-15…D-23. No code before approval.**

Covers WS2 DI-01…DI-06 and the paired contracts FE-DI-01…05 / BE-DI-01…05. Numbering
(D-08): Feature 02 uses global **Phases 7–13**, so its task IDs run from `BE-7.1`/`FE-7.1`
to `BE-13.n`/`FE-13.n` and cannot collide with Feature 01 (Phases 1–6). WS2-internal
phases are cited as `WS2-P0…P5`.

Constraining decisions:
- **D-01** Every WS2 migration is additive. `import_job.mapping_version_id` arrives as a
  new nullable column in Phase 10, not by altering Phase 8 tables.
- **D-02** Tested downgrades. Rows hide by status and are never deleted. Every phase sits
  behind a setting (`INGESTION_ENABLED`, `MAPPING_CHANGE_ENABLED`,
  `DRIFT_DETECTION_ENABLED`).
- **D-03** Isolated worktree plus a DB copy plus a paired artifact directory (D-15).
- **D-04** Fail closed. Each endpoint names its permission (table below). Tenant row scope
  comes from `ScopedRepository`.
- **D-05** The WS plans' endpoint names are canonical (`POST /imports`, not Arch §9's
  `datasets/imports`). Endpoints these plans need beyond those are added alongside them.
- **D-06** UPPERCASE Data Model §9 enums with one transition table per object:
  - `import_job` gains `QUARANTINED` from Appendix B.
  - `mapping_version` uses the `schema_version` set.
  - Enums that are in neither source are proposed in D-23.
- **D-07** Error envelope.
- **D-10** Audit trail. Every WS2 mutation and every refusal is audited, and quarantine
  history lives in `audit_event`.
- **D-11** WS2 seeds (permissions, illustrative source, default validation profile) are
  tagged and PROVISIONAL.
- **D-12** No object storage yet (see D-15).
- **D-13** New settings that matter in deployments are required outside local/test.
- **D-14** Synthetic data only outside production (see D-16 §5).

Pre-plan findings (codebase at the Phase 6 merge):
- **Modules.** `backend/app/ingestion`, `backend/app/mapping` and `backend/app/reconciliation`
  are empty 7-file stubs, and none of them is mounted in `backend/app/api/v1/router.py`.
  The migration head is `0008_schema_change_proposals`.
- **Reuse rather than rebuild:**
  - registry: `schema_definition`/`schema_version`/`schema_field` (`classification`,
    `unit`, `scale`, `extension_eligible`) and `schema_dependency`. The seed already has an
    illustrative `MAPPING core_banking_instrument` dependent (0007:138).
  - Phase 5 impact analysis, which walks `schema_dependency`, so real mapping rows show up
    there once they exist.
  - `ScopedRepository`, `require_permission`, `governance.service.audit(...)`,
    `mask_model`/`mask_mapping` (core/classification.py; unknown keys fail closed), the
    log redaction filter, `ERROR_RESPONSES`, `test_route_registry`, the WS1 verification
    `AUDITED` set (which every new mutating route must join), and the OpenAPI export with
    FE `gen:api`.
  - frontend: `StatusPill`, `MaskedValue`, `AuditTimeline`, `RequirePermission` and
    `Forbidden`.
- **Missing:**
  - no multipart dependency (`python-multipart`), no XLSX library and no request-size
    limit anywhere in the app.
  - no `ARTIFACT_STORE_DIR` or other storage setting. ARCHITECTURE.md names a "local:
    filesystem adapter".
- **Frontend.** `/ingestion` is a `planned` entry in `frontend/src/config/navigation.ts:47-54`
  and is rendered by the `[workspace]` placeholder route.
- **Lessons from Feature 01 that become Done-when criteria here:**
  - every protective trigger also blocks `TRUNCATE`, and no session-settable bypass setting
    is used (BE-4.1 follow-ups a, b).
  - status/enum columns get CHECK constraints (BE-4.1 e).
  - downgrade refusals are tested (BE-4.1, BE-5.1).
  - maker-checker refusals are audited (BE-5.3 a).
  - the verification matrix asserts the 2xx side and that every `AUDITED` key is called
    (BE-6.3 a, b).
  - FE fetches that depend on changing inputs are cancelled (FE-4.2, FE-5.1 a).
- **Carry-over (not prerequisites):** FE-2.2, BE-2.15, BE-2.16 and FE-2.5 are still open
  under Feature 01.

### WS2 endpoints and permissions (all under `/api/v1`)

| Endpoint | Permission | Phase | Source |
|---|---|---|---|
| `POST sources` | `source:manage` | 7 | WS plan |
| `GET sources`, `GET sources/{source_id}` | `source:read` | 7 | added (D-05) |
| `POST sources/{source_id}/contracts` | `source:manage` | 7 | added |
| `POST imports` (multipart) | `import:write` | 8 | WS plan |
| `GET imports`, `GET imports/{import_id}` | `import:read` | 8 | WS plan (+ list added) |
| `GET imports/{import_id}/profile` | `import:read` | 9 | WS plan |
| `GET mappings`, `GET mappings/{mapping_id}` | `mapping:read` | 10 | WS plan (+ detail added) |
| `POST mappings`, `POST mappings/{mapping_id}/versions` | `mapping:propose` | 10 | WS plan / Arch §9 |
| `POST imports/{import_id}/mappings/validate` | `mapping:propose` | 10 | WS plan |
| `POST mappings/{mapping_id}/versions/{version}/submit` | `mapping:propose` | 10 | added |
| `POST mappings/{mapping_id}/versions/{version}/approval` | `mapping:approve` | 10 | added |
| `POST imports/{import_id}/validate` | `import:write` | 11 | WS plan |
| `GET imports/{import_id}/issues` | `import:read` | 11 | WS plan |
| `GET quarantine/{batch_id}` | `quarantine:read` | 11 | WS plan |
| `POST quarantine/{batch_id}/items/{quarantine_id}/disposition` | `quality:review` | 11 | added |
| `GET imports/{import_id}/reconciliation` | `import:read` | 12 | WS plan |
| `GET imports/{import_id}/drift` | `import:read` | 12 | WS plan |
| `POST drift-events/{drift_event_id}/review` | `quality:review` | 12 | added |

Raw artifacts have no download endpoint in this feature (Arch §10: "restrict raw
artifacts"). That is deferred.

### Role → permission matrix additions (seed, BE-7.2; PROVISIONAL, D-11)

| Role | source:read | source:manage | import:read | import:write | mapping:read | mapping:propose | mapping:approve | quarantine:read | quality:review |
|---|---|---|---|---|---|---|---|---|---|
| platform_admin | ✓ | | ✓ | | ✓ | | | | |
| data_steward | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | | ✓ | ✓ |
| risk_analyst | ✓ | | ✓ | | ✓ | | | | |
| approver | ✓ | | ✓ | | ✓ | | ✓ | ✓ | |
| auditor | ✓ | | ✓ | | ✓ | | | ✓ | |

How the roles split the new permissions:
- `platform_admin` gets read-only access to WS2 and no quarantined values. This matches the
  WS1 segregation of duties and Arch §10 data minimisation.
- Only `data_steward` writes, and only `approver` activates mappings (maker-checker).
- Values shown anywhere are still masked by each role's clearance (BE-3.5). Columns that are
  not mapped use the source profile's default classification.
- The final role model needs client approval (Arch §10).

### Phase 7 — Source registration and synthetic sample contracts (DI-01; WS2-P0, WS2-P1)

Goal: a data steward can register a source system, its profile and a PROVISIONAL source
contract that names the target canonical schema. Synthetic samples exist for every later
phase, and the Ingestion Workbench goes live, read-only for everyone else.

- [x] **BE-7.1** Migration plus models in `ingestion`:
  - `source_system`, with `kind` per D-23.
  - `source_profile`, with format, encoding, delimiter, sheet name, cadence, owner,
    `default_classification` (FK to `data_classification`) and `provisional`.
  - `source_contract`, with `version`, `status` per Data Model §9, target `schema_id`,
    expected columns, business-key columns, control-total columns, `content_sha256` and
    `supersedes_id`.

  Contract content is frozen from creation, in every status (D-24 §3, amended at the Phase 7
  review). Only `status` and `updated_at` change, along `CONTRACT_TRANSITIONS`. A new
  contract version supersedes the old one, which is not edited. CHECK constraints cover every enum column. A trigger
  blocks UPDATE of contract content and DELETE/TRUNCATE. The downgrade refuses while user
  rows exist.
  - *Done-when:* the round trip passes, and tests prove the trigger rejects an
    UPDATE/DELETE/TRUNCATE on an APPROVED contract, that a bad enum value is refused by the
    CHECK, and that the downgrade refuses while a user-created source exists. — ✔ verified
    (checks at 8bdb0cd; 2026-10-16 Phase 7 verification, docs/evidence/phase-7/REPORT.md).
    Migration 0009 has CHECKs on kind, format, encoding, delimiter and status, and a
    `source_contract_guard` trigger for UPDATE/DELETE plus a statement-level TRUNCATE trigger.
    No session-settable bypass exists. `test_sources.py::test_approved_contract_rejects_update_and_delete`
    covers 9 statements, plus `::test_approved_contract_rejects_truncate`,
    `::test_enum_columns_are_checked` (9 cases), `::test_downgrade_refuses_while_a_user_source_exists`
    and `::test_source_migrations_round_trip_without_user_sources`. The round trip
    0011→0008→0011 is in checks.txt:28-33, and 352 passed (checks.txt:8). ⚠ follow-ups (REPORT
    D1, D5, D6, D11): a PROVISIONAL contract's content is still editable in the DB, and Phase 8
    pins `source_contract_id` (needs a user decision). `source_system.tenant_id` and
    `source_profile.source_system_id` are updatable. The guard does not freeze
    `created_by`/`created_at` or enforce the transition table. The downgrade-refusal test cannot
    tell a re-seed from "nothing removed".
- [x] **BE-7.2** Seed migration adding the nine WS2 permissions and the matrix above
  (tagged, D-11). `governance/constants.py` `Permission` and `ROLE_PERMISSIONS` are
  extended to match.
  - *Done-when:* `test_identity_seed.py::test_seeded_matrix_matches_plan` passes against the
    extended matrix, `test_me.py` per-role permission tests pass with the new sets, and a
    test proves the downgrade removes only rows tagged by this migration. — ✔ verified
    (checks at 8bdb0cd). Migration 0010's MATRIX, `governance/constants.py`
    `WS2_ROLE_PERMISSIONS` and the plan table agree cell by cell.
    `test_identity_seed.py::test_seeded_matrix_matches_plan` asserts DB = constants, and also
    states the plan's holders explicitly (writes only data_steward, `mapping:approve` only
    approver, `quarantine:read` = steward/approver/auditor). `test_me.py::test_ws2_permissions_per_role`
    writes the matrix out from the plan, not from the constants.
    `test_identity_seed.py::test_ws2_permission_downgrade_removes_only_its_rows` adds an
    untagged `custom:read` grant, downgrades to 0009 and asserts WS1 ∪ {custom:read} remains
    with zero tagged rows. All of these are in the 352 passed (checks.txt:8).
- [x] **BE-7.3** Setting `INGESTION_ENABLED` (default `true`). When it is off, every WS2
  write route returns 409 `ingestion_disabled` (audited DENIED) and reads still work.
  - *Done-when:* a test with the setting off gets 409 plus one DENIED audit event on
    `POST /sources`, and 200 on `GET /sources`. — ✔ verified (checks at 8bdb0cd).
    `ingestion/router.py:46-70` is a dependency declared after the permission dependency on
    both write routes. Its refusal goes through `record_denial`, which rolls back and audits
    in its own session, matching the Phase 5 `SCHEMA_CHANGE_ENABLED` pattern.
    `test_sources.py::test_switch_off_refuses_writes_with_one_audit_event_and_reads_work`
    asserts 409 `ingestion_disabled` with exactly one DENIED event for `POST /api/v1/sources`
    (by correlation ID), 200 on the list and on get-by-id, 409 on `POST …/contracts`, a plain
    403 `forbidden` for risk_analyst (the switch reveals nothing) and no rows written.
    ⚠ follow-up (REPORT D4): no structural test makes future WS2 write routes (Phases 8–13)
    carry the switch.
- [x] **BE-7.4** `POST /sources` creates the system, the profile and contract v1 in one
  transaction (audited `source.create`). It also adds `GET /sources` (paginated),
  `GET /sources/{source_id}` and `POST /sources/{source_id}/contracts`, which creates the
  next version and marks the previous one SUPERSEDED. The target schema must exist in the
  tenant and have an ACTIVE version. Permissions and tenant scope come from the table above.
  - *Done-when:* tests cover create/list/get; a cross-tenant `GET` returns the same 404 as a
    random ID; `risk_analyst` `POST` returns 403 with a DENIED audit event; and after
    contract v2 is created, v1 is SUPERSEDED with an unchanged `content_sha256`. — ✔ verified
    (checks at 8bdb0cd). The tests:
    - `test_sources.py::test_create_list_and_get`: one `source.create` event, `X-Data-Class`,
      and pagination ordered by code.
    - `::test_cross_tenant_get_is_the_same_404_as_a_random_id`: identical envelopes apart from
      the correlation ID, an empty list, and 404 on writes by id.
    - `::test_post_without_source_manage_is_403_and_audited`: risk_analyst, auditor, approver
      and platform_admin each get 403 with one DENIED event.
    - `::test_new_contract_version_supersedes_the_previous_unchanged`: the v1 row is identical
      except status and `updated_at`, and the create and supersede events are present.
    - `::test_target_schema_must_exist_here_and_be_active` and `::test_seed_hash_matches_the_service`.

    Contract versioning is serialised by `SELECT … FOR UPDATE` on the source
    (`ingestion/repository.py:45-53`), backed by `uq_source_contract_current` and
    `UNIQUE(source_profile_id, version)`. The WS1 matrix and the audit-completeness tests
    include both write routes (checks.txt:10-21). ⚠ follow-ups (REPORT D2, D3, D8, D9, D12):
    - the registrant can send `profile.provisional=false`;
    - business-rule refusals (`duplicate_code`, `no_active_version`, `unsupported_source_kind`,
      `no_change`) are not audited, although the Feature 02 constraint reads "every refusal is
      audited";
    - control characters and whitespace-only text are accepted, and NUL probably causes a 500;
    - no concurrency test exists for two simultaneous new versions.
- [x] **BE-7.5** Synthetic samples (WS2-P0):
  - `backend/scripts/make_synthetic_samples.py` uses a fixed seed and writes ILLUSTRATIVE
    files for `instrument` (CSV), `position` (XLSX) and `cashflow_event` (JSON). It also
    writes deliberately bad variants: missing required column, bad date, over-scale decimal,
    duplicate business key, unknown extra column, non-UTF-8 and bad currency code.
  - A tagged seed migration adds one PROVISIONAL illustrative source
    (`core_banking (illustrative)` → `instrument`).
  - *Done-when:* a test runs the generator twice and gets identical SHA-256s for every
    file, every file carries an "ILLUSTRATIVE / SYNTHETIC" marker (header comment, sheet
    name or top-level key per format), and `GET /sources` lists the seeded source with
    `provisional: true`. — ✔ verified (checks at 8bdb0cd). The tests:
    - `test_synthetic_samples.py::test_two_runs_give_identical_files` runs the script twice as
      a subprocess and compares SHA-256 per file and against `SHA256SUMS`.
    - `::test_every_file_carries_the_marker` (parametrised over every file): a CSV `# …` first
      line, a JSON top-level `marker`, and for XLSX the sheet name "ILLUSTRATIVE SYNTHETIC" plus
      the full marker in `docProps/core.xml`.
    - `::test_bad_variants_carry_their_defect` checks all 7 variants.
    - `::test_committed_samples_are_current`, and "synthetic samples are up to date" in
      checks.txt:25-26.
    - `test_sources.py::test_seeded_source_is_listed_as_provisional`: 3 tagged rows, and the
      contract columns equal the sample header.

    ⚠ before Phase 8 (REPORT deviations 1–3, user decision): the JSON samples are a
    `{"marker","records"}` object, but D-16 says a top-level array. The CSV marker line has to
    be skipped by the BE-8.3 adapter. The XLSX is hand-written (stdlib zip), not with openpyxl
    (D-17). The seed uses random IDs (REPORT D7).
- [x] **FE-7.1** The Ingestion Workbench goes live:
  - `navigation.ts` drops `planned` for `ingestion` and requires `source:read`.
  - The `/ingestion` landing page has Sources and Imports tabs. Imports shows an empty state
    until Phase 8.
  - "Register source" is wrapped in `RequirePermission('source:manage')`.
  - *Done-when:* a browser run shows `data_steward` with the Register button and `auditor`
    without it; `auditor` on `/ingestion/sources/new` sees the 403 state and the backend
    `POST /sources` also returns 403. — ✔ verified (checks at 8bdb0cd).
    - `navigation.ts:47-54` has `permission: 'source:read'` and no `planned`.
    - The landing page `ingestion/page.tsx` has Sources and Imports tabs (the Imports tab is an
      empty state). Its Register button is in `RequirePermission('source:manage')` with a null
      fallback, and `sources/new/page.tsx:205` wraps the form in `RequirePermission`.
    - `IngestionGate` restores the placeholder when the nav entry is planned again
      (`gate.test.ts`, D-02).
    - The two `e2e/ingestion.spec.ts` FE-7.1 tests cover steward vs auditor, the auditor's 403
      state on `/ingestion/sources/new`, and a backend 403 `forbidden` on `POST /sources` with
      the auditor's token. Playwright passed 11/11 (checks.txt:46-56).

    ⚠ evidence gap (REPORT D13): checks.txt is missing the per-test lines 1–2. The total
    proves they passed; re-capture the full list.
- [x] **FE-7.2** Source registration form and source detail page (profile, contract versions
  with status pills, a resource-scoped `AuditTimeline`).
  - *Done-when:* a browser run registers a source as `data_steward` and sees it in the list.
    After a second contract version is added, the detail page shows v1 Superseded and v2
    Provisional, and the timeline shows both events with correlation IDs. — ✔ verified (checks
    at 8bdb0cd). `e2e/ingestion.spec.ts:70-135` covers:
    - the steward registers a source through the form and sees it in the list as v1;
    - contract v2 is added from v1 and the detail page shows v1 `SUPERSEDED`/"Superseded" and
      v2 `PROVISIONAL`/"Provisional";
    - the steward sees the `audit-unavailable` note;
    - the auditor, on the same detail URL, sees exactly 3 events (`source.create`,
      `source_contract.create`, `source_contract.supersede`), each SUCCESS with a correlation ID.

    The detail fetch and the schema-field copy are cancelled with `AbortController`
    (`[sourceId]/page.tsx:122-140`, `ContractEditor.tsx:48-82`), and the pills are tested
    against the OpenAPI enums (`sources.test.ts:82-90`). ⚠ follow-ups:
    - the steward cannot see the timeline because data_steward lacks `audit:read`
      (deviation 7, user decision);
    - the page stays in a stuck loading state after a non-API load failure (REPORT D10);
    - the classification codes are hard-coded (deviation 8).

*Reversibility:* additive tables and seed migrations. The downgrades refuse while user rows
exist and remove only tagged seed rows. Turning off `INGESTION_ENABLED` makes WS2
read-only. Reverting FE-7.1's nav entry restores the Phase 6 "planned" placeholder.

### Phase 8 — Bounded file import and the adapter interface (DI-02; WS2-P1)

Goal: a bounded synthetic upload becomes an import job with a write-once raw artifact, a
checksum and one immutable `source_record` per row. Parsing goes through an adapter
interface that does not depend on the first three formats.

- [x] **BE-8.1** Migration plus models:
  - `import_job`: status per D-06/D-21, pinned `source_contract_id` and `schema_version_id`
    (the ACTIVE version at creation), `data_class`, `synthetic_attested`,
    `idempotency_key` (unique per tenant and actor), `correlation_id`, `retry_of_import_id`,
    and optional expected row count and control totals.
  - `import_artifact`: `sha256`, size, detected MIME, original filename (length-bounded),
    `storage_ref`, `classification` and `retention_state`.
  - `import_batch`
  - `source_record`: batch, locator (row number / sheet / JSON index), `raw_values` JSONB of
    strings, `row_sha256`.

  Triggers make `import_artifact` and `source_record` append-only, TRUNCATE included.
  CHECKs cover the enums. The downgrade refuses while user rows exist.
  - *Done-when:* the round trip passes; tests prove UPDATE/DELETE/TRUNCATE on
    `source_record` and `import_artifact` raise; the refusal to downgrade is tested. — ✔ verified
    (checks at 8b9d006). Migration `0012_import_tables.py`: `import_append_only` row and TRUNCATE
    triggers on `import_artifact`, `import_batch` and `source_record`; `import_job_guard` freezes
    the content columns, refuses DELETE/TRUNCATE and allows status moves only along a frozen copy
    of `IMPORT_TRANSITIONS`; `import_job_same_tenant`; no `current_setting` bypass. The tests:
    - `test_import_tables.py::test_append_only_rows_reject_update_and_delete` (10 statements,
      row unchanged afterwards) and `::test_import_tables_reject_truncate` (all four tables).
    - `::test_import_job_content_is_frozen_and_never_deleted` (16 statements),
      `::test_import_job_cannot_move_tenant`, `::test_import_job_enums_are_checked`,
      `::test_artifact_retention_state_is_checked`, `::test_artifact_ref_must_match_its_hash`.
    - `::test_import_migration_round_trips_without_imports` and
      `::test_downgrade_refuses_while_an_import_exists` (rows compared before/after); the CLI
      round trip 0012→0011→0012 is in checks.txt:22-26.

    ⚠ follow-up (Phase 8 REPORT D15): the same-tenant trigger does not check that the pinned
    schema version belongs to the contract's schema, and an INSERT may start in any status.
- [x] **BE-8.2** `ArtifactStore` interface plus a filesystem implementation (D-15). It is
  content-addressed by SHA-256 and write-once (an existing object is never rewritten). Reads
  re-verify the hash. The root is `ARTIFACT_STORE_DIR`, which is required outside local/test
  (D-13) and must sit outside the repository.
  - *Done-when:* tests prove storing the same bytes twice gives one object and the same
    ref, a tampered file makes the read raise `artifact_integrity_error`, and startup is
    refused in staging without the setting. — ✔ verified (checks at 8b9d006).
    `app/core/artifacts.py`: refs are regex-checked (`sha256/<2>/<64>`, no traversal), new
    objects go to a `mkstemp` file in the target directory and are `os.link`ed into place (never
    replaces), every read and every re-put re-hashes. `app/core/config.py:135-150` requires the
    root outside local/test, absolute and outside `REPO_ROOT`. The tests:
    - `test_artifacts.py::test_same_bytes_twice_give_one_object_and_the_same_ref`,
      `::test_a_tampered_object_fails_the_read`, `::test_an_existing_object_is_never_rewritten`,
      `::test_a_missing_object_fails_the_read`, `::test_malformed_refs_are_refused`.
    - `::test_startup_is_refused_without_the_setting` (staging and production),
      `::test_the_store_may_not_sit_inside_the_repo`, `::test_the_store_must_be_an_absolute_path`,
      and the IMPORT_MAX_* lower-only tests.

    ⚠ follow-ups (REPORT D6, D7): objects are 0444 and directories follow the umask (world
    readable); the local default `/tmp/moodymate-artifacts` is shared by every worktree; the
    directory is not fsynced after the link; orphan objects after a failed transaction are not
    detected (BE-13.3).
- [x] **BE-8.3** `SourceAdapter` interface (`detect`, `iter_rows` → locator plus
  `dict[str, str]`, never floats) and CSV, XLSX and JSON adapters per D-16/D-17.
  - *Done-when:* parser tests cover:
    - UTF-8 BOM, quoted newlines and a configured delimiter;
    - the exact string `"12345678901234.123456"` surviving each format, and an XLSX numeric
      cell becoming `Decimal(repr(x))` with a precision flag beyond 15 significant digits;
    - an XLSX formula cell yielding its cached value and a flag;
    - `.xlsm` and non-UTF-8 refused;
    - nested JSON refused;
    - each D-16 limit (bytes, rows, columns, cell length, XLSX uncompressed size/ratio)
      refused with its own error code.

    — ✔ verified (checks at 8b9d006). `app/ingestion/adapters/` (`base.py`, `csv_adapter.py`,
    `xlsx_adapter.py`, `json_adapter.py`, `parse_upload` in `__init__.py`). The tests in
    `test_adapters.py`:
    - `::test_csv_utf8_bom_is_accepted_and_not_part_of_the_header`,
      `::test_csv_quoted_newlines_stay_in_the_value_and_the_locator_is_the_start_line`,
      `::test_csv_uses_the_configured_delimiter` (`;` tab `|` `,`);
    - `::test_exact_decimal_string_survives_csv`, `…_json` (number and string),
      `…_xlsx_as_text`, `::test_xlsx_numeric_cell_is_decimal_of_repr_with_a_precision_flag`,
      `::test_xlsx_precision_flag_counts_significant_digits`;
    - `::test_xlsx_formula_yields_its_cached_value_and_a_flag` (cached and uncached);
    - `::test_xlsm_is_refused_by_content_not_extension` (content type and vbaProject),
      `::test_non_utf8_samples_are_refused`, `::test_nested_json_values_are_refused`;
    - `::test_each_limit_has_its_own_code`, `::test_each_limit_applies_to_xlsx`,
      `::test_hard_ceilings_hold_at_their_values`, `::test_xlsx_uncompressed_size_is_bounded`,
      `::test_xlsx_compression_ratio_is_bounded`, `::test_xlsx_uncompressed_ceiling_holds_at_50_mib`;
    - also `::test_xml_entity_attacks_are_refused`, `::test_openpyxl_uses_defusedxml`, the D-24
      marker tests and `::test_openpyxl_opens_every_committed_position_workbook`.

    ⚠ before merge (REPORT D1, D2, MAJOR): an XLSX can amplify one 4,000-character shared string
    into every cell (about 2 GB of `raw_values` from a 10 MiB stored workbook), and a
    `<row r="2000000000">` makes openpyxl yield two billion empty rows. Neither is bounded by a
    D-16 limit. ⚠ follow-ups (REPORT D4, D5, D12, D14): openpyxl silently drops a row whose number
    does not increase; an out-of-range date serial becomes `#VALUE!`; the private `_get_row` hook
    is not proven by a test; a bare JSON scalar is reported as `format_mismatch` ("is CSV").
- [x] **BE-8.4** `POST /imports` (multipart: `source_id`, file, `Idempotency-Key` header,
  `synthetic_attested`, optional `expected_row_count`/control totals):
  - It enforces the byte limit while streaming (413 `payload_too_large` before the whole
    body is read).
  - It stores the artifact, parses into `source_record` and sets RECEIVED, all in one
    transaction, audited `import.create`.
  - It also adds `GET /imports` (paginated; filter by source and status) and
    `GET /imports/{import_id}`.
  - *Done-when:* tests cover:
    - one fixture per format reaching RECEIVED with `row_count` equal to the file's data
      rows;
    - an 11 MiB upload getting 413 with no job, artifact or file left behind;
    - a repeated `Idempotency-Key` returning the same `import_id`, and with different bytes
      a 409 `idempotency_conflict`;
    - a missing attestation under `DATA_CLASS=synthetic` getting 422
      `synthetic_attestation_required`;
    - `auditor` getting 403 with a DENIED event;
    - a cross-tenant `GET` getting 404.

    — ✔ verified (checks at 8b9d006). The router reads the multipart body itself after the
    permission and switch dependencies (`router.py:311-349`): a declared Content-Length over
    `IMPORT_MAX_BYTES + 64 KiB` is refused before any read, the stream is cut at that bound, and
    `parse_upload` then checks the file's exact size. The service stores, parses and moves
    CREATED→RECEIVED in one transaction with `import.create`; refusals go through
    `ImportService.refuse` (DENIED, reason code). The tests in `test_imports.py`:
    - `::test_each_format_reaches_received_with_one_record_per_data_row` (CSV, XLSX, JSON; 25
      rows; pinned ACTIVE schema version; artifact read back verified; one SUCCESS event);
    - `::test_an_11_mib_upload_is_413_with_nothing_left_behind`,
      `::test_just_over_the_file_limit_is_413_too`, `::test_the_byte_limit_is_enforced_while_streaming`
      (chunked 20 MiB, at most 12 chunks pulled), `::test_a_declared_oversize_body_is_refused_before_reading_it`
      (0 chunks pulled);
    - `::test_a_repeated_key_with_the_same_bytes_returns_the_same_import` (200,
      `Idempotent-Replayed`), `::test_a_repeated_key_with_different_bytes_is_409`,
      `::test_keys_are_unique_per_actor`, `::test_concurrent_uploads_with_one_key_make_one_import`;
    - `::test_a_missing_attestation_under_synthetic_is_422`;
    - `::test_without_import_write_is_403_with_a_denied_event` (auditor and three other roles);
    - `::test_cross_tenant_get_is_the_same_404_as_a_random_id` (also the cross-tenant upload,
      audited `not_found`);
    - the route is in the ws1 matrix and the AUDITED set (checks.txt:10-12,
      `test_ws1_verification.py:256,334-344`).

    ⚠ follow-ups (REPORT D7, D8, D9, D13): an artifact written before a failed commit stays as an
    orphan; `get_schema` at `service.py:555` is not wrapped in `refuse`; the Starlette version that
    `max_part_size` needs is not pinned; file names may hold bidi/format characters.
- [x] **BE-8.5** The `import_job` transition table (D-06, D-21) in `ingestion/constants.py`,
  enforced by the service.
  - *Done-when:* a unit test walks every (from, to) pair and asserts that only the table's
    transitions are accepted. — ✔ verified (checks at 8b9d006).
    `test_import_transitions.py::test_service_accepts_only_the_tables_transitions` walks all 100
    pairs through `service.move` (409 `invalid_status`, status unchanged on refusal);
    `::test_the_status_set_is_d21`, `::test_outcomes_are_final_and_reachable`. The database side
    walks the same 100 pairs (`test_import_tables.py::test_import_status_moves_only_along_the_transition_table`)
    and `::test_trigger_transition_table_matches_the_service` keeps the frozen copy equal. The
    table matches D-21 (profiling returns to RECEIVED or MAPPING_REQUIRED; outcomes are final).
- [x] **BE-8.6** No raw value leaks. Cell values never reach logs, error envelopes or audit
  details.
  - *Done-when:* a test uploads a fixture full of a sentinel string (including malformed
    rows and a parse failure). The captured logs, every error body and the audit `details`
    for the import never contain the sentinel. — ✔ verified (checks at 8b9d006).
    `test_import_no_leak.py::test_no_sentinel_in_logs_error_bodies_or_audit` uploads an accepted
    CSV with the sentinel in every header and cell (ragged, extra, blank, quoted rows), 14 parse
    failures across the three adapters, an idempotency conflict, a missing attestation, a 413 and
    broken multipart framing. It asserts the sentinel reached `source_record`, then checks every
    response body, the app's JSON-formatted log records at DEBUG plus raw stdout/stderr, and
    the audit details of every request. Adapter errors carry positions only
    (`test_adapters.py::test_refusals_carry_no_cell_values`).

    ⚠ before merge (REPORT D3, MAJOR): openpyxl's `warn()` for a date-formatted cell outside the
    date range quotes the raw serial value (`openpyxl/worksheet/_reader.py:222`). Python
    warnings go to raw stderr, bypassing the redacting formatter, and pytest captures warnings, so
    this test cannot see that channel.
- [x] **FE-8.1** Import wizard:
  1. Choose a source.
  2. Upload. A client-side size pre-check runs, and when `X-Data-Class` is `synthetic` a
     synthetic-data attestation checkbox is shown, with the attestation recorded.
  3. Status page: status pill, row count, SHA-256, pinned schema version and the audit
     timeline.

  It uses multipart through the generated client types.
  - *Done-when:* a browser run uploads the synthetic CSV and lands on a detail page showing
    RECEIVED, the row count and the SHA-256 from the API. A second run with the client-side
    check bypassed shows the backend's 413 toast with its correlation ID. — ✔ verified (checks
    at 8b9d006). `e2e/imports.spec.ts`:
    - `:61` uploads `instrument.csv` as data_steward. It checks that the attestation is required
      and recorded, the request is multipart with an `Idempotency-Key`, the response is 201
      RECEIVED with 25 rows and the sample's SHA-256, and the status page shows the same values
      plus the pinned schema version (and again after a reload);
    - `:137` shows that 10 MiB + 1 is stopped client-side (no POST). It then swaps the body on the
      wire (test-only `page.route`; the app has no bypass switch) and gets the backend's 413
      `payload_too_large` toast carrying the response's correlation ID; nothing is created;
    - `:195`: the auditor sees the `import.create` event with its correlation ID, the 403 state
      on the wizard and a backend 403.

    The wizard uses `CreateImportBody` from the generated `paths` with `multipart()`
    (`imports.test.ts` "multipart through the generated client types"). The attestation fails
    closed on a missing or unknown `X-Data-Class` (`attestationRequired`, tested), and the
    backend enforces it itself. Playwright passed 15/15 (checks.txt:43-59). ⚠ follow-ups (REPORT
    D10, D11): the upload POST is not cancellable, so leaving mid-upload later navigates the user
    to the import; the key reuse-on-retry logic and the `NEXT_PUBLIC_IMPORTS_UI=off` reversal
    are untested.
- [x] **FE-8.2** Imports list with an `ImportStatusPill` covering every `import_job` status.
  - *Done-when:* a unit test asserts that the pill map covers the full status enum from the
    generated types, and the list shows the uploaded import. — ✔ verified (checks at 8b9d006).
    `imports.test.ts` "maps exactly the import_job status enum of the generated API" compares
    `IMPORT_STATUSES` with `openapi.json`'s `ImportStatus` (a `Record<ImportStatus, …>` also makes
    a missing status a compile error). It renders every status and checks that only COMPLETED looks
    like success. `e2e/imports.spec.ts:117` finds the uploaded import first in the list,
    RECEIVED/"Received", with source, file name and SHA-256 prefix, and its link opens the detail
    page.

*Reversibility:* additive tables. The downgrade refuses while imports exist. Turning off
`INGESTION_ENABLED` stops new uploads. Stored artifacts are write-once and stay on disk; no
code path deletes them (D-20).

### Phase 9 — Field profiling and type suggestions (DI-06 profiling half, DI-02; WS2-P4)

Goal: every received import is profiled deterministically, and the user sees candidate
types and unknown columns as suggestions. Profiling never changes a schema or contract.

- [x] **BE-9.1** Migration plus model `data_profile_snapshot`, one per import. It holds
  per-column observations: candidate types, null rate, distinct count (capped), min/max for
  numbers and dates, maximum length and precision/scale, date formats seen, currency-pattern
  hits, a capped list of top values, and a `content_sha256`. The table is append-only
  (trigger, TRUNCATE included).
  - *Done-when:* the round trip passes; a test proves UPDATE/DELETE/TRUNCATE raise; the
    downgrade refusal is tested. — ✔ verified by code read at 07c437b (the pytest run in
    `docs/evidence/phase-9/checks.txt` was still in progress; the pass result is as the engineer reported).
    Migration `0013_data_profile_snapshot.py`: row trigger for UPDATE/DELETE and a statement trigger
    for TRUNCATE, both on `data_profile_append_only()`, which reads no setting; the downgrade refuses
    while any snapshot exists. Tests in `test_profile_tables.py`:
    `::test_snapshot_rejects_update_and_delete` (5 statements, row unchanged),
    `::test_snapshot_rejects_truncate`, `::test_no_session_setting_unlocks_the_trigger`,
    `::test_profile_migration_round_trips_without_snapshots` and
    `::test_downgrade_refuses_while_a_snapshot_exists` (still at head, rows unchanged).
- [x] **BE-9.2** Deterministic profiler using Decimal arithmetic only. It runs
  synchronously after receipt (RECEIVED → PROFILING → RECEIVED, or MAPPING_REQUIRED when the
  source contract has no usable ACTIVE mapping, D-21).
  - *Done-when:* a golden-file test matches the profile of the synthetic CSV exactly, and
    profiling the same artifact twice gives the same `content_sha256`. — ✔ verified by code read
    at 07c437b. `ingestion/service.py:719-785` (`profile()`: a locked, tenant-scoped job; RECEIVED →
    PROFILING → MAPPING_REQUIRED/RECEIVED, each move flushed so the 0012 trigger checks it; records
    streamed in 1,000-row partitions; the audit holds metadata only) and `:837-1164` (the profiler:
    Decimal only; distinct values capped at 10,000; top values capped at 10 and truncated at 100 characters).
    Tests in `test_profiler.py`: `::test_golden_profile_of_the_synthetic_csv` (the stored profile
    equals `golden/instrument_profile.json`, and an independent CSV count agrees),
    `::test_profiling_the_same_artifact_twice_gives_the_same_hash`, `::test_the_profiler_uses_no_float`
    (AST plus output), `::test_profiling_ends_mapping_required_and_is_audited` and
    `::test_a_usable_active_mapping_returns_the_import_to_received`. See the follow-up
    on D-02 and on the retry after a profiling failure in the Phase 9 verification report.
- [x] **BE-9.3** `GET /imports/{import_id}/profile`:
  - Top values are masked through `mask_mapping`. A column's class comes from its mapped
    `schema_field.classification`, otherwise from the source profile's
    `default_classification`.
  - Each column carries `in_contract: bool` and a `suggested_target_field` (normalised
    exact-name match against the pinned schema version), both marked `suggestion: true`.
  - *Done-when:* tests prove a confidential column's top values are masked for
    `risk_analyst` and clear for `data_steward`; the extra-column fixture reports
    `in_contract: false`; and the schema registry and contract hashes are unchanged after
    profiling. — ✔ verified by code read at 07c437b. `ingestion/router.py:522-593` (`import:read`;
    min, max and top values go through `mask_mapping` with the column's class, fail closed);
    `service.py:787-809` (a scoped `get`, which gives the same 404). Tests in `test_import_profile.py`:
    `::test_confidential_top_values_are_masked_by_clearance` (5 roles; sentinels absent for the
    uncleared ones), `::test_the_column_class_is_the_source_default_when_not_mapped`,
    `::test_the_extra_column_fixture_reports_in_contract_false`,
    `::test_profiling_leaves_registry_and_contract_hashes_unchanged` (every registry row, not only the
    hashes), `::test_without_import_read_is_403` and
    `::test_cross_tenant_profile_is_the_same_404_as_a_random_id`. No mapping exists before Phase 10, so
    the mapped-field-class branch is a stub (`mapped_classifications` returns `{}`). Phase 10 must test it.
- [x] **FE-9.1** Profile tab: per-column table, the inferred type labelled "Suggested", a
  null-rate bar, `MaskedValue` for masked samples, and unknown columns flagged "Not in
  contract". The only schema action is a link to the existing Phase 5 "New proposal" page.
  - *Done-when:* a browser run on the extra-column fixture shows the Suggested labels and
    the flagged column. A grep finds no profile code calling a mutating endpoint. — ✔ verified by
    code read at 07c437b (the Playwright 17/17 and vitest 106 results are as the engineer reported).
    `e2e/profile.spec.ts`: the steward run on `instrument__unknown_extra_column.csv` asserts a
    "Suggested: …" label on every row, exactly one "Not in contract" badge (on `legacy_flag`), and no
    non-GET API request. The auditor run asserts that the count of `masked-value` elements equals the
    count of masked values in the API response, and that the proposal link is hidden (it sits behind
    `RequirePermission schema:propose`). The grep is in `profile.test.ts` ("profile code is
    read-only", with a self-test). Both fetches are cancelled with an AbortController on unmount.

*Reversibility:* additive table. Profiling is a pure function whose output is stored. If it
is removed (downgrade), imports go RECEIVED → MAPPING_REQUIRED/RECEIVED with no profile, and
the FE tab shows an empty state.

### Phase 10 — Versioned mapping workbench (DI-03; WS2-P2)

Goal: a steward builds a versioned, immutable mapping from source columns to the fields of
one pinned schema version, using allowlisted deterministic transforms. The mapping is
dry-run validated against a real import, and a different person activates it.

- [x] **BE-10.1** Migration plus models in `mapping`:
  - `field_mapping`: code, `source_contract_id`, target `schema_id`.
  - `mapping_version`: status as `schema_version` (D-06), pinned `schema_version_id`,
    `content_sha256`, author, decision columns, `last_validation` reference.
  - `mapping_rule`: source column, target field, transform code plus params, null/default
    policy, column policy `MAP|PRESERVE`.
  - `import_job.mapping_version_id`, added as a new nullable column (D-01).

  Triggers make non-DRAFT versions and their rules immutable, TRUNCATE included, with no
  session-settable bypass. CHECKs cover the enums.
  - *Done-when:* the round trip passes; tests prove a rule INSERT/UPDATE/DELETE on an ACTIVE
    version, `TRUNCATE mapping_rule` and `SET`-based bypass attempts all fail; the downgrade
    refusal is tested. — ✔ verified by code read at 0d7b07a. `db/migrations/versions/0014_mapping_tables.py`
    (guards `:73-268`, no `current_setting`; TRUNCATE refused on all four tables; enum CHECKs `:352-411`;
    `import_job_mapping_pin` `:244-268` writes once, only an ACTIVE version of the import's own
    contract and schema version; downgrade refuses while `field_mapping` has rows `:435-439`).
    `test_mappings.py::test_non_draft_versions_and_rules_are_immutable_in_the_database` (rule
    INSERT/UPDATE/DELETE on an ACTIVE version, version/mapping/validation edits, INSERT as ACTIVE,
    TRUNCATE CASCADE on each table, `set_config`/`SET LOCAL` bypass, rows unchanged, downgrade
    refusal). Round trip: `test_ws1_verification.py::test_migration_round_trip_and_downgrade_guards`
    (head → base → head) and the reported 0014↔0013 run. Follow-ups P10-2 (status moves without a
    decision are not refused in the DB) and the pin trigger having no test yet (Phase 11 must cover it).
- [x] **BE-10.2** Transform engine (D-19): a closed allowlist of pure, deterministic
  functions with JSON-schema-validated params. There is no eval, no expressions and no
  regex.
  - *Done-when:* every allowlisted transform has unit tests, including Decimal results that
    never pass through a float; an unknown transform code or bad params gets 422; and a test
    asserts that the allowlist equals the D-19 list. — ✔ verified by code read at 0d7b07a.
    `mapping/transforms/engine.py` (strict pydantic param models with `extra="forbid"`, validated in
    JSON strict mode `:313-337`; hand-written parsers, ASCII digits only, no exponent/NaN `:174-256`;
    Decimal in a context that traps Inexact, fitting refuses rather than rounds `:362-404`; reason codes
    never carry the value). `test_mappings.py::test_transform_allowlist` (all 11 transforms with
    Decimal results compared by type and value, failure reasons without the value, NUMERIC(20,2)
    fit, source scan for float/regex/eval/strptime, allowlist == D-19 set, 7 bad definitions refused
    by the engine and by `POST /mappings` with 422 and nothing written).
- [x] **BE-10.3** `GET /mappings` (filter by source and schema), `GET /mappings/{mapping_id}`
  (versions and rules), `POST /mappings` (definition plus DRAFT v1 pinned to the target's
  ACTIVE schema version) and `POST /mappings/{mapping_id}/versions` (the next DRAFT).
  - *Done-when:* tests cover create/list/get; a new version leaves the previous version's
    rules and hash unchanged; there is a cross-tenant 404; `risk_analyst` `POST` gets 403
    with a DENIED event. — ✔ verified by code read at 0d7b07a. `mapping/router.py:189-263`
    (`mapping:read` / `mapping:propose`); `mapping/service.py:676-801` (v1 pinned to the ACTIVE schema
    version; each refusal audited DENIED through `_refuse` `:502-514`; the next DRAFT never touches
    earlier rows); `mapping/repository.py` (every query tenant-scoped). `test_mappings.py::test_create_list_get_and_versions`
    (create/list with source and schema filters/get; v1 row and rules byte-equal after v2;
    `no_change`, `unknown_target_field`, `duplicate_code`, `contract_already_mapped` with one DENIED
    each; foreign id body equal to a random id's; foreign writes 404; `risk_analyst` 403 with one DENIED).
    Deviation accepted: a draft "edit" is a new DRAFT version (no in-place edit endpoint).
- [x] **BE-10.4** `POST /imports/{import_id}/mappings/validate` (body: `mapping_version_id`).
  It is a dry run. It checks:
  - every required target field is mapped;
  - the profile's types are compatible with the targets;
  - every transform applies to every row, reported as counts per rule with no values;
  - every source column has `MAP` or `PRESERVE`;
  - business keys are unique.

  It stores a validation record (audited `mapping.validate`) and writes no canonical rows.
  - *Done-when:* tests prove an unmapped required field gives a BLOCKER, an unmapped column
    with no policy gives an issue, and a valid mapping returns `ok` with per-rule counts.
    The `canonical_record` count is unchanged, and the issue texts contain no sentinel
    values. — ✔ verified by code read at 0d7b07a. `mapping/service.py:193-412` (the dry run keeps
    counts, reason codes and names only; business keys as hashes) and `:805-919` (scoped import and
    version, contract and schema-version pairing, profile required; it writes only the
    `mapping_validation` row, the DRAFT's `last_validation_id` and the `mapping.validate` audit).
    `test_mappings.py::test_validate_dry_run` (BLOCKER `required_field_unmapped`, ERROR
    `column_without_policy`, per-rule counts on the clean import, sentinel absent from the response and
    from the stored `issues`/`rule_results`, import status and `source_record` count unchanged).
    `canonical_record` does not exist until Phase 11; no code path here can write it, and BE-11.x
    must assert the count. Follow-up P10-1 (a race with submit gives an unaudited 500).
- [x] **BE-10.5** Submit and approval:
  - `…/versions/{version}/submit` needs the author, `mapping:propose` and a recorded `ok`
    validation for that version.
  - `…/approval` (APPROVE/REJECT) needs `mapping:approve`, and the approver must not be the
    author (403 `self_approval`, audited DENIED).
  - Approval, in one transaction:
    1. activates the version and DEPRECATES the mapping's previous ACTIVE version;
    2. refuses with 409 `schema_version_not_active` if the pinned schema version is no
       longer ACTIVE;
    3. upserts a `schema_dependency` row (`MAPPING`, mapping code) (D-18).
  - *Done-when:* tests cover:
    - a self-approval returning 403 with exactly one DENIED event;
    - a submit without a validation returning 409 `mapping_validation_required`;
    - a valid approval leaving the new version ACTIVE and the old one DEPRECATED, with the
      old hash unchanged and the audit events sharing one correlation ID;
    - the Phase 5 `impact-analysis` on the target schema then listing this mapping as a
      dependent;
    - an approval after the schema version was deprecated returning 409.

    — ✔ verified by code read at 0d7b07a. `mapping/service.py:1003-1041` (submit: author only, DRAFT,
    an ok validation whose hash equals the version's and the rules' recomputed hash, pinned schema
    version ACTIVE) and `:1043-1115` (approval: `mapping:approve`, author refused before any status check,
    schema locked, ACTIVE re-checked, previous ACTIVE DEPRECATED and flushed before the activation,
    `register_dependent` upsert, every event in one commit). `test_mappings.py::test_submit_and_maker_checker_approval`
    (409 `mapping_validation_required` with one DENIED; `not_author` 403; `self_approval` 403 with
    exactly one DENIED `mapping.approve`; v2 ACTIVE, v1 DEPRECATED with its hash unchanged, the three
    events on one correlation ID; impact analysis lists `MAPPING core_instrument`; 409
    `schema_version_not_active` after the Phase 5 approval, audited; the Phase 9 seams: RECEIVED after
    activation and the profile classified by the mapped field).
- [x] **BE-10.6** Setting `MAPPING_CHANGE_ENABLED` (default `true`). When it is off, mapping
  writes get 409 `mapping_change_disabled` (audited) and reads and existing ACTIVE versions
  keep working.
  - *Done-when:* a test with it off gets 409 plus a DENIED event on `POST /mappings`, and
    200 on `GET /mappings`. — ✔ verified by code read at 0d7b07a. `core/config.py:79` (default
    `True`); `mapping/router.py:55-94` (the switch, after the permission, audited DENIED) on all five
    writes. `test_mappings.py::test_mapping_change_switch` (409 plus one DENIED on `POST /mappings`,
    200 on `GET /mappings`, the version and validate routes refused too, `risk_analyst` still gets
    403, nothing written). Writes also sit behind `INGESTION_ENABLED` (accepted).
- [ ] **FE-10.1** Mapping workbench grid:
  - columns: source column, profiled type, target field, transform plus params, required,
    policy;
  - a version badge;
  - draft editing;
  - "Validate against this import" with per-rule results.

  Changing the import or version cancels in-flight requests, and unsaved edits prompt before
  they are discarded.
  - *Done-when:* a browser run maps the synthetic CSV: validation shows the
    missing-required BLOCKER until the field is mapped, then passes. A unit test proves a
    stale response is ignored after a switch.
- [ ] **FE-10.2** Mapping versions and approval view (reusing the Phase 5 proposal
  patterns): Submit is disabled until a validation is `ok`, the author sees "You cannot
  approve your own mapping", and the approver gets the decision panel.
  - *Done-when:* a two-user browser run goes author → approver → ACTIVE badge, and a forced
    early submit shows the backend's 409 envelope.

*Reversibility:* additive tables and a nullable column. The downgrade refuses while
mappings exist. Turning off `MAPPING_CHANGE_ENABLED` freezes mappings at their current
ACTIVE versions. A bad mapping is undone by approving a new version, never by editing one.

### Phase 11 — Validation, quarantine and canonical records (DI-04; WS2-P3)

Goal: validating an import applies its pinned schema and mapping versions. Every source row
ends in exactly one outcome (ACCEPTED → `canonical_record`, QUARANTINED or REJECTED). Issues
are actionable and carry no values, and quarantine is reviewable and retryable. Nothing is
edited in place.

- [x] **BE-11.1** Migration plus models:
  - `validation_profile`: versioned and immutable; maps issue code → severity → disposition.
  - `validation_issue`: code, severity, field, `source_record_id`, rule ref, value-free
    message, disposition per D-23.
  - `quarantine_item`: `source_record_id`, batch, reason codes, owner, status per Data Model
    §9, `resolved_by_import_id`.
  - `canonical_record`: import, `schema_version_id`, `mapping_version_id`,
    `source_record_id`, typed-string `payload` JSONB, `payload_sha256` (D-22).
  - `record_lineage`: canonical → source, mapping version, transform trace.
  - a tagged PROVISIONAL default `validation_profile` seed.

  `canonical_record`, `record_lineage`, `validation_issue` and `validation_profile` are
  append-only. `quarantine_item` allows status changes only. TRUNCATE is blocked
  everywhere, and CHECKs cover the enums.
  - *Done-when:* the round trip passes; trigger tests cover each table, including an UPDATE
    of `quarantine_item.source_record_id` being refused while a status change is allowed;
    the downgrade refusal is tested. — ✔ verified by code read at b4e0c27.
    `db/migrations/versions/0015_validation_tables.py` (append-only function on five tables plus
    TRUNCATE triggers `:132-139,284-292`; `quarantine_item_guard` freezes content, moves only along
    the D-20 table and writes `resolved_by_import_id` once, by a direct retry of the item's import
    `:141-177`; insert checks for items, canonical records, lineage, issues and manifests
    `:179-272`; manifest conservation CHECK `:416-419`; seed tagged `0015_validation_profile_seed`
    `:711-729`; downgrade refuses on any result or untagged profile `:732-740`).
    `test_validation.py::test_validation_tables_are_guarded_in_the_database` (UPDATE/DELETE/
    TRUNCATE CASCADE on each append-only table; item `source_record_id`/`reason_codes`/`owner`
    edits, illegal moves, DELETE and TRUNCATE refused; OPEN → UNDER_REVIEW allowed with the rest of
    the row unchanged; insert checks; seed and frozen copies equal the code; downgrade refused with
    counts unchanged). Round trip: `test_ws1_verification.py::test_migration_round_trip_and_downgrade_guards`
    and the reported 0015↔0014 run. Follow-up P11-1 (no CHECK on `validation_issue.code`).
- [x] **BE-11.2** Validation engine. It applies the import's mapping version and checks:
  - type and required per `schema_field`;
  - DECIMAL precision/scale (fixed precision; over-scale is an error, never rounded);
  - date validity and format;
  - ISO 4217 codes for fields with `unit = 'ISO4217'`;
  - duplicate business keys from the source contract;
  - the default profile's severities.

  Columns marked `PRESERVE` and columns not in the contract stay in `source_record` and
  produce an `UNMAPPED_COLUMN` warning (never dropped).
  - *Done-when:*
    - a test runs every bad fixture and asserts the exact expected (row, field, code)
      issues;
    - an invariant test asserts `source_rows == accepted + quarantined + rejected` for every
      fixture, plus a property test over generated rows;
    - validating the same artifact twice through two imports gives identical
      `payload_sha256`s;
    - no canonical value is a float (JSON-type assertion).

    — ✔ verified by code read at b4e0c27. `ingestion/service.py:1822-2066` (`Validator`: every row
    returns exactly one outcome, with counters per outcome; a file-level BLOCKER rejects every row;
    blank and "" cells count as missing through `apply` in `mapping/transforms/engine.py:481-505`;
    over-scale refused, never rounded `engine.py:371-386`; ISO 4217 by normalised unit
    `constants.py:354-356`; the first occurrence owns a business key and later ones are
    DUPLICATE_BUSINESS_KEY; messages name fields, columns and rules, never values).
    `test_validation.py::test_engine_fixtures_invariant_and_no_floats` (24 fixtures, exact
    (row, field, code) sets and status; conservation per fixture and over 25 seeded generated
    batches; string-only payloads at the field's scale; no `float` in the engine source).
    `test_validate_statuses_issues_and_identical_payloads` (the same artifact in two imports gives
    identical `payload_sha256`s and `canonical_sha256`; no JSON number in any stored payload).
- [x] **BE-11.3** `POST /imports/{import_id}/validate` and `GET /imports/{import_id}/issues`
  (paginated; filter by severity, code and field).
  - `validate` needs a usable ACTIVE mapping version (otherwise 409 `mapping_required`, and
    the status is MAPPING_REQUIRED). It pins `mapping_version_id` and moves VALIDATING →
    COMPLETED / COMPLETED_WITH_WARNINGS / QUARANTINED / FAILED (a file-level BLOCKER means
    no rows are accepted).
  - It runs once per import; a second call gets 409 `already_validated`, and a re-run means
    a new import (D-21). It is audited `import.validate`.
  - *Done-when:* tests cover each final status with its fixture, a 409 for an import with no
    mapping, a 409 on a second validate, and issue filters and pagination. — ✔ verified by
    code read at b4e0c27. `ingestion/router.py:666-732` (`import:write` plus `IngestionEnabled`;
    issues under `import:read`); `ingestion/service.py:908-1090` (locks the import; audited 404 /
    `already_validated` / `invalid_status` / `mapping_required` refusals; pins the mapping version,
    then streams records in keyset pages of 1,000 and inserts everything in one transaction,
    committed once at the end). `test_validation.py::test_validate_statuses_issues_and_identical_payloads`
    (409 `mapping_required` with one DENIED event and the status still MAPPING_REQUIRED;
    COMPLETED / COMPLETED_WITH_WARNINGS / QUARANTINED / FAILED with their fixtures, the pinned
    version, the counts adding up to 25 and stored rows equal to the counts; a second validate
    gets 409 `already_validated` with DENIED and no change; filters by severity/code/field, a
    second page, 422 on an unknown code; no fixture value in any message). Follow-ups P11-2, P11-5.
- [x] **BE-11.4** `GET /quarantine/{batch_id}`: items with masked raw values (D-20), using
  `mask_mapping` with column classes as in BE-9.3.
  `POST /quarantine/{batch_id}/items/{quarantine_id}/disposition`: transitions per the D-20
  table, audited, with no row deletion. Retry is `POST /imports` with
  `retry_of_import_id`. When that import contains and then accepts the same business key,
  the RETRY_READY items move through CORRECTED to RESOLVED with `resolved_by_import_id`
  (automatic and audited, D-20).
  - *Done-when:* tests prove:
    - every legal transition succeeds and every illegal one gets 409;
    - `risk_analyst` gets 403 on both endpoints;
    - `auditor` reads with masked values;
    - a retry import resolves the matching items;
    - the `quarantine_item` row count never decreases across the whole test.

    — ✔ verified by code read at b4e0c27. `ingestion/router.py:735-816` (`quarantine:read` /
    `quality:review` plus `IngestionEnabled`; `mask_mapping` by each column's class, where a mapped
    field's class applies and otherwise the source default, extra cells by the default);
    `ingestion/service.py:2139-2290` (scoped lookups give the same 404; manual moves only, every
    refusal audited; automatic RETRY_READY → CORRECTED → RESOLVED / RETRY_READY moves are separate
    UPDATEs, audited, inside the retry's validation transaction); `_check_retry` `:1092-1115`
    (same source, QUARANTINED or FAILED only, audited). `test_quarantine.py::test_quarantine_masking_dispositions_and_retry`
    (auditor gets masked values, approver and steward get the allowed ones; `risk_analyst` 403 on
    both endpoints; a foreign batch gets a 404 body equal to a random id's; every manual move
    succeeds and equals MANUAL_QUARANTINE_MOVES; every other target is 409 `invalid_transition`
    with DENIED and no change, including from REJECTED and RESOLVED; `retry_not_applicable` and
    404 retries refused; the retry resolves row 4 and returns row 6 to RETRY_READY with the
    expected audit actions; item count checked after every step and never decreasing).
    Follow-ups P11-3, P11-4, P11-6.
- [x] **BE-11.5** Import manifest. `GET /imports/{import_id}` returns a `manifest` with:
  artifact SHA-256 and classification, source contract version, `schema_version_id`,
  `mapping_version_id`, validation profile version, and accepted/quarantined/rejected/warning
  counts (the Data Model §10 shape).
  - *Done-when:* a test asserts the manifest keys and values after validation, and that
    they are unchanged after a later mapping version is activated and after a schema change
    proposal is applied. — ✔ verified by code read at b4e0c27. `ingestion/service.py:1001-1063`
    (the manifest is built once at validation and stored in the append-only `import_manifest`,
    with its insert check against the pinned versions and the row count, and a conservation
    CHECK). `test_quarantine.py::test_manifest_is_stable_after_mapping_and_schema_changes` (the key
    set equals the schema; each value is checked against the DB; it is unchanged, along with the
    pin and the canonical count, after mapping v2 is ACTIVE and after an applied proposal
    deprecates schema v1; the 0014 pin trigger refuses pinning a DEPRECATED version).
- [x] **FE-11.1** Validation dashboard tab: accepted/quarantined/rejected/warning counts, an
  issues table with filters and field-level detail, and the manifest panel.
  - *Done-when:* a browser run on the mixed-errors fixture shows counts and issue codes
    equal to the API response, and the counts add up to the row count.
- [x] **FE-11.2** Quarantine view: masked values, disposition actions gated on
  `quality:review`, and "Retry with corrected file", which opens the import wizard
  pre-filled with `retry_of_import_id`.
  - *Done-when:* a browser run as `data_steward` moves an item OPEN → UNDER_REVIEW →
    RETRY_READY → RESOLVED via a retry upload; `risk_analyst` sees no Quarantine tab, and the
    backend returns 403 on the direct call.

*Reversibility:* additive tables. The downgrade refuses while canonical records or
quarantine items exist. Turning off `INGESTION_ENABLED` stops validation and dispositions.
Canonical records are versioned by import, so a bad result is superseded by a new import,
never edited.

### Phase 12 — Reconciliation and drift detection (DI-05, DI-06 drift half; WS2-P3/P4)

Goal: every validated import has a reconciliation that cannot report MATCHED while any check
fails. A changed source shape raises drift events that a person must review. Nothing
changes a schema, contract or mapping automatically.

- [x] **BE-12.1** Migration plus models: `reconciliation_run` (outcome per D-23, tolerance
  policy) and `reconciliation_item` in `backend/app/reconciliation`; `data_drift_event`
  (`drift_type` and `status` per D-23, baseline import, column, observed vs baseline
  summary) in `mapping`. Results are append-only, and drift events allow status and review
  fields only. TRUNCATE is blocked and CHECKs cover the enums.
  - *Done-when:* the round trip passes; the trigger tests pass; the downgrade refusal is
    tested. — ✔ verified by code read at cdbade9 (D-28). `db/migrations/versions/0016_reconciliation_and_drift.py`:
    append-only and TRUNCATE triggers on runs and items `:94-101,195-203`; run insert check
    (validated import, own tenant) `:103-117`; item insert check (never non-MATCHED under
    MATCHED, an unexplained gap needs BROKEN) `:119-138`; drift guard (content frozen, no
    DELETE/TRUNCATE, review once from OPEN) `:140-165`; drift insert check (created OPEN,
    baseline COMPLETED/COMPLETED_WITH_WARNINGS of the same source and tenant) `:167-191`; enum,
    variance and status-of-variance CHECKs `:266-399`; downgrade refusal `:452-458`. Models in
    `reconciliation/models.py:41-68` and `mapping/models.py:231-285`.
    `test_reconciliation.py::test_phase12_tables_are_guarded_in_the_database` (frozen enum copies
    equal the code; UPDATE/DELETE/TRUNCATE refused; item and event insert refusals; review once;
    downgrade to 0015 refused with counts unchanged, and 0015's own refusal still holds once the
    Phase 12 rows are gone). Round trip: `test_ws1_verification.py::test_migration_round_trip_and_downgrade_guards`
    (head → base → head, head = 0016) and the CI step `Migration round trip (D-02)`
    (`.github/workflows/ci.yml:48-49`). Pass results as reported: local 47 passed, CI run 43.
- [x] **BE-12.2** Reconciliation runs at the end of validation:
  - **Count conservation:** `source_rows = accepted + quarantined + rejected`, with zero
    tolerance.
  - **Upload control count:** `expected_row_count` against the source rows, when provided.
  - **Control totals:** for each control-total column in the contract, the source total of
    the parseable values against the accepted total plus the held total, and against the
    upload-declared total if one exists. Decimal throughout; default tolerance 0,
    PROVISIONAL.

  The outcome is MATCHED, REVIEW_REQUIRED or BROKEN. The route is
  `GET /imports/{import_id}/reconciliation`.
  - *Done-when:* tests prove the clean fixture is MATCHED, the quarantine fixture is
    REVIEW_REQUIRED with the exact Decimal variance as a string, and a wrong
    `expected_row_count` is BROKEN. A table test asserts the outcome is never MATCHED when
    any item is not MATCHED. — ✔ verified by code read at cdbade9 (D-28). Called from
    validation in its transaction, for every final status (`ingestion/service.py:1101,1118-1163`);
    figures read back from the DB in one scoped aggregate (`ingestion/repository.py:441-489`);
    item status, outcome and checks in `reconciliation/service.py:60-66,103-110,153-215`
    (Decimal under an exact context that traps rounding, `:49`); tolerance from
    `RECONCILIATION_TOLERANCE` (default `0`, `core/config.py:84`; route `ingestion/router.py:693`);
    `GET /imports/{import_id}/reconciliation` under `import:read`, figures as `format(d, "f")`
    text and masked by classification (`reconciliation/router.py:35-83`).
    `test_reconciliation.py::test_reconciliation_outcomes_and_exact_variances` (clean fixture
    MATCHED; mixed-errors fixture REVIEW_REQUIRED with notional variance `"4237607.14001"` equal
    to the held rows' total; `expected_row_count=24` BROKEN with variance `"-1"`; risk_analyst and
    auditor see masked totals; 404 `reconciliation_not_found` before validation; table test over
    155 combinations: MATCHED iff every item MATCHED, BROKEN iff a gap is unexplained; every
    stored run checked; no `float` in the reconciler source).
- [x] **BE-12.3** Drift detection after profiling, behind `DRIFT_DETECTION_ENABLED` (default
  `true`). It compares the import's profile with the baseline, which is the latest
  COMPLETED or COMPLETED_WITH_WARNINGS import of the same source profile. It emits
  COLUMN_ADDED, COLUMN_REMOVED, TYPE_CHANGED, NULL_RATE_SHIFT and RANGE_SHIFT events, with
  PROVISIONAL thresholds as settings. A first import has no baseline and no events. The route
  is `GET /imports/{import_id}/drift`.
  - *Done-when:* tests prove the extra-column fixture after a clean baseline emits exactly
    one COLUMN_ADDED; schema, contract and mapping hashes are unchanged after detection; and
    with the setting off, no events are written. — ✔ verified by code read at cdbade9 (D-28).
    Settings `core/config.py:87-92` (thresholds `0.10` / `0.25`, PROVISIONAL, guarded
    `:173-184`); off means no thresholds and no detection (`ingestion/router.py:407-411`,
    `ingestion/service.py:856-859`, in the profiling transaction); baseline query
    `ingestion/repository.py:491-507` (latest COMPLETED / COMPLETED_WITH_WARNINGS import of the
    same source, other than this one, with a profile); detector `mapping/service.py:1269-1314`;
    events and audit `import.drift_detect` `:1349-1411`; `GET /imports/{import_id}/drift` under
    `import:read` (`mapping/router.py:420-437`). `test_reconciliation.py::test_drift_detection_setting_and_review`
    (first import: no baseline, no events, no detection audit; extra-column fixture after the
    clean baseline: exactly one COLUMN_ADDED `legacy_flag`; `governed(db)` (schema, contract and
    mapping hashes and statuses, field and proposal counts) equal before and after; with
    `DRIFT_DETECTION_ENABLED=False` no event and no `import.drift_detect`; the five types on
    synthetic profiles, including the at-threshold non-events; no `float` in the detector).
    Note: the baseline is matched by source system, which equals "same source profile" while a
    source has one profile (D-28 §5).
- [x] **BE-12.4** `POST /drift-events/{drift_event_id}/review` (`quality:review`; outcome
  ACKNOWLEDGED or ACTION_REQUIRED plus a required comment; audited). ACTION_REQUIRED returns
  a link hint to the Phase 5 proposal page and creates nothing else.
  - *Done-when:* tests prove the review is recorded with the reviewer and audit event, a
    second review gets 409, `risk_analyst` gets 403, and the `schema_change_proposal` count
    is unchanged after ACTION_REQUIRED. — ✔ verified by code read at cdbade9 (D-28).
    `mapping/router.py:440-458` (`quality:review` plus `IngestionEnabled`; body
    `mapping/schemas.py:369-373`, outcome ACKNOWLEDGED | ACTION_REQUIRED, comment required, at
    most 1,000 characters); `mapping/service.py:1444-1493` (row lock; scoped 404 for absent and
    foreign; 409 `already_reviewed` when the move is not in `DRIFT_TRANSITIONS`; every refusal
    audited DENIED; success audited `drift_event.review`; ACTION_REQUIRED returns `proposal_link`
    to `/schemas/proposals/new?schema={id}` only). `test_reconciliation.py::test_drift_detection_setting_and_review`
    (risk_analyst and approver 403 with `required_permission` `quality:review`; 422 for missing,
    blank or OPEN outcome; foreign tenant 404 body equal to a random id's, audited DENIED;
    ACTION_REQUIRED recorded with `reviewed_by = user:{id}`, comment and time, one SUCCESS audit
    event; `governed(db)` unchanged, so the `schema_change_proposal` count is unchanged; second
    review 409 `already_reviewed`, audited DENIED, nothing changed).
- [x] **FE-12.1** Reconciliation tab: per-check rows with source, target, held, variance and
  tolerance, and an outcome pill. Decimals are rendered from API strings and never parsed to
  numbers.
  - *Done-when:* a browser run on the quarantine fixture shows REVIEW_REQUIRED with the
    variance text equal to the API string. A grep finds no `parseFloat`/`Number(` on
    reconciliation values. — ✔ verified by code read at cdbade9.
    `frontend/src/components/ingestion/ImportReconciliationTab.tsx` (GET only; figures rendered
    through `figureDisplay`, `reconciliation.ts:61-65`, as the server's text; outcome pill from
    the server). Browser: `frontend/e2e/reconciliation.spec.ts` test "FE-12.1: the quarantine
    fixture reconciles REVIEW_REQUIRED; …" (`:56-115`: validates `instrument__mixed_errors.csv`
    in the browser, `data-outcome="REVIEW_REQUIRED"`, every figure of every row equals the API
    string, notional variance text equals `notional.variance`), plus the risk_analyst masking test
    (`:117-133`). Grep: `src/components/ingestion/__tests__/reconciliation.test.ts:125-146` greps
    the seven reconciliation and drift files for `parseFloat`, `parseInt`, `Number(`, `Math.`,
    `toFixed`, unary `+` and more; an architect grep of those files also finds none. The spec runs
    in the CI `e2e` job (`ci.yml:82-129`, all of `e2e/`, no skip); CI run 43 on cdbade9 passed, as
    reported (log not opened by the architect).
- [x] **FE-12.2** Drift alerts tab with a review dialog. "Propose schema change" links to the
  existing Phase 5 new-proposal page (prefilled schema only).
  - *Done-when:* a browser run uploads a clean import, then the extra-column import, sees
    the COLUMN_ADDED alert, acknowledges it as `data_steward`, and the alert then shows the
    reviewer and comment. — ✔ verified by code read at cdbade9.
    `ImportDriftTab.tsx` (alerts, Review gated on `quality:review`, "Propose schema change"
    gated on `schema:propose` and shown only for ACTION_REQUIRED), `DriftReviewDialog.tsx`
    (outcome and comment required; closes on the server's 200 and reloads), `drift.ts:88-90`
    (link built from `schema_id` only). Browser: `reconciliation.spec.ts` test "FE-12.2: a clean
    import, then the extra-column import: …" (`:135-194`: both uploads through the wizard, the
    clean one validated; exactly one `COLUMN_ADDED:legacy_flag` against the clean baseline;
    ACKNOWLEDGED with a comment; the alert shows `reviewed_by` and the comment, also after a
    reload), and the "Action required" test (`:196-247`: link to
    `/schemas/proposals/new?schema={id}` with only the schema prefilled, proposal total unchanged;
    risk_analyst has no Review button and gets 403). Same CI note as FE-12.1.

*Reversibility:* additive tables. The downgrade refuses while runs or events exist.
Turning off `DRIFT_DETECTION_ENABLED` stops new drift events (existing ones stay
readable). Reconciliation is a control and has no off switch, but it only adds read-only
results.

### Phase 13 — Adapter readiness and WS2 verification (WS2-P5, WS2 Definition of Done)

Goal: the adapter contract is documented and enforced by tests, so a future feed or
warehouse adapter plugs in without changing the core. The whole of WS2 is proven by a
verification suite and browser smoke tests in CI.

- [x] **BE-13.1** `docs/ADAPTERS.md`, the adapter contract: interface, limits, the row/locator
  shape, error codes, the no-float rule and the provenance fields. It comes with a
  parametrised contract test suite that every adapter must pass. The suite runs over CSV,
  XLSX, JSON and a test-only in-memory `FakeFeedAdapter`.
  - *Done-when:* the contract suite passes for all four adapters, and the fake adapter is
    wired in only from `app/tests` (a grep of `app/` outside tests finds no reference to it).
- [x] **BE-13.2** WS2 verification suite (`pytest -m ws2`, a new CI step that prints the
  matrix). It covers:
  - every WS2 route × every seeded role, asserting the expected 2xx on a prepared fixture
    for allowed roles and 403 `forbidden` with the declared `required_permission` for the
    others;
  - cross-tenant isolation for sources, imports, mappings, quarantine and drift (items must
    belong to the caller's tenant);
  - audit completeness, where every WS2 mutating route is in `AUDITED` and every `AUDITED`
    key is called;
  - an end-to-end CSV → profile → mapping → approval → validate → reconcile run;
  - the row-conservation invariant;
  - a base → head migration round trip.
  - *Done-when:* the step runs green in CI on the Phase 13 PR and the matrix table appears in
    the CI log.
- [x] **BE-13.3** `docs/RETENTION.md` gets PROVISIONAL rows for raw artifacts, source
  records, canonical records and lineage, validation issues and quarantine, profiles and
  drift, and reconciliation (owners named, no numbers). The restore procedure gains the
  check that artifact store hashes match `import_artifact`.
  - *Done-when:* every WS2 table appears in a RETENTION.md row and the restore outline lists
    the artifact hash check.
- [x] **FE-13.1** Playwright WS2 smoke:
  1. a steward registers a source and uploads;
  2. the profile is shown;
  3. the steward maps and validates the mapping;
  4. the approver activates it;
  5. the steward validates the import;
  6. quarantine disposition and reconciliation;
  7. the drift alert is reviewed.
  - *Done-when:* the `e2e` job passes in CI with the WS2 specs and the `playwright-report`
    artifact exists.

*Reversibility:* tests and docs only.

### Deferred (out of scope for Feature 02)

- **Live feeds, warehouse, API and event adapters:** only the interface and a test fake
  (BE-13.1). Real connectors need client protocols (Data Model §11).
- **Object storage (S3-compatible), pre-signed uploads and malware scanning:** these are
  infrastructure, and hosting is undecided (D-12, D-15). Acceptable for now only because
  non-production holds synthetic data (D-14). They are a pre-production gate.
- **Background/async import processing (`workflow_job`) and files beyond the D-16 limits:**
  WS4 RO-01 owns the job model (D-21).
- **Other formats and encodings:** non-UTF-8, multi-sheet XLSX, `.xls`/`.xlsm`, nested JSON,
  fixed-width, XML and Parquet.
- **Projecting canonical records into WS3 domain tables** (`instrument_version`,
  `position_version`, …) and a cross-import `dataset_snapshot`: WS3 owns them (D-22).
- **Reference-data checks** (e.g. that a counterparty exists): they need WS3 reference data.
- **Authoring custom validation profiles** and regex rules: only the seeded default profile
  (ReDoS and governance risk).
- **AI mapping or type suggestions:** WS5. Suggestions here are deterministic exact-name
  matches.
- **Retention, purge and archival jobs** for artifacts, records and quarantine: the policy
  is documented only (D-20, BE-13.3).
- **Source, dataset and legal-entity row scopes beyond tenant:** this waits for the client
  role model (D-04).
- **Statistical distribution drift** (e.g. KS tests): only shape, null-rate and range drift.
- **Automatic schema change proposals from drift:** forbidden by Arch §6.1 step 7; the
  feature gives a link only.
- **Downloading raw artifacts or exporting quarantined rows:** restricted by Arch §10. If
  added later, it needs its own permission and CSV-injection safe output.
- **Client data in local, test or staging:** forbidden by D-14.
- **Incumbent and benchmark reconciliation:** WS4 RO-04.

---

## Feature 03 — WS3 Financial Domain Model & Calculation Services

Kickoff: `docs/prompts/kickoffs/03-ws3-financial-calculations.md` · Status: **APPROVED by the user 2026-10-17** (plan, D-28…D-35 active; fold 4 tests in BE-14.1; illustrative methodology seeds; BE-14.6 and FE-14.3 ship as a separate PR before Phase 14).

Covers WS3 FC-01…FC-08 and the paired contracts FE-FC-01…05 / BE-FC-01…05. Numbering
(D-08): Feature 03 uses global **Phases 14–18**, so its task IDs run from `BE-14.1`/`FE-14.1`
to `BE-18.n`/`FE-18.n`. WS3-internal phases are cited as `WS3-P0…P6`.

**Order.** A thin vertical slice comes first:
- Phase 14 projects canonical records into the domain tables.
- Phase 15 runs one deterministic module end to end: snapshot, pinned run, manifest, results
  and lineage back to the source row.

Breadth follows:
- Phase 16: governed rule changes.
- Phase 17: the LCR/HQLA prototype.
- Phase 18: extension points and WS3 verification.

**Parallel build.** In every phase, the backend lands the OpenAPI contract for that phase
first: Pydantic schemas plus declared routes and permissions, exported to `openapi.json`. The
frontend starts from `npm run gen:api` against that commit. The FE tasks need no hand-written
payload types, and any mock is labelled as one (WS3 Definition of Done).

**Methodology.** The client's real methodology is unknown (Data Model §11). Every finance
parameter is therefore data in a versioned rule:
- bucket boundaries;
- cash-flow direction;
- HQLA eligibility and haircuts;
- run-off and inflow rates.

The seeded versions are **ILLUSTRATIVE / PROVISIONAL** (D-11, D-35). Modules sit behind the
pluggable interface of D-31. No output is labelled regulatory, validated or comparable to
Moody's.

Constraining decisions:
- **D-01** Feature 03 only adds tables. No WS1 or WS2 table or column changes.
- **D-02** Downgrades are tested, and they refuse while non-seed rows exist. Rows hide by
  status and are never deleted. Every phase sits behind a switch:
  - backend: `DOMAIN_PROJECTION_ENABLED`, `CALCULATIONS_ENABLED`, `RULE_CHANGE_ENABLED`;
  - frontend: `NEXT_PUBLIC_DOMAIN_UI` and `NEXT_PUBLIC_CALCULATIONS_UI`. Turning these off
    brings back the "planned" placeholders.
- **D-03** Each phase is built in its own worktree, with a DB copy and a paired artifact
  directory.
- **D-04** Fail closed.
  - Each endpoint names its permission (table below).
  - Every domain and calculation table carries `tenant_id` and is read through
    `ScopedRepository`.
  - `canonical_record` has no `tenant_id` column, so the projection copies the tenant from
    `import_job`, and a same-tenant insert trigger backs this up.
- **D-05** The WS plan endpoint names are canonical. The endpoints added alongside them are
  marked "added" in the table.
- **D-06** Enums are UPPERCASE:
  - `calculation_run.status` uses the Data Model §9 set;
  - `rule_version.status` uses the §9 set plus `REJECTED`.

  Each object has one transition table in code, mirrored by a DB trigger.
- **D-07** Error envelope. **D-10/D-24 §4** Every WS3 mutation and every refused WS3 write is
  audited.
- **D-11** WS3 seeds are tagged and PROVISIONAL: permissions, instrument types, modules, rule
  versions and the assumption set.
- **D-13** New settings that matter in a deployment are validated at startup.
- **D-14** Calculations read only data that was attested at upload, so non-production stays
  synthetic.
- **D-19 (spirit)** Rule parameters are typed data from a closed list of kinds. There are no
  formulas, expressions or user code.
- **D-22** This feature delivers the projection into the WS3 domain tables and the
  cross-import `dataset_snapshot`. **D-27** A snapshot pins `import_manifest` rows.
  **D-28** (active) The snapshot gate reads `reconciliation_run.outcome`.
- **D-26** The backend suite stays under 50 tests. It has 49 now. Every phase states its
  budget below.
- **New, active:** D-29 (projection), D-30 (dataset snapshot), D-31 (calculation framework),
  D-32 (rule, module and assumption registry), D-33 (classification of results), D-34 (WS3
  permissions), D-35 (illustrative methodology seeds).

Pre-plan findings (codebase at master `eebcaf6`):
- **Modules.** `backend/app/domain`, `rules`, `calculations` and `lineage` are empty 7-file
  stubs (e.g. `domain/models.py`, `calculations/service.py`) and are not mounted. The
  migration head is `0016_reconciliation_and_drift`.
- **Reuse rather than rebuild:**
  - `canonical_record`: typed-string `payload` and `payload_sha256`.
  - `record_lineage`: canonical → source record.
  - `import_manifest`: append-only, unique per import, conservation CHECK.
  - `reconciliation_run.outcome`.
  - The seeded schemas (0007:58-108):
    - `instrument` v1 is ACTIVE, and its `maturity_date` is **STRING free text**. v2, a DRAFT,
      types it as DATE.
    - `position` v1.
    - `cashflow_event` v1 has **no direction field**, and every sample amount is positive.
  - The seeded `MODULE "liquidity_gap (illustrative)"` dependents (0007:139-140).
  - The synthetic samples: instrument CSV, position XLSX and cashflow_event JSON. They have
    four currencies and the types LOAN, DEPOSIT and BOND.
  - `ScopedRepository`, `require_permission`, `audit(...)` and `mask_model`.
  - Exact Decimal contexts already exist in three places (`reconciliation/service.py:49`,
    `mapping/transforms/engine.py:167`, `mapping/service.py:1218`). WS3 adds one shared
    `core/decimals.py` for calculations and does not refactor WS2.
  - Frontend: `startLoad`/`LoadFailed`, `StatusPill`, `MaskedValue`, `AuditTimeline` and
    `RequirePermission`.
- **The verification tests are generic.** Three existing tests iterate every protected
  route:
  - `test_ws1_verification.py::test_route_by_role_authorization_matrix`;
  - `::test_another_tenant_sees_and_changes_nothing_here`;
  - `::test_every_mutating_route_is_listed_and_emits_an_audit_event`.

  WS3 routes join them once their ids are added to `ids` and their actions to `AUDITED`, at
  no test cost.
- **Frontend.** `/domain` and `/calculations` are `planned` entries
  (`frontend/src/config/navigation.ts:55-70`), rendered by the `[workspace]` placeholder.
- **Test budget.** There are 49 backend test functions and none is parametrised, so the suite
  is at the D-26 cap. Phase 14 frees headroom by folding tests together (BE-14.1).
- **Open gaps found (carried over, see below):**
  - D-25 §1 is *active* but not implemented: no `content_too_large` exists anywhere in
    `backend/`.
  - The Phase 8 review's D2 and D3 are not fixed.
  - `/schemas` stays loading forever on a non-403 error (`schemas/page.tsx:27-34`: it toasts,
    and the state stays `loading`). `/schemas/proposals/new` does the same when the list
    fails (`proposals/new/page.tsx:37-44,115`).
- **Still open under Feature 01 (not prerequisites):** FE-2.2, BE-2.15, BE-2.16 and FE-2.5.

### Carried over from Feature 02 (placed in Phase 14)

These are open items from the Phase 7 and Phase 8 reviews. They are independent of WS3, so
**BE-14.6 and FE-14.3 may ship as a separate small PR ahead of the rest of Phase 14.**

- **Upload hardening:**
  - the D-25 §1 decoded-content cap of 50 MiB (`content_too_large`), which is also the fix
    for Phase 8 D1, XLSX shared-string amplification;
  - D2, the row-number-gap DoS;
  - D3, openpyxl warnings that carry cell values to stderr.

  → BE-14.6.
- **Endless loading** on `/schemas` and `/schemas/proposals/new` (Phase 7 report follow-up)
  → FE-14.3.

### WS3 endpoints and permissions (all under `/api/v1`)

| Endpoint | Permission | Phase | Source |
|---|---|---|---|
| `POST imports/{import_id}/projection` | `domain:project` | 14 | added (D-29) |
| `GET instruments`, `GET instruments/{instrument_id}` | `domain:read` | 14 | WS plan |
| `GET imports/{import_id}/projection` | `domain:read` | 14 | added (Phase 14 build; read-only, same body as the POST) |
| `POST dataset-snapshots` | `calc:run` | 15 | added (D-30) |
| `GET dataset-snapshots`, `GET dataset-snapshots/{snapshot_id}` | `calc:read` | 15 | added |
| `GET calculation-modules` | `rule:read` | 15 | WS plan |
| `POST calculation-runs` | `calc:run` | 15 | WS plan |
| `GET calculation-runs` | `calc:read` | 15 | added (list) |
| `GET calculation-runs/{run_id}`, `GET …/{run_id}/results` | `calc:read` | 15 | WS plan |
| `GET calculation-runs/{run_id}/maturity-ladder` | `calc:read` | 15 | WS plan |
| `GET rules`, `GET rules/{rule_id}` | `rule:read` | 16 | WS plan |
| `POST rules/{rule_id}/versions` | `rule:propose` | 16 | added (D-32) |
| `POST rules/{rule_id}/versions/{version}/submit` | `rule:propose` | 16 | added |
| `POST rules/{rule_id}/versions/{version}/approval` | `rule:approve` | 16 | added |
| `GET assumption-sets`, `GET assumption-sets/{assumption_set_id}` | `rule:read` | 16 | WS plan |
| `GET calculation-runs/{run_id}/liquidity` | `calc:read` | 17 | WS plan |

`GET calculation-runs/{id}/lineage` (the generic lineage graph) belongs to WS4 RO-03. In this
feature, the `results` responses carry the lineage references themselves (D-31 §6).

### Role → permission matrix additions (seed, BE-14.2; PROVISIONAL, D-11, D-34)

`ROLE_PERMISSIONS` becomes WS1 ∪ WS2 ∪ WS3.

| Role | domain:read | domain:project | rule:read | rule:propose | rule:approve | calc:read | calc:run |
|---|---|---|---|---|---|---|---|
| platform_admin | ✓ | | ✓ | | | ✓ | |
| data_steward | ✓ | ✓ | ✓ | | | ✓ | |
| risk_analyst | ✓ | | ✓ | ✓ | | ✓ | ✓ |
| approver | ✓ | | ✓ | | ✓ | ✓ | |
| auditor | ✓ | | ✓ | | | ✓ | |

- The data steward curates the data (projection).
- The risk analyst owns methodology proposals and runs.
- Only the approver activates rule versions (maker-checker).
- platform_admin and auditor read only.

Values are still masked by each role's clearance, with result classification per D-33. The
final role model needs client approval (Arch §10).

### Test budget (D-26)

The suite is at **49** of the 49 allowed (the cap is under 50).

| Phase | Change | Count after |
|---|---|---|
| 14 | −4 by folding (BE-14.1), +1 `test_domain.py` | 46 |
| 15 | +1 `test_calculations.py` | 47 |
| 16 | +1 `test_rules.py` | 48 |
| 17 | +0 (folded into `test_calculations.py`) | 48 |
| 18 | +0 (folded into the WS1 matrix, isolation and audit tests, marked `ws3`) | 48 |

That leaves one slot spare. The count is checked with `pytest --collect-only -q`. Loops inside
one test replace variants (D-26 §2). Frontend unit tests and Playwright are not capped.

### Phase 14 — Domain projection and the instrument catalogue (FC-01, FC-02 data half; WS3-P0, WS3-P1)

Goal: a data steward projects a validated import's accepted canonical records into immutable
domain versions: `instrument`/`instrument_version`, `position`/`position_version` and
`cashflow_event`. Each version keeps lineage to its canonical record. Everyone with
`domain:read` can browse the instrument catalogue. The carried-over WS2 hardening also lands
in this phase.

- [x] **BE-14.1** Test headroom (D-26). Fold four test functions into existing ones, keeping
  every assertion:
  - `test_profiling.py::test_the_extra_column_fixture_reports_in_contract_false` and
    `::test_profile_values_are_masked_by_role` go into
    `::test_golden_profile_of_the_synthetic_instrument_csv`;
  - `test_imports.py::test_the_synthetic_attestation_is_required` goes into
    `::test_over_the_byte_limit_is_413_with_nothing_left_behind`;
  - `test_mappings.py::test_validate_dry_run` goes into `::test_create_list_get_and_versions`.

  An equivalent fold is fine if it is named in the report.
  - *Done-when:*
    - `pytest --collect-only -q` reports 45 tests;
    - the diff shows the folded bodies moved, with no assertion removed (the engineer lists
      them, and the architect checks the diff);
    - the full suite passes.
- [x] **BE-14.2** Migrations and models in `domain`, plus a tagged seed:
  - `domain_projection`: one row per import, append-only, unique `import_job_id`, with
    `projector_version` and a value-free `summary`;
  - `instrument_type`: a seeded PROVISIONAL vocabulary (LOAN, DEPOSIT, BOND);
  - `instrument`: stable id, unique per tenant, source system and business key;
  - `instrument_version`;
  - `instrument_attribute_definition` and `instrument_attribute_value`;
  - `position` and `position_version`;
  - `cashflow_event`.

  Every version and event row:
  - carries `tenant_id`, `import_job_id`, a unique `canonical_record_id`,
    `schema_version_id` and `payload_sha256`;
  - stores amounts as exact `NUMERIC` and dates as DATE.

  The tables are append-only, including TRUNCATE. A same-tenant insert trigger checks the
  import's tenant. CHECKs cover the enums.

  The WS3 permissions (matrix above) and the role grants are a tagged seed migration.
  - *Done-when:*
    - the 0016 → head → 0016 → head round trip passes;
    - `test_domain.py` (new, the phase's one test) proves UPDATE, DELETE and TRUNCATE are
      refused on every version table and that the downgrade refuses while a projection
      exists;
    - `test_domain.py` compares the seeded `role_permission` join with a new
      `WS3_ROLE_PERMISSIONS` in `governance/constants.py`, and that equals the table above
      (checked by the architect).
- [x] **BE-14.3** Projection service, plus `POST /imports/{import_id}/projection`
  (`domain:project`, audited `import.project`, D-29). It accepts:
  - imports with a manifest and status COMPLETED, COMPLETED_WITH_WARNINGS or QUARANTINED
    (accepted rows only);
  - target schema codes `instrument`, `position` or `cashflow_event`.

  It is idempotent: a second call returns the existing projection. Refusals:
  - 409 `projection_unsupported` for another schema;
  - 409 `not_validated`;
  - 409 `domain_projection_disabled` while the switch is off.

  Each refusal is audited DENIED. One version or event row is written per canonical record,
  so the projection conserves rows.

  Fields map through a frozen per-schema spec (`projector_version` 1):
  - a STRING schema field is never parsed into a typed column, so `instrument` v1
    `maturity_date` stays NULL with a `TYPE_NOT_PROJECTED` count;
  - extension-eligible instrument fields go to attribute values;
  - every other field stays reachable in `canonical_record` through lineage.
  - *Done-when:* `test_domain.py` projects the synthetic instrument, position and cashflow
    imports and proves:
    - rows projected = `accepted_count` for each import;
    - a second call writes nothing;
    - every row's `canonical_record_id` resolves to a `record_lineage` row of that import;
    - no stored payload value is a float (JSON type and Python type check);
    - each refusal code appears with its DENIED audit event;
    - with the switch off, a 409 and nothing written.
- [x] **BE-14.4** `GET /instruments` (paginated; filter by type, currency, source and import)
  and `GET /instruments/{instrument_id}`. The detail holds the versions, each with:
  - its attributes;
  - its import id;
  - its source row ordinal;
  - its canonical record id;
  - its cash flows from projected `cashflow_event` rows that match the instrument business
    key in the same tenant.

  Values are masked by classification from the pinned `schema_field`. Another tenant's id
  gets the same 404 as a random id.
  - *Done-when:*
    - the WS1 matrix and isolation tests list both routes, with 2xx for every role;
    - `test_domain.py` asserts `notional` is masked for `risk_analyst` and clear for
      `data_steward`, and the version → source row chain on one instrument.
- [x] **BE-14.5** Contract and reversibility: the `openapi.json` export, the
  `DOMAIN_PROJECTION_ENABLED` setting, AUDITED entries, and ARCHITECTURE.md module rows
  updated once D-29 is active.
  - *Done-when:*
    - the CI OpenAPI drift check passes;
    - the WS1 audit-completeness test lists `POST /imports/{import_id}/projection`.
- [x] **BE-14.6** *(Carried over from Phase 8, may ship as a separate PR)* Upload hardening:
  - (a) D-25 §1: a running total of decoded characters for every format, refused above
    50 MiB with 422 `content_too_large` and audited DENIED. It is a hard ceiling;
    `IMPORT_MAX_DECODED_CHARS` may only lower it.
  - (b) An XLSX row number above 1,048,576 is `xlsx_invalid`. Collection stops after
    `max_rows` consecutive blank rows.
  - (c) openpyxl warnings are suppressed on the request path, and they are errors under
    pytest (`filterwarnings` in `pyproject.toml`).
  - *Done-when:* everything below is folded into existing tests (+0):
    - `test_adapters.py::test_bad_files_and_limits_are_refused_with_their_own_code` gains a
      shared-string workbook refused as `content_too_large` and a `<row r="2000000000">`
      workbook refused as `xlsx_invalid` in under 2 s;
    - `test_import_no_leak.py::test_no_sentinel_in_logs_error_bodies_or_audit` gains an
      out-of-range-date XLSX whose sentinel never reaches stderr;
    - the suite runs with openpyxl warnings as errors.
  - *Built (hardening PR):* all three folded checks pass, and the suite stays at 49 tests. A
    long blank run is not a hard stop. Once it passes `max_rows`, nothing more of it is kept,
    and a later data row is refused as `too_many_rows`, so no data row is ever dropped
    silently (D-20). The XLSX reader now uses openpyxl's private `WorkSheetParser`, still
    pinned `<3.2`, instead of the old `_get_row` patch. Still open from the Phase 8 review: D4
    (a row number that does not increase is skipped) and D5 (an out-of-range date becomes
    `#VALUE!`).
- [x] **FE-14.1** Financial Domain workspace (`/domain`, gated on `domain:read`; the
  `planned` entry is removed; `NEXT_PUBLIC_DOMAIN_UI=off` restores it):
  - an instrument catalogue with filters;
  - an instrument detail with versions, attributes and cash flows;
  - `MaskedValue` for masked amounts;
  - a lineage link per version to its import (`/ingestion/imports/{id}`) and source row
    ordinal;
  - a "Provisional / illustrative" badge on seeded vocabularies.
  - *Done-when:*
    - a Playwright test projects the synthetic instrument import, then browses the catalogue
      to an instrument and its import, with no hand-written payload types (grep);
    - `risk_analyst` sees `notional` as "masked".
- [x] **FE-14.2** A "Project to domain" action on the import detail page (gated on
  `domain:project`, shown only for a validated import of a supported schema). It shows the
  projection summary: counts and `TYPE_NOT_PROJECTED`.
  - *Done-when:*
    - a Playwright test: the steward projects, and the summary counts equal the manifest's
      accepted count;
    - a second click shows the same projection;
    - `auditor` has no button and gets a backend 403 on a direct POST.
- [x] **FE-14.3** *(Carried over from Phase 7, may ship with BE-14.6)* `/schemas` and
  `/schemas/proposals/new` move to `startLoad`/`LoadFailed`, so a failed load shows "Try
  again" instead of loading forever.
  - *Done-when:*
    - vitest covers both pages' failure → LoadFailed → retry → ready path;
    - a Playwright test with `/api/v1/schemas` forced to 500 once shows "Try again", and one
      click renders the list.

*Test budget:* 49 → 45 (BE-14.1) → 46 (+`test_domain.py`). BE-14.6 is +0.
*Reversibility:* additive tables and tagged seeds. The downgrade refuses while any projection
exists. `DOMAIN_PROJECTION_ENABLED=false` stops new projections (409, audited), and existing
domain rows stay readable. `NEXT_PUBLIC_DOMAIN_UI=off` restores the "planned" placeholder.
BE-14.6 only tightens upload limits, and reverting it is a code revert under a new decision
(D-25 §1 "only ever tightens").

### Phase 15 — Snapshot, pinned run and one deterministic module end to end (FC-04, FC-07 registry read, FC-08 pinning; WS3-P2/P3/P4 thin slice)

Goal: a risk analyst pins a dataset snapshot (projected imports) and runs the illustrative
**maturity ladder v1** module for a reporting date. The run records an immutable manifest of
every pinned version and its hash. Results per currency and bucket trace through intermediate
values to cash-flow events, canonical records and source rows. Re-running with the same pins
gives byte-identical results. All configuration in this phase is seeded (ACTIVE by seed,
PROVISIONAL). Governed change comes in Phase 16.

- [ ] **BE-15.1** Migrations and models for the `rules` registry (D-32):
  - `calculation_module` and `module_version`: `implementation_status`
    AVAILABLE/PLANNED, declared input and output contracts, `engine_version`;
  - `rule_definition`: category, target module, `parameter_schema` from the closed kinds;
  - `rule_version`: immutable `parameters`, `parameters_sha256`, the D-06 status set, author
    and approver;
  - `rule_set` and `rule_set_member`: content-addressed;
  - `maturity_bucket_definition`: immutable rows materialised from a bucket rule version;
  - `assumption_set`: versioned, with provenance and status.

  It also has a tagged seed (D-35): module `maturity_ladder` v1 AVAILABLE, plus ACTIVE
  ILLUSTRATIVE rule versions:
  - `maturity_buckets`;
  - `cashflow_direction` (instrument type → INFLOW/OUTFLOW);
  - `past_due_treatment`.

  The seed also adds an ACTIVE `baseline (illustrative)` assumption set, empty in v1.
  - *Done-when:*
    - the round trip passes;
    - `test_calculations.py` (new, the phase's one test) proves rule and module versions
      outside DRAFT are immutable (UPDATE, DELETE and TRUNCATE refused);
    - at most one ACTIVE version exists per rule definition (DB);
    - the seed rows are tagged `provisional` and illustrative;
    - the downgrade refuses while a non-seed row exists.
- [ ] **BE-15.2** Migrations and models in `calculations` (D-30, D-31):
  - `dataset_snapshot` and `dataset_snapshot_member`: per member, the import, its
    `manifest_sha256`, the projection id and the schema code; plus `content_sha256`;
  - `calculation_run`: the Data Model §9 statuses, `reporting_date`, the module version,
    `rule_set_id`, the assumption set, the snapshot, `engine_version`, `manifest` plus
    `manifest_sha256`, `result_sha256`, `idempotency_key`, and start and end times;
  - `calculation_input_ref`;
  - `liquidity_gap_result`;
  - `intermediate_value`;
  - `calculation_warning`.

  A run's status moves only RUNNING → one terminal status, once, by trigger. Results,
  intermediates and warnings can be inserted only while the run is RUNNING, and are
  append-only afterwards. Every table carries `tenant_id`.
  - *Done-when:*
    - the round trip passes;
    - `test_calculations.py` proves that changing a terminal run, or inserting a result
      under one, is refused, and that the downgrade refuses while a run exists.
- [ ] **BE-15.3** `POST /dataset-snapshots`, `GET /dataset-snapshots` and
  `GET /dataset-snapshots/{snapshot_id}` (D-30).
  - A snapshot takes at most one projected import per schema code.
  - It refuses with:
    - 409 `not_projected`;
    - 409 `reconciliation_broken` (D-28 BROKEN);
    - 409 `duplicate_schema`;
    - 409 `ambiguous_business_key`.
  - It is content-addressed, so the same member set returns the existing snapshot.
  - It is audited `dataset_snapshot.create`, and its refusals are audited DENIED.
  - *Done-when:* `test_calculations.py` asserts:
    - the same members give the same snapshot id;
    - each refusal code;
    - `content_sha256` equals the hash recomputed from the member manifests.
- [ ] **BE-15.4** The module interface and engine (D-31), maturity ladder v1, and
  `POST /calculation-runs` (`calc:run`, an `Idempotency-Key` header,
  `CALCULATIONS_ENABLED`).
  - **Module.** Modules live in `app/calculations/modules/`. A module is a pure function over
    frozen typed inputs and pinned parameters. It does no I/O and reads no clock or
    randomness. It uses no float and does Decimal arithmetic only, under a module-declared
    context that traps inexact results unless an explicit quantize is declared.
  - **Engine.** The engine:
    1. resolves the pins (the ACTIVE rule versions → a content-addressed `rule_set`);
    2. writes `calculation_input_ref` rows and the manifest;
    3. runs the module;
    4. runs the post-checks: every in-scope cash flow is bucketed once or excluded with a
       warning code, and the bucket totals equal the sum of their contributions;
    5. sets SUCCEEDED, SUCCEEDED_WITH_WARNINGS, FAILED (a post-check failed) or BLOCKED (the
       input contract was not met).
  - **Ladder v1.** Per currency, with no FX: buckets by calendar days after
    `reporting_date`, direction by instrument type, and past-due cash flows per the rule.
    Unmapped types and instruments missing from the snapshot are excluded with warnings,
    never silently.
  - **Refusals and audit.** Refusals are 409 `module_not_available`, 409
    `calculations_disabled` and 422 for a bad body. The run is audited `calculation_run.create`.
  - *Done-when:* `test_calculations.py` runs the synthetic snapshot and asserts:
    - the hand-computed golden ladder for an engineering fixture with boundary cases: due on
      a bucket edge, due on the reporting date, past due, a zero amount, an unmapped type and
      an instrument missing from the snapshot;
    - the status and the warning codes;
    - a second run with identical pins has a new run id with the same `manifest_sha256` and
      `result_sha256`;
    - an `Idempotency-Key` replay returns the original run;
    - no float anywhere in `app/calculations/` (source grep, as BE-11.2);
    - `CALCULATIONS_ENABLED=false` gives 409 and writes nothing.
- [ ] **BE-15.5** Reads:
  - `GET /calculation-modules`;
  - `GET /calculation-runs` (paginated, filter by module and status);
  - `GET /calculation-runs/{run_id}`: the manifest with every pinned id and version;
  - `GET …/results`: intermediate values with their lineage refs (cash-flow event →
    canonical record → import and source row);
  - `GET …/maturity-ladder`: per currency and bucket, inflows, outflows, net and cumulative
    gap.

  Every decimal is exact text (`format(d, "f")`). Masking follows D-33.
  - *Done-when:*
    - the WS1 matrix lists every route with 2xx for every role;
    - `test_calculations.py` asserts one bucket's total = the sum of its `results`
      contributions, and that each contribution resolves to a `source_record` of a member
      import;
    - contributions are masked for `risk_analyst` and aggregates are clear (D-33).
- [ ] **FE-15.1** Calculations workspace (`/calculations`, gated on `calc:read`;
  `NEXT_PUBLIC_CALCULATIONS_UI=off` restores the placeholder):
  - a run list;
  - a "New run" panel (gated on `calc:run`). It picks one projected import per schema to
    build or reuse a snapshot, then a module (AVAILABLE only), then a reporting date.
    **Before submitting**, it shows the versions that will be pinned: rule versions,
    assumption set and engine.
  - an "Illustrative prototype — not regulatory" banner.
  - *Done-when:*
    - a Playwright test: `risk_analyst` builds a snapshot and submits a run that reaches
      SUCCEEDED or SUCCEEDED_WITH_WARNINGS;
    - `approver` has no New-run button and gets a backend 403 on a direct POST;
    - vitest covers the panel's state machine, including the 409 refusal toasts with the
      correlation ID.
- [ ] **FE-15.2** Run detail:
  - a manifest panel with every pinned id, version and hash;
  - a warnings table;
  - a maturity ladder table per currency (inflows, outflows, net and cumulative gap);
  - a contributions drill-down from a bucket to its cash-flow events, then to the
    instrument, the import and the source row.

  A run-status pill covers every `calculation_run` status (an enum test against
  `openapi.json`).
  - *Done-when:*
    - a Playwright test opens the run, checks one bucket's displayed total against the sum of
      its drill-down rows, and follows a row to its import;
    - the pill-enum vitest passes.

*Test budget:* 46 → 47 (+`test_calculations.py`). The matrix, isolation and audit coverage
comes from the existing WS1 tests (+0).
*Reversibility:* additive tables and a tagged seed. Downgrades refuse while non-seed rows
exist. `CALCULATIONS_ENABLED=false` refuses new snapshots and runs (409, audited), and
existing runs stay readable. The frontend switch restores the placeholder. Runs are never
updated or deleted (Arch §6.2 step 7).

### Phase 16 — Governed rule changes, rule and module registry, assumption sets (FC-07, FC-08; WS3-P2)

Goal: methodology parameters change only through versioned, maker-checker-approved rule
versions. A new run pins the new ACTIVE versions. An old run, and a re-run of it, stays
reproducible on its original pins. Rules, modules and assumption sets can be browsed.

- [ ] **BE-16.1** The parameter-kind allowlist (D-32):
  - `DECIMAL(min, max, scale)`;
  - `INTEGER(min, max)`;
  - `ENUM(values)`;
  - `DAY_BUCKETS`: ordered, contiguous half-open intervals with an open last bucket;
  - `ENUM_MAP(key_vocabulary → value_enum)`;
  - `DECIMAL_MAP(key_vocabulary → DECIMAL)`.

  Each rule definition's `parameter_schema` is checked against it, and so is every proposed
  version. There are no expressions and no free-form JSON.
  - *Done-when:* `test_rules.py` (new, the phase's one test) loops over valid and invalid
    parameter sets for each kind, and every invalid one gets 422 `invalid_parameters` with a
    value-free message.
- [ ] **BE-16.2** `GET /rules` and `GET /rules/{rule_id}` (versions, status, author,
  approver, and the runs that pin each version). Also:
  - `POST /rules/{rule_id}/versions`: a DRAFT copied from the ACTIVE version plus a
    parameter patch; `no_change` is refused;
  - `…/{version}/submit`;
  - `…/{version}/approval` (APPROVE activates and SUPERSEDES the previous ACTIVE version in
    one transaction; REJECT).

  The approver must not be the author (409 `self_approval`). Everything is audited (`rule.*`),
  refusals included. `RULE_CHANGE_ENABLED=false` gives 409 `rule_change_disabled`.
  - *Done-when:* `test_rules.py` asserts:
    - the author cannot approve;
    - after approval there is exactly one ACTIVE version and the old one is SUPERSEDED;
    - DRAFT → ACTIVE without submit is refused;
    - the switch-off 409;
    - the WS1 audit-completeness test lists the three POST routes.
- [ ] **BE-16.3** Pinning across rule changes. A run submitted after an approval pins the
  new version, in a new `rule_set`. `POST /calculation-runs` with `rerun_of: run_id`
  re-executes on the original run's exact pins, even when they are SUPERSEDED. The new run
  records `rerun_of`.
  - *Done-when:* `test_rules.py` changes the bucket rule, then asserts:
    - the new run differs in `rule_set_id` and `result_sha256`;
    - the old run's rows are unchanged (row hash before and after);
    - a re-run of the old run has the old run's `manifest_sha256` and `result_sha256`.
- [ ] **BE-16.4** `GET /assumption-sets` and `GET /assumption-sets/{assumption_set_id}`
  (versions, provenance, owner, effective dates, status). They are read-only in this
  feature; authoring is deferred.
  - *Done-when:* the WS1 matrix lists both with 2xx for every role, and the run manifest's
    `assumption_set_id` resolves through the detail route.
- [ ] **FE-16.1** Registry pages under `/domain`:
  - rules: list and detail, with a version history, a parameter diff between versions, and
    "used by N runs";
  - modules: AVAILABLE and PLANNED shown distinctly, never as ready;
  - assumption sets.
  - *Done-when:* a Playwright test opens the seeded `maturity_buckets` rule and sees its
    ILLUSTRATIVE badge and version history; a vitest snapshot shows the PLANNED-module
    rendering.
- [ ] **FE-16.2** Propose → submit → approve flow for a rule version: a typed parameter
  editor per kind, with client-side checks mirroring BE-16.1 (cosmetic only). The approval
  dialog has a required comment, and the approve button is hidden from the author.
  - *Done-when:*
    - a Playwright test: `risk_analyst` proposes new bucket boundaries and submits;
    - `approver` approves;
    - a new run shows the new version in its manifest, and the old run still shows the old
      version;
    - the author sees no Approve button and gets a backend 409 `self_approval` on a direct
      POST.

*Test budget:* 47 → 48 (+`test_rules.py`).
*Reversibility:* no new tables beyond Phase 15. If a lifecycle trigger is needed, it ships as
a migration with a tested downgrade. `RULE_CHANGE_ENABLED=false` freezes the rules at their
ACTIVE versions: writes get 409 (audited), and runs keep working. Superseded versions are
hidden by status, never deleted.

### Phase 17 — LCR/HQLA prototype module (FC-03; WS3-P4)

Goal: an illustrative `lcr_hqla` v1 module runs over the same snapshot model. Every parameter
lives in ILLUSTRATIVE rule versions:
- HQLA eligibility, level and haircut by instrument type;
- outflow run-off by instrument type;
- inflow rate, horizon days and inflow cap.

It shows HQLA components, outflows, capped inflows and a per-currency ratio. It is labelled a
prototype throughout.

- [ ] **BE-17.1** Migration: `hqla_asset_result` and `lcr_result` (append-only, with the run
  guards of BE-15.2). A tagged seed adds module `lcr_hqla` v1 AVAILABLE and its ACTIVE
  ILLUSTRATIVE rule versions (D-35).
  - *Done-when:* the round trip passes, and the Phase 15 guard assertions in
    `test_calculations.py` also loop over the two new tables (+0).
- [ ] **BE-17.2** Module `lcr_hqla` v1 (D-31 interface). Inputs: position versions (balance
  as the value, PROVISIONAL), instrument versions (type) and cash flows within the horizon.
  - HQLA = Σ eligible balance × (1 − haircut).
  - Outflows = Σ balance × run-off.
  - Inflows = min(Σ inflow × rate, cap × outflows).
  - Ratio = HQLA / (outflows − inflows), quantized to 4 dp ROUND_HALF_EVEN (PROVISIONAL).
  - A zero or negative denominator gives a NULL ratio plus the warning `LCR_DENOMINATOR_ZERO`.

  Level caps (L2A/L2B) are not applied; this is a documented limitation and a warning.
  - *Done-when:* the golden loop in `test_calculations.py` gains a hand-computed `lcr_hqla`
    engineering fixture covering:
    - an ineligible asset;
    - the inflow cap binding;
    - a zero denominator;
    - mixed currencies.

    A rerun gives an identical `result_sha256` (+0).
- [ ] **BE-17.3** `GET /calculation-runs/{run_id}/liquidity`: per currency, HQLA by level
  with its asset rows (inclusion or exclusion reason), outflows, inflows before and after
  the cap, and the ratio. Exact decimal text, masking per D-33.
  - *Done-when:* the WS1 matrix lists it with 2xx for every role, and
    `test_calculations.py` asserts that the HQLA total equals the sum of the included asset
    rows (+0).
- [ ] **FE-17.1** Liquidity view in the run detail: HQLA components (included and excluded,
  with reasons), outflows, inflows with the applied cap shown, the ratio or "not computable"
  with the warning, and the prototype banner.
  - *Done-when:* a Playwright test runs `lcr_hqla` on the synthetic snapshot and checks the
    displayed HQLA total against its rows; a vitest covers the NULL-ratio rendering.

*Test budget:* 48 → 48 (folded).
*Reversibility:* additive tables and a tagged seed. The downgrade refuses while LCR results
exist. Retiring the module version, which hides it by status, removes it from "New run", and
existing runs stay readable.

### Phase 18 — Extension points, domain validation pack and WS3 verification (FC-05, FC-06, WS3-P5/P6, WS3 Definition of Done)

Goal: ALM/IRRBB, collateral and capital exist as declared contracts only, so they are never
runnable or presented as ready. The calculation limitations and the golden cases are
documented, awaiting a named domain expert. WS3 is proven by the verification suite and by
Playwright in CI.

- [ ] **BE-18.1** A tagged seed adds `alm_irrbb`, `collateral` and `capital` module versions
  with `implementation_status=PLANNED` and declared input and output contracts.
  `POST /calculation-runs` refuses them with 409 `module_not_available` (audited).
  `docs/CALCULATION_MODULES.md` documents:
  - the module interface;
  - the input and output contracts;
  - the planned contracts;
  - the decimal and rounding policy;
  - each module's limitations;
  - the "not regulatory, no Moody's parity" statement.
  - *Done-when:* the WS1 audit test shows a DENIED event for a PLANNED module run; the doc
    exists and lists every module row in the seed (grep).
- [ ] **BE-18.2** The domain validation pack: `backend/app/calculations/golden/*.json` holds
  every engineering fixture with its expected outputs and the status `ENGINEERING — awaiting
  domain-expert approval`. A README names who must approve them, along with the tolerance
  policy (exact for v1, per metric, per Data Model §7).
  - *Done-when:* the `test_calculations.py` golden loop reads the files rather than inline
    literals, and every file carries the status field.
- [ ] **BE-18.3** The WS3 verification marker `ws3`, as a CI step printing the matrix. It is
  applied to:
  - the WS1 matrix, isolation and audit tests (now covering every WS3 route and resource);
  - `test_domain.py`, `test_calculations.py` and `test_rules.py`;
  - the migration round trip.

  `docs/RETENTION.md` gains PROVISIONAL rows for every WS3 table.
  - *Done-when:*
    - `pytest -m ws3` runs green in CI on the Phase 18 PR, with the matrix in the log;
    - every WS3 table appears in RETENTION.md;
    - `pytest --collect-only -q` reports 48 or fewer.
- [ ] **FE-18.1** PLANNED modules appear in the registry and the New-run picker as disabled
  "Planned — extension point" entries with their declared contracts. They are never
  selectable.
  - *Done-when:* a vitest and a Playwright check show they cannot be selected, and a direct
    POST is refused by the backend.
- [ ] **FE-18.2** Playwright WS3 smoke:
  1. the steward projects the instrument, position and cashflow imports;
  2. the analyst builds a snapshot and runs the maturity ladder and LCR;
  3. a drill-down to a source row;
  4. the analyst proposes a bucket change and the approver approves;
  5. a new run differs, and the old run is unchanged.
  - *Done-when:* the `e2e` job passes in CI with the WS3 specs, and the `playwright-report`
    artifact exists.

*Test budget:* 48 → 48 (marker and folds only).
*Reversibility:* a seed, docs and tests. PLANNED rows are hidden by retiring them.

### Deferred (out of scope for Feature 03)

- **Real methodology and approved golden cases.** Bucket sets, direction rules, HQLA levels,
  haircuts, run-off and inflow rates, rounding and tolerances all wait for the client's named
  financial owner (Arch §12, §17 release gates). The seeded values are ILLUSTRATIVE.
- **Regulatory LCR features:** L2A/L2B caps, significant-currency rules, a consolidated
  reporting-currency LCR, and regulatory templates. FX and market data come first, and there
  are no regulatory claims.
- **FX conversion and `market_observation` feeds:** ladders and ratios are per currency.
  Market data needs provenance and a feed (FC-08 has its interface only).
- **Assumption-set authoring** (create, propose and approve versions). The sets are seeded
  and read-only, and runs already pin them.
- **Schedule generation from terms** (`contractual_term`, `cashflow_schedule`,
  `cashflow_component`, day-count and business-day calendars): this is methodology. Cash
  flows come from source data only.
- **Reference entities** `legal_entity`, `counterparty`, `account` and `reference_data_item`:
  they are business-key strings on versions until reference data exists. WS2's
  reference-data checks wait for them too.
- **ALM/IRRBB, collateral and capital calculations** (FC-05, FC-06): contracts only.
  `collateral_asset`, `collateral_link`, `collateral_result` and `capital_result` tables are
  not created yet (D-01 lets them be added later).
- **Effective-dated "current" domain views and overlapping-version rules:** versions are
  per-import snapshots, and time is chosen by pinning a snapshot (D-29).
- **Merging several imports of one schema into a snapshot**, and source-of-truth precedence
  across sources (Data Model §11).
- **Async runs, queues and retries** (`workflow_job`), scenarios, the generic lineage graph
  (`lineage_node`/`lineage_edge`, `GET …/lineage`), benchmark comparison and reports:
  WS4 RO-01…RO-05.
- **Projecting instrument v2 or other DRAFT schema versions:** only the ACTIVE versions the
  projector spec knows. A new schema version needs a new `projector_version`.
- **AI explanations of runs:** WS5 only. Never on the calculation path.
- **Row scopes below tenant** (legal entity, portfolio): these wait for the client role
  model.
- **Exports of results:** these need their own permission and CSV-injection safe output
  (WS4 reports).
