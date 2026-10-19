# Phase 25: Deterministic scenario engine and baseline comparison (checks)

**Scope.** This report covers BE-25.1 to BE-25.4, FE-25.1 and FE-25.2 (WS4 RO-02, RO-07, FC-08; D-44, D-45, D-46, D-47).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f05-p25` by:
  - a backend-engineer and a test writer;
  - two frontend-engineers (FE-25.1 "run panel" and FE-25.2 "comparison");
  - one review agent, one e2e run, and a backend and a frontend fix round.
- **Nothing is committed yet.**
- **Migration 0027 is pre-numbered** with `down_revision` "0026". Phases 21 and 22 hold 0024 and 0025, and Phase 26 holds 0028. The coordinator re-chains it at merge (protocol step 6, Q5).

## What was built

- **BE-25.1 Migration `0027_scenario_runs`** (revision 0027, down_revision 0026). It adds four tables:
  - **`scenario_run`** is append-only. `scenario_run_guard()` refuses UPDATE, DELETE and TRUNCATE. On insert it checks the version, scenario, tenant and `shocks_sha256`, and that the baseline belongs to the tenant.
  - **`calculation_scenario_pin`, `scenario_contribution` and `scenario_shock_application`** sit under 0018's `calc_output_guard()`: they are written only while their run is RUNNING, then append-only. Each also has an insert-check trigger:
    - the pin repeats its `scenario_run` and copies the baseline's pins exactly;
    - the contribution repeats the event lineage, a member import of the snapshot, a ladder row of its own run, currency and bucket, and an instrument version of the tenant;
    - the application names a shock in the run's pinned subset.
  - **`scenario_contribution`** is `UNIQUE (calculation_run_id, cashflow_event_id, part_path)`. The part path is CHECKed as `^0(/[1-5][MSA])*$`, and `UNCHANGED` holds exactly when the path is `"0"`.
  - **No seed.** `intermediate_value`, `calculation_input_ref.ref_type` and `calculation_warning.code` are untouched (D-01).
  - **Downgrade** (:449-456): it refuses while any of the four tables has a row.
- **BE-25.2 Overlay** (`app/calculations/scenario_overlay.py`). It is pure, Decimal only and exact:
  - the three templates, applied in order to parts;
  - `uuid5` part ids for shocked flows only (Q9), with at most 32 parts per flow;
  - counts for direction mismatches, past-due and incomplete flows;
  - the `run_overlay` seam.

  **Golden pack** `golden/scenario_overlay_v1_engineering_cases.json`: nine cases, status `ENGINEERING — awaiting domain-expert approval`. Case 1 is the §5 example. Its `tolerance` map follows the house convention in `golden/README.md:31-35`, where `"0"` means exact.
- **BE-25.3 Engine and run route** (`calculations/service.py`, `repository.py`, `models.py`, `router.py`, `constants.py`, `schemas.py`; `orchestration/service.py`, `repository.py`, `models.py`, `router.py`):
  - **Stressed runs:**
    - they copy the baseline's pins and add a `scenario` manifest section;
    - they run the unchanged module on the parts and write no `intermediate_value` rows;
    - `result_sha256` covers the parts and applications;
    - a re-run re-pins the same version and subset after CLOSED.
  - **Manifests of non-stressed runs** omit `scenario`, so they keep the Feature 03 key set. `RunSummary.scenario` is a nullable, additive field (Q14).
  - **Validation dry run** (`calc:read`).
  - **`POST scenarios/{id}/run`** (`calc:run`):
    - Idempotency-Key, k + 1 member runs, and the `scenario.run` audit;
    - every refusal is audited DENIED, in the documented order (`orchestration/router.py:320-328`, Q13).
  - **Review fix (MAJOR):** the run detail now masks picked instrument keys in `manifest.scenario.shocks`. It uses the shared `DomainService.mask_instrument_keys` and `CalculationService.shown_manifest`. The contract types `instrument_keys` as `string | MaskedValue`, and `shocks_sha256` stays the pin over the stored keys.
  - **Ruling Q23:** an optional `stressed` filter on `GET /calculation-runs`.
- **BE-25.4 Reads:** `GET scenario-runs`, `GET scenarios/{id}/comparison` and `…/comparison/contributions`.
  - Deltas are exact, with turns-negative flags.
  - `breaks_only_combined` is `null` when not assessable (Q12).
  - The largest changes are ranked per currency over clear deltas only (Q12).
  - The templated summary cites only clear figures.
  - The drill-down links each part to its `scenario_contribution` row and its source row.
  - D-46 masking counts distinct changed flows and depends on the caller's clearance (Q11, Q17, Q28).
- **FE-25.1** (`ScenarioRunPanel.tsx`, `scenario-runs.ts`, `StressedRunBadge.tsx`):
  - **The panel** sits on CONFIRMED_FOR_EXPLORATION and APPROVED_FOR_TEST versions, behind `NEXT_PUBLIC_STRESS_COMPARE_UI`. It holds the baseline picker, the dry-run counts (Run fails closed on a 0 match), the reused pins, and submission with an Idempotency-Key that is reused on retry.
  - **Stressed-run badges** on the run list and run detail. Per Q21 they are not behind the switch.
  - **`STRESS_COMPARE_UI_ENABLED`** in `features.ts`. It is forced off when the explorer is off.
  - **Post-check labels** for `SCENARIO_OVERLAY` and `SCENARIO_CONSERVATION` in `calculations.ts`.
  - **Rulings:**
    - Q27: the runs list is mounted in `ScenarioView.tsx`, and the run result has a "Compare with baseline" link, both gated.
    - Q25: the stale comment in `VersionActions.tsx` is updated.
- **FE-25.2:**
  - **Pages:** `/advanced/stress-explorer/[scenarioId]/runs` and `/runs/[scenarioRunId]`, under a layout that returns 404 when the switch is off. Components: `ComparisonView`, `ComparisonDrilldown`, `WhatIfComparison`, `DeltaValue`, `ScenarioRunsList`, `CompareGate`, and `comparison.ts`.
  - **The view:**
    - per-currency bucket tables and a heat map, shaded by exact BigInt comparison;
    - columns for each shock alone and for the combined run;
    - turns-negative markers and a combined-only highlight;
    - the largest changes, the first negative cumulative bucket and the summary;
    - a drill-down that leads to the source row.
  - **"What if" (Q30):** it names the parent run used (id, created time, link) and shows an empty state when there is none.

**Shared and existing files changed:**
- `test_calculations.py`:
  - part 2 calls `check_overlay_pure` (:573);
  - part 5b adds `check_lcr_baseline` (:1404);
  - part 5c calls `check_scenario_runs` (:1161);
  - part 6 adds the guard loop over `SCENARIO_OUTPUT_TABLES` (:1580-1598), `check_scenario_append_only` (:1603), the 0027 refusal and round trip with `intermediate_value_shape` (:1606-1626), and the head pin `"0027"` (:1605).
- `test_ws1_verification.py`, four hunks: the matrix (:351-371), the isolation list (:430 and :496-510), AUDITED (:743) and the DENIED drive (:963-969).
- `conftest.py`: one cleanup hunk for the four tables (Q18).
- `helpers.py`: `events()` now orders by `occurred_at, ctid`. This is a fixer change beyond the rulings; see LOW 6.
- `app/domain/service.py`: the shared `mask_instrument_keys`.
- `advanced.test.ts`: the FE-24.2 "no run action" test is rewritten (Q24).
- `backend/openapi.json` and `frontend/src/api/schema.d.ts`: regenerated. `schema.d.ts` contains `stressed?: boolean | null` (:8317), `ManifestScenario` (:4595) and the Q10 description (:7569).

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration round trip (CLI, scratch `moodymate_scratch_p25`) | `Running downgrade 0027 -> 0026` / `0026` / `Running upgrade 0026 -> 0027` / `0027 (head)`; four tables listed; "scratch DB dropped" |
| Downgrade with rows (temporary e2e test) | `refused: ...; they are kept (D-02, D-45), so this downgrade is refused` |
| DB guards (temporary e2e test) | `scenario_run is append-only (D-45 §9): UPDATE rejected`; `scenario_contribution is append-only (D-31 §5): DELETE rejected`; insert after finish: `refused: scenario_shock_application rows are written only while their run is RU...` |
| Backend engineer, first targeted run | WS1 `1 failed` (`Extra items in the left set: 'POST /api/v1/scenarios/{scenario_id}/run'`). After the AUDITED hunk: `4 passed, 1 warning in 33.90s` |
| `test_calculations.py` before the rulings | `KeyError: 'snapshot_id'` (the kit did not project its imports, Q1); `1 failed` |
| After the fix round (`moodymate_test_p25fix`) | first `('calculation_run.finish', 'SUCCESS') != ('calculation_run.create', 'SUCCESS')`, then `Regex: 'LCR results.*this downgrade is refused'`; after both test fixes `test_registry_snapshot_run_and_maturity_ladder`: `1 passed, 1 warning in 35.66s` |
| Targeted rules + WS1 + calculations (p25fix) | `6 passed, 1 warning in 94.72s (0:01:34)` |
| Collected (D-26) | `49 tests collected in 0.39s` (49 -> 49) |
| ruff / format / mypy | `All checks passed!` / `25 files already formatted` (files changed) / `Success: no issues found in 159 source files` (the 11 + 6 kit errors are fixed without ignores, Q4) |
| OpenAPI drift | `openapi.json is up to date`, exit=0 |
| Frontend gen:api / tsc / lint | `src/api/schema.d.ts [422.2ms]` / `tsc exit 0` / `✔ No ESLint warnings or errors` |
| vitest (full, FE-25.2 step) | `Test Files 35 passed (35)`, `Tests 615 passed (615)` |
| vitest (advanced, after the FE rulings) | `Test Files 4 passed (4)`, `Tests 119 passed (119)` |
| `npm run build` | e2e: exit=0, `/advanced/stress-explorer/[scenarioId]/runs` and `/runs/[scenarioRunId]` built; FE fix round: `build exit 0` |
| Playwright `scenario-run.spec.ts` + `scenario-compare.spec.ts` | e2e (`moodymate_test_p25fe`, 8025/3025): `5 passed (13.5s)`; after the FE rulings (`moodymate_e2e_p25fefix`, 8761/3761): `5 passed (12.6s)` |
| Post-e2e DB check | `scenario_run\|2`, `scenario_contribution\|228`, `calculation_scenario_pin\|6`, `scenario_shock_application\|84`, `calculation_run\|8` |
| Ad-hoc browser check (data_steward) | `runs rows on scenario page: 1`, `what-if state: what-if-none`; `1 passed (5.7s)` |
| Full backend suite | **not yet run** (the coordinator runs it once) |
| Full Playwright | CI |

All runs used per-phase databases: `moodymate_scratch_p25` (dropped), `moodymate_test_p25`, `_p25b`, `_p25fix`, `_p25fe` and `moodymate_e2e_p25fefix` (a `-T moodymate_dev` copy). `moodymate_dev` was never touched, and no `backend/.env` was created.

## Plan ticks

I checked each Done-when against the code and test lines myself. Each tick has an evidence line in DELIVERY_PLAN.md.

- **BE-25.1 ticked.**
  - `0027_scenario_runs.py` (guard :449-456).
  - The CLI round trip.
  - `test_calculations.py`: guard loop :1580-1598; `scenario_run` append-only at :1603 (`scenario_run_kit.py:1037-1048`); refusal, round trip and frozen `intermediate_value` shape at :1613-1626.
- **BE-25.2 ticked.**
  - `scenario_run_kit.py:242-370`: the §5 case :254-295, stable ids :296-305, the golden loop and the `>= 9` cases :307-370, and the `app.ai` grep over calculations and orchestration :248-252.
  - The existing no-float and `app.ai` grep at `test_calculations.py:565-570`.
  - The pack has status `ENGINEERING — awaiting domain-expert approval` and nine named cases.
- **BE-25.3 ticked.** Evidence is in `scenario_run_kit.py` (`check_scenario_runs`):
  - Feature 03 key sets :557-571;
  - validation 200 for all roles :577-579;
  - 403 for the other roles and 201 :590-597;
  - replay :609-612;
  - manifest equal except `scenario` :613-622;
  - the §5 figures end to end :626-638;
  - no `intermediate_value` rows :649-650;
  - k + 1 runs :652-674;
  - picked-key masking on the run detail :690-718;
  - the tampered part :877-908;
  - refusals :910-938, plus `check_lcr_baseline`;
  - re-runs after CLOSED :940-950.

  The WS1 Done-when was reworded per Q16.
- **BE-25.4 not ticked.** Every listed assertion exists and passes: `scenario_run_kit.py:720-875`, and `test_ws1_verification.py:361-371`, :430 and :496-510. But the open review MEDIUM (below) may change the masking that this task asserts. Tick it after the resolver rules and any fix is re-run.
- **FE-25.1 not ticked.** Playwright (`scenario-run.spec.ts:94`, :184) and the vitest (`scenario-run.test.ts:148-247`) pass. But the frontend half of Q23 is not done: `scenario-runs.ts:76` still calls `GET /calculation-runs` without `stressed: false`. The Q22 and Q26 wording is applied in the plan.
- **FE-25.2 not ticked.** Playwright (`scenario-compare.spec.ts:98`) and the vitest (`comparison.test.ts:145-389`) pass. But the UI does not follow the Q12 change in what `null` means (architect MEDIUM below).

## Review outcome

- **MAJOR (fixed, backend).** The run-detail manifest returned picked `instrument_keys` in clear, which skipped the D-33 masking of the scenario detail.
  - The fix masks on read through `CalculationService.shown_manifest` on both GET and POST create/replay, and changes the contract to `string | MaskedValue`.
  - It is asserted at `scenario_run_kit.py:690-718`, using a classification double, because the seeded `instrument_id` is `internal`. Results: data_steward sees the key in clear; risk_analyst gets `MaskedValue`, and "SC-LOAN-2" appears nowhere in the response; the stored manifest and hash are unchanged.
- **MEDIUM (open, review; must be resolved before merge).** In `orchestration/service.py:1408-1445`, a delta changed by fewer than `CALC_MIN_AGGREGATE_COUNT` distinct flows is masked. But that cell's own `baseline` and `stressed` figures stay clear in the same response, and so does the stressed run's own ladder. So the masked delta can be recovered exactly as stressed minus baseline.
  - Behaviour is unchanged so far. The kit comment (`scenario_run_kit.py:774-779`) now says that only the delta field is masked, and that this is a D-46 §5 residual risk.
  - **Coordinator:** send this to the decision-resolver. The options are:
    - the stricter fix: also mask the stressed figure, and the stressed run's ladder rows, whenever the delta is masked for the few-flows reason;
    - or record it explicitly as the D-46 §5 client open item.

    Then re-run and tick BE-25.4.
- **MEDIUM (open, architect finding; FE-25.2 frontend-engineer).** After Q12, `breaks_only_combined: null` can also mean "not assessable": k = 1, or a single-shock member that was not compared.
  - `ComparisonView.tsx:254-256` renders every `null` as "combined-only: masked", and `comparison.ts:213` and the test at `comparison.test.ts:252` say the same. So every single-shock scenario shows a false "masked" marker.
  - **Fix:** show "not assessable" when no masked figure is read, or a neutral "unknown" that names both reasons. Update the comment and the vitest, then re-run `scenario-compare.spec.ts` against the fixed backend.
- **LOW findings:**
  1. `orchestration/service.py:1073-1075`: `scenario_run` is committed and audited SUCCESS before its members are created. If a member's `calc.create` then refuses (for example, a race into `baseline_not_reproducible`), the client gets a 409, but an append-only `scenario_run` with missing members stays behind. Fix: run the member pin and manifest checks before the commit, or return the partial `scenario_run`, which a replay completes (Q13).
  2. `orchestration/service.py:1103` -> `calculations/service.py:1337`: member runs use the hard-coded 900 s stale window, not `settings.CALC_RUN_STALE_SECONDS`. Fix: pass the setting through the router and the service.
  3. `test_ws1_verification.py:496-503`: the comparison and contributions isolation tuples use a random `scenario_run_id` on the real scenario. So a dropped tenant filter in `ScenarioRepository.scenario_run` would not be caught. Fix (+0 tests): use a real `scenario_run_id`, and add `GET /scenario-runs?scenario_id=<real>` to the `total == 0` loop.
  4. `orchestration/router.py:258-277`: any `calc:read` role can call the validation dry run repeatedly, and each call loads the full snapshot and runs up to five overlays. This is accepted, because the permission table requires it. Optionally, cache the counts per `(baseline_run_id, shocks_sha256)`.
  5. (Architect) Q23 is built as "the manifest has a `scenario` section", not "a `calculation_scenario_pin` row exists". Both mark the same runs, because the pin and the manifest section are written together. For `stressed=false` the manifest test is the stricter one, since a crashed RUNNING run with a scenario manifest but no pin is still excluded. No change needed.
  6. (Architect) The fixer changed the shared `helpers.events()` to order by `occurred_at, ctid`, a change beyond the rulings. Ordering is more correct than relying on table storage, but every test that reads audit trails depends on it, so the full backend suite must confirm it. In the same round, `clear_scenario_runs` also clears the Phase 24 scenario tables, through conftest's `_SCENARIO_CLEANUP`.
  7. (Architect) `test_calculations.py:1612` pins `m27.down_revision == "0026"`, and :1605 pins head `"0027"`. Both need updating at re-chain (Q5), and the head pin again when 0028 lands.
  8. (FE fix round) After a run from the panel, the runs list on the same page does not refresh until the page is reloaded. The compare link in the run result covers the immediate need.
  9. (FE fix round) `next.config` has no setting for a separate build folder, so the fix-round build went into the worktree's own `.next`. D-03 asks for separate output folders for concurrent Playwright builds. The e2e run used a copied frontend for this reason.
  10. (FE-25.2) The drill-down's moved/stayed/added filter applies only to the loaded page, and is labelled "Filters this page of N" (Q29).

## Rulings (auto-resolved)

**Recorded in DECISIONS.md (dated amendments 2026-10-19, Phase 25 build, auto-resolved):**
- **D-44, Q21:** stressed-run badges are not behind `NEXT_PUBLIC_STRESS_COMPARE_UI`. This is a narrow D-02 exception for the §8 labels. Runs that are not stressed render as in Phase 24.
- **D-45:**
  - Q8: `SCENARIO_OVERLAY` checks the stored rows against the recomputation through `run_overlay`, and `SCENARIO_CONSERVATION` checks the arithmetic. The seam is the only production path.
  - Q9: the part-id and `DEPOSIT_WITHDRAWAL` shape (STAYED plus ADDED; `uuid5` ids for shocked flows only; ADDED counts toward the 32-part cap).
  - Q10: per-shock counts apply each shock alone. `skipped_past_due` means "days <= 0".
  - Q13: the refusal order, recorded as built (`router.py:320-328`); `scenarios_disabled` on a stressed re-run; a replay completes crash-missing members from the recorded pins.
- **D-46:**
  - Q11: direction counts and the union for net; a cumulative delta stays masked even at zero.
  - Q17: masking applies from the masked bucket itself onward, per member run.
  - Q12: the succeeded family; `breaks_only_combined` is `null` when not assessable; the largest changes are ranked per currency.

**Report notes (auto-resolved):**
1. **Q1:** the kit projects both imports (201) before the snapshot (`scenario_run_kit.py:549-550`). The shared `load` is unchanged.
2. **Q2:** `shock_subset` is sent as a repeated int list (:789, :841).
3. **Q3:** NOTHING_SHOCK is a valid LOAN selector with `due_to_day: 1` that matches nothing (:426-431). The 422 `unknown_instrument` refusal is unchanged.
4. **Q4:** `switched_off` is typed `dict[str, Any]` (:919), and six further kit mypy errors are fixed without ignores.
5. **Q5:** at merge, re-chain 0027's `down_revision` to the last merged head, update `test_calculations.py:1612`, and re-run the round trip. Phase 26 keeps 0028 on top of 0027.
6. **Q6:** a `calculations/service.py` conflict with BE-21.2 (`create`, `_manifest`, `_pins_of` and `_rerun_pins`, `result_document`) goes to the Phase 25 backend engineer. The merged tree must keep the Feature 03 key sets and hash reproduction. Then the full suite runs.
7. **Q7:** `ruff format` over the test writer's files is accepted if the diff is formatting only (coordinator to confirm). From now on, agents format only the files they own.
8. **Q14:** the exact Feature 03 key sets are asserted for both the non-stressed manifest and the result document (:564-571).
9. **Q15:** the backend engineer owns the golden file. The fixer's review found it matches the kit schema, plus a top-level `tolerance` and a per-case `note`. Both are house convention and ignored by the exact comparison.
10. **Q16:** the WS1 matrix proves "not refused" and 403 for the others. The real 201 and 200s are in part 5c. The BE-25.3 and BE-25.4 Done-whens are reworded in the plan.
11. **Q18:** `conftest.py` keeps exactly one cleanup hunk (the test writer's), with children deleted before parents.
12. **Q19:** after the test writer's accidental `git stash` / `stash pop`, the coordinator checks that `git stash list` is empty. The backend engineer's later passing targeted runs indirectly show its files are intact. No agent runs stash, checkout, reset or clean in the phase worktree.
13. **Q20:** one switch, `STRESS_COMPARE_UI_ENABLED`, gates both panels. Already true.
14. **Q22 and Q26:** the plan wording is applied: SUCCEEDED or SUCCEEDED_WITH_WARNINGS with exactly three runs, and "Approved scenario (illustrative)".
15. **Q23:** the backend filter has landed and is asserted (:681-689). The frontend half is **open** (FE-25.1 not ticked).
16. **Q24:** the FE-24.2 "no run action" test is rewritten as "no run action outside ScenarioRunPanel", which is stricter.
17. **Q25 and Q27:** the comment fix, the mounted runs list and the compare link are done in hunks owned by FE-25.1.
18. **Q28:** the few-flows delta takes the changed flows' classification and is masked by clearance (asserted at :780-786). See the open MEDIUM for its limit.
19. **Q29:** a server-side `change` filter on contributions is an open follow-up for a later phase.
20. **Q30:** the "what if" view names the parent run it uses and shows an empty state when there is none. A picker is deferred to Phase 26.

## Open items

**Blocking merge:**
- **Coordinator:** get a resolver ruling on the review MEDIUM (few-flows delta derivable by differencing). Then apply the fix or record the client item, re-run, and tick BE-25.4.
- **FE-25.2 frontend-engineer:** fix the architect MEDIUM (`null` `breaks_only_combined` shown as "masked"). Then re-run the vitest and `scenario-compare.spec.ts`, and tick FE-25.2.
- **FE-25.1 frontend-engineer:** the picker passes `stressed: false` (`scenario-runs.ts:76`) and keeps the client filter. Then re-run `scenario-run.test.ts` and both specs, and tick FE-25.1.
- **Coordinator:** run the full backend suite once. Its first real run covers the `helpers.events()` ordering change (LOW 6).
- **Coordinator:** confirm CI is green, including the `gen:api` diff and the full Playwright run.
- **Coordinator at merge:**
  - re-chain 0027 and update `test_calculations.py:1612` (Q5);
  - resolve the `calculations/service.py` conflict with Phase 21 (Q6);
  - keep one conftest hunk (Q18);
  - confirm the format-only diff (Q7);
  - check that `git stash list` is empty (Q19).
- **Drop** `moodymate_test_p25`, `moodymate_test_p25b`, `moodymate_test_p25fix`, `moodymate_test_p25fe` and `moodymate_e2e_p25fefix`.

**Not blocking:**
- LOW 1 to 4 (backend) and LOW 8 (runs list refresh) are follow-ups.
- `docs/ARCHITECTURE.md` needs entries for D-45 and D-46, `scenario_overlay.py` and the four Phase 25 tables after merge. It also still lacks the Phase 24 entries.
- A server-side `change` filter on contributions (Q29) and a parent-run picker for "what if" (Q30).

**For the client before production (hard stops; not blocking the build):**
- **Golden pack:** `scenario_overlay_v1_engineering_cases.json` is awaiting domain-expert approval (D-35 §4). The approver is not yet named.
- **D-46 §5 residual risk:** differencing between scenarios and the baseline can narrow down amounts. This is sharpened by the open MEDIUM, depending on how it is ruled. D-46 is PROVISIONAL.
- **Exploration model (D-47):** carried over from Phase 24.
- **Illustrative content:** templates, bounds and labels are illustrative. Every response carries the statement. No regulatory certification or vendor parity is claimed.
- **Retention period** for the four new tables, set by Compliance.

No new hard stop arose in this phase. There is no destructive migration, no change to roles or permissions, and no real client data. The Q21 badge exception is a D-02 matter, not a D-01 exception.

**Not checked:**
- the full backend suite;
- a build or e2e run with `NEXT_PUBLIC_STRESS_COMPARE_UI=off` (vitest only);
- the e2e specs against the final backend. Both Playwright runs may have come before the backend fix round, so re-run them after the FE fixes above;
- the "parent run found" what-if state in a browser (vitest only).

Files edited in this step:
- `/home/user/MoodyMate-wt/f05-p25/docs/DELIVERY_PLAN.md`
- `/home/user/MoodyMate-wt/f05-p25/docs/DECISIONS.md`
