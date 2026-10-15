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

Kickoff: `docs/prompts/kickoffs/01-ws1-foundation.md` · Status: **APPROVED 2026-10-15 — Phase 1 in progress**

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

- [ ] **BE-1.1** Backend project: `backend/` with `pyproject.toml` (FastAPI, SQLAlchemy 2.0
  async, asyncpg, Alembic, pydantic-settings, pytest, ruff, mypy), app factory
  `app/main.py`, and the module layout from `docs/ARCHITECTURE.md` (empty modules carrying
  the standard 7-file set).
  - *Done-when:* `ruff check`, `mypy app` and `pytest` all pass on the empty skeleton.
- [ ] **BE-1.2** `core/config.py` settings: `ENV` (`local|test|staging|production`),
  `DATABASE_URL`, `AUTH_MODE`, `AI_ENABLED=false`, `DATA_CLASS=synthetic`.
  - *Done-when:* a test proves the app refuses to start if `ENV=production` and
    `AUTH_MODE=dev`.
- [ ] **BE-1.3** `core/errors.py`: error envelope `{code, message, details, correlation_id,
  retryable}` (D-07) for HTTP, validation and unhandled errors.
  - *Done-when:* tests assert the envelope shape for a 404, a 422 and a forced 500, and
    that no stack trace leaks in the body.
- [ ] **BE-1.4** `core/correlation.py` middleware: accepts or generates `X-Correlation-ID`,
  echoes it in the response, binds it to logs.
  - *Done-when:* a test sends an ID and gets the same ID back in the header and the error
    body.
- [ ] **BE-1.5** `core/logging.py`: structured JSON logs with a redaction filter
  (Authorization headers, tokens, `password|secret|key` fields, values tagged sensitive).
  - *Done-when:* a test logs a payload containing a bearer token and a secret field, and
    the captured log contains neither.
- [ ] **BE-1.6** `db/`: async engine/session, declarative base with the common columns
  (`id` UUID, `tenant_id`, `created_at`, `updated_at`, `created_by`), Alembic env (async),
  an empty baseline revision, and `docker-compose.yml` with Postgres 16 (D-12).
  - *Done-when:* `alembic upgrade head && alembic downgrade base && alembic upgrade head`
    succeeds against the compose DB.
- [ ] **BE-1.7** `GET /api/v1/health` (no auth; returns version and DB reachability) and
  `scripts/export_openapi.py` writing `backend/openapi.json`.
  - *Done-when:* the health test passes, and the exported `openapi.json` validates as
    OpenAPI 3.1.
- [ ] **BE-1.8** CI workflow `.github/workflows/ci.yml`: backend lint, type check, tests,
  migration round trip on a Postgres service container, OpenAPI drift check; frontend
  lint, type check, build.
  - *Done-when:* the workflow runs green on the Phase 1 PR.
- [ ] **FE-1.1** `frontend/`: Next.js 14 App Router + TS + Tailwind 3 under `src/`,
  `@/*` alias, with `fp_design_system_template/` applied verbatim per its INSTRUCTIONS.md
  (Option A logo; `api/logo` route removed).
  - *Done-when:* `npm run build` and `tsc --noEmit` pass, and the bundle's "Verify the
    install" checklist is confirmed with screenshots attached to the report.
- [ ] **FE-1.2** App shell: Sidebar and Header with this platform's nav (Admin, Schemas,
  Audit; later workspaces shown as disabled "planned" items), and `ROUTE_LABELS` replaced.
  - *Done-when:* breadcrumbs render the platform labels, and no `fp_reporting` route label
    remains (grep proves it).
- [ ] **FE-1.3** Generated API client: `openapi-typescript` + a thin fetch wrapper in
  `src/api/` that sends `X-Correlation-ID` and parses the error envelope. Includes an
  `npm run gen:api` script.
  - *Done-when:* the health page renders backend health through the generated types, and
    no hand-written payload types exist in `src/` (grep).
- [ ] **FE-1.4** Shared error and state components: error-envelope toast showing the
  correlation ID, empty state, loading skeleton.
  - *Done-when:* a forced backend 500 shows a toast with the correlation ID from the
    response.

*Reversibility:* additive new folders and an empty baseline migration. Rollback is
reverting the merge; no data exists yet. The AI flag ships `false`.

### Phase 2 — Identity and authorization (DG-02)

Goal: every request has a principal; nothing is permitted unless a permission says so;
row scope is applied in repositories.

- [ ] **BE-2.1** Migration + models: `tenant`, `user_account` (IdP subject, no password),
  `role`, `permission`, `user_role`, `role_permission`, with a tested downgrade.
  - *Done-when:* the migration round trip passes, and model tests create and read each
    entity.
- [ ] **BE-2.2** Seed data migration (D-11): one tenant; roles `platform_admin`,
  `data_steward`, `risk_analyst`, `approver`, `auditor`; WS1 permissions `schema:read`,
  `schema:propose`, `schema:approve`, `audit:read`, `admin:read`. Its downgrade deletes
  only seed rows tagged by the migration (documented exception under D-01: seed data,
  not user data).
  - *Done-when:* after upgrade, the role→permission matrix query matches the table in
    this plan; after downgrade, the tables are empty.
- [ ] **BE-2.3** `core/auth.py` principal resolution behind `AUTH_MODE` (D-09):
  `oidc` validates a JWT against the issuer's JWKS; `dev` validates JWTs signed by a
  local dev key. No token or an invalid token returns 401. Unknown users are not
  auto-provisioned outside `ENV=local`.
  - *Done-when:* tests cover a valid token, an expired token, a wrong issuer, no token,
    and `dev` mode being rejected in production.
- [ ] **BE-2.4** `require_permission(...)` dependency, fail closed (D-04), plus a route
  registry test that fails if any `/api/v1` route other than `health` lacks a declared
  permission.
  - *Done-when:* the registry test fails when a deliberately unguarded test route is
    added, and passes otherwise.
- [ ] **BE-2.5** `ScopedRepository` base: every query is filtered by
  `tenant_id = principal.tenant_id` (and further scopes when added); no repository
  method accepts a tenant from request input.
  - *Done-when:* a test with two tenants proves cross-tenant reads return nothing, even
    with a crafted ID in the URL.
- [ ] **BE-2.6** `GET /api/v1/me` and `GET /api/v1/me/permissions`.
  - *Done-when:* tests for each seeded role return exactly that role's permissions.
- [ ] **FE-2.1** Session handling for both `AUTH_MODE`s (an OIDC redirect flow, and a dev
  user picker shown only when the backend reports `AUTH_MODE=dev`).
  - *Done-when:* manual test notes show sign-in as each seeded role in dev mode; the
    picker doesn't render when the mode is `oidc`.
- [ ] **FE-2.2** Permission-aware nav and actions driven by `GET /me/permissions`; a 403
  page and an access-denied inline state.
  - *Done-when:* `auditor` sees no Propose button; a direct URL to a forbidden page shows
    the 403 state, and the backend also returns 403 (cosmetic hiding is not relied on).

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
