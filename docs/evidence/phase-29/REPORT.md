# Phase 29: Canonical hashing and shared helpers (checks)

**Scope.** This report covers BE-29.1 to BE-29.4, FE-29.1 and FE-29.2 (Workstream B: DG-03, FC-07, FC-08; D-52).
- **Where and who.** The phase was built in the worktree `/home/user/MoodyMate-wt/f06-p29` by:
  - a backend-engineer and a test writer;
  - a frontend-engineer, who owned FE-29.1 and FE-29.2;
  - one review agent, one e2e run, and a backend fix round.
- **Commits.** Nothing is committed yet. The coordinator commits and merges.
- **Migration.** There is none (Q7). The head stays `0027`, and nothing needs re-chaining. The pre-numbered 0030 and 0031 of Phases 31 and 32 are not affected.
- **Contract.** `openapi.json` is byte-identical, and `schema.d.ts` did not change.

## What was built

- **BE-29.1 `app/core/canonical.py`** (D-52 §1).
  - `canonical_json` and `canonical_sha256`:
    - keys must be `str`;
    - a finite Decimal becomes its exact text (`Decimal("1.10")` gives `"1.10"`);
    - a float, NaN, Infinity or any other type raises `CanonicalValueError`, and the message carries no value.
  - The named `legacy_ascii_sha256` and `legacy_key_sha256`, plus `sha256_hex` for raw bytes.
  - Every production content hash now goes through `sha256_for(kind, value)`. That covers rules, calculations, orchestration, mapping, governance and ingestion.
  - Migrations and their frozen copies are untouched (§4).
  - `app.rules.service.canonical_sha256` is removed, with no re-export (Q10). The test writer changed one import line in each of five test files to `app.core.canonical`.
- **BE-29.2 `HASH_SITES`** (`canonical.py:173-282`). It lists 19 kinds, each with its policy, where it is built, where it is stored and why:
  - **12 CANONICAL:** these were always built with `ensure_ascii=False`.
  - **5 LEGACY_ASCII:**
    - SOURCE_CONTRACT, IMPORT_REQUEST and SCHEMA_CONTRACT accept non-ASCII free text through the API;
    - IMPORT_MANIFEST and VALIDATION_PROFILE_RULES are built from seeds, so no 422 can prove them ASCII-only.
  - **2 LEGACY_KEY:** the stored business key and the in-memory dry-run key.
  - No kind moved to CANONICAL, and no code writes a stored `*_sha256` column.
- **BE-29.3 core helpers.**
  - **`core/decimals.parse_decimal_text`:** three named profiles (STRICT, UNSIGNED, SOURCE). It refuses over-scale input and too many digits with a value-free code, and it never rounds. It replaces the parsers in rules, lcr_hqla, scenario templates, the profiler, mapping `plain_decimal` and the repository SQL regex (same pattern text).
  - **`core/classification`:**
    - `most_sensitive`: an unknown code ranks above every known code, and no code at all gives `unclassified` (Q4);
    - `storable()`: maps an unknown code to `restricted`, used only for the calculations FK column;
    - `mask_value`: the one single-value masking path;
    - the domain method is renamed `SchemaPins.payload_classification`, and `governance/constants` checks at import that the order matches `ClassificationCode`.
  - **`core/refusals`:** `refuse` and `refuse_endpoint` roll back, audit DENIED and raise. Module `_refuse` wrappers only delegate to `refuse` (Q1/Q11). `record_denial(` is called only from `core/refusals.py:36`.
  - **`core/locks.advisory_xact_lock`:** replaces five repository copies.
  - **`core/idempotency`:** `idempotency_key_header` and `mark_replayed`, used by the three Idempotency-Key routes and the four replay responses. Each route keeps its own description.
  - **Renames:** `ImportService.refuse` became `_refuse` (the router uses the new `refuse_upload`), and the shock checker's `refuse` became `problem`.
- **BE-29.4.** `hypothesis>=6.100,<7` is a dev dependency (`pyproject.toml:32`). The kit runs with `derandomize=True` and `max_examples=60` (`canonical_kit.py:43-44`). The property folds cover:
  - canonical serialisation: idempotent, independent of key order, and the same under both policies for ASCII without DEL;
  - decimals: round trip, over-scale refused, and the refusals of each profile;
  - the overlay: totals conserved per currency and direction, deterministic and unique part ids, no negative part, and agreement with the engine's own conservation check.
- **FE-29.1** (`src/lib`).
  - `idempotency.ts` (`newIdempotencyKey`).
  - `refusal.ts` (`Refusal`, and `refusalOf` with a code → title map). Each area's refusal function is now one line that calls `refusalOf`, and mappings uses the new `mappingRefusal`.
  - `forms.ts`:
    - `COMMENT_MAX` and `textLength`;
    - `commentProblem`, which takes a `required` option and counts code points, not UTF-16 units;
    - `isIsoDate` and `dateProblem`, which accept only real calendar dates (year 1 to 9999, with the leap-year rule).
  - The old names stay importable from their old modules. `isReportingDate` now uses `isIsoDate`.
- **FE-29.2.**
  - **`components/common/StatusPill.tsx`** is the one generic pill. All 18 wrappers are now thin enum → variant maps over it (Q17). An unknown value is shown as sent, with the neutral `status-draft` style. The schema version pill's export is renamed `SchemaStatusPill`.
  - **`components/common/Masked.tsx`** holds `isMasked`, `<Masked>` and `MaskedFlag` (Q15). `MaskedValue.tsx` now just re-exports it. `ShownValue`, `CreditValueCell`, the ParameterView leaf, `DeltaValue`, the ComparisonView first-negative bucket and the LiquidityView cap indicator all render through it. The module has no `'use client'` and no hooks.
  - **`src/lib/maker-checker.ts`** holds `makerCheckerGate`, `mayDecide` and `isAuthorOf`. The checks run in order: in review, then not the author, then the switch, then the permission. Rules, scenarios, ratings, schema proposals and mappings all use it.

**Behaviour changes (intended, recorded):**
- **lcr_hqla `fraction` and the rules decimal check** now use `fullmatch` with ASCII digits only. A trailing newline and Unicode digits are now refused (LOW 1).
- **The mapping dry run** keeps its in-memory duplicate set as hex strings. Nothing is stored.
- **The rule decision dialog** now reads "At most 1,000 characters (1,001 now)." (Q14, LOW 3).
- **Comment length** is counted in code points (Q14). This closes Phase 20 LOW 3, and the astral vitest is at `helpers.test.ts:114-119`.
- **`isReportingDate`** refuses year 0000 (Q14).

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Collected (D-26) | `49 tests collected in 0.39s` (49 -> 49, no new test function) |
| Test writer baseline (3 target tests, before any edit, `moodymate_test_p29b`) | `3 passed, 1 warning in 9.41s` |
| Engineer run 1 (rules, calculations, mappings, imports, validation, profiling, quarantine, reconciliation, sources, schema_registry, domain, credit, ws1_verification, auth, committed_artifacts; `moodymate_test_p29`) | `2 failed, 36 passed, 1 warning in 209.28s (0:03:29)`. Failures: `HASH_SITES names columns the database does not have: {'source_row': [('source_row', 'row_sha256')]}` (fixed: `source_record.row_sha256`, Q8), and the one-definition grep (ruled Q1/Q11). `test_every_mutating_route_is_listed_and_emits_an_audit_event` passed |
| Test writer, 4 target tests | `2 failed, 2 passed, 1 warning in 45.65s` (`test_calculations` and `test_schema_registry` passed; both failures were open implementation gaps at the time) |
| Fix round, 3 target tests (`moodymate_test_p29fix`) | `3 passed, 1 warning in 11.91s` |
| Fold mutation check (copy of `app/` in the scratchpad) | `extra record_denial call -> caught`; `_refuse without core import -> caught: ['rules/service.py']`; a second core-style `refuse` caught by the ONE_DEFINITION assert |
| Standalone kit | `overlay ok` (60 examples, 2.6s; `{'n': 60, 'apps': 47, 'added': 5, 'split': 15}`); `decimal props ok` |
| Golden values | ASCII `f050fdc3...6e75df`; Unicode `ab15aaa9...175d06c`; Unicode legacy_ascii `a59ca622...c9aee9`; refused: `a float at $ / a non-finite Decimal at $ / a int key at $ / a tuple at $` |
| DEL boundary | `'\x7f' json.dumps -> "\u007f", ensure_ascii=False -> raw DEL, equal=False`; `'\x00', '\x1f', '~' equal=True` |
| ruff (pinned 0.8.6) / format / mypy | `All checks passed!` / `170 files already formatted` / `Success: no issues found in 170 source files` (with hypothesis available; 4 kit typing errors fixed with annotations only) |
| OpenAPI drift | `openapi.json is up to date`, exit=0; `git diff --stat -- openapi.json` empty |
| Migration chain (scratch `moodymate_scratch_p29`, dropped) | `upgrade head -> 0027 (head); downgrade -1 -> 0026; upgrade head -> 0027 (head); exit=0` |
| Backend grep folds (production code) | `hashlib` only in `core/artifacts.py` (raw bytes) and `core/canonical.py`; `json.dumps` only in `core/canonical.py`, `core/logging.py:139` and `mapping/transforms/engine.py:327` (neither hashed); `record_denial(` only at `core/refusals.py:36`; no added UPDATE of a sha256 column. The architect re-ran these greps |
| Frontend lint / tsc / vitest | `✔ No ESLint warnings or errors` / `tsc exit 0` / `Test Files 41 passed (41)`, `Tests 695 passed (695)` (baseline 38 files, 630 tests) |
| Existing tests unchanged | `git diff --stat -- 'src/**/*.test.ts' 'e2e'`: empty |
| gen:api | `schema.d.ts` unchanged (run twice) |
| `npm run build` (e2e, `NEXT_PUBLIC_API_BASE_URL=http://localhost:8029`) | `✓ Compiled successfully`, exit=0, 34 static pages |
| Playwright, 21 specs touching pills or masked values (`moodymate_test_p29fe`, 8029/3029) | `Running 56 tests using 1 worker` ... `56 passed (2.2m)`, exit 0. Only `advanced-nav.spec.ts` not run (checks neither) |
| Full backend suite | **not yet run** (coordinator, after Q2 and Q6) |
| BE-29.4 suite-time comparison | **not yet run.** `test_registry_snapshot_run_and_maturity_ladder` took 33.98s and 31.66s after the change, and there is no pre-change figure |
| Full Playwright | CI |

The runs used per-phase databases: `moodymate_f06_p29`, `moodymate_test_p29`, `_p29b`, `_p29fix`, `_p29fe` and `moodymate_scratch_p29` (dropped). `moodymate_dev` was never touched. Hypothesis was supplied by a scratchpad `PYTHONPATH` or `uv run --with`. The shared `.venv`, a symlink into the main checkout, was not changed (Q2).

**Masked-literal grep definition (Q16).**
- **Patterns.** The grep looks for four literals in every `.tsx` under `frontend/src` except `components/common/Masked.tsx`:
  - `data-masked="true"`
  - `data-testid="masked-value"`
  - `>masked<` (with optional whitespace)
  - `: masked`

  The test form at `src/lib/__tests__/helpers.test.ts:64-65` also matches a line whose only text is `masked`.
- **Result.** 0 files outside `Masked.tsx`.
- **Exclusion 1: the D-33 hint strings** in `components/calculations/calculations.ts:183-188` and `:226-230`. They are returned only when `isMasked(value)` is true, and they are rendered beside the value, which still goes through `ShownValue` (`LadderFigure.tsx:20-27`, `LiquidityView.tsx:51-58`, `MaturityLadderView.tsx:77-87`). They never stand in for a value and contain none.
- **Exclusion 2: data-attribute discriminators** such as `data-delta='masked'`. These are attributes, not rendered text.
- **Pill class.** The literal `rounded-sm border px-2 py-0.5 text-xs font-medium` appears only in `ui/badge.tsx` and `common/StatusPill.tsx`, apart from the new test and the existing registry snapshot.

## Plan ticks

The architect checked each Done-when against the code and the test lines. Each tick, and each open status, has an evidence line in `docs/DELIVERY_PLAN.md`.

- **BE-29.1 ticked.**
  - The policy is at `canonical.py:68-113` and `sha256_for` at :285-292.
  - The ast fold is at `canonical_kit.py:97-150`, and golden, DEL and key checks at :203-220. They run in `test_validation.py:411-412`.
  - The re-run checks are at `test_calculations.py:927, 1290`, and the stored-hash recompute at :1051-1053.
  - Stored contract, manifest and fingerprint hashes are checked at `canonical_kit.py:577-700`.
  - Result: "3 passed". The Done-when was reworded to "ASCII without U+007F" (Q9).
- **BE-29.2 ticked.**
  - The 19 kinds with reasons are at `canonical.py:173-282`.
  - The moved-kind guard with the empty `ASCII_ONLY_PROBES` is at `canonical_kit.py:294-322`.
  - Every LEGACY kind's rows re-verify, and the fold asserts each kind has rows (:600-671, :691-700).
  - The `information_schema` column check is at :582-592, and the no-UPDATE regex at :146-150.
- **BE-29.3 not ticked.**
  - Done: one definition each (architect grep, as above, wording per Q1/Q11 and Q4) and the OpenAPI check.
  - **Missing:** the full-suite "49 passed", and a WS1 audit-completeness run on the final tree. Its last pass was engineer run 1, before the last backend edits.
- **BE-29.4 not ticked.**
  - The folds pass.
  - **Missing:** the "+10 s at most" before/after `--durations` of the full suite.
- **FE-29.1 ticked.**
  - The definitions are at `lib/idempotency.ts:8`, `lib/refusal.ts:8, 19` and `lib/forms.ts:25, 57`. The one binding is `ingestion/drift.ts:75`, pinned at `helpers.test.ts:56-60`. The wording was reworded per Q13.
  - vitest: 695 passed, with existing tests unchanged.
- **FE-29.2 not ticked.**
  - The vitest loop and the literal grep pass, and local Playwright gives 56 passed.
  - **Missing:** green Playwright CI. The wording was reworded per Q15 and Q17.

## Review outcome

- **MAJOR:** none.
- **MEDIUM:** none.
- **LOW:** five findings, listed under Open items (not blocking). LOW 2, the DEL docstring, is fixed: `canonical.py:18-19, 91-93` and the D-52 amendment. LOW 3 is accepted by Q14.

Gaps the test writer found during the build were all closed before the fix round:
- the `source_record` table name (Q8);
- the five unconsolidated helpers;
- `mapping/service.py:387` `AttributeError: 'DryRun' object has no attribute '_duplicates'`, a 500 on `POST /imports/{id}/mappings/validate`.

## Rulings (auto-resolved)

The decision-resolver gave each ruling. "Report" means this note is the ruling's record. DECISIONS.md amendments are dated 2026-10-24, Phase 29 build, auto-resolved.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | Keep the delegating `_refuse` wrappers. The grep is narrowed to `async def refuse(` (`core/refusals.py`). New folds check that `record_denial(` is called only from `core/refusals.py`, and that every `_refuse` file imports the core `refuse` (`canonical_kit.py:329-371`). Renaming wrappers to slip past the grep is not allowed. | Report; plan BE-29.3 |
| Q2 | Do not install into the main checkout's `.venv`. The coordinator uses a worktree-local venv with `pip install -e ".[dev]"` for the full suite and mypy. CI already installs `.[dev]`. The main checkout picks up hypothesis at its next dev sync after merge. | Report |
| Q3 | D-52 stays **proposed**. The amendment records the Phase 29 implementation and the five LEGACY_ASCII kinds. Acceptance is an open item for the user. | D-52 amendment |
| Q4 | An unknown code ranks above every known code (fail closed). `storable()` maps it to `restricted` for the calculations FK column only. Results are identical to the old copies. | Report; plan BE-29.3 |
| Q5 | `DECIMAL_TEXT` and `DECIMAL_TEXT_PATTERN` stay as named contract patterns, so OpenAPI does not change. The Unicode-digit gap in control_totals is LOW 6, with follow-up BE-35.8. | Report; plan BE-35.8 |
| Q6 | Before the full suite, create the untracked `backend/.env` pointing at `moodymate_f06_p29` (confirm the `-T moodymate_dev` copy first), and keep passing `DATABASE_URL` explicitly. | Report |
| Q7 | No migration. The head stays 0027, and there is nothing to re-chain. | Report |
| Q8 | `HASH_SITES[SOURCE_ROW]` is stored in `source_record.row_sha256` (`canonical.py:225`). The `information_schema` fold passes. | Report |
| Q9 | §2 holds for ASCII without U+007F. §5 now requires a future move to refuse non-ASCII text and DEL with 422. The docstrings are corrected, and the BE-29.1 Done-when is reworded. | D-52 amendment; plan BE-29.1 |
| Q10 | Repoint Phases 30-32 at merge, with no re-export. The coordinator hands the import change to each phase's backend engineer. | D-52 amendment; plan dependency note |
| Q11 | Same as Q1. The domain method is `payload_classification`, and the only `def most_sensitive(` is `core/classification.py:75`. | Report |
| Q12 | hypothesis is already in `pyproject.toml:32`, and CI installs `.[dev]`. It falls under the D-55 §4 pip-audit gate. | Report |
| Q13 | Option (a): `drift.ts:75` keeps its one-line short-wording binding, and the existing vitests stay unchanged. The Done-when means one implementation. | Report; plan FE-29.1 |
| Q14 | Accepted: the rule dialog wording, code-point counting (Phase 20 LOW 3 closed) and refusing year 0000. | Report |
| Q15 | `MaskedFlag` in the Masked module is acceptable. | Report; plan FE-29.2 |
| Q16 | The grep definition and its two exclusions are written above. | Report |
| Q17 | 18 pills, not 14, and the `SchemaStatusPill` rename. The pre-plan finding at `DELIVERY_PLAN.md:3804` stays as a dated observation. | Report; plan FE-29.2 |

## Open items

**Blocking merge:**
1. **Full backend suite** on the final tree: `49 passed`, plus ruff and mypy clean. Run it with the worktree-local venv (Q2) and `backend/.env` set to `moodymate_f06_p29` (Q6). This also closes the BE-29.3 WS1 re-run.
2. **BE-29.4 timing.** Run the full suite with `--durations` before (master, on a `-T moodymate_dev` copy) and after (this tree). The rise must be no more than 10 s. Record both totals here.
3. **Playwright CI green** on the PR (FE-29.2).

Then tick BE-29.3, BE-29.4 and FE-29.2 with that evidence.

**Not blocking (LOW findings and follow-ups):**
1. **Stricter decimal parsing** (`rules/service.py:158-163`, `calculations/modules/lcr_hqla.py:182-191`). STRICT and UNSIGNED now use `fullmatch` with ASCII digits. A stored `rule_version.parameters` or scenario shock value with a trailing newline or Unicode digits would now raise `ParameterError` on re-run, which breaks D-31 reproducibility for that run.
   - The fixture seeds are unaffected (the rules and calculations re-runs pass). The tightening is intended (safer).
   - Fix (+0 tests): fold a check that every stored DECIMAL parameter and shock parses under its new profile.
2. **DEL claim** (`core/canonical.py`). **Fixed:** the docstrings say "ASCII text without U+007F (DEL)", and D-52 §2/§5 are amended (Q9).
3. **Rule decision dialog wording** (`frontend/src/components/rules/rules.ts:426-427`, used by `RuleVersionActions.tsx:81`). It now reads "At most 1,000 characters (1,001 now)." and counts code points. Accepted (Q14); no vitest or e2e asserts the old text.
4. **Self-approval comment** (`frontend/src/lib/maker-checker.ts:2-5`, also `components/advanced/scenarios.ts:127`). The comment says self-approval gets 409 `self_approval`, but the schema-change path refuses it with 403 (`backend/app/governance/service.py:897-906`). Fix: "the server refuses self-approval (403 or 409, by area) with code `self_approval`".
5. **Unhelpful refusal path** (`backend/app/core/canonical.py:59`). `_normalise` uses `f"{path}.key"` for every dict entry, so a refused float deep in a manifest is reported as `.key.key.key`. Fix: a structural, value-free path such as the sorted key position. Never include key text.
6. **(Q5)** `control_totals` accepts Unicode digits server-side, because Python `\d` is wider than the published ECMA-262 pattern. `Decimal()` parses them exactly, so no wrong value is stored. Follow-up: BE-35.8, a 422 with no OpenAPI diff.
7. **(Architect) Re-chain hand-offs.** Phases 30-32 must repoint `from app.rules.service import canonical_sha256` to `app.core.canonical` when they merge Phase 29 in (Q10).
8. **(Architect) Missing kit dependency in the main checkout.** Its `.venv` lacks hypothesis, so `canonical_kit` fails at import there until a dev-dependency sync after merge (Q2).
9. **(Architect) Docs to update after merge.** `docs/ARCHITECTURE.md` needs the `app/core` helpers (`canonical`, `decimals`, `classification`, `refusals`, `locks`, `idempotency`), the `HASH_SITES` policy table and the frontend `src/lib`/common components.

**For the user or client (not blocking the build):**
- **D-52 acceptance.** D-52, and the whole Feature 06 plan (D-52 to D-62), are still proposed and wait for the user's approval (Q3).
- **Client data.** If a client database may hold rule parameters or shocks with non-ASCII digits or trailing whitespace, it must be checked before production (LOW 1).

No hard stop arose in this phase: no migration, no stored hash changed, no CHECK widened, no change to roles, permissions or approvers, and no real client data or regulatory or vendor-parity claim.

**Not checked locally:** the full backend suite, the suite-time comparison and the full Playwright run (CI).

---

Files edited by the architect:
- `/home/user/MoodyMate-wt/f06-p29/docs/DELIVERY_PLAN.md`:
  - BE-29.1, BE-29.2 and FE-29.1 ticked with evidence;
  - BE-29.3, BE-29.4 and FE-29.2 left unticked, with their missing evidence;
  - Done-when wording changed per Q1/Q11, Q4, Q9, Q13, Q15 and Q17;
  - the Q10 note added to the Feature 06 dependency notes;
  - new task BE-35.8 (Q5).
- `/home/user/MoodyMate-wt/f06-p29/docs/DECISIONS.md`: a D-52 amendment dated 2026-10-24 (Phase 29 build, auto-resolved), covering Q3, Q9 and Q10. The status stays proposed.

The report text above is for the coordinator to write to `/home/user/MoodyMate-wt/f06-p29/docs/evidence/phase-29/REPORT.md`.
