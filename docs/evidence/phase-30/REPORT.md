# Phase 30: Methodology transparency (checks)

**Scope.** This report covers BE-30.1 to BE-30.4 and FE-30.1 (Workstream C, the part that can be built now: FC-03, FC-04 and FC-07; D-56).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f06-p30` by:
  - a backend-engineer and a test writer;
  - one frontend-engineer, who owns FE-30.1;
  - one review agent, one e2e run, and a backend and a frontend fix round.
- **No migration.** The changes are additive response fields and docs only. The Alembic head stays at 0027, so no revision was pre-numbered.
- **Hashed documents are unchanged.** `validation_status` and `context` live only in the response wrapper (D-56 §4).
- **Commits.** The coordinator commits and merges.

## What was built

- **BE-30.1 Methodology docs**: `docs/methodology/maturity_ladder.md`, `lcr_hqla.md` and `scenario_overlay.md`.
  - Each has the 10 required headings and cites file:line and rule codes.
  - The units sections quote `context.units` word for word (`calculations/constants.py:176-180`).
  - The rounding sections of `maturity_ladder.md` and `lcr_hqla.md` say that trailing zeros past the scale are accepted as an exact value (Q6).
  - `lcr_hqla.md:37-39` states that the cap flag uses strict `>` (`lcr_hqla.py:427`, Q8).
- **BE-30.2 `validation_status`** (`rules/constants.py:105-156`):
  - `ValidationStatus` has three values. `ValidationRecord.__post_init__` refuses a validated status unless it names an approver, `approved_on` and `evidence_ref`.
  - `MODULE_VALIDATION` has five entries, all ILLUSTRATIVE; three of them are PLANNED (0021). An unknown (code, version) reads as ILLUSTRATIVE.
  - Where it appears:
    - the registry, through `validation_status_of` (`rules/router.py:144`);
    - run summary and detail, results, ladder and LCR, through `CalculationService.validation_status(run)` (`calculations/service.py:2329-2333`), which reads the pinned module;
    - the comparison, from the baseline run (`orchestration/service.py:1222`).
- **BE-30.3 `context` block**: `CalculationService.result_context(run)` (`calculations/service.py:2336-2371`).
  - **Built only from the pins:**
    - from the manifest: module, engine and rule versions, the reporting date and the statement;
    - from the outcome, which is written once: the currency codes;
    - unit labels: the `CONTEXT_*` constants.
  - **Carried by:**
    - run detail, ladder and LCR, through `_context()` (`calculations/router.py:252-254`);
    - **results** (`router.py:485`, added in the fix round per Q3 and Q10);
    - the comparison, from the baseline run (`orchestration/service.py:1223`).
  - `ResultPage.context` is in `openapi.json`.
- **BE-30.4 Boundary cases**, in `validation_kit.py` and labelled `ENGINEERING — awaiting domain-expert approval`. One lcr_hqla run on the 0020 seed covers five currencies:
  - EUR 1.0000 and USD 1.0002 (ROUND_HALF_EVEN ties);
  - GBP exactly at the cap gives `applied=False`, and CHF one minor unit above gives `applied=True`;
  - SEK gives `ratio: null` with `LCR_DENOMINATOR_ZERO`.

  The ladder cases:
  - `999999999999999999.99` stays exact in inflows, net gap and cumulative gap (that row and every later one), in the results read and in the stored NUMERIC;
  - `1.235` is QUARANTINED with a single `SCALE_EXCEEDED` on `amount`, the raw text is kept, and it never reaches the canonical records;
  - `1.230` is accepted as `1.23`, checked row by row.

  The code agreed with every hand-computed value, and no formula was changed.
- **FE-30.1**:
  - **New components:** `ValidationStatusBadge`, `ResultContextPanel` and `validation.ts`.
    - The badge map is typed against the generated enum.
    - An unknown or missing status reads "Not validated — status not reported".
    - The two validated labels are marked PROVISIONAL (Q11).
  - **Where they appear:** the registry card (PLANNED included), the module picker, run list rows, run detail (header, fields and a "Result context" section), the run identity strip, compact panels on the ladder and LCR, and the comparison, checked against its baseline.
  - **The mismatch alert** is display-only and non-blocking. It uses the `default` Alert variant, neutral wording and a set-based rule-version comparison (Q13).
  - There is no feature flag (Q12), and `navigation.ts` and `features.ts` are unchanged.

**Shared and existing files changed:**
- **Backend:** `calculations/{constants,service,router,schemas}.py`, `rules/{constants,schemas,router}.py`, `orchestration/{schemas,service}.py` and `openapi.json`.
- **Backend tests:**
  - `validation_kit.py` (new);
  - `test_rules.py`: part 1 calls `check_module_validation` (:339);
  - `test_calculations.py`: :894-895, :1041-1050, :1093, :1201-1221, :1289-1299, :1389 and :1464-1475.
- **Frontend:**
  - `schema.d.ts`, generated before the fix round and now stale (see Open items);
  - `RunView`, `RunIdentity`, `RunList`, `MaturityLadderView`, `LiquidityView`, `ModuleOption`, `ModuleRegistry` and `ComparisonView`;
  - fixtures in `calculations.test.ts`, `planned.test.ts`, `scenario-run.test.ts` and `registry.test.ts`, plus the snapshot (the only change is the added badge);
  - e2e folds in `calculations.spec.ts`, `liquidity.spec.ts`, `registry.spec.ts` and `scenario-compare.spec.ts`.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Backend, after wiring | `2 passed, 1 warning in 55.86s` (calculations + rules) |
| Temporary response-recording check (deleted) | all 7 reads returned ILLUSTRATIVE; LCR context `{'currency': {'basis': 'PER_CURRENCY_NO_FX', 'codes': ['EUR', 'GBP', 'USD']}, 'as_of_date': '2026-12-31', … 'validation_status': 'ILLUSTRATIVE', …}`; `1 passed, 1 warning in 30.98s` |
| WS1 | `4 passed, 1 warning in 35.47s` |
| Pure-code LCR cases | `tie even down 1.0000`; `tie even up 1.0002`; `150.00 … False`; `150.02 … True`; `zero 0 None (<LcrWarningCode.LCR_DENOMINATOR_ZERO …>,)` |
| Test writer, targeted | `1 passed` (rules), `1 passed` (calculations), `2 passed, 1 warning in 55.96s` |
| Deliberate break (then restored) | `validation_kit.py:406: AssertionError` (results status "X"); `('ENGINEERING — awaiting domain-expert approval', 'EUR', '1.0000', '1.0001')`; "diff against the saved copy: identical" |
| Fix round, first run | `asyncpg.exceptions.UndefinedColumnError: column "currency" does not exist` (`check_stored_hashes`). Fixed by taking the currency from `liquidity_gap_result` |
| Fix round, targeted | `test_registry_snapshot_run_and_maturity_ladder`: `1 passed, 1 warning in 33.30s`; calculations + rules `2 passed, 1 warning in 62.21s (0:01:02)` |
| Collected (D-26/D-60) | `49 tests collected in 0.30s` (49 -> 49) |
| ruff / format / mypy | `All checks passed!` / `165 files already formatted` / `Success: no issues found in 165 source files` |
| OpenAPI drift | `openapi.json is up to date`; `ResultPage.required: [… 'validation_status', 'context']` |
| Migration round trip (scratch `moodymate_scratch_p30`) | `0027 (head)` -> downgrade -1 `0026` -> `0027 (head)`; `dropped` |
| Docs grep | `^## ` gives 10/10/10; `compliant\|certif\|moody` (case-insensitive) finds nothing |
| Frontend gen:api / tsc / lint | `openapi-typescript 7.13.0 ../backend/openapi.json → src/api/schema.d.ts` (before the fix round) / `tsc exit 0` / `✔ No ESLint warnings or errors` |
| vitest (full, build step) | `Test Files 38 passed (38)`, `Tests 630 passed (630)` |
| vitest (calculations, registry, advanced; after the FE fix round) | `Test Files 12 passed (12)`, `Tests 288 passed (288)`; `validation.test.ts` `12 passed` |
| `npm run build` (8030) | `build exit 0` |
| Playwright, 4 specs (`moodymate_test_p30fe`, 8030/3030) | e2e: `10 passed (39.6s)`; after the FE fix round: `10 passed (33.9s)` |
| CI contract check `gen:api && git diff --exit-code src/api/schema.d.ts` | **will fail.** `schema.d.ts:6082-6104` `ResultPage` has no `context` (see Open items) |
| Full backend suite | **not yet run** (the coordinator runs it once) |
| Full Playwright | CI |

All runs used per-phase databases: `moodymate_f06_p30`, `moodymate_test_p30`, `_p30b`, `_p30fe` and `moodymate_scratch_p30` (dropped). `moodymate_dev` was never touched. `backend/.env` is not created yet (Q2).

## Plan ticks

The architect checked each Done-when against the code and test lines. Each tick has an evidence line in DELIVERY_PLAN.md, dated 2026-10-24.

- **BE-30.1 ticked.**
  - Headings: 10 per file (grep).
  - **Seed check by the architect:** `maturity_ladder.md:104-111` against `0018_calculations.py:158-193`, and `lcr_hqla.md:114-121` against `0020_lcr_results.py:139-184`. All parameters are equal.
  - The forbidden words occur nowhere.
- **BE-30.2 ticked.**
  - `check_module_validation` (`validation_kit.py:88-131`, `test_rules.py:339`).
  - Key sets: `validation_kit.py:182-186` and :236-241.
  - `check_stored_hashes` (:189-241, `test_calculations.py:1050`).
  - Re-run: `test_calculations.py:1287-1293`.
  - The Done-when was reworded per Q7.
- **BE-30.3 ticked.**
  - `check_context` runs on all five reads: POST run and ladder (:894-895), detail (:1041), results (:1093), comparison (:1210) and LCR (:1389).
  - The units are frozen as literals (`validation_kit.py:66-78`).
  - The task was reworded to five reads (Q3/Q10).
- **BE-30.4 ticked.**
  - `validation_kit.py:250-346`, :424-436 and :483-538, called at `test_calculations.py:1212` and :1465.
  - The 3-dp bullet was reworded per Q6.
- **FE-30.1 not ticked.** Both parts of the Done-when are evidenced against the earlier contract: `validation.test.ts:59-71`, and `calculations.spec.ts:243-262` in "10 passed". But after the BE-30.3 fix, `schema.d.ts` is stale, and CI's drift step (`.github/workflows/ci.yml:76`) will fail. Still needed:
  - re-run `gen:api`;
  - fix any `ResultPage` fixtures;
  - add `ResultPage` to the context list at `validation.test.ts:98`;
  - show tsc, lint and vitest green again.

## Review outcome

- **MAJOR:** none. **MEDIUM:** none.
- **LOW 1 (fixed):** the results read had no `context`, although D-56 §5 requires one. Ruled in Q3/Q10 and fixed (`router.py:485`, asserted at `test_calculations.py:1093`).
- **LOW 2, 3 and 4 are open.** They are listed under Open items, Not blocking.

## Rulings (auto-resolved)

The decision-resolver gave each ruling. "Report" means this note is the ruling's record. DECISIONS.md amendments are dated 2026-10-24, Phase 30 build, auto-resolved.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | BE-30.1 (`docs/methodology/`) belongs to the backend engineer. Precedent: BE-6.2 and BE-13.1. | Report |
| Q2 | Before the full suite, the coordinator creates `backend/.env` from `.env.example` with `DATABASE_URL` pointing at `moodymate_f06_p30` (never `moodymate_dev`), and confirms it is untracked. | Report (coordinator action, open) |
| Q3 | The results read carries `context` too. "Every result read" in D-56 §5 includes it, and BE-30.3 covers five reads. | D-56 amendment; plan BE-30.3 |
| Q4 | `context.units` keeps human-readable labels. The frontend shows them as sent and never branches on them. Codes can be added later. | Report |
| Q5 | Unquantized cap figures are compared as Decimal, plus a plain-decimal-text check. Ratio ties and the 18-digit case use exact strings. | Report |
| Q6 | `1.235` is held as `scale_exceeded`; `1.230` is accepted as `1.23`. The plan bullet is reworded, and the docs got the trailing-zero sentence. | Report; plan BE-30.4 |
| Q7 | The `rerun_of` reading is accepted. The stricter addition is that the original run's stored hashes re-verify by recomputation, and neither document carries `validation_status` or `context`. | Report; plan BE-30.2 |
| Q8 | The cap flag is strict `>` (`lcr_hqla.py:427`). The code stays, with the ENGINEERING label. Equality semantics go to the domain-expert sign-off. | Report |
| Q9 | `context.units` strings are frozen as literals in the test, and the docs quote them word for word. | Report |
| Q10 | Same as Q3. | D-56 amendment |
| Q11 | The two validated-status labels are PROVISIONAL. Their final wording joins the D-56 §3 hard stop. | D-56 amendment |
| Q12 | There is no NEXT_PUBLIC switch for the labels, and no D-02 amendment. The plan's reversibility note applies, and a switch that hides the safety label would be unsafe. | Report |
| Q13 | The mismatch alert is display-only, non-blocking and neutrally worded, with a set-based rule comparison. It extends to the results view once its type has `context`. | Report |

## Open items

**Blocking merge:**
1. **Frontend contract drift (FE-30.1).** `npm run gen:api`, then fix the fixtures and extend `validation.test.ts:95-98` to `ResultPage`. After that, tsc, lint and vitest must pass again. Otherwise the CI step `git diff --exit-code src/api/schema.d.ts` fails.
2. **Q2:** create `backend/.env` (pointing at `moodymate_f06_p30`, untracked), then run the full backend suite once. It must show 49 passed.
3. CI's full Playwright run on the PR.

**Not blocking (LOW findings and follow-ups):**
1. (Review LOW 2) `orchestration/schemas.py:616-629`: `ResultContextRef` is `dict[str, Any]` with only a `$ref` in the JSON schema, so the comparison's context is never validated against `ResultContext`. Fix: have `result_context` return `ResultContext(...).model_dump(mode="json")`, or validate it in the orchestration service.
2. (Review LOW 3) `rules/constants.py:131-138`: the guard checks truthiness only, so a whitespace-only approver or `evidence_ref` passes. `approved_on` is not type-checked, and a plain-str `status` fails on `.value`. Fix: require `.strip()`, check that `approved_on` is a `date`, coerce `status`, and extend `check_module_validation`.
3. (Review LOW 4, still present) `scenario_overlay.md:123` cites `orchestration/constants.py:115-119`, but the D-44 bounds start at `MIN_SHOCKS` on :114. Fix: cite `:114-119`.
4. (Architect) The Q8 action is half done. `lcr_hqla.md` states the strict `>` under Formula (:37-39), but "Open client decisions" (:157-169) does not name the cap-flag equality semantics. Fix: add one bullet (backend engineer, docs only).
5. (Architect) The Q13 mismatch check on the results view is deferred until `schema.d.ts` carries `ResultPage.context`. There is a comment at `fetchContributions`.
6. (Architect) `docs/ARCHITECTURE.md` needs the D-56 surfaces after merge: `MODULE_VALIDATION`, `ResultContext` on five reads, and `docs/methodology/`.

**For the client before production (hard stops; not blocking the build):**
- **D-56 is still "proposed".** The user confirms it, including its 2026-10-24 amendment.
- **D-56 §3:**
  - adding any `ValidationRecord`;
  - who may approve one;
  - the display wording of "Internal validation recorded" and "Regulatory validation recorded" (Q11).
- **D-56 §6 methodology items:**
  - SME review of the three methodology docs;
  - approved golden packs;
  - L2A/L2B caps;
  - the HQLA valuation basis;
  - deposit classes and run-offs;
  - day-0, past-due, missing, negative and out-of-horizon flows;
  - the FX method;
  - intended use, jurisdiction and reporting basis.
- **ENGINEERING cases** (D-35 §4): the BE-30.4 hand-computed values await domain-expert approval. This includes whether inflows exactly at the cap count as "capped" (Q8).
- **Illustrative content:** every output is ILLUSTRATIVE and carries the D-35 statement. No regulatory certification or vendor parity is claimed.

No new hard stop arose in this phase. There is no destructive migration, no change to roles or permissions, and no real client data.

**Not checked locally:** the full backend suite (coordinator) and the full Playwright run (CI).

---
Files edited:
- /home/user/MoodyMate-wt/f06-p30/docs/DELIVERY_PLAN.md: BE-30.1 to BE-30.4 ticked with evidence; BE-30.2, BE-30.3 and BE-30.4 reworded per Q7, Q3/Q10 and Q6; FE-30.1 left unticked with what is still missing.
- /home/user/MoodyMate-wt/f06-p30/docs/DECISIONS.md: D-56 amendment (2026-10-24, Phase 30 build, auto-resolved) covering Q3/Q10 and Q11.
