# Phase 32: Rate terms, effective dating, lifecycle and currency/FX reference (checks)

**Scope.** This report covers BE-32.1 to BE-32.4 and FE-32.1 (Workstream D part 2; FC-01, FC-02, FC-08; D-61).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f06-p32` by:
  - a backend-engineer and a test writer;
  - one frontend-engineer, who owns FE-32.1 and `features.ts`. `navigation.ts` is unchanged because FE-32.1 adds no nav entry;
  - one review agent and one e2e run;
  - a backend fix round and a frontend fix round.
- **Commits.** Nothing is committed yet. The coordinator commits the phase. The `conftest.py` `_TERMS_CLEANUP` hunk must go in the same commit as the `test_domain.py` and `terms_kit.py` changes, because `test_domain.py` imports it (Q12).
- **Migration 0031 is pre-numbered,** with `down_revision` "0030".
  - Phases 33 and 34, built in parallel, add no migration.
  - The coordinator re-chains 0031 at merge (protocol step 6). The WS1 test reads 0031's `down_revision` from the script (`test_ws1_verification.py:1105-1106`), so the test survives the re-chain.

## What was built

- **BE-32.1 Migration `0031_rate_terms_currency_fx`.** It only adds things (D-01): four new tables, and no column, CHECK or trigger on any existing table.
  - **`instrument_term_version`:**
    - at most one row per `instrument_version` (`UNIQUE (instrument_version_id)`);
    - typed NUMERIC and DATE columns;
    - `unknown_fields`, which holds field names only, never values;
    - three CHECKs that repeat the D-61 §4 rules (:240-250);
    - an insert trigger that checks the version belongs to the row's tenant and projection (:102-116).
  - **`instrument_term_vocabulary`:** the D-61 §3 vocabularies, 15 rows. No reference index is seeded.
  - **`currency`:** CHF, EUR, GBP and USD.
  - **`fx_rate_observation`:** composite FKs to the tenant's own currencies, `base_currency <> quote_currency`, `rate > 0`. No seed and no write route.
  - **Seeds.** Both are tagged (`0031_term_vocabulary`, `0031_currencies`), PROVISIONAL, and for the default tenant only (:259-325).
  - **Append-only.** All four tables refuse UPDATE, DELETE and TRUNCATE through 0017's `domain_append_only` (:119-125).
  - **Downgrade** (:342-359): it refuses while any term row, any FX observation, or any vocabulary or currency row that is not the seed exists.
- **BE-32.2 Rate-terms contract and projector v3.**
  - **The contract.** `INSTRUMENT_RATE_TERMS` is `INSTRUMENT_TYPED_MATURITY` plus the 11 optional D-61 §1 fields. `spread`, `cap_rate` and `floor_rate` are (11,6); `next_reset_date` and `effective_from` are DATE.
  - **Frozen hash.** `INSTRUMENT_RATE_TERMS_SHA256` = `be741da6…f749` (`constants.py:631`).
  - **Publication** goes only through the governed proposal path: ADDITIVE, with a second-person approval. No migration and no default seed publishes it.
  - **Spec chosen by hash.** Projector v3 is registered in `INSTRUMENT_SPECS_BY_HASH` (`constants.py:679`). Projectors 1 and 2 are unchanged.
  - **`project_terms`** (`service.py:258-289`):
    - consistent terms write a term row. A value outside the tenant's vocabulary is kept as given, named in `unknown_fields` and counted `UNKNOWN_VALUE`;
    - inconsistent terms write no term row and are counted in `terms_inconsistent`;
    - the `instrument_version` is written in both cases.
  - **`ProjectionSummary`** gains optional `term_rows` and `terms_inconsistent`. Only projector 3 fills them, so projector 1 and 2 bodies are unchanged (Q1).
  - **Switch.** `INSTRUMENT_TERMS_PROJECTION_ENABLED` (`core/config.py:145`, default true). Off gives 409 `projection_unsupported`, audited DENIED.
- **BE-32.3 Effective dating.**
  - **Reads.** Every instrument version shows `effective_from` (taken from its term row) beside `imported_at` (when its import was created), plus `terms_typed` and `terms`.
  - **The pick.** `GET /instruments/{instrument_id}?effective_on=` echoes the date and returns `effective_version_id`: the version with the latest `effective_from` on or before that date. On a tie the higher version wins (`service.py:1131-1161`). The `versions` list is never filtered or reordered.
  - **Fails closed** since the review fix: the pick is null whenever any rate-terms version has no term row, or has an `effective_from` the caller is not cleared for (D-61 amendment).
- **BE-32.4 Currency and FX reads.**
  - **Routes.** `GET /currencies` and `GET /fx/consolidation-status` (`router.py:1179-1210`), both `domain:read` (`DomainReader`). The repository filters both by tenant (`repository.py:435-449`).
  - **Status.** Always `{BLOCKED, fx_policy_not_approved, observation_count}`.
  - **Result context.** Result reads gain `context.consolidation: "BLOCKED"` (`calculations/service.py:2379`). The context is built at read time and not hashed, so `result_sha256` and the Phase 30 goldens are unchanged (Q11).
  - **Nothing converts.** There is no FX write route and no conversion code. `openapi.json` is re-exported.
  - **The `RETENTION.md` rows are not in the worktree yet** (see Open items).
- **FE-32.1** sits behind `NEXT_PUBLIC_INSTRUMENT_TERMS_UI` (`features.ts:146`). It is on by default. It does not depend on `NEXT_PUBLIC_DOMAIN_UI`, because the notice is on the calculations pages. Switching it off restores the Phase 31 UI.
  - **Instrument detail.** Each version has a "Contract terms" panel with PROVISIONAL badges. It shows:
    - the recorded terms;
    - "Not in vocabulary: kept as given" on each field the server lists in `unknown_fields`;
    - the masked marker for withheld values;
    - "Not stated" for empty fields.
    - When the contract has no rate terms: "No rate terms in this schema version".
    - When the terms were inconsistent (TERMS_INCONSISTENT): "Terms not recorded: inconsistent in the source row".
  - **Versions table.** Every version in server order, with "Effective from" beside "Imported at", the import link and the terms state. It never picks a "current" version. A version with no term row shows "Not recorded: terms inconsistent" under Effective from (Q15).
  - **Ladder and LCR views.** Both show the notice "Per-currency results only; FX consolidation is blocked until an approved rate source and conversion policy exist", placed after the Result context.
    - The notice shows even when `consolidation` is missing from the read (fail closed).
    - The Result context panel shows the server's value.
  - **Projection summary.** It shows the term-row and inconsistent counts, but only when the server sends them.

**Shared and existing files changed:**
- **`conftest.py`:** `_TERMS_CLEANUP` (:411), listed at :431 and in `clean()` at :493. It deletes the non-seed 0031 rows before the domain rows they reference; the seeds stay.
- **`validation_kit.py`:** `CONTEXT_KEYS` (:66-69) and the expected context (:170) now include `consolidation` (Q11).
- **`test_ws1_verification.py`,** in separate hunks:
  - the matrix (:290-296);
  - the no-FX-write-route check (:297-303);
  - isolation (:490-493);
  - the BE-32.1 migration block (:1101-1149).
- **`test_domain.py`:**
  - 0031's frozen copies (:322-333);
  - the calculations grep (:341);
  - the projector 2 summary keys (:817-821);
  - part 6 (:823-953), which ends with `script(db, _TERMS_CLEANUP)` (Q12).
- **New:** `app/tests/terms_kit.py`. `clear_terms` was removed from it (Q12).
- **Frontend:**
  - new: `terms.ts`, `TermsParts.tsx`, `FxConsolidationNotice.tsx` and `terms.test.ts` (14 cases);
  - changed: `features.ts`, `schema.d.ts` (generated), `InstrumentView.tsx`, `ProjectToDomain.tsx`, `LiquidityView.tsx`, `MaturityLadderView.tsx` and `ResultContextPanel.tsx`;
  - in other agents' tests: `consolidation: 'BLOCKED'` added to the context fixtures in `calculations.test.ts` and `validation.test.ts`, and to the required-keys pin in `validation.test.ts`;
  - e2e checks added to `liquidity.spec.ts` and `calculations.spec.ts`.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration round trip (CLI, scratch `moodymate_scratch_p32`) | `Running upgrade 0030 -> 0031` / `0031 (head)` / downgrade -1 `0030` / upgrade `0031 (head)`. After the non-seed row was removed: `Running downgrade 0031 -> 0030` / `0030` / `to_regclass('currency')` and `to_regclass('instrument_term_version')` both NULL / `Running upgrade 0030 -> 0031` / `0031 (head)`. Then `dropped moodymate_scratch_p32` |
| Seed tags (scratch) | `0031_term_vocabulary \| 15 ;  0031_currencies \| 4` |
| Append-only (scratch) | `INSERT of a non-seed currency (JPY): INSERT 0 1`; `ERROR:  currency is append-only (BE-14.2): UPDATE rejected` |
| Downgrade with a non-seed row (scratch) | `instrument term rows, FX observations, or term vocabulary or currency rows that are not the 0031 seed exist; they are kept (D-02, D-61), so this downgrade is refused`; current stays `0031 (head)` |
| Backend engineer, targeted (`moodymate_test_p32`) | domain `1 passed, 1 warning in 30.16s`; WS1 + committed-artifacts + config `9 passed, 1 warning in 73.22s`; calculations + credit `2 passed, 1 warning in 61.69s` |
| Test writer (`moodymate_test_p32b`) | Before the `validation_kit` edit: `1 failed, 8 passed` (`AssertionError: ('run (POST)', {... 'consolidation', ...})`, `validation_kit.py:174`). After it: calculations + validation `4 passed, 1 warning in 65.90s`. Final targeted run: `9 passed, 1 warning in 139.74s (0:02:19)` |
| Fix round (`moodymate_test_p32fix`) | domain `1 passed, 1 warning in 24.88s`; `test_migration_round_trip_and_downgrade_guards` `1 passed, 1 warning in 26.40s` |
| Mutation check (pre-fix "skip a version with no term row" put back temporarily; afterwards `grep -c "TEMP: pre-fix"` gave 0) | `AssertionError: assert ('2026-06-30'...ec072341fc97') == ('2026-06-30', None)` at `app/tests/test_domain.py:932`; `1 failed` |
| Collected (D-26, D-60 §1) | `49 tests collected in 0.57s` (49 -> 49) |
| ruff / format / mypy (fix round) | `All checks passed!` / `176 files already formatted` / `Success: no issues found in 176 source files` |
| OpenAPI drift (fix round) | before re-export `openapi.json is out of date` (rc=1); after `openapi.json is up to date` (rc=0) |
| FX route list (`openapi.json`) | `/api/v1/currencies ['get']`; `/api/v1/fx/consolidation-status ['get']` |
| Conversion grep over `app/calculations/` | no matches |
| WS1 matrix | `GET /api/v1/currencies` and `GET /api/v1/fx/consolidation-status`: `domain:read`, 200 for all five roles |
| Frontend gen:api / tsc / lint | `schema unchanged`; `tsc-exit=0`; `No ESLint warnings or errors` |
| vitest | terms `14 passed (14)`; full `Test Files 44 passed (44)`, `Tests 750 passed (750)` (one failure before the pin update: `validation.test.ts:100`); after the FE fix round, four targeted files `Tests 135 passed (135)` |
| `npm run build` | `Compiled successfully in 19.6s`, 35 pages, exit 0; FE fix round `build exit=0` |
| Playwright, e2e run (`moodymate_test_p32fe`, ports 8032/3032) | calculations + domain + liquidity: `8 passed (34.1s)`. Screenshots show the notice and "FX consolidation: BLOCKED" on the LCR and ladder runs |
| Playwright, after the FE fix round (`moodymate_test_p32fefix`, ports 8732/3732) | `liquidity.spec.ts`: `3 passed (15.5s)` |
| `schema.d.ts` against `openapi.json` | In sync at e2e time. **Stale after the backend fix round:** `4284c4284`, `4483c4483`, `6171c6171` (description lines only). The architect's grep confirms `schema.d.ts:6171` does not have the new `terms_inconsistent` sentence |
| RETENTION patch | `git apply --check OK` |
| Full backend suite | **not yet run** (the coordinator runs it once) |
| Full Playwright and full vitest after the fix round | CI |

Every run used its own database: `moodymate_scratch_p32` (dropped), `moodymate_test_p32`, `_p32b`, `_p32fix`, `moodymate_test_p32fe` and `_p32fefix`. `moodymate_dev` and the phase copy `moodymate_f06_p32` were not touched.

## Plan ticks

The architect checked each Done-when against the code and the test lines on 2026-10-24. Each tick has an evidence line in DELIVERY_PLAN.md.

- **BE-32.1 ticked.**
  - Migration: triggers :119-125, CHECKs :240-250, insert check :102-116, seeds :259-325, downgrade guard :342-359.
  - WS1 test:
    - seed tagged and PROVISIONAL :1107, :1146;
    - append-only :1108, :1125, and `test_domain.py:948`;
    - downgrade refused with counts unchanged :1112-1124;
    - round trip with existing shapes equal on both sides and the seed restored :1127-1149.
  - The CLI round trip.
- **BE-32.2 ticked** (`test_domain.py`):
  - the contract and its hash :825;
  - matching by hash :826-836;
  - the governed publication :837-846;
  - the switch off :853-862;
  - the counts and term rows :869-875;
  - typed in SQL and in the read :880-898;
  - the unknown value :896;
  - the v1 golden :422 and the projector 2 keys :817-821.
- **BE-32.3 ticked** (`test_domain.py`):
  - the later import with an earlier `effective_from` :911-915;
  - the picks by business date :916-922;
  - fail closed :923-932;
  - the snapshot unchanged :933.
  - The mutation check fails at :932.
- **BE-32.4 ticked.**
  - The matrix `test_ws1_verification.py:290-296`, no write route :297-303, isolation :490-493.
  - The calculations grep `test_domain.py:341`; the status and `context.consolidation` :939-946.
  - The drift check, rc=0.
  - The task text's `RETENTION.md` rows are not part of the Done-when. They are still open (pre-merge item 1).
- **FE-32.1 ticked.**
  - `terms.test.ts`: fixed :108-122, floating :124-134, absent :136-154, unknown :156-170.
  - `e2e/liquidity.spec.ts:123-128` inside the LCR test at :105; Playwright `8 passed`, then `3 passed`.
  - The regenerated `schema.d.ts` is still a pre-merge item (item 2).

## Review outcome

- **MEDIUM 1 (fixed, backend).** `?effective_on=` used to skip a version with no term row, or with a masked date, and silently pick an older one.
  - Now `_effective_version` returns null whenever any rate-terms version has no term row or an `effective_from` the caller is not cleared for (`service.py:1149-1161`).
  - The descriptions are updated (`schemas.py:219-225`, :256-266), and the fold has a floor-above-cap restatement case (`test_domain.py:923-932`).
  - The mutation check proves the fold catches the old behaviour.
  - Recorded as a D-61 amendment.
- **MEDIUM 2 (open, docs).** BE-32.4's `RETENTION.md` rows are missing.
  - A patch is ready at `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/retention_0031_rows.patch`, and `git apply --check` passes.
  - It does not yet carry the Q2 edits:
    - edit 1 is missing from the "Rate terms" row: that term values are masked on read by the classification of their pinned schema field (D-33; all `internal` in the frozen contract);
    - half of edit 2 is missing from the "Term vocabulary and currency reference" row: that the seed is for the default tenant only. The downgrade wording is already there.
  - Merge is blocked until the edited rows are in.
- **LOW findings:**
  1. `frontend/src/components/domain/TermsParts.tsx` (formerly :137): an inconsistent version's Effective-from cell said "Not stated". **Fixed** (Q15): `TermsParts.tsx:141` uses `EFFECTIVE_FROM_NOT_WRITTEN`, checked at `terms.test.ts:199-201`.
  2. `backend/app/domain/router.py:388`: `unknown_fields` is returned whatever the caller's clearance. That would reveal that a masked value is outside the vocabulary. It cannot happen today, because every term field is `internal` in the frozen contract. **Open.** Fix: filter `unknown_fields` by the pinned classification of each field.
  3. `backend/app/domain/service.py:1097-1101` and `router.py:477`: every detail read runs one `ImportService.find` per distinct import (N+1). If a lookup returns None, `view.imported_at[v.import_job_id]` raises KeyError, which becomes an unstructured 500. **Open.** Fix: one tenant-scoped select, plus a defined failure when an id is missing.
  4. `backend/app/tests/test_ws1_verification.py:493`: the outsider's `observation_count == 0` check is vacuous, because no observation exists at that point. The repository filter is in place (`repository.py:443-449`) but not proven by a test. **Open.** Fix: next to :1126, also assert that an outsider's count is 0 (no new function; the budget stays 49).
  5. `backend/app/domain/service.py:283-287`: unknown values are counted only after the consistency check. **Resolved** by Q3: the behaviour is kept and documented (`schemas.py:75-77`, D-61 amendment).

## Rulings (auto-resolved)

The decision-resolver gave each ruling. "Report" means this note is the ruling's record. The D-61 amendment is dated 2026-10-24, Phase 32 build, auto-resolved.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | Accept the optional `term_rows` and `terms_inconsistent` (projector 3 only; `response_model_exclude_none` keeps projector 1 and 2 bodies byte-identical; D-57 precedent, D-62 §6). Frontend re-runs `gen:api`. It was re-run, but before the backend fix round's re-export, so it must be run again (pre-merge item 2). | Report |
| Q2 | Accept the three RETENTION rows with two edits: masking by the pinned classification in row 1; the downgrade refusals and "default tenant only" in row 2. The coordinator writes them. Not a hard stop: they delete nothing. **Not applied yet**, and the patch lacks the edits. | Report |
| Q3 | Keep as built. An inconsistent record's term values are counted only as TERMS_INCONSISTENT, never as UNKNOWN_VALUE. Done: `schemas.py:71-78`, `openapi.json` re-exported. | D-61 amendment |
| Q4 | A version with no term row shows `effective_from: null` and is never a candidate. The date stays in the canonical record. The fix round goes further and makes the pick undetermined (MEDIUM 1). Done: `schemas.py:219-225`, :256-266. | D-61 amendment |
| Q5 | Only callers cleared for `effective_from` take part (D-04, D-33). **Superseded the same day** by the stricter rule: an uncleared date makes the pick null (`service.py:1156-1157`). | Report; D-61 amendment |
| Q6 | `?effective_on=` exists only on `GET /instruments/{instrument_id}`. The versions list is never filtered. A list-level pick is deferred. | Report |
| Q7 | Currencies and vocabularies are seeded for the default tenant only (D-11, D-37 §5). Another tenant gets an empty list, and its values are counted UNKNOWN_VALUE. Asserted at `test_ws1_verification.py:491-493`. Reference data for other tenants is a client item. | Report |
| Q8 | The opt-in seed flag that publishes the rate-terms contract is deferred. When built, it must be off by default (D-57 §3). | Report |
| Q9 | **Hard stop**, not ruled here (D-61 §6). See below. | Open items; hard-stop message |
| Q10 | Same as Q4. The fold also pins the null: `test_domain.py:891`. | D-61 amendment |
| Q11 | Accept the shared `validation_kit.py` hunk (:66-69, :170). The context is not hashed (`calculations/service.py:2379`; D-56 §4, D-31 §4). | Report |
| Q12 | Keep `_TERMS_CLEANUP` in conftest's `clean()`. `test_domain.py:953` uses `script(db, _TERMS_CLEANUP)`, and `clear_terms` is gone from `terms_kit.py`. The conftest hunk is committed with them. | Report |
| Q13 | An effective-on picker is out of scope for FE-32.1. Deferred, LOW. If built, it must be labelled a business-date read aid, never "current". | Report |
| Q14 | No screen for currencies or FX status this phase; the notice is enough. A reference read-out is deferred until the client's FX policy exists. | Report |
| Q15 | The absent-terms mapping is confirmed. Masked values render as masked. A not-written version's Effective from says "Not recorded: terms inconsistent". Done: `TermsParts.tsx:141`, fixture `terms.test.ts:185-191`. | Report |

## Open items

**Blocking merge:**
1. **RETENTION.md rows (MEDIUM 2).** Apply `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/retention_0031_rows.patch` after the row 1 and row 2 edits (Q2), then grep for `instrument_term_version`, `currency` and `fx_rate_observation`.
   - The scratchpad belongs to this session, so copy the patch before the session ends.
2. **Regenerate `frontend/src/api/schema.d.ts`** with `npm run gen:api` and commit it. Otherwise CI's `npm run gen:api && git diff --exit-code src/api/schema.d.ts` (`ci.yml:78`) fails on three description lines.
3. **Commit the `conftest.py` `_TERMS_CLEANUP` hunk** together with the `test_domain.py` and `terms_kit.py` changes (Q12).
4. **The full backend suite,** run once by the coordinator on the merged tree.
5. **At merge:** re-chain 0031 to the merged head and re-run the round trip.

**Not blocking (LOW findings and follow-ups):**
- LOW 2: `unknown_fields` is not filtered by clearance.
- LOW 3: N+1 import lookups and a possible KeyError 500.
- LOW 4: the vacuous outsider FX count.
- The masked-date fail-closed branch (`service.py:1156-1157`) has no test. It cannot be reached through the API, because every seeded role is cleared for `internal`.
- Not run after the FE fix round, left to CI: the full vitest suite and `calculations.spec.ts` (the ladder notice passed in the first e2e run). The instrument detail versions table was not checked in a browser; vitest covers it.
- **Deferred:**
  - a list-level `effective_on` (Q6);
  - the opt-in rate-terms publish flag (Q8);
  - an effective-on picker on the instrument detail (Q13);
  - a currency and FX status reference read-out (Q14).
- **ARCHITECTURE.md** needs D-61 and today's amendment, projector v3, the four 0031 tables, the two reads and the two switches, after merge.
- **Databases to drop:** `moodymate_test_p32`, `_p32b`, `_p32fix`, `moodymate_test_p32fe`, `_p32fefix`.
- **Build folders to remove:** `frontend/.next-p32fe` and `.next-p32fix` (gitignored).

**For the client before production (not blocking the build):**
- **Business date of a version with inconsistent terms** (Q4/Q10), added to the existing business-date item (D-29 §3).
- **Reference data (currencies, term vocabularies) for additional tenants** (Q7); this belongs to tenant onboarding.
- **Rate-terms contract in shared tenants.** Publishing `INSTRUMENT_RATE_TERMS` in a shared or production tenant is a client decision (D-57 §3).
- **Retention periods** for the four 0031 tables, set by Compliance.
- **Seeds.** The vocabulary and currency seeds stay PROVISIONAL until the client confirms them.
- **Bond attributes and deposit classes** (D-61 §7).
- **D-61** is still at status "proposed".
- **Illustrative content.** The samples are synthetic, and no regulatory certification or vendor parity is claimed.

**Hard stop (for the single end-of-phase message): the FX policy (Q9, D-61 §6).**
- The client must choose four things:
  - (a) the rate source: central-bank reference rates, a market-data feed, or internal treasury rates;
  - (b) the rate types: closing or spot, period average, or historical;
  - (c) the reporting currency: the group's functional currency, or one per legal entity;
  - (d) the conversion method.
- **Recommendation:** keep consolidation BLOCKED (`fx_policy_not_approved`), with no conversion code and no FX write route, and pick no default.
- **Once decided:** record a new decision, implement the policy as governed, versioned methodology data with maker-checker (the D-32 pattern), and add an audited observation write route.

No other hard stop arose:
- 0031 only adds things, and its downgrade refuses rather than drops;
- no existing CHECK is widened;
- no role or permission change: both reads use `domain:read`, and the projection uses the existing `domain:project`;
- no real client data;
- the only deletions were in per-agent test databases and the scratch database.

---

Files edited by the architect:
- `/home/user/MoodyMate-wt/f06-p32/docs/DELIVERY_PLAN.md`: BE-32.1, BE-32.2, BE-32.3, BE-32.4 and FE-32.1 ticked, each with an evidence line. BE-32.4 and FE-32.1 also carry their pre-merge notes (the RETENTION rows; the regenerated `schema.d.ts`).
- `/home/user/MoodyMate-wt/f06-p32/docs/DECISIONS.md`: a D-61 amendment (§3/§4 for Q3; §5 for Q4, Q10 and the MEDIUM 1 fix, which supersedes the Q5 note), dated 2026-10-24 and marked auto-resolved.

## Coordinator merge (2026-10-24)

- **Blocking items 1-3 done:** RETENTION.md rows applied with the Q2 edits (term values masked by their pinned field's classification; seeds for the default tenant only); `schema.d.ts` regenerated; the `conftest.py` `_TERMS_CLEANUP` hunk committed with `test_domain.py` and `terms_kit.py`.
- **Build side effects reverted:** `frontend/tsconfig.json` and `frontend/next-env.d.ts` (scratch `distDir` paths).
- **Master merged in** (Phases 29, 31 and the BE-31.3 follow-up): no conflicts. 0031's `down_revision` is already `0030`, the merged head, so no re-chain was needed. The round trip runs in the suite.
- **Checks on the merged tree:**
  - backend: ruff `All checks passed!`, format `176 files already formatted`, mypy `Success: no issues found in 176 source files`, `openapi.json is up to date`;
  - full suite: `49 passed, 1 warning in 293.00s`; the BE-31.3 goldens still hold;
  - frontend: `gen:api` gives no diff, tsc clean, `No ESLint warnings or errors`, vitest `750 passed (750)`.
