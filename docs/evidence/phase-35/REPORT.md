# Phase 35: Maintainability and test isolation (checks)

**Scope.** This report covers BE-35.1 to BE-35.8 and FE-35.1 to FE-35.7 (Workstream F, plus Workstream G's test items; DG-02, FC-07; D-60).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f06-p35` by:
  - a backend-engineer and a test writer, both scoped to `backend/`;
  - two frontend-engineers. One did FE-35.1, FE-35.2 and FE-35.7. The other did FE-35.3 to FE-35.6 and is the one owner of `src/config/navigation.ts` and `src/config/features.ts`;
  - one review agent and one e2e run;
  - a backend fix round and a frontend fix round.
- **Commits.** Nothing is committed yet.
- **No migration.** The head stays `0031 (head)`. No revision number is reserved.
- **Not done in this build.** BE-35.5 (the D-60 test split) has not been done. Per Q8 it is a separate test-writer run inside Phase 35, and it must land before the PR merges.

## What was built

- **BE-35.1 Import cycles broken.**
  - **core and governance.** The new `app/core/interfaces.py` is a port. It declares what core needs from governance (`resolve_principal`, `audit_denied`, `record_denial`) and defines `AccountDenied`.
    - `app/governance/service.py` implements the port and binds it when the module is imported (:1083).
    - `core/auth.py`, `core/ratelimit.py` and `core/refusals.py` now go through the port. Audit rows are field-for-field the same as before.
    - If nothing is bound, `governance()` raises `RuntimeError` (fail closed). Per Q3, `create_app` calls it once after building the API router (`main.py:100-103`), so a missing binding stops startup.
    - `DEV_ISSUER` and `DEV_AUDIENCE` moved to `core/tokens.py`, and `governance.constants` re-exports them.
  - **calculations and orchestration.** The shock contract the engine applies moved to `calculations/constants.py` with the same values: `ScenarioTemplate`, `MIN_SHOCKS`/`MAX_SHOCKS`, `ApprovalLabel`, `ApprovalState`, `APPROVAL_LABELS`, `PartChange` and `MAX_PARTS_PER_FLOW`. `orchestration/constants.py:14-21` re-exports every name.
  - **Result.** `app.core` imports only `app.db`, and none of the four packages is in a cycle with another.
- **BE-35.2 Service files split.**
  - **`calculations/service.py`** (2886 lines) is now `snapshots.py`, `results.py`, `queries.py` (`RunQueries`), `execution.py` (`RunExecution`) and `runs.py` (`CalculationService` and the sweeper). The classes form one linear chain, RunQueries -> RunExecution -> CalculationService, so every method body moved unchanged.
  - **`ingestion/service.py`** (2401 lines) is now `contracts.py`, `imports.py` (`ImportReads`, `limits_from`), `profiling.py`, `validation.py`, `manifests.py` (`ImportValidation`), `uploads.py` (`ImportService`) and `quarantine.py`.
  - **Routers.** `ingestion/router.py` (910 lines) moved its multipart reader to `ingestion/upload_form.py`. `calculations/router.py` moved `LCR_LIMITATIONS` into `calculations/constants.py`. `CREATE_IMPORT` moved to `ingestion/constants.py`.
  - **Re-exports.** Both `service.py` files are re-export modules with an explicit `__all__`, kept for one release. Their removal is in Deferred (Q4).
  - **One deliberate change.** `QuarantineService.imports` is now `ImportReads`, which has the same constructor. This avoids the module cycle manifests -> quarantine -> uploads.
  - **Hash sites.** The `HASH_SITES` `built_by` strings now name the defining modules.
- **BE-35.3 `GET /api/v1/config/limits` (`authenticated`).**
  - `api/v1/config.py:91-108` builds each group with `Schema(**asdict(obj))` from three sources:
    - `rules.service.rule_parameter_limits()`, which returns the frozen `RULE_PARAMETER_LIMITS` (`rules/constants.py:337`). A new `MAX_BUCKET_CODE_LENGTH = 20` now drives the `BUCKET_CODE` regex; the pattern text is unchanged;
    - `orchestration.service.scenario_limits()`, which returns `SCENARIO_BOUNDS` (`orchestration/constants.py:213`). The scenario write schemas and `FundingSourceSelector` now use these constants instead of literals;
    - `ingestion.imports.limits_from(settings)` (:99), imported from where it is defined (Q5).
  - All four schemas forbid unknown fields.
  - The route's docstring is now a comment, so it no longer leaks into the OpenAPI description.
- **BE-35.4 Nine dead helpers removed.** The list and the grep evidence are under "BE-35.4 evidence" below.
- **BE-35.5 Not started** (Q8).
- **BE-35.6 OIDC/JWKS tests.** Six focused functions in `test_auth.py`, run against a fake IdP on `httpx.MockTransport` through the existing `fetch` and `clock` hooks of `JwksKeys`. No implementation change was needed.
- **BE-35.7 Security tests and fixes.**
  - **New file.** `test_security.py` has three functions.
  - **Correlation id.** `core/correlation.py:36` now uses `fullmatch`. Before, `X-Correlation-ID: abc\n` was accepted, because `$` also matches before a trailing newline.
  - **The same class of bug, swept (Q2).** `.match` became `.fullmatch` at:
    - `rules/service.py:212` (`ENUM_VALUE`) and :278 (`BUCKET_CODE`);
    - `core/config.py:339` and :341 (the S3 bucket and prefix);
    - `core/artifacts.py:80` (`_REF`).

    Pattern texts are unchanged.
- **BE-35.8 Unicode digits refused.** `DecimalText` (`ingestion/schemas.py:190-200`) gains `AfterValidator(_ascii_digits)`. A non-ASCII control total gets 422 before anything is written, and the message names no value. The `DECIMAL_TEXT` pattern is unchanged (:187; the plan cites :184).
- **FE-35.1 Shared helpers in `src/lib/runs.ts`.**
  - `RESULT_STATUSES`, `hasResults`, `runHref`, `approvalVariant` and `memberLabel` moved there.
  - `scenarioHref` and `explorerHref` are defined in the leaf `src/lib/scenario-links.ts`, which imports nothing, and `runs.ts` re-exports them (Q12).
  - `StressedRunBadge.tsx` and the other cross-workspace callers import from `@/lib/runs`. The home modules re-export the moved names for their own workspace.
- **FE-35.2 Large files split.**
  - The mapping workbench page went from 761 to 47 lines. Its 8 parts are in `components/ingestion/mapping-workbench/`; the largest is 208 lines.
  - `ComparisonView.tsx` went from 657 to 170 lines, plus `ComparisonTable.tsx` (283), `ComparisonIdentity.tsx` (116), `ComparisonRankings.tsx` (109) and `useComparison.ts` (30).
  - No visible text, test id or CSS class changed.
- **FE-35.3 Served limits.**
  - `src/lib/limits.ts`: `fetchLimits()` reads the route once per page life and forgets a failed read.
  - The rules, scenario and upload editors read the served values. Literals removed: `LIMITS`, `NAME_MAX`, `DESCRIPTION_MAX`, `SUMMARY_MAX`, `FRACTION_SCALE`, `PERCENT_SCALE` and `IMPORT_MAX_BYTES`.
  - Per Q20, the scenario submit and decision dialogs use the served `max_comment_length` through `CommentLimit.tsx`. Neither dialog can send until the limit has been read.
- **FE-35.4 Re-auth.**
  - `api/client.ts` passes a 401 `token_expired` for the current token to a handler that `SessionProvider` installs.
  - `lib/reauth.ts` and `lib/reauth-browser.ts`:
    - concurrent 401s give one re-auth;
    - OIDC tries a silent renew only when a refresh token exists, otherwise does one `signinRedirect` back to the route;
    - dev goes to `/login?next=<path+query+hash>&reason=expired`;
    - a second expiry within 60 s stops on `/login?reason=renew_failed`.
  - The sign-in page shows the new `SessionExpiredNotice`.
  - Switch: `NEXT_PUBLIC_REAUTH_UI=off`.
- **FE-35.5 Route gates.**
  - `navigation.ts` gains `navEntries()` and `navEntryFor()` (longest href match, on segment boundaries).
  - `NavPermissionGate` is rendered once in `(platform)/layout.tsx`. A path with no nav entry passes through.
  - Switch: `NEXT_PUBLIC_ROUTE_GATES_UI=off`.
- **FE-35.6 Agent demo retired (status code still wrong).**
  - Deleted: the route, `AgentDemo.tsx`, `agentDemo.ts` and `agentDemo.test.ts`. The explorer page's actions are byte-identical to `664ae45^`.
  - `DEMO_BANNER` and `ratio` moved to `liquidityIntel.ts`.
  - The new server layout `[scenarioId]/layout.tsx` calls `notFound()` for any segment that is not a UUID. The match is full-string and case-insensitive.
  - **The URL still answers HTTP 200, not 404.** See the open items.
- **FE-35.7 Dead frontend helpers removed.** The list is under "FE-35.7 evidence" below.

**Files changed:**
- **Backend, new:**
  - `app/core/interfaces.py`
  - `app/calculations/{snapshots,results,queries,execution,runs}.py`
  - `app/ingestion/{contracts,imports,profiling,validation,manifests,uploads,quarantine,upload_form}.py`
- **Backend, modified:**
  - `app/main.py`, `app/api/v1/config.py`
  - `app/calculations/{service,constants,router,scenario_overlay,repository,executor}.py`
  - `app/ingestion/{service,router,schemas,constants}.py`
  - `app/core/{auth,ratelimit,refusals,tokens,correlation,canonical,logging,config,artifacts}.py`
  - `app/governance/{service,constants}.py`
  - `app/orchestration/{constants,schemas,service,repository}.py`
  - `app/rules/{constants,service}.py`
  - `openapi.json` (+236 lines, the contract export only)
- **Backend tests:**
  - new: `limits_kit.py`, `test_security.py`
  - modified: `test_auth.py`, `test_committed_artifacts.py`, `test_validation.py`, `test_ws1_verification.py`
- **Frontend, new:**
  - `src/lib/{runs,scenario-links,limits,reauth,reauth-browser}.ts`
  - `components/auth/{NavPermissionGate,SessionExpiredNotice}.tsx`
  - `components/advanced/{ComparisonTable,ComparisonIdentity,ComparisonRankings,CommentLimit}.tsx`, `useComparison.ts`
  - `components/ingestion/mapping-workbench/` (8 files)
  - `app/(platform)/advanced/stress-explorer/[scenarioId]/layout.tsx`
  - tests: `lib/__tests__/{runs,limits,reauth}.test.ts`, `served-limits.ts`, `config/__tests__/route-gates.test.ts`, `comparison-parts.test.ts`, `mapping-workbench.test.ts`, `e2e/reauth.spec.ts`
- **Frontend, modified:**
  - config and auth: `config/{features,navigation}.ts`, `.env.example`, `api/client.ts`, `SessionProvider.tsx`, `login/SignIn.tsx`, `(platform)/layout.tsx`
  - the mapping page and `ingestion/imports/new/page.tsx`, `stress-explorer/page.tsx`
  - `components/advanced/`: `ComparisonView.tsx`, `comparison.ts`, `scenario-runs.ts`, `scenarios.ts`, `editor.ts`, `ScenarioEditor.tsx`, `ScenarioRunPanel.tsx`, `ScenarioRunsList.tsx`, `VersionActions.tsx`, `LiquidityIntel.tsx`, `liquidityIntel.ts`
  - `calculations/calculations.ts`, `StressedRunBadge.tsx`, `registry/RuleView.tsx`, `credit/review/review.ts`, `ingestion/{MappingsTab.tsx,imports.ts}`, `rules/{rules.ts,ProposeRuleVersion.tsx}`
  - tests: `rules.test.ts`, `advanced.test.ts`, `imports.test.ts`, `liquidityIntel.test.ts`, `e2e/advanced-nav.spec.ts`
  - regenerated: `api/schema.d.ts`
- **Frontend, deleted:** `advanced/stress-explorer/agent-demo/page.tsx`, `AgentDemo.tsx`, `agentDemo.ts`, `agentDemo.test.ts`.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| ruff / format / mypy (fix round, final) | `All checks passed!` / `204 files already formatted` / `Success: no issues found in 204 source files`. Before the fix round: `Found 25 errors in 3 files`, all in test files |
| OpenAPI drift | `python -m scripts.export_openapi --check`: `openapi.json is up to date (exit=0)`; `test_committed_openapi_and_samples_are_current` PASSED (fix round) |
| Declared routes | `undeclared: []`; `[(['GET'], '/api/v1/config/limits', 'authenticated')]` |
| Collected | baseline `49 tests collected in 2.90s`; test writer `58 tests collected in 0.55s`; fix round `59 tests collected in 0.71s` (+1 `test_import_graph_and_size_bounds`). The final D-60 count is recorded after BE-35.5 |
| BE-35.1 / BE-35.2 fold (engineer, using the test writer's functions) | `cycles among the four: []`; `app.core imports: ['db']`; `files over 800 lines in calculations/ingestion: {}`; largest `(792, 'calculations/router.py'), (790, 'ingestion/router.py'), (762, 'calculations/runs.py')` |
| BE-35.2 verbatim-move AST check | calculations `115 / missing: [] / different: []`; ingestion `149 / missing: [] / different: ['method:__init__']` (the `QuarantineService.imports` change); router `43 / missing: [] / different: []` |
| BE-35.3 WS1 matrix (after the `_max_length` fix) | `GET /api/v1/config/limits  authenticated  200 200 200 200 200`; `1 passed, 1 warning in 19.22s` |
| BE-35.3 in process | default `"upload": {... "max_rows": 50000 ...}`, `identity: True True True`; `IMPORT_MAX_ROWS=7`: `"max_rows": 7`, `identity: True True True` |
| BE-35.8 in process | `'١٢٣' refused [('value_error', 'Value error, A control total must be decimal text in ASCII digits (0-9).')]`; `'１２'`, `'12٣.5'`, `'1.٥'`, `'-१'` refused; `'-123.45'`, `'0'`, `'1.5'` ok |
| BE-35.7 correlation id | `b'abc' -> kept`, `b'abc\n' -> replaced`, `b'abc\r\n' -> replaced`, `b'a\r\nX-Injected: 1' -> replaced`, `129 chars -> replaced`. Before the fix: `correlation 'abc\n' accepted: True` |
| Q2 probe, before the fix | `check_parameters(DAY_BUCKETS, code 'B01\n') -> ({'buckets': [{'code': 'B01\n', ...}]}, [])` (accepted) |
| Migration round trip (scratch `moodymate_scratch_p35`) | `0031 (head) -> downgrade -1 -> 0030 -> upgrade head -> 0031 (head)`; `dropped moodymate_scratch_p35`. No migration changed |
| Test writer, before the implementation (`moodymate_test_p35b`) | `3 failed, 14 passed, 1 warning in 36.42s` (config/limits 500, Unicode total 201, files over 800 lines); `test_auth.py`: `10 passed, 1 warning in 14.73s`; `test_security.py`: `3 passed, 1 warning in 5.70s` |
| Backend engineer, targeted (`moodymate_test_p35`) | 11 files: `2 failed, 27 passed, 1 warning in 226.43s (0:03:46)`. Failures: the BE-35.5 bound in the committed-artifacts test, and `limits_kit.py:44 ValueError: not enough values to unpack` in the WS1 matrix. test_calculations, test_validation (with the BE-35.8 fold), test_profiling and test_rules passed |
| Fix round baseline (`moodymate_test_p35be`, the 5 large files plus committed artifacts) | `2 failed, 8 passed, 1 warning in 188.71s (0:03:08)`. test_domain and test_credit passed on the split code. Durations: `54.81s test_calculations...ladder`, `28.18s test_rules`, `24.03s test_domain`, `19.69s ws1 migration`, `15.55s test_credit`, `12.77s ws1 matrix`, `8.13s ws1 another_tenant` |
| Fix round, after the fixes | `2 failed, 21 passed, 1 warning in 127.18s` (size bound; `limits_kit.py:47 too many values to unpack`, fixed next). Final rerun: `1 failed, 19 passed, 1 warning in 25.24s`, failing only at `test_committed_artifacts.py:153` (the five functions over 300 lines). All three test_security functions, all 10 test_auth functions and the test_config and test_artifacts functions PASSED |
| Frontend gen:api | `schema.d.ts unchanged since last gen`; md5 `c80d6b8427d10370ea4e22e83f116ef4` before and after; e2e: `schema.d.ts matches openapi.json` |
| lint / tsc | `No ESLint warnings or errors` (exit 0) / `tsc exit: 0` (both rounds) |
| vitest | FE-35.1/2/7: `Test Files 50 passed (50)`, `Tests 819 passed (819)`. FE-35.3-6: `52 passed`, `942 passed`. Frontend fix round (final): `Test Files 52 passed (52)`, `Tests 945 passed (945)` |
| FE-35.1 cycle check bites | a temporary advanced import in `StressedRunBadge.tsx`: `Tests 1 failed / 8 passed (9)`, with `"components/calculations/StressedRunBadge.tsx -> components/advanced/scenario-runs.ts"`; file `restored-identical` |
| next build | `.next-gating`, `.next-p35fe`: `Compiled successfully`, `Generating static pages (34/34)`; no agent-demo route in the route table. `tsconfig.json` and `next-env.d.ts` were reverted to HEAD after each build |
| FE-35.3 grep (`src/components`, `__tests__` excluded) | exit 1 (no match); now also a vitest (`limits.test.ts`) |
| FE-35.6 grep | `grep -rniE "agent[-_ ]?demo" frontend/src` exit 1; `frontend/e2e`: only `advanced-nav.spec.ts:51` (the 404 check itself) |
| Playwright, e2e run (`moodymate_test_p35fe`, ports 8035/3035) | 8 specs: `1 failed`, `19 passed (1.3m)`. Failure: `advanced-nav.spec.ts:52 Expected: 404 Received: 200`. Passed: FE-35.4 `reauth.spec.ts:47 (35.5s)`, the mappings, scenario-compare, imports, rules, stress-explorer and scenario-run specs, and FE-24.1 |
| curl (port 3035) | `agent-demo 200`, `not-a-scenario 200`, valid UUID `200`, `/advanced/no-such-page 404`, `/no-such-top-level 404`, `liquidity-intelligence 200`; server HTML has only the `aria-busy` loading block (`404 text in non-script HTML: False`) |
| Playwright, frontend fix round (ports 8351/3351) | `1 failed, 5 passed (51.1s)`: FE-35.6 again `Expected 404, Received 200`; `reauth.spec.ts:59 (35.6s)` passed. Re-check on the final build: `stress-explorer.spec.ts` `3 passed (10.5s)` |
| Full backend suite | **not run** (the coordinator runs it once; it stays red until BE-35.5 lands) |
| Full Playwright | CI; not run |

Databases used: `moodymate_scratch_p35` (dropped), `moodymate_test_p35`, `_p35b`, `_p35be` and `_p35fe`. `moodymate_dev` was never touched.

### BE-35.4 evidence: removed (each had zero code references)

| Removed | At HEAD | Now |
|---|---|---|
| `ScenarioRepository.versions_by_id` | `orchestration/repository.py:209` | none |
| `CalculationRepository.shock_applications` | `calculations/repository.py:435` | none |
| `ModuleView.pinned_versions` | `rules/service.py:422` | none |
| `RunExecutor.background` | `calculations/executor.py:312` (`\.background\b` at HEAD: none) | none |
| `ImportWriter` | `ingestion/router.py:116` | none |
| `Sensitive.reveal` | `core/logging.py:52` (`\.reveal\b` at HEAD: none) | none |
| `profile_rows` and `PROFILER_PARTS` (whose only consumer was a test removed in the D-26 trim) | `ingestion/service.py:1694` (def), :1702, :1709 | none |
| `TIMING_TEMPLATES` | `calculations/scenario_overlay.py:73` | none |
| `_SEGMENT` | `calculations/scenario_overlay.py:74` | none |

- **How the engineer searched:** `git grep`, HEAD against the working tree, over backend app, tests and scripts plus `frontend/src` and `frontend/e2e`. The search counted Load names, attributes, imports and words in string literals.
- **Architect re-grep (2026-10-24),** over the whole worktree: no reference to any of the nine. The only hits were different names (`instrument_versions_by_id` in domain and calculations/queries, `CreditVocabularyKind.CREDIT_SEGMENT`) and the word "reveal" in prose.

### BE-35.4 evidence: kept (Q6; unreferenced, but each mirrors a migration, a CHECK or a decision)

These come from an architect grep over `backend/` with `\b<name>\b`. Each name matched only its own definition line.

| Kept | Mirrors |
|---|---|
| `CREDIT_SEED_TAG` (`domain/constants.py:162`) | migration 0022 seed tag (D-11: downgrade removes only tagged rows) |
| `CLASSIFICATION_SEED_TAG` (`governance/constants.py:201`) | 0005 seed tag (D-11) |
| `SCHEMA_SEED_TAG` (`governance/constants.py:252`) | 0007 seed tag (D-11) |
| `WS2_PERMISSION_SEED_TAG` (`ingestion/constants.py:67`) | 0010 seed tag (D-11) |
| `VALIDATION_PROFILE_SEED_TAG` (`ingestion/constants.py:298`) | 0015 seed tag (D-11) |
| `SCENARIO_SEED_TAG` (`orchestration/constants.py:186`) | 0026 seed tag (D-11) |
| `RUN_TRANSITIONS` (`calculations/constants.py:37`) | D-06 / D-31 §5, mirrored by the run lifecycle trigger |
| `SCENARIO_TRANSITIONS` (`orchestration/constants.py:40`) | D-44 §5, enforced by the 0026 trigger |
| `ROLE_CLEARANCE` (`governance/constants.py:193`) | the frozen copy in migration 0005 |
| `RolePermission` (`governance/models.py:103`) | table `role_permission`. `Role.permissions` uses `secondary="role_permission"` (models.py:66), which resolves through the metadata this class registers |
| `ProjectionNote` (`domain/constants.py:38`) | D-29 §4-5 summary count names |
| `IdentityLinkNote` (`domain/constants.py:388`) | D-58 §3 |
| `TermNote` (`domain/constants.py:538`) | D-61 §3-4; CHECK-constrained by 0031 |
| `ConsolidationStatus` (`domain/constants.py:557`) | D-61 §6 (consolidation BLOCKED, a client hard stop) |

### BE-35.6 functions (`test_auth.py`)

- `test_jwks_key_rotation_refetches_once_and_refuses_a_removed_key` (:357)
- `test_oidc_refuses_alg_none_and_hs256_signed_with_the_public_key` (:400)
- `test_oidc_exp_nbf_leeway_issuer_and_audience` (:429)
- `test_jwks_token_without_kid` (:461)
- `test_jwks_down_is_provider_unavailable` (:481, db)
- `test_oidc_malformed_tokens_are_invalid` (:557)

### BE-35.7 items and the functions that hold them

| Item | Function |
|---|---|
| Upload bombs (zip-bomb ratio, uncompressed size, entity expansion, macros) | existing: `test_adapters.py::test_bad_files_and_limits_are_refused_with_their_own_code` (:63) |
| Malformed archives | new: `test_security.py::test_malformed_archives_are_refused_with_an_upload_refusal` (:59) |
| Formula-like cells kept as text (CSV, JSON, XLSX) | new: `test_security.py::test_formula_like_cells_are_stored_as_text` (:94) |
| CRLF in `Idempotency-Key` (422, nothing written); an unsafe correlation id is replaced by a fresh UUID and reaches no audit row; the Q2 `fullmatch` folds | new: `test_security.py::test_crlf_in_idempotency_key_or_correlation_id_is_refused` (:112, db) |
| Rate limits | existing: `test_auth.py::test_missing_permission_is_403_with_a_denied_audit_event` via `_rate_limits_refuse_audit_and_switch_off` (:185-224, called at :249) |
| Tenant and authorization boundaries | existing: `test_ws1_verification.py::test_another_tenant_sees_nothing_here` and `::test_another_tenant_changes_nothing_here` (split from the old function in BE-35.5) and their foreign-vs-random list; `test_quarantine.py:121-136` (cross-tenant 404 parity). The review found no uncovered boundary, so no new function was added |

### FE-35.7 evidence

- **How "unused" was confirmed:**
  - a scan of every exported name across `src/`, `e2e/`, `scripts` and the config files, with spread uses counted;
  - a scan for modules that nothing imports;
  - a one-off `tsc --noEmit --noUnusedLocals` run, with no config change.
- **Removed:**
  - `combinedFirstNegative` (`advanced/comparison.ts`)
  - `SCENARIO_STATUSES` (`advanced/scenarios.ts`)
  - `snapshotLabel` (`calculations/calculations.ts`)
  - `ratingsHref` (`credit/review/review.ts`)
  - the `ApprovalState` alias in `scenario-runs.ts`, whose only user moved to `lib/runs.ts`
  - unused imports: `ApiError` (`calculations.ts`), `isProposer` (`review.ts`), `EmptyState` (`MappingsTab.tsx`)
- **Kept, under the verbatim-bundle rule (Q14).** Each file is identical to its `fp_design_system_template/` copy:
  - `lib/utils.ts`, whose unused helpers are `formatDate`, `getStatusColor` and `formatXml`;
  - `components/common/PageSection.tsx` (`SectionHeader`, `CardContent` import);
  - `components/layout/header.tsx` (`Button` import);
  - `ThemeToggle.tsx`, `lib/theme.ts`;
  - all 29 `components/ui/*` files, including the 7 primitives nothing imports: accordion, alert-dialog, collapsible, command, date-picker, select, sheet.

  `PageHeader.tsx` (ROUTE_LABELS only) and `sidebar.tsx` (the FE-24.1 group) have diverged from the bundle, but neither has dead code.
- **Kept, for other reasons:**
  - `dynamicParams`, which Next.js reads;
  - helpers that only tests use, because existing vitests assert on them: `unknownCombinedOnly`, `buildImportFormData`, `canReview`, and the `*_STATUSES`/`OUTCOMES`/`CHECKS`/`IMPORT_ERROR_CODES`/`DRIFT_TYPES` lists.
- **Kept, type-only (Q13):** 13 unused aliases of generated types. **The list with file and reason is still to be supplied by the FE-35.7 owner** (open item).

### FE-35.3 exclusions (Q15: bounds that `GET config/limits` does not serve)

Each is checked against the contract in `limits.test.ts` `UNSERVED`:
- `mapping-workbench/ApprovalSections.tsx:23` `maxLength={2000}` (SubmitIn.comment)
- `ApprovalSections.tsx:66` `maxLength={2000}` (ApprovalIn.comment)
- `mapping-workbench/RulesSection.tsx:129` `maxLength={1000}` (MappingVersionIn.change_summary)
- `ContractEditor.tsx:140` `maxLength={1000}` (ContractIn.change_summary)

Also kept: `COMMENT_MAX` in `src/lib/forms.ts` for the decision schemas that are not served (it already has an openapi.json test), and the input-only format caps (3 for currency, 12 for numeric).

## Plan ticks

The architect checked each Done-when against the code and the test lines on 2026-10-24. Each ticked box has an evidence line in DELIVERY_PLAN.md, and each open box has a "Not yet ticked" line naming what is missing.

- **BE-35.1 ticked.** The Done-when is reworded per Q10.
  - `_maintainability_bounds` asserts `cycles == []` at :133 and `graph["core"] <= {"db"}` at :134. Both passed in the fix round's final run, which fails only later, at :153.
  - The engineer's fold output agrees, and the architect's grep finds no core import outside core and db.
- **BE-35.2 ticked.**
  - The size assert (:135-142) passed.
  - `export_openapi --check` is clean.
  - The golden and re-run hash tests passed on the split code: test_calculations, test_validation, test_profiling, test_domain and test_credit.
- **BE-35.3 ticked.** `test_ws1_verification.py:424-429` plus `limits_kit.py:75-143`. Run: `1 passed`.
- **BE-35.6 ticked.** Six functions; `test_auth.py` all 10 PASSED.
- **BE-35.7 ticked.** The CRLF bullet is reworded per Q11. Three functions PASSED, and every item is mapped above. See LOW 5.
- **BE-35.8 ticked.** `test_validation.py:427-458` passed in the engineer's targeted run; `--check` is clean.
- **FE-35.1 ticked.** `runs.test.ts` passes and bites; vitest `945 passed`.
- **FE-35.2 ticked.**
  - The 300-line vitests pass.
  - `comparison.test.ts`, `mappings.test.ts`, `mappings.spec.ts` and `scenario-compare.spec.ts` are unchanged, and all pass.
- **FE-35.3 ticked.** The grep vitest, the served-value editor vitests and the fixture-to-contract check.
- **FE-35.4 ticked.** The Done-when is reworded per Q17. `reauth.test.ts` (16 tests); `reauth.spec.ts` passed twice.
- **FE-35.5 ticked.** `route-gates.test.ts` (90 tests).
- **FE-35.7 ticked.** The list is in this report and vitest is green. The Q13 alias list is still owed.
- **BE-35.4 not ticked.** The list and grep evidence are above. **Missing:** a green suite.
- **BE-35.5 not ticked.** **Missing:**
  - the splits;
  - the final count;
  - `--durations` within 180 s;
  - the hunk mapping.
- **FE-35.6 not ticked.** The grep half holds. **Missing:** the HTTP 404 (`advanced-nav.spec.ts:52` gets 200).

## Review outcome

- **MAJOR (partly fixed; the open half blocks merge).** BE-35.5 is not done, but the new fold already asserts that no test function is over 300 lines. That turns the suite red, and it also hid the result of the OpenAPI drift check.
  - **Fixed:** `_maintainability_bounds()` now has its own function, `test_import_graph_and_size_bounds` (:57-68). `test_committed_openapi_and_samples_are_current` passes again.
  - **Open:** the split of the five functions (Q8).
- **No MEDIUM findings.**
- **LOW findings:**
  1. (review) `limits_kit.py`: CPython caches the ints -5..256, so `is` cannot tell 1, 5, 18, 20, 50 or 200 from an equal literal. The validators also read the module constants, not the dataclasses. **Partly addressed (Q9):**
     - container identity (`rule_parameter_limits() is RULE_PARAMETER_LIMITS`, `scenario_limits() is SCENARIO_BOUNDS`);
     - field-set completeness against the dataclasses;
     - per-field identity with the container;
     - a validator boundary check on `max_bucket_code_length`;
     - `max_instrument_key_length` added.

     **Residual, open:** the validators could read the dataclass fields directly.
  2. (review) `rules/service.py` `BUCKET_CODE` and `ENUM_VALUE` accepted a trailing newline. **Fixed (Q2)** (`fullmatch` at :212 and :278), with a fold at `test_security.py:147-156`.
  3. (review) `frontend/src/lib/reauth-browser.ts:29-38` and `lib/oidc.ts:17`: `UserManager` has no `requestTimeoutInSeconds`. If the IdP token endpoint hangs, `signinSilent()` never settles, every later 401 joins the pending episode, and neither the redirect nor the sign-in fallback runs. **Open.** Fix: set the timeout, or race the renew against a timeout that resolves null. Add a vitest with a renew that never resolves and expect exactly one redirect.
  4. (review) BE-35.7's scope was not evidenced. **Addressed** by the item-to-function table above.
  5. (architect) `test_security.py:128`: none of the unsafe correlation ids is the exact regressed case, a trailing LF alone (`"c\n"`). Every listed value also fails the old `.match`, so the fold would have passed before the `fullmatch` fix. The fold also checks no log record, which Q11 asks for. **Open.** Fix: add `"c\n"` to the tuple, and a caplog assertion that the sent value appears in no log record or error body.
  6. (Q2 follow-up) `domain/service.py:316` and :318 (`_CURRENCY`, `_COUNTRY`) still use `.match`. The DB CHECKs already refuse a trailing newline (0017, 0022). Switching would change frozen projector behaviour (D-29 §4, D-57 §2). **Follow-up,** tied to a new projector version.
  7. (Q13) The 13 unused type-only aliases of generated types. **Follow-up:** remove them once file ownership frees up.
  8. (Q15) The mapping and contract `maxLength`s are not served. **Follow-up:** serve them additively from `GET config/limits`, then move them into the grep.
  9. (Q16) Two folder-level cycles remain:
     - the app shell: api, config, lib, components/auth, components/common, components/layout, components/ui (including config -> components/layout);
     - `components/credit` <-> `components/domain` (`domain/identity.ts` -> `credit/credit.ts`).

     **Follow-up.** For credit <-> domain, use the FE-35.1 pattern: move the shared identity helpers into a `src/lib` leaf. There are no file-level cycles.
  10. (Q18) Renewal happens only after a 401. **Follow-up:** proactive renewal ahead of expiry.
  11. (Q22) The FE-35.5 gate shows `/ingestion/imports/*` and `/ingestion/mappings/*` by `source:read`, while the pages check `import:read` and `mapping:read`. That is UI-only and cannot happen with the seeded roles. If roles ever become configurable, give those routes nav entries with their own permission. `REAUTH_WINDOW_MS = 60 000` is **PROVISIONAL**.
  12. (build) A `NEXT_DIST_DIR` build rewrites `frontend/tsconfig.json` and `next-env.d.ts`. Both were reverted. **Re-check before commit,** and remove `frontend/.next-p35fe`.

## Rulings (auto-resolved)

The decision-resolver gave each ruling. All 22 are recorded as phase-report notes; none amends a decision, so DECISIONS.md is unchanged. D-60 stays **proposed**.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | The limits kit finds the route through `served_routes(app)`, filtering on the path and `'GET' in methods`, with a strict unpack. It imports the bounds from `rules.constants` and `orchestration.constants`. Applied (`limits_kit.py:53-61`). | Report |
| Q2 | `fullmatch` for the rules, config and artifacts validators, folded into the BE-35.7 function. Domain `_CURRENCY`/`_COUNTRY` is a recorded follow-up (projector versions, D-29 §4, D-57 §2). Applied. | Report (LOW 6) |
| Q3 | The core port bound by governance is accepted. An unbound port must fail at startup: `main.py:100-103`, asserted at `test_committed_artifacts.py:61-67`. | Report; ARCHITECTURE.md "Core ports" |
| Q4 | The BE-35.2 layout is accepted, with `service.py` as the facade for one release. The re-exports are removed in the next feature's first backend phase. | Report; ARCHITECTURE.md "Split modules"; DELIVERY_PLAN Deferred item |
| Q5 | `config/limits` at the API root is accepted. It imports `limits_from` from `ingestion.imports` (`config.py:25`). | Report; ARCHITECTURE.md "Served limits" |
| Q6 | Keep the unreferenced declarations that mirror migrations, CHECKs or decisions, listed in a "kept" table. | Report (BE-35.4 kept table) |
| Q7 | Keep the 9 focused functions. D-26's under-50 cap ends when Phase 35 merges (D-60). The recorded count is the post-BE-35.5 figure. Updating the CLAUDE.md step 4 wording is the user's call. | Report; plan test-budget note; user item below |
| Q8 | BE-35.5 is a separate test-writer run (or runs, each owning distinct files) inside Phase 35, landing before the PR merges. No skip, xfail or raised threshold. | Report; open item 1 |
| Q9 | The kit matches the implementation. Add the `max_bucket_code_length` identity and boundary checks, `max_instrument_key_length`, field-set completeness and container identity. Applied. | Report (LOW 1) |
| Q10 | Keep the stricter cycle check: function-level and TYPE_CHECKING imports count, and paths through any package count. | Plan BE-35.1 Done-when reworded |
| Q11 | Replacing an unsafe correlation id counts as refused; no new 4xx (BE-1.4, D-62 §6). Idempotency-Key CRLF stays 422. | Plan BE-35.7 bullet reworded; LOW 5 |
| Q12 | `scenarioHref` and `explorerHref` go in the leaf `scenario-links.ts`. The FE-24.2 guard is unchanged. | Plan FE-35.1 note |
| Q13 | The 13 type-only aliases are kept this phase and listed as kept; removing them is a LOW follow-up. | Report (LOW 7; the list is still owed) |
| Q14 | Bundle copies are kept only while identical. All were diffed: identical, except `PageHeader.tsx` and `sidebar.tsx`, which have diverged but contain no dead code. | Report (FE-35.7) |
| Q15 | The FE-35.3 grep covers only the served fields. The mapping and contract literals are listed as exclusions. Applied. | Report (FE-35.3 exclusions; LOW 8) |
| Q16 | The remaining folder cycles are a LOW follow-up. The FE-35.1 vitest asserts no folder-level cycle for calculations and advanced. Applied. | Report (LOW 9) |
| Q17 | Re-signing a dev token with a 5 s TTL is accepted: assert `token_expired`, bound the polling, mark the test slow. No backend TTL setting. Applied. | Plan FE-35.4 Done-when reworded |
| Q18 | Silent renew by refresh token only, with no iframe renew (D-54 §1, §3). Proactive renewal is a LOW follow-up. | Report (LOW 10) |
| Q19 | The non-UUID 404 layout is confirmed: full-string, case-insensitive match, and an unknown UUID keeps "Scenario not found". Verified, except the HTTP 404 status. | Report; open item 2 |
| Q20 | The scenario comment dialogs read the served `max_comment_length`, and a vitest serves 250. Applied. There is no close-comment dialog in the frontend. | Report |
| Q21 | Record both D-02 switches. | ARCHITECTURE.md "Phase 35 switches"; plan overview row 35 and the reversibility line |
| Q22 | Gating `/ingestion/imports/*` and `/ingestion/mappings/*` by `source:read` is confirmed (cosmetic, D-04). `REAUTH_WINDOW_MS` is PROVISIONAL. | Report (LOW 11) |

## Reversibility

- There is no migration, and `0031 (head)` round-trips.
- The refactors keep every hash unchanged. The `service.py` re-exports keep every old import path working for one release.
- `GET /config/limits` is additive: `openapi.json` only grows, and the base-copy additive check passes.
- `NEXT_PUBLIC_REAUTH_UI=off` and `NEXT_PUBLIC_ROUTE_GATES_UI=off` restore the Phase 34 behaviour.
- The demo removal and the FE-35.3 literal removal are reverted from git.

## Open items

**Blocking merge:**
1. **BE-35.5 (Q8).** Launch test-writer run(s), each owning distinct files:
   - `test_calculations.py` (1379 lines);
   - `test_domain.py` (763) and `test_credit.py` (710);
   - `test_rules.py` (499);
   - `test_ws1_verification.py::test_another_tenant_sees_and_changes_nothing_here` (328), as one small, separate hunk.

   Every assertion is kept. Splits follow calculation boundaries, permissions, lifecycle and errors, and parametrisation is used only for isolated cases (D-60 §2-3). Deliver for this report:
   - the old-hunk -> new-test mapping;
   - the `--collect-only` count;
   - a `--durations` run within 180 s.

   The fix round's baseline durations are in the checks table. LOW 5 (`"c\n"` and the caplog check in `test_security.py`) can go to the same backend test writer.
2. **FE-35.6 HTTP status (frontend-engineer).** `/advanced/stress-explorer/agent-demo` answers 200 with Next's not-found page. The e2e run's explanation: `SessionProvider` renders only its loading block on the server, so the `notFound()` at `[scenarioId]/layout.tsx:14` never runs before the status is sent. The architect has not confirmed this. Do not weaken the spec; it must still assert 404. Possible fixes: answer 404 before rendering (for example, a Next middleware for non-UUID segments under `/advanced/stress-explorer/`), or run the check outside the client session boundary. The unknown-UUID "Scenario not found" state must stay unchanged (Q19). If the approach changes plan wording or a spec, send it to the decision-resolver.
3. **Full backend suite**, run once by the coordinator after item 1. Then tick BE-35.4, record the BE-35.5 count and durations, and set the D-60 test budget line.
4. **Full Playwright in CI** after item 2.
5. **Before commit:** confirm `frontend/tsconfig.json` and `next-env.d.ts` match HEAD, and remove `frontend/.next-p35fe`.

**Not blocking:**
- LOW findings 1, 3 and 5 to 12.
- The FE-35.7 owner lists the 13 type-only aliases (Q13), with file and reason, in the FE-35.7 section.
- Databases to drop when no longer needed: `moodymate_test_p35`, `_p35b`, `_p35be` and `_p35fe`.

**For the user at the end of the phase:**
- **CLAUDE.md step 4** and the phase-build task text still say "under 50 tests (D-26)". From the Phase 35 merge, D-60 replaces that cap: no count cap, the suite at most 180 s, no function over 300 lines. Changing CLAUDE.md is the user's call (Q7).
- **D-60 acceptance.** It is still "proposed", and so are D-52 to D-62. The hardening plan §11 authorises removing the cap; the replacement budget is the architect's proposal.
- **Content.** All data is synthetic. Nothing claims regulatory certification or vendor parity.

No new hard stop arose in this phase:
- there is no migration;
- no CHECK was widened;
- no data was deleted outside per-agent test and scratch databases;
- no role or permission changed. `GET config/limits` uses `authenticated`, and the FE-35.5 gate is cosmetic (D-04; the divergence is recorded in LOW 11).

**Not checked locally:** the full backend suite, the full Playwright run, and FE-35.6's HTTP status after a fix.

---

Files edited by the architect:
- `/home/user/MoodyMate-wt/f06-p35/docs/DELIVERY_PLAN.md`:
  - ticked, each with an evidence line: BE-35.1, BE-35.2, BE-35.3, BE-35.6, BE-35.7, BE-35.8, FE-35.1, FE-35.2, FE-35.3, FE-35.4, FE-35.5 and FE-35.7;
  - left unticked, each with a "Not yet ticked" line: BE-35.4, BE-35.5 and FE-35.6;
  - reworded, with the old text struck through: BE-35.1's Done-when (Q10), BE-35.7's CRLF bullet (Q11) and FE-35.4's Playwright line (Q17);
  - FE-35.1 note (Q12);
  - the Feature 06 overview row 35 Switch column, and the Phase 35 reversibility and test-budget notes (Q21, Q7);
  - the Deferred item for removing the BE-35.2 re-exports (Q4).
- `/home/user/MoodyMate-wt/f06-p35/docs/ARCHITECTURE.md`:
  - under Shape: "Split modules" (Q4), "Core ports" (Q3) and "Served limits" (Q5);
  - under Frontend workspaces: "Phase 35 switches" (Q21).
- `/home/user/MoodyMate-wt/f06-p35/docs/DECISIONS.md`: unchanged, because no ruling amends a decision.

## Coordinator completion (2026-10-24)

Open items 1-5 above were done by coordinator-dispatched agents, each owning distinct files, then checked on the tree merged with master (Phase 33's last ticks and D-63).

### BE-35.5: the splits (old hunk → new test)

Every split was copied by line range, not retyped. An ast comparison of every `assert`, `pytest.raises` and kit `check_*` call in the old function against the new file shows none missing: calculations 336 → 347, domain 203 present, credit 195 present, rules 97 → 101, ws1 33 → 33. The extra asserts are setup checks in fixtures and helpers. Each new test also passes alone (`-k`).

**`test_calculations.py::test_registry_snapshot_run_and_maturity_ladder`** (old :422-1800, 1379 lines) became 10 tests. The longest is 235 lines. Shared setup is in function-scoped fixtures `world`, `ran` and `all_runs`, plus the helpers `Loader`, `copy()`, `seeded_rule_versions()`, `lcr_inputs()`, `stressed_run()` and `run_ids()`.

| Old lines | New test |
|---|---|
| :425-595 | `test_registry_seed_matches_the_code_and_the_catalogue` |
| :597-724 | `test_modules_are_pure_exact_and_float_free` |
| :766-852 | `test_snapshot_refusals_replay_and_content_hash` |
| :854-1055 | `test_runs_golden_ladder_replay_and_refusals` |
| :1056-1061 | `test_background_execution_matches_the_inline_run` |
| :1062-1206 | `test_run_reads_manifest_contributions_and_masking` |
| :1208-1269 | `test_scenario_runs_comparison_boundaries_and_pages` |
| :1271-1395 | `test_rule_change_pinning_and_reruns` |
| :1397-1588 | `test_lcr_golden_liquidity_masking_and_rule_change` |
| :1590-1800 | `test_guards_refuse_changes_and_downgrades_round_trip_the_seed` |

**`test_domain.py::test_domain_projection_catalogue_and_guards`** (old :290-1053, 763 lines) became 8 tests. The longest is 172 lines. The BE-31.3 golden checks are unchanged in strength: (a) is in the v1 test on the original links-off path, and (b) and (c) are in the typed-runs test.

| Old lines | New test |
|---|---|
| :295-352 | `test_domain_seed_matrix_frozen_copies_and_typed_defects` |
| :354-397 | `test_domain_projection_refusals_and_switch_off_write_nothing` |
| :399-572 | `test_domain_v1_projection_matches_golden_and_links` |
| :574-654 | `test_domain_instrument_catalogue_filters_masking_and_lineage` |
| :656-766, :835-839 | `test_domain_typed_maturity_contract_and_projector_v2` |
| :767-834 | `test_domain_typed_runs_match_v1_and_keys_turn_ambiguous` |
| :841-966 | `test_domain_rate_terms_effective_dating_and_fx_status` |
| :967-1053 | `test_domain_append_only_guards_and_downgrade` |

**`test_credit.py::test_credit_domain_projection_snapshots_reads_and_guards`** (old :306-1016, 710 lines) became 7 tests. The longest is 143 lines.

| Old lines | New test |
|---|---|
| :310-391 | `test_credit_seed_schemas_vocabulary_and_rating_grants` |
| :393-524 | `test_credit_samples_project_once_with_lineage_and_links` |
| :526-582 | `test_credit_snapshot_identity_hash_and_ambiguous_key` |
| :584-678 | `test_credit_catalogue_reads_masking_and_lineage` |
| :680-796 | `test_credit_rating_proposals_decisions_and_supersede` |
| :797-883 | `test_credit_rating_reads_in_effect_and_switch` |
| :885-1016 | `test_credit_append_only_guards_and_downgrade` (it now makes its own credit snapshot) |

**`test_rules.py::test_governed_rule_changes_and_pinning`** (old :301-799, 499 lines) became 5 tests, all marked `ws3`. The longest is 201 lines.

| Old lines | New test |
|---|---|
| :311-361 | `test_rule_reads_and_the_closed_kind_list` |
| :363-573 | `test_rule_proposals_are_checked_for_every_parameter_kind` |
| :589-687 | `test_rule_maker_checker_refusals_and_rejection` |
| :576-586, :690-796 | `test_rule_activation_switch_and_pinning` |
| :799 | `test_stress_scenarios_follow_the_governed_change_pattern` (now projects its own ladder snapshot) |

**`test_ws1_verification.py::test_another_tenant_sees_and_changes_nothing_here`** (old :436-763, 328 lines) became 2 tests, both marked `ws1` and `ws3`, sharing a `cross_tenant()` helper. The route × role matrix stays one function.

| Old lines | New test |
|---|---|
| :458-583, :751-763 | `test_another_tenant_sees_nothing_here` |
| :586-749 | `test_another_tenant_changes_nothing_here` |

**LOW 5 fixed.** `test_security.py` now includes `"c\n"` among the unsafe ids. It checks the 404 envelope's `correlation_id`, and it formats `caplog` with the app's `JsonFormatter` to assert that no sent value reaches a log line. A mutation that swaps `fullmatch` back to `match` fails it.

**Counts and budget.**
- `--collect-only`: `86 tests collected` (59 before).
- Full suite on the merged tree: `86 passed, 1 warning in 406.14s`.
- **The D-60 180 s budget is not met.** Each test now builds its own state, and the `db` fixture cleans before and after every test. BE-35.5 stays unticked, and a "parallel backend tests" item is added under Deferred. No test was weakened or folded back to save time.

### FE-35.6: the 404 (root cause confirmed)

- **Cause.** The layout's `notFound()` does run on the server; the payload holds `NEXT_HTTP_ERROR_FALLBACK;404`. But `SessionProvider` renders only its loading block in the server HTML. Next sets the 404 status only when the error surfaces during the HTML render, so the status went out as 200.
- **Fix.** A new `src/middleware.ts`, matched only on `/advanced/stress-explorer/:segment/:path*`. A segment that is neither `new` nor a UUID (the layout's own pattern) is rewritten to the not-found page with `Cache-Control: private, no-cache, no-store`. The security headers still apply.
- **Checks.**
  - Status by path: `agent-demo`, `not-a-scenario` and `agent-demo/runs` give 404; a UUID, an upper-case UUID, `new` and the explorer root give 200.
  - Playwright: `advanced-nav`, `security-headers`, `stress-explorer`, `scenario-run` and `scenario-compare` pass.
  - A vitest pins the pattern and the static-sibling list.
  - Recorded in ARCHITECTURE.md under Frontend workspaces.
- **New LOW 13:** other `notFound()` calls below the session provider likely answer 200 the same way, for example `AdvancedGate` and `CompareGate` with their switches off. The page shown is right; the status isn't. No spec asserts those statuses.

### Final checks (merged tree)
- Backend:
  - ruff `All checks passed!`;
  - format `204 files already formatted`;
  - mypy `Success: no issues found in 204 source files`;
  - `openapi.json is up to date`;
  - full suite `86 passed`.
- Frontend:
  - `gen:api` gives no diff;
  - tsc clean;
  - `No ESLint warnings or errors`;
  - vitest `946 passed (946)`.
- Ticked: BE-35.4 and FE-35.6. Still open: BE-35.5 (the budget).
- D-60 was accepted by the user (D-63).
- **For the user:** CLAUDE.md step 4 still says "under 50 tests (D-26)". Changing it to the D-60 budget is the user's call.
