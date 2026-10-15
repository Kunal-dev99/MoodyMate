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

Kickoff: `docs/prompts/kickoffs/01-ws1-foundation.md` · Status: **APPROVED 2026-10-15 — Phase 1 merged (PR #2); Phase 2 merged (PR #3); Phase 2 hardening merged (PR #4); PR #5 merged; rotation follow-up in progress (BE-2.15, BE-2.16, FE-2.5)**

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

- [ ] **BE-3.1** `audit_event` table plus a DB trigger rejecting `UPDATE`/`DELETE`
  (D-10), with a tested downgrade.
  - *Done-when:* a test attempting `UPDATE` and `DELETE` on `audit_event` gets a DB error;
    the round trip passes.
- [ ] **BE-3.2** `governance.service.audit(...)`: records actor, action, resource
  type/id, outcome, correlation ID, safe metadata and approval reference, in the same
  transaction as the action it records. Denied (403) requests are audited too.
  - *Done-when:* tests show a successful action and a denied action each produce exactly
    one event with the correct correlation ID.
- [ ] **BE-3.3** `GET /api/v1/audit-events?resource_id=&resource_type=&actor=&from=&to=`,
  paginated, requiring `audit:read`. Reading audit is itself audited.
  - *Done-when:* tests cover filters, pagination, 403 for `risk_analyst`, and the
    self-audit event.
- [ ] **BE-3.4** `data_classification` and `access_policy` tables. Seeded classifications
  are `public`, `internal`, `confidential`, `restricted`, marked PROVISIONAL pending the
  client (D-11).
  - *Done-when:* the round trip passes and seed classifications exist after upgrade.
- [ ] **BE-3.5** Masking utility: response serialisation masks fields whose
  classification exceeds the principal's allowance, and the log filter (BE-1.5) uses the
  same classification.
  - *Done-when:* a test proves the same record returns masked to `risk_analyst` and clear
    to `data_steward` for a `restricted` field.
- [ ] **FE-3.1** Audit timeline component (resource-scoped, filterable, paginated) and
  an Audit page.
  - *Done-when:* the timeline renders events created in BE-3.2 tests on the seeded
    environment, with the correlation ID visible.
- [ ] **FE-3.2** `MaskedValue` component with an explicit "masked by policy" state.
  - *Done-when:* a masked API value renders as masked text with a tooltip, never as an
    empty string.

*Reversibility:* tables are additive; masking ships behind setting `MASKING_ENABLED`
(default `true`), which, if set to `false`, restores unmasked output in `ENV=local` only.

### Phase 4 — Schema registry (DG-01)

Goal: versioned canonical schemas with owners, lifecycle and compatibility, which later
workstreams bind to.

- [ ] **BE-4.1** Tables `schema_definition`, `schema_version` (immutable; content SHA-256;
  status per D-06), `schema_field` (type, required, unit, scale, classification,
  extension eligibility), `schema_dependency`, with a tested downgrade.
  - *Done-when:* the round trip passes; a test proves an UPDATE to an ACTIVE version's
    contract is rejected (DB trigger or service guard plus test).
- [ ] **BE-4.2** Seed PROVISIONAL synthetic schemas `instrument`, `position`,
  `cashflow_event` from Data Model §4, explicitly labelled illustrative.
  - *Done-when:* `GET /schemas` lists three schemas, each with status and a
    `provisional: true` marker.
- [ ] **BE-4.3** `GET /api/v1/schemas`, `GET /schemas/{id}/versions`,
  `GET /schemas/{id}/diff?from=&to=`, requiring `schema:read`. The diff classifies each
  change as `ADDITIVE | COMPATIBLE | BREAKING`.
  - *Done-when:* diff tests cover an added optional field (additive), a widened scale
    (compatible), and a removed field, a newly required field and a type change
    (breaking).
- [ ] **FE-4.1** Schema catalogue: owner, status pill, effective version, dependents.
  - *Done-when:* the catalogue shows the three seeded schemas with platform status pills
    (not the bundle's draft/published set).
- [ ] **FE-4.2** Schema detail and version compare: side-by-side diff with a
  classification badge per change.
  - *Done-when:* comparing two seeded versions renders every change from the BE-4.3
    fixture with the correct badge.

*Reversibility:* additive tables; seed downgrade removes only migration-tagged rows.
Nothing consumes the registry yet.

### Phase 5 — Controlled schema change (DG-05)

Goal: no schema change goes live without impact analysis and approval by someone other
than the proposer.

- [ ] **BE-5.1** `schema_change_proposal` table (status per D-06; stores the diff, impact
  result, regression-evidence reference, approver and rollback reference), with a tested
  downgrade.
  - *Done-when:* the round trip passes.
- [ ] **BE-5.2** `POST /schema-change-proposals`, `GET /schema-change-proposals/{id}`,
  `POST /{id}/impact-analysis` (diff + compatibility + `schema_dependency` walk listing
  affected mappings, rules, modules and reports).
  - *Done-when:* a test proposal that removes a field reports BREAKING and lists the
    seeded dependent.
- [ ] **BE-5.3** `POST /{id}/approval` with maker-checker: the approver must hold
  `schema:approve` and must not be the proposer. BREAKING proposals require recorded
  impact analysis. Approval + activation creates a new ACTIVE `schema_version` and sets
  the previous one to DEPRECATED, in one transaction, audited.
  - *Done-when:* tests cover the proposer self-approving (403), approval without impact
    analysis (409), and a valid approval (new version active, old one deprecated, old
    contract unchanged, audit event present).
- [ ] **BE-5.4** Rollback path: `POST /{id}/revert` creates a new proposal reinstating
  the prior version's contract as a NEW version, not by editing history.
  - *Done-when:* after a revert is approved, the active version's contract hash equals
    the original hash, and the version count increased.
- [ ] **FE-5.1** Proposal workflow: create proposal → run impact analysis → submit
  (blocked until analysis exists) → approver decision view.
  - *Done-when:* manual test notes show the full flow with two seeded users; the Submit
    button stays disabled before analysis, and the backend also returns 409.
- [ ] **FE-5.2** Impact analysis display: compatibility summary and dependent list.
  - *Done-when:* the BREAKING fixture renders a red badge with the dependents listed.

*Reversibility:* the proposal workflow sits behind setting `SCHEMA_CHANGE_ENABLED`; off
means the registry is read-only, which is the Phase 4 behaviour. Activated versions are
reverted only through new versions.

### Phase 6 — Environment controls and WS1 verification (DG-06, WS1 verification)

- [ ] **BE-6.1** Environment guard: `DATA_CLASS=synthetic` is required outside
  production; startup refuses `DATA_CLASS=client` unless `ENV` is staging or production,
  and every response carries an `X-Data-Class` header.
  - *Done-when:* tests cover each ENV × DATA_CLASS combination.
- [ ] **BE-6.2** `docs/RETENTION.md`: provisional retention, deletion and backup
  assumptions per artifact class (audit, schemas, proposals), each marked as needing
  client confirmation. No numeric RPO/RTO (Arch §11).
  - *Done-when:* the document exists and is referenced from ARCHITECTURE.md.
- [ ] **BE-6.3** WS1 verification suite: authorization matrix test (every route × every
  seeded role, expected 2xx/403), cross-tenant isolation, breaking-change approval,
  audit completeness (every mutating route emits an event), and migration round trip
  from base to head.
  - *Done-when:* the suite runs in CI and the matrix table is printed in the test report.
- [ ] **FE-6.1** Environment banner showing ENV and data class (always visible outside
  production).
  - *Done-when:* the banner reads "LOCAL · SYNTHETIC DATA" in local dev.
- [ ] **FE-6.2** Frontend checks: Playwright smoke for sign-in per role, 403 page, schema
  compare and the proposal flow.
  - *Done-when:* the Playwright report passes in CI.

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
