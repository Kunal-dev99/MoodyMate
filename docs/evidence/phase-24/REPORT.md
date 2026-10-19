# Phase 24: Scenario API, exploration and promotion, and the Advanced section (checks)

**Scope.** This report covers BE-24.1 to BE-24.5, FE-24.1 and FE-24.2 (WS4 RO-02, RO-06; D-44, D-47). The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/st-p24` by a backend-engineer, a test writer, two frontend-engineers (FE-24.1 "Advanced nav" and FE-24.2 "explorer"), one review agent and one e2e run. Nothing is committed yet. Migration 0026 is pre-numbered with `down_revision` "0023", because Phases 21 and 22 hold 0024 and 0025. The coordinator re-chains it at merge (protocol step 6).

## What was built

- **BE-24.1 Migration `0026_scenarios`** (revision 0026, down_revision 0023):
  - **`scenario`** is fully append-only: UPDATE, DELETE and TRUNCATE are refused. On insert the author must be a user of the row's tenant. The code is unique per tenant.
  - **`scenario_version`:**
    - It is written as DRAFT with no lifecycle fields. Its shocks change only while it is DRAFT.
    - The only moves are the D-44 §5 ones (`TRANSITIONS` :65-71), each at most once. Each move sets only its own columns (`MOVE_COLUMNS` :92-104).
    - The trigger also refuses `decided_by = authored_by` (Q6).
    - DELETE and TRUNCATE are refused.
    - CHECKs keep the lifecycle columns paired and consistent with the status.
  - **`scenario_change`** is append-only. A row can only be written once its version has left DRAFT, and it must repeat that version's shock at the same index, with the same template.
  - **Tagged seed `0026_scenario_permissions`** (D-47, user-approved): `scenario:propose` goes to risk_analyst and `scenario:approve` to approver. MATRIX :82-85 equals the plan's Feature 05 matrix and `governance/constants.py:145-151` (`SCENARIO_ROLE_PERMISSIONS`). `ROLE_PERMISSIONS` now also includes the scenario grants (:160).
  - **Downgrade:** it refuses while any scenario exists. Otherwise it removes the tables and only the tagged seed rows.
  - **ORM:** `Scenario`, `ScenarioVersion` and `ScenarioChange` in `app/orchestration/models.py`. `parameter_sources` is `JSONB(none_as_null=True)`; this was a bug fixed during the build.
- **BE-24.2 Templates and validators.**
  - `app/orchestration/scenario_templates.py` is pure. It holds the three template specs, `validate_shocks` (which returns value-free `{shock_index, field, code}` problems) and `shocks_sha256`.
  - Fractions are exact text padded to scale 4. Extra places are refused, never rounded.
  - Types are checked against the ACTIVE `cashflow_direction` rule, read through the new `RulesService.active_parameters(code)`.
  - Every pattern now uses `fullmatch`, after the review fix.
  - `GET /scenario-templates` (`calc:read`) returns the three templates, their bounds and allowed values, and `max_shocks` 5.
- **BE-24.3 Lifecycle routes.** `POST /scenarios`, `POST …/versions`, `PUT …/versions/{n}`, `…/confirm`, `…/submit`, `…/approval` and `…/close`, in `ScenarioService`:
  - Every refusal is audited DENIED. Audit details carry only the shocks' sha256, count and templates, never their text or a comment.
  - `scenario_change` rows are written when a version leaves DRAFT.
  - Close is any-of `scenario:propose|scenario:approve` at the route (`require_any_permission`, `core/auth.py:135`). The service then checks the permission that applies to the version's status (Q5).
  - `SCENARIOS_ENABLED: bool` (`core/config.py:130`).
  - **Review fix:** FUNDING_UNAVAILABLE `instrument_keys` are checked on write. A key must be an instrument of the tenant with a version of the selector's `instrument_type` that the author may see; otherwise the answer is 422 `unknown_instrument`, the same for absent and hidden keys. On read, keys are masked by clearance as `MaskedValue`, in both `shocks` and `changes`, and an unknown key is masked as `unclassified` (fail closed). This adds `DomainService.instrument_key_classes` and two tenant-scoped repository queries.
- **BE-24.4 Reads.** `GET /scenarios` defaults to the shared library (APPROVED_FOR_TEST). It also filters by status, origin and `mine`. `GET /scenarios/{id}` lists every version, CLOSED and REJECTED included. Every response carries `labels.statement` = `SCENARIO_STATEMENT` (`orchestration/constants.py:108`).
- **BE-24.5 Contract.**
  - `openapi.json` was exported first, then re-exported after the fix round. The fixes changed `instrument_keys` to `str | MaskedValue`, changed the `invalid_shock` details to `{"problems": [...]}`, and extended the parent_version 404 docstring.
  - Three PROVISIONAL rows were added at `docs/RETENTION.md:49-51`.
- **FE-24.1 Advanced section.**
  - `NEXT_PUBLIC_STRESS_EXPLORER_UI` (on unless set to 'off') in `features.ts`.
  - `advancedWorkspaces` in `navigation.ts`, with "Agentic Stress-Test Explorer" (`/advanced/stress-explorer`, `calc:read`, `FlaskConical`). `findWorkspace` includes it.
  - `sidebar.tsx` gains an `advancedNav` prop and draws the "Advanced" group between the main nav and Administration. `AppShell.tsx` passes the list in (Q11).
  - `AdvancedGate` (in `advanced/layout.tsx`) gives a 404 when the switch is off. `StressExplorerBanner` sits in `stress-explorer/layout.tsx` (Q14, Q15).
  - The AI Assistant placeholder is relabelled `{ phase: 'WS5', workstream: 'planned' }`, unconditionally (Q13).
  - Two breadcrumb labels were added to `PageHeader.tsx`.
- **FE-24.2 Explorer.**
  - **Library:** Shared library, My explorations (with a show-closed option) and Review (for `scenario:approve`).
  - **Editor:**
    - template cards come from the server's field list;
    - percent input is converted to exact scale-4 text with no float;
    - FUNDING_UNAVAILABLE has an instrument picker that uses `GET /instruments`;
    - up to `max_shocks` shocks can be combined, and there is no formula field.
  - **Detail:** every version, with status, approval label, origin, author, submitter, decider, comment, parent and sha256.
  - **Actions:** author-only Edit, Confirm and Submit; Approve and Reject (with a comment) for an approver who is not the author; "New version from vN". There are no run actions and no Close UI (Q16).

**Shared and existing files changed:**
- `conftest.py`: scenario cleanup runs first in `clean()` (:343-346; Q7).
- `test_calculations.py:1560`: the head pin moves from `"0023"` to `"0026"` (Q2, Q7).
- `test_ws1_verification.py`: four hunks (matrix, isolation, AUDITED, round trip). The matrix hunk teaches the any-of declaration.
- `test_rules.py:788-789`: part 5 calls `scenario_kit.check_scenarios`.
- `app/core/auth.py`: `require_any_permission` / `permission_alternatives`. A single-permission declaration behaves exactly as before.
- `frontend/src/components/common/PageHeader.tsx`: two breadcrumb labels.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration round trip (CLI, scratch `moodymate_scratch_p24`) | `Running upgrade 0023 -> 0026` / `0026 (head)` / `Running downgrade 0026 -> 0023` / `0023` / `Running upgrade 0023 -> 0026` / `0026 (head)`; scratch DB dropped ("dropped") |
| DB guards (psql, scratch) | `status move DRAFT -> APPROVED_FOR_TEST is not allowed`; `changes only while DRAFT; it is IN_REVIEW`; `the author never decides on a scenario_version (D-44 §6)`; `move IN_REVIEW -> APPROVED_FOR_TEST changes only its own fields`; `APPROVED_FOR_TEST -> IN_REVIEW is not allowed`; `a scenario_change repeats the shock of a version … that has left DRAFT`; DELETE, TRUNCATE (and UPDATE on `scenario`, `scenario_change`) `rejected` on all three tables |
| Downgrade while a scenario exists | `RAISE EXCEPTION 'scenarios exist; they are kept (D-02, D-44), so this downgrade is refused'` -> `alembic current` stayed `0026 (head)` |
| Seeded grants | `risk_analyst\|scenario:propose\|0026_scenario_permissions`, `approver\|scenario:approve\|0026_scenario_permissions` |
| WS1 (p24) | `4 passed, 1 warning in 36.65s` |
| WS1, rules, credit, calculations, domain, committed artifacts, config, auth (p24) | `17 passed, 1 warning in 120.48s` |
| Test writer (p24b) | `test_rules.py`: `1 passed, 1 warning in 30.91s`; WS1: `4 passed, 1 warning in 37.27s`; calculations + credit: `2 passed, 1 warning in 39.11s` |
| After the review fixes (`moodymate_st_p24_be_fix`) | `test_rules.py::test_governed_rule_changes_and_pinning`: `1 passed, 1 warning in 29.42s`; WS1 + rules + calculations: `6 passed, 1 warning in 78.63s` |
| WS1 matrix printout (platform_admin, data_steward, risk_analyst, approver, auditor) | three reads `calc:read 200 200 200 200 200`; POST scenarios / versions, PUT, confirm, submit `scenario:propose 403 403 422 403 403`; approval `scenario:approve 403 403 403 422 403`; close `scenario:propose\|scenario:approve 403 403 422 422 403` |
| Collected (D-26) | `49 tests collected in 0.45s` (49 -> 49) |
| ruff / format / mypy (venv) | `All checks passed!` / `161 files already formatted` / `Success: no issues found in 161 source files` |
| OpenAPI drift | before re-export: `openapi.json is out of date` (rc=1); after: `openapi.json is up to date` (rc=0) |
| `grep -c scenario docs/RETENTION.md` | `3` |
| Frontend tsc, lint | `tsc --noEmit` no errors / `✔ No ESLint warnings or errors` |
| vitest (advanced) | `advanced.test.ts (66 tests)`, `advanced-nav.test.ts (9 tests)`: `75 passed` |
| vitest (full, FE-24.2 step) | `Test Files 31 passed (31)`, `Tests 542 passed (542)` |
| `npm run build` | `BUILD_EXIT=0`, `/advanced/stress-explorer` routes in `app-path-routes-manifest.json` |
| Playwright `advanced-nav.spec.ts` + `stress-explorer.spec.ts` | first run `2 failed` (spec mistakes only: the persona label, and the all-null `selector`); after the spec fixes `4 passed (11.8s)` (`moodymate_test_p24fe`, 8024/3024); after the rulings `4 passed (10.5s)` (`moodymate_e2e_p24fe_fix`, 8124/3124) |
| `npm run gen:api` vs final `openapi.json` | **not in sync**: `schema.d.ts:3011` `instrument_keys?: string[] \| null`, while `openapi.json:3435` allows `string \| MaskedValue` items. CI step `npm run gen:api && git diff --exit-code src/api/schema.d.ts` (`ci.yml:76`) will fail |
| Full backend suite | **not yet run** (the coordinator runs it once) |
| Full Playwright | CI |

All runs used per-phase databases (`moodymate_scratch_p24`, `moodymate_test_p24`, `_p24b`, `moodymate_st_p24_be_fix`, `moodymate_test_p24fe`, `moodymate_e2e_p24fe_fix`). `moodymate_dev` was never touched. `moodymate_st_p24_be_fix` was left for the coordinator to drop.

## Plan ticks

I checked each Done-when against the code and the test lines myself. Each tick has an evidence line in DELIVERY_PLAN.md.

- **BE-24.1** ticked:
  - `test_ws1_verification.py:954-999` covers the downgrade refusal (rows unchanged), the round trip, seeded join == `ROLE_PERMISSIONS`, and every seed row tagged.
  - `scenario_kit.py:608-631` (`check_triggers`) covers the IN_REVIEW shock UPDATE, the backwards and reserved moves, and DELETE and TRUNCATE on all three tables.
  - **The architect checked the matrix:** migration MATRIX, `SCENARIO_ROLE_PERMISSIONS` and the plan table all match.
- **BE-24.2** ticked:
  - `scenario_kit.py:177-225` is the INVALID table with every listed case; `:277-303` drives it.
  - `:305-337` covers the templates accepted alone and combined.
  - `:228-274` covers the templates GET.
  - `test_ws1_verification.py:327-334` covers the reads.
- **BE-24.3** ticked:
  - `scenario_kit.py`: not_author :410-420; self-confirm :427-432; changes :433-442 and :481-484; invalid_status :465-468 and :515-518; missing comment :489-493; self_approval and promotion :495-514; library :451-457 and :530-538; CLOSED :540-564; switch-off :568-595.
  - The WS1 AUDITED list is at :697-704 and driven at :894-921.
- **BE-24.4** ticked: `test_ws1_verification.py:327-334`, :473 and :575-605; `scenario_kit.py:155-159` (`assert_labelled`) on every response.
- **BE-24.5** ticked: the drift check is rc 0 and RETENTION.md:49-51 has three rows. The Q1 rewording of the Done-when was not needed, because the fix round added one row per table and `grep -c` shows 3.
- **FE-24.1** ticked:
  - `advanced-nav.test.ts:68` (order), :134-177 (switch off) and :117-130 (WS5 label).
  - `advanced-nav.spec.ts:11` passed, with the banner asserted at :32.
  - Plan wording updated per Q11.
- **FE-24.2 not ticked.** Its tests pass, but they were run against types generated before the final contract. Missing:
  - regenerate `schema.d.ts`;
  - fix `scenarios.ts:228` to read `details.problems`, and the fixture at `advanced.test.ts:363`;
  - stop `editor.ts:237` from silently dropping masked keys;
  - re-run tsc, the vitests and both specs.

  See MEDIUM below.

These ticks rest on targeted runs. The open MEDIUM blocks the merge and the FE-24.2 tick, not the other ticks.

## Review outcome

- **MEDIUM (fixed, backend):**
  1. A trailing newline passed `re.match` with `$`, so `"USD\n"` was stored and then made `GET` return 500 for good. Now `fullmatch` is used everywhere, and the INVALID table has `"USD\n"` and `"0.2\n"` rows.
  2. Instrument keys were neither masked nor checked. They are now checked on write (`unknown_instrument`) and masked on read, asserted at `scenario_kit.py:338-349`.
  3. RETENTION.md had no scenario rows. Three rows were added.
- **MEDIUM (open, architect finding; must be fixed before merge, frontend-engineer FE-24.2):** the frontend did not follow the backend contract change.
  - `frontend/src/api/schema.d.ts` is stale, so CI `ci.yml:76` will fail.
  - `components/advanced/scenarios.ts:228` checks `Array.isArray(err.envelope.details)`, so a 422 `invalid_shock` now shows no problem lines.
  - `editor.ts:237` drops `MaskedValue` keys without telling the user when a version is loaded from a stored shock.
  - **Fix:** run `npm run gen:api`; read `details.problems` and update the fixture at `advanced.test.ts:363`; show masked keys as masked in the editor and say they cannot be carried over; then re-run tsc, `npx vitest run src/components/advanced` and both Phase 24 Playwright specs.
- **LOW findings:**
  1. `orchestration/schemas.py:178-199`: `ShocksIn = list[dict[str, Any]]`. A non-list `shocks`, or a non-object element, gets FastAPI's generic, unaudited 422 `validation_error`, not an audited `invalid_shock`, so the validator's `not_a_list` and `not_an_object` branches are never reached through the API. Fix: type it as `list[Any]` or `Any` and keep the JSON-schema override.
  2. `orchestration/router.py:60-61`: with `SCENARIOS_ENABLED=false`, a write whose body fails pydantic gets an unaudited 422 rather than the audited 409 `scenarios_disabled`. Accepted for now. An optional fix is a router-level switch dependency that runs before body validation.
  3. `core/auth.py:125-180`: the any-of permission declaration. I checked this against D-47 §1 ("close an approved version" belongs to `scenario:approve`) and the Feature 05 endpoint table ("`scenario:propose` or `scenario:approve`"). The user-approved model already covers it, so it is **not a hard stop**. The mechanism is recorded in the D-47 amendment.
  4. `ScenarioLibrary.tsx:134,144`: the Shared library tab shows the pill for `latest_status`, so a scenario with an approved v1 and a newer DRAFT v2 appears with a DRAFT or "Exploration" pill. Fix: in that tab, show `approved_version` with an APPROVED_FOR_TEST pill.
  5. (Architect) The Q8 ordering, as built, is not literally 403 -> 404 -> 422 -> 409. `PUT …/versions/{n}` checks `not_author` and `invalid_status` (409) before the body (422), and a missing `parent_version` is a 404 after the body check. Neither leaks scope, because the scenario is already scoped, and the PUT order is stricter. The D-44 amendment records the order as built.
  6. (Architect) The `test_rules.py` fold does not assert the DB-level `decided_by = authored_by` refusal (Q6, optional). It is shown only by the psql smoke test. One more statement in `check_triggers` would cover it.
  7. (Architect) The `orchestration/constants.py:151` comment still describes `invalid_shock` details as a bare list. The docstrings and openapi.json have the Q9 shape.
  8. (Architect) The "missing comment gets 422" check (`scenario_kit.py:489-493`) asserts only the status, not the error code. This matches the Done-when, so the check is minimal but sufficient.

## Rulings (auto-resolved)

**Recorded in DECISIONS.md:**
- **D-44** (dated amendment 2026-10-19, Phase 24 build, auto-resolved):
  - Q3: the ShockProblem vocabulary, including `unknown_instrument`; unknown key names are never echoed; `fullmatch`.
  - Q9: the exact `details = {"problems": [...]}` shape; 0-based `shock_index` in both the 422 details and `scenario_change`.
  - Q8: the scoped 404 always comes before body validation, recorded as built (see LOW 5).
  - Q6: the DB self-approval refusal, and `scenario` fully append-only.
- **D-47** (dated amendment 2026-10-19, Phase 24 build, auto-resolved), Q5 and the any-of mechanism:
  - close permission by status;
  - the 403 `details.required_permission` names the single applicable permission;
  - 409 `not_author`, and 409 `invalid_status` for DRAFT, CLOSED, RUNNING and TESTED.
  - No change to who can do what.

**Report notes (auto-resolved):**
1. **Q1 RETENTION rows.** These are coordinator-owned docs. The fix round added three rows (`RETENTION.md:49-51`), so the original Done-when holds verbatim and no rewording was made.
2. **Q2 Re-chaining.** At merge, if 0024 or 0025 merged first, set 0026's `down_revision` to the merged head and keep the head pin `"0026"` while 0026 is the newest. Then re-run `test_migration_round_trip_and_downgrade_guards`.
3. **Q4 parent_version 404.** The docstring now reads "scenario or parent_version not found; another tenant's id answers the same". openapi.json was re-exported.
4. **Q7 Shared test hunks.** The `conftest.py` scenario cleanup and the `test_calculations.py` head pin belong to the test writer. Precedent: the Phase 20 report, Q6.
5. **Q10 Phase 23 nav list.** At merge, if master's nav changed after a290978, refresh `PHASE_23_MAIN` and `PHASE_23_ADMIN` in `advanced-nav.test.ts`.
6. **Q11 Plan wording.** FE-24.1 now reads "`AppShell` (rendered by `(platform)/layout.tsx`) passes it in". Applied in DELIVERY_PLAN.md.
7. **Q12 page.tsx ownership.** FE-24.2 owns it. Exactly one banner is asserted with `toHaveCount(1)` in the existing compose test.
8. **Q13 AI Assistant relabel.** It is unconditional. It corrects a placeholder label, and the sidebar items, order and gating are unchanged, so the switch-off still restores the Phase 23 sidebar.
9. **Q14 layout.tsx ownership.** FE-24.1 owns `stress-explorer/layout.tsx`, and FE-24.2 does not edit it.
10. **Q15 Banner placement.** The banner stays in the shell layout, which is the stricter D-44 §8 guarantee.
11. **Q16 Close UI.** Deferred as an unscheduled follow-up: a hide action with the D-47 §1 split shown cosmetically. A note was added before FE-25.1 in DELIVERY_PLAN.md.
12. **Q17 e2e step 5.** risk_analyst gets 403 from the dependency before `self_approval` (WS1 row `403 403 403 422 403`). The missing Approve button is asserted while the version is IN_REVIEW.
13. **e2e selector.** Stored and hashed shocks use `"selector": {}`. The all-null selector comes only from serialising the response model, so `shocks_sha256` and Phase 25 reproducibility are unaffected.
14. **Test budget.** 49 collected, 49 -> 49. No test function was added.

## Open items

**Blocking merge (coordinator):**
- Fix the open MEDIUM (frontend contract sync), then re-tick FE-24.2 with the result lines.
- Run the full backend suite once.
- Confirm CI is green, including the `gen:api` diff and the full Playwright run, and cite it here.
- At merge, re-chain 0026 and refresh the Phase 23 nav list if needed (notes 2 and 5).
- Drop `moodymate_st_p24_be_fix`.

**Not blocking (architect, docs):**
- `docs/ARCHITECTURE.md` has no entry yet for D-44, D-47, `app/orchestration/` or the three scenario tables. It needs an update after merge.

**For the client before production (hard stops; not blocking the build):**
- **Exploration model (D-47):** whether a self-confirmed exploration scenario is acceptable in production, and whether promotion needs a committee or an ALM role. The grants are PROVISIONAL (D-11).
- **Illustrative content:** the templates, bounds (for example the `increase_fraction` cap) and labels are illustrative. Every response and page states "not a regulatory stress test … no parity with any vendor system is claimed". No regulatory certification or vendor parity is claimed.
- **Retention period** for `scenario`, `scenario_version` and `scenario_change`, set by Compliance.
- **Funding-source selection:** counterparty selection and a real funding-facility attribute are deferred (D-44 §3).

No new hard stop arose in this phase. The close-by-approver permission is within the user-approved D-47 §1.

**Not checked:**
- a build or e2e run with `NEXT_PUBLIC_STRESS_EXPLORER_UI=off` (covered by vitest only);
- the pages by hand, beyond what the two Playwright specs click through;
- the editor's 422 problem display against the final contract (see MEDIUM).

Files edited in this step:
- `/home/user/MoodyMate-wt/st-p24/docs/DELIVERY_PLAN.md`
- `/home/user/MoodyMate-wt/st-p24/docs/DECISIONS.md`
## Coordinator follow-up (merge preparation)

- **Open MEDIUM fixed (frontend contract sync).**
  - `scenarios.ts` now reads the `{problems: [...]}` details of `invalid_shock`.
  - Masked `instrument_keys` render as `MaskedValue`, and a masked value is never sent in a request.
  - Masked picks block saving until they are removed. This is the stricter option and is
    auto-resolved (D-36).
  - Results: vitest 543 passed; lint and tsc clean; `npm run build` passed;
    `stress-explorer.spec.ts` 3 passed; `advanced-nav.spec.ts` 1 passed. FE-24.2 is ticked.
- **Full backend suite:** 49 passed. ruff, format, mypy and the OpenAPI drift check are clean.
- **Q10 (nav list).** Master's navigation has not changed since `a290978`, so
  `PHASE_23_MAIN/ADMIN` stand.
- **Coordinator rulings on the fix agent's remaining questions** (auto-resolved, safer
  default):
  1. **Shared shock models.** Requests and responses keep sharing the shock models, so
     `MaskedValue` stays in the request type. The server refuses it with 422 `not_text`.
     This is the smaller change.
  2. **A key held by several source systems** takes the most sensitive classification among
     their latest versions. A key that no longer matches any instrument is masked as
     `unclassified`.
  3. **Null selector fields.** The response keeps them. Stored data and hashes already leave
     them out.
  4. **RETENTION.md row.** It follows the Feature 04 and WS3 rows. A docs edit is within
     BE-24.5.
