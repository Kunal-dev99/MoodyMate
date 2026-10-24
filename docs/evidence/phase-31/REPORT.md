# Phase 31: Typed maturity, legal entities and identity links (checks)

**Scope.** This report covers BE-31.1 to BE-31.5 and FE-31.1 (Workstream D part 1; FC-01, FC-02, DG-05; D-57, D-58).
- **Where and who.** The phase was built in parallel in the worktree `/home/user/MoodyMate-wt/f06-p31` by:
  - a backend-engineer and a test writer (the test writer stopped at its time limit);
  - one frontend-engineer, who owns FE-31.1, `navigation.ts` and `features.ts`;
  - one review agent and one e2e run;
  - a backend and a frontend fix round.
- **Commits.** The coordinator committed the phase and merged Phases 28 and 29 in. In that merge the service moved to the shared helpers: `most_sensitive` from `core.classification`, the reconcile refusal through `core.refusals.refuse`, and the test import of `canonical_sha256` to `app.core.canonical` (Q10). The maturity masked cell also renders through `<Masked>` (FE-29.2).
- **Migration 0030 is pre-numbered,** with `down_revision` "0027".
  - Phases 21–22 hold 0024–0025 and Phases 26–27 hold 0028–0029; none of these is built. Phases 28–30 add no migration. Phase 32 holds 0031.
  - The coordinator re-chains 0030 at merge (protocol step 6). The WS1 test reads 0030's `down_revision` from the script, so the test survives the re-chain.

## What was built

- **BE-31.1 Migration `0030_identity_links`.** It only adds things and has no seed (D-01).
  - **`legal_entity`:** one id per (tenant, source system, code), always `provisional`, with an insert check that the source belongs to the row's tenant.
  - **`domain_identity_link`:**
    - `UNIQUE (source_kind, source_row_id, target_kind)`;
    - CHECKs on the kinds, the method and `resolver_version >= 1`;
    - an insert trigger (`domain_identity_link_checks`, :105-128) that checks:
      - the pair is one D-58 allows;
      - both ends are rows of the link's tenant (D-04);
      - the target's key equals the source row's own key.
  - **Both tables** refuse UPDATE, DELETE and TRUNCATE, using 0017's `domain_append_only` (:133-139).
  - **Downgrade** (:238-243): it refuses while either table has a row.
  - No column is added to `instrument_version`, `position_version`, `cashflow_event` or the credit tables (D-58 §1).
- **BE-31.2 Typed-maturity contract.**
  - **The contract.** `INSTRUMENT_TYPED_MATURITY` is instrument v1 with `maturity_date` as DATE and `interest_rate` at (11,6), and nothing else.
  - **Frozen hash.** `INSTRUMENT_TYPED_MATURITY_SHA256` = `20f272b4…` (`app/domain/constants.py:467`), next to `INSTRUMENT_V1_SHA256` = `388d9bc0…` (:408).
  - **Publication** goes only through the governed proposal path.
  - **Seed flag.** `seed_demo.py --publish-typed-instrument` is opt-in: it proposes, analyses, submits, has the approver approve, then imports and projects the instrument sample.
  - **Q7 fix.** `_snapshot` now picks each feed's import through that feed's own mapping (`_mapped_import`, `seed_demo.py:250`, :536-542), so a re-run keeps the v1 instrument import and creates no new snapshot or run.
- **BE-31.3 Projector v2.**
  - **Spec chosen by contract hash.** `INSTRUMENT_SPECS_BY_HASH` (`constants.py:487-489`, used at `service.py:590`): the v1 hash uses projector 1, unchanged; the typed hash uses projector 2; any other hash gives `projection_unsupported`.
  - **Switch.** With `INSTRUMENT_PROJECTOR_V2_ENABLED` (`core/config.py:134`) off, a typed import gets 409 `projection_unsupported`, audited DENIED.
  - **`ProjectionSummary.missing_values`** is optional and written only by projector 2. The two projection routes drop null fields, so projector 1 bodies are unchanged.
- **BE-31.4 Identity links.**
  - **At projection.** Projecting positions, cash flows or exposures registers new legal entities and links the projection's own rows in the same transaction. This happens only while `IDENTITY_LINKS_ENABLED` (`config.py:138`) is on.
  - **Rule.** Exactly one identity in the tenant gives a link. Zero gives `LINK_UNRESOLVED` and several give `LINK_AMBIGUOUS`; both are counted and write no link.
  - **Reconcile.** `POST /identity-links/reconcile` (`domain:project`) covers historical rows. It is idempotent and audited `identity_link.reconcile`. With the switch off it answers 409 `identity_links_disabled`, audited DENIED.
  - **Counts from a projection** go only in the `import.project` audit details (Q6).
  - **Exposures** link to their COUNTERPARTY only (Q5).
- **BE-31.5 Reads.**
  - `GET /legal-entities` (ordered by code, then source), `GET /legal-entities/{legal_entity_id}` and `GET /identity-links`, all `domain:read`.
  - A legal entity's code is masked by the most sensitive classification of `legal_entity_id` across the position schema versions that name it. If no version names it, the code is unclassified.
  - `openapi.json` is re-exported. **`docs/DOMAIN_IDENTITY.md` and the `docs/RETENTION.md` rows are not in the worktree yet** (see Open items).
- **FE-31.1** sits behind `NEXT_PUBLIC_DOMAIN_LINKS_UI`. It is on by default and forced off when `NEXT_PUBLIC_DOMAIN_UI` is off.
  - **Instrument detail:**
    - a typed maturity shows the server's ISO text with a "Typed date" badge;
    - a v1 instrument shows "Not stated (schema v1 free text)";
    - a typed row with no maturity shows "Not stated in the source row";
    - a masked value shows as masked;
    - with the switch off, the Phase 30 field returns.
  - **Linked identity.** Cash flows (on the instrument detail) and exposure versions show a "Linked identity": the target's kind and id with a link to its page, or "Unresolved (text key)". A 403 or a failed read shows as such, never as unresolved (D-04).
  - **Legal entities tab** under Financial Domain, after the credit tabs: a list at `/domain/legal-entities` and a detail at `/domain/legal-entities/[legalEntityId]`. The detail shows the link counts and the links that point to it (ids only). Every entity has a "Provisional identity" badge. With the switch off, these routes return 404 (`LinksGate`).
  - **Generated types only.** `npm run gen:api` was run twice; the second run picked up `missing_values` and the new ordering wording.

**Shared and existing files changed:**
- **`conftest.py`:**
  - one `_IDENTITY_CLEANUP` (:379-388), listed once in `_CLEANUP` (:402) and once in `clean()` (:463). The duplicate block is removed (Q12).
  - `clear_identity_links(url)` (:391).
  - the WS3 matrix covers `/legal-entities` and `/identity-links`.
- **`test_ws1_verification.py`,** in separate hunks:
  - the matrix (:268-286);
  - isolation (:421-423, :455-459, :499);
  - AUDITED (:774) and the call (:1020);
  - the BE-31.1 migration block (:1036-1066).
- **`test_calculations.py`:** the `head == "0027"` pin is replaced by the computed head (still asserted at :1606), and identity rows are cleared before its 0026 downgrade.
- **`test_credit.py`:**
  - the BE-31.4 credit fold (:500-524);
  - `clear_identity_links` before the downgrade block (:947);
  - the downgrade match narrowed to "obligor ratings exist" (:949) (Q1).
- **`test_domain.py`:** `golden_check` (:383), the link and reconcile fold (:412-477) and part 5, typed maturity and ambiguous keys (:608-781). Before part 4 it clears the calc rows and the links, so 0017's own refusal is the one tested.
- **New:** `app/tests/identity_kit.py` and `app/tests/golden/domain_v1_projection.json`, the v1 golden captured from the unchanged projector.
- **Frontend:**
  - new: `identity.ts`, `IdentityParts.tsx`, `LinksGate.tsx`, `LegalEntityCatalogue.tsx`, `LegalEntityView.tsx`, the `legal-entities` pages and `identity.test.ts` (24 cases);
  - changed: `InstrumentView.tsx`, `ExposureView.tsx`, the `PageHeader.tsx` route labels, `schema.d.ts` (regenerated), `features.ts` and `navigation.ts`;
  - the earlier reversibility tests in `registry.test.ts`, `credit.test.ts` and `ratings.test.ts` now set `NEXT_PUBLIC_DOMAIN_LINKS_UI=off`;
  - `e2e/ws3-smoke.spec.ts` (FE-31.1 test) and `e2e/counterparties.spec.ts` (step 4b).

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration round trip (CLI, scratch `moodymate_scratch_p31`) | `Running upgrade 0027 -> 0030` / `Running downgrade 0030 -> 0027` / `0027` / `Running upgrade 0027 -> 0030` / `0030 (head)`; "scratch dropped" |
| Downgrade with seeded rows (scratch) | `legal entities or identity links exist; they are kept (D-02, D-58), so this downgrade is refused` / `0030 (head)` |
| Backend, first targeted run (`moodymate_test_p31`) | `1 failed, 11 passed` with `FAILED app/tests/test_credit.py::test_credit_domain_projection_snapshots_reads_and_guards` (0030 refused the test's downgrade to 0022; Q1) |
| Test writer, partial run (`moodymate_test_p31b`) | `2 failed, 1 passed` (`KeyError: 'total'`; `Can't locate revision identified by '0030'`). These were implementation gaps at the time and are closed. |
| WS1 alone (backend engineer) | `4 passed, 1 warning in 38.31s`. Matrix rows: `GET /api/v1/identity-links domain:read 200 200 200 200 200`; `POST /api/v1/identity-links/reconcile domain:project 403 200 403 403 403`; `GET /api/v1/legal-entities` and `GET /api/v1/legal-entities/{legal_entity_id}` `domain:read 200 200 200 200 200` |
| Fix round, targeted domain + credit + WS1 + calculations + committed-artifacts (`moodymate_test_p31c`) | `9 passed, 1 warning in 106.34s (0:01:46)`; final re-run of domain + credit `2 passed, 1 warning in 33.88s` |
| Mutation checks (service.py restored, `cmp` ok) | M1 reconcile ignores the switch, `test_domain.py:418: KeyError`; M2 v2 switch ignored, `:673: KeyError`; M3 matched by version number, `:649: AssertionError`; M4 links written with the switch off, `{'domain_identity_link': 75} != {'domain_identity_link': 0}`. Each gave `1 failed`. |
| Collected (D-26, D-60 §1) | `49 tests collected in 0.29s` (49 -> 49) |
| ruff / format / mypy | `All checks passed!` / `165 files already formatted` / `Success: no issues found in 165 source files` |
| OpenAPI drift | `openapi.json is up to date`, rc=0; `ProjectionSummary.missing_values True required False` |
| Scratch verification (outside the repo) | position projection audit `links_written 25, links_unresolved 25, legal_entities_new 2, projector_version 1`; reconcile 1 `POSITION_VERSION/INSTRUMENT rows 25 linked 25 links_written 25`; cash-flow links 25; exposure links 13; typed projection `projector_version 2`, `missing_values {'maturity_date': 1}`, `('TM-1', date(2030, 6, 30), Decimal('2.123456'))`; "trigger guards ok"; `1 passed in 9.65s` |
| Seed Q7 (`moodymate_seed_p31c`; counts are snapshots, runs, imports) | pass 1 `1\|2\|5`; pass 2 (`--publish-typed-instrument`) `1\|2\|6`, same snapshot, runs skipped; pass 3 `1\|2\|6`, "already ACTIVE, skipped" / "already projected, skipped" |
| Seed ladder over the typed vs the v1 snapshot | `result_sha256` different (`1635dbb6…` vs `84f709cb…`); "ladder rows minus ids: df2e62d6... 32 rows for both runs (figures identical)" (Q2) |
| Frontend gen:api / tsc / lint | `src/api/schema.d.ts [415.5ms]` / `TSC_OK`, `tsc exit=0` / `✔ No ESLint warnings or errors` |
| vitest | full: `Test Files 38 passed (38)`, `Tests 642 passed (642)`; after the FE rulings (domain + credit): `Test Files 7 passed (7)`, `Tests 176 passed (176)` |
| `npm run build` | `/domain/legal-entities` and `/domain/legal-entities/[legalEntityId]` built; FE fix round `build exit=0` |
| Playwright, e2e run (`moodymate_test_p31fe`, ports 8031/3031) | `ws3-smoke.spec.ts`: `2 passed (18.9s)` |
| Playwright, after the FE rulings (`moodymate_e2e_p31fx`, ports 8731/3731, `CI=1`) | `counterparties.spec.ts` + `ws3-smoke.spec.ts`: `3 passed (24.0s)` |
| Full backend suite | **not yet run** (the coordinator runs it once) |
| Full Playwright, including `e2e/smoke.spec.ts` | CI |

Every run used a per-phase database: `moodymate_scratch_p31` (dropped), `moodymate_test_p31`, `_p31b`, `_p31c`, `moodymate_seed_p31c`, `moodymate_test_p31fe` and `moodymate_e2e_p31fx`. `moodymate_dev` was never touched.

## Plan ticks

The architect checked each Done-when against the code and the test lines, on 2026-10-24. Each tick has an evidence line in DELIVERY_PLAN.md, and each open box has a "Not yet ticked" line naming what is missing.

- **BE-31.1 ticked.**
  - `0030_identity_links.py`: triggers :133-139, link checks :105-128, downgrade guard :238-243.
  - `test_ws1_verification.py:1041-1066`: append-only at :1044 (`identity_kit.py:244-257`), refusal with counts unchanged at :1054-1056, round trip with the `identity_shape` of 13 existing tables equal on both sides at :1058-1066.
  - The CLI round trip. `9 passed`.
- **BE-31.2 not ticked.**
  - The domain fold is evidenced at `test_domain.py:609-636`: the frozen hash; BREAKING with the `core_instrument` MAPPING dependent and a second-person approval (`identity_kit.py:121-147`); statuses `{1: DEPRECATED, 2: DRAFT, max+1: ACTIVE}`; the v2 DRAFT hash unchanged.
  - **Missing:** "the e2e smoke diff still passes". `e2e/smoke.spec.ts` was not run in this phase. Tick it when CI shows it green.
- **BE-31.3 not ticked.** The last bullet is reworded per Q2, with a D-57 amendment.
  - Evidenced in `test_domain.py`:
    - the v1 golden at :383 and `TYPE_NOT_PROJECTED` 25 at :480;
    - the bad dates held by validation (:659-666) and never projected (:706);
    - `2030-06-30` stored as a DATE (:707) and read back as the same text (:712-718);
    - a missing maturity NULL (:708), counted in `missing_values` (:693) and absent from the v1 summary (:414);
    - 6 dp exact (:709-711);
    - matching by hash (:637-649, :676-687);
    - the switch off giving 409, audited DENIED (:668-675);
    - Q2 (b) via `run_outputs` (:719-741).
  - **Missing:**
    - (c) no repeat run over the typed snapshot is asserted;
    - (a) there is no pre-phase golden of a v1 run's `result_sha256`. The test only shows the hash equal before and after reconcile (:476-477), which is BE-31.4's check. A literal golden may not be possible, because run hashes include row ids that differ per database. The test writer should add the golden or send the wording back to the decision-resolver.
- **BE-31.4 ticked.**
  - `test_domain.py:412-420`: the switch off.
  - :432-466: expected ids, 75 links.
  - :471-474: a second reconcile writes 0 with equal counts.
  - :476-477: the run hash is equal before and after reconcile.
  - :742-777: ambiguous and unresolved keys counted, existing links reported as `linked_now_ambiguous`.
  - `test_credit.py:500-524`: exposure to counterparty, the rest `LINK_UNRESOLVED`.
  - The WS1 matrix, isolation and audit lines above. `9 passed`. M1-M4 each fail the test.
- **BE-31.5 not ticked.**
  - Evidenced: the drift check passes; the matrix gives 200 for all five roles (`test_ws1_verification.py:268-275`, and every seeded role holds `domain:read`); another tenant's id gives the 404 of a random id (:499); the tenant's lists total 0 (:455-459).
  - **Missing:** `docs/DOMAIN_IDENTITY.md` does not exist, and `docs/RETENTION.md` has no `legal_entity` or `domain_identity_link` row (grep).
- **FE-31.1 ticked.** The position bullet is reworded per Q14.
  - `identity.test.ts`: maturity states :49-77, link states :79-121, switch off restores the Phase 30 nav :202-210, the routes 404 :227.
  - `ws3-smoke.spec.ts:656`: the typed date :703 and v1 "Not stated" :708-713.
  - `counterparties.spec.ts` step 4b (:192-221).
  - Playwright `3 passed`.
  - The switch-off state was checked by vitest only, which the Done-when allows.

## Review outcome

- **MAJOR (fixed, backend; partly open).** The BE-31.2–31.4 fold helpers existed, but no test called them.
  - The fix round folded them into `test_domain.py` (:412-477, :608-781) and `test_credit.py` (:500-524), with the count still at 49. The four mutations prove the switches and the hash matching are tested.
  - Still open: BE-31.3 (c) and the (a) golden, as above.
- **MEDIUM (open, docs).** `docs/DOMAIN_IDENTITY.md` and the `RETENTION.md` rows are missing, so BE-31.5's grep fails. Per Q3 the coordinator writes them. Merge is blocked until they are in.
- **LOW findings:**
  1. `conftest.py`: `_IDENTITY_TABLES` and `_IDENTITY_CLEANUP` were defined twice and listed twice. **Fixed** (Q12; verified at :379-402 and :463).
  2. `frontend/src/components/domain/identity.ts:115-130` (`fetchSourceLinks`): each row the target page does not cover gets its own `GET /identity-links?source_id=…`, all fired in parallel. That means up to N requests per detail page before a reconcile, and one per version on `ExposureView`. **Open.** Fix: a batched, capped `source_id` filter, or capped concurrency.
  3. FE-31.1 named a position detail that does not exist. **Resolved** by Q14 (plan reworded; follow-up below).
  4. `backend/app/domain/repository.py:929`: `GET /legal-entities` sorts by the raw `code` even for callers not cleared for it, so masked codes leak their relative order. **Open; recorded as D-33-class residual risk.** The instrument and counterparty lists already sort by `business_key` (:216, :448), so this is the same existing class.
  5. `backend/app/domain/service.py` `project()`: the v2 refusal comes before the existing-projection read-back. With `INSTRUMENT_PROJECTOR_V2_ENABLED=false`, a POST for an already projected typed import answers 409 instead of 200 with the existing projection. This matches `CREDIT_PROJECTION_ENABLED`. **Open; not ruled.** Send it to the decision-resolver, or keep the order and record it as consistent with the credit switch.
  6. `e2e/ws3-smoke.spec.ts`: the FE-31.1 test permanently publishes the typed contract in the e2e database. A local re-run on the same database makes earlier specs hit `MAPPING_REQUIRED`. **Mitigated:** a must-stay-last comment is at :653-655 (Q16). The e2e database must be fresh for each run (D-03).
  7. (Q17) `ProjectionSummary.missing_values` is in the generated types but not shown on the import page. **Follow-up.**

## Rulings (auto-resolved)

The decision-resolver gave each ruling. "Report" means this note is the ruling's record. The DECISIONS.md amendments are dated 2026-10-24, Phase 31 build, auto-resolved.

| # | Ruling | Recorded in |
|---|---|---|
| Q1 | Clear the identity links before both credit downgrades (each agent has its own disposable test DB, so this is not a hard stop). Narrow the match to the rating guard's message, and keep the BE-31.4 credit checks above the cleanup. Done: `test_credit.py:947-950`; the checks are at :500-524. | Report |
| Q2 | The typed-snapshot run cannot have v1's `result_sha256`, because contributions carry `instrument_version_id`. The check becomes (a)-(c); only `instrument_version_id` is stripped. | D-57 amendment; plan BE-31.3 |
| Q3 | The coordinator writes `docs/DOMAIN_IDENTITY.md` and the RETENTION rows. It first checks that the drafts follow D-58 §5 as written and make no regulatory or vendor-parity claims, then commits them in the worktree and runs the grep. | Report |
| Q4 | Optional `missing_values` and the ordering wording are accepted. `response_model_exclude_none` is safe only while `missing_values` is the only nullable field on those routes; a future nullable field there must be checked against the golden. | Report |
| Q5 | In Phase 31, exposures link to COUNTERPARTY only; INSTRUMENT targets are deferred. | D-58 amendment |
| Q6 | Projection link counts stay in the audit details. `ProjectionOut` gains no field. | Report |
| Q7 | A seed re-run must create nothing: `_snapshot` keeps the v1 instrument import. Done and checked: 1\|2\|6 on both passes. | Report |
| Q8 | **Two recorded behaviours.** (1) Legal entities and links are written only while `IDENTITY_LINKS_ENABLED` is on. (2) Legal-entity codes are matched tenant-wide, so the same code from two sources is `LINK_AMBIGUOUS`. Cross-source precedence is a client item. | Report |
| Q9 | The field is `missing_values`, a per-field count, written only by projector 2. Asserted at `test_domain.py:693` and :414. | Report |
| Q10 | The frozen hash is `app.domain.constants.INSTRUMENT_TYPED_MATURITY_SHA256` (:467), with no second name. Asserted at `test_domain.py:614` and :635. | Report |
| Q11 | With the switch off, no `legal_entity` rows are written. `new_identities` stays 25, and new entities appear only as `legal_entities_new` in the audit details. Asserted at :412-413. | Report |
| Q12 | The test-infrastructure hunks stay with the test-writer role. The duplicate cleanup block is removed (done). | Report |
| Q13 | The test-writer role keeps the remaining folds, under one owner. The fix round completed them, except BE-31.3 (a) and (c). BE-31.2–31.4 are not ticked until those assertions pass (BE-31.4 now passes). | Report |
| Q14 | Defer a position read route and page. Position links show on the legal entity detail and through `GET /identity-links`. | Report; plan FE-31.1 |
| Q15 | Remove the cash-flow loop that always saw zero rows (done) and add the `counterparties.spec.ts` linked-counterparty fold (done, step 4b; `CI` requires the linked case). | Report |
| Q16 | Accept the ordering: `workers: 1`, and ws3-smoke sorts last. A comment is at the test (done). The coordinator re-checks at each merge that no spec sorting after `ws3-smoke.spec.ts` maps instrument imports. | Report |
| Q17 | `missing_values` on the import page is a LOW follow-up. | Report |
| Q18 | Accept the copied e2e hash constant. Its comment names its source, `INSTRUMENT_TYPED_MATURITY_SHA256` (done, `ws3-smoke.spec.ts:577-583`). | Report |

## Open items

**Blocking merge:**
1. **BE-31.5 docs.** Write `docs/DOMAIN_IDENTITY.md` and the two `docs/RETENTION.md` rows from the drafts, after the Q3 review, and run the grep.
   - The drafts are at `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/DOMAIN_IDENTITY.md` and `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/RETENTION_rows.md`. Earlier copies are under `/tmp/claude-0/-home-user-MoodyMate/2e44b3e8-4f78-5589-9f4b-92d026bf8593/scratchpad/docs-drafts/`.
   - The scratchpad belongs to this session, so copy the drafts before the session ends.
   - `DOMAIN_IDENTITY.md` must also say that exposures link to counterparties only (D-58 amendment).
2. **BE-31.3** (test writer): assert Q2 (c), a repeat run over the typed snapshot giving an equal `result_sha256`. Resolve (a): add a pre-phase golden, or get a resolver ruling on its wording.
3. **BE-31.2:** `e2e/smoke.spec.ts` passing in the CI Playwright run.
4. The full backend suite, run once by the coordinator on the tree merged with master.
5. **At merge:** re-chain 0030 to the merged head and re-run the round trip. Re-check the Q16 ordering.

**Not blocking (LOW findings and follow-ups):**
- LOW 2: `fetchSourceLinks` fires N parallel requests.
- LOW 4: the legal-entity code ordering side channel (D-33 residual risk).
- LOW 5: the v2 switch overrides the idempotent read-back; needs a ruling or a record.
- LOW 6: the e2e database must be fresh for each run.
- LOW 7 (Q17): `missing_values` is not shown on the import page.
- Follow-ups:
  - a position read route and position detail page (Q14);
  - `CREDIT_EXPOSURE -> INSTRUMENT` links (D-58 amendment);
  - a batched `source_id` filter on `GET /identity-links`.
- **ARCHITECTURE.md** needs entries for D-57 and D-58 (with today's amendments), projector v2, the `legal_entity` and `domain_identity_link` tables, the reconcile route and the two switches, after merge.
- **Databases to drop when no longer needed:** `moodymate_test_p31`, `_p31b`, `_p31c`, `moodymate_seed_p31c`, `moodymate_test_p31fe` and `moodymate_e2e_p31fx`.
- **e2e build folder.** The e2e build used the shared `frontend/.next`, because `next.config` has no option for a separate output folder. That only matters if another frontend agent builds in this worktree at the same time.

**For the client before production (not blocking the build):**
- **Typed contract in shared tenants.** Publishing `INSTRUMENT_TYPED_MATURITY` in a shared or production tenant deprecates instrument v1 for new imports (D-57 §3).
- **Cross-source legal-entity precedence (Q8, D-58 §5, Data Model §11).** A tenant whose position sources share legal-entity codes sees every such link as ambiguous until a source-of-truth rule is set.
- **Legal-entity attributes** and the end of `provisional` (D-58 §2).
- **Retention period** for `legal_entity` and `domain_identity_link`, set by Compliance.
- **D-57 and D-58** are still at status "proposed". Option B was adopted by the coordinator ruling of 2026-10-24.
- **Illustrative content.** The samples are synthetic, and no regulatory certification or vendor parity is claimed.

No new hard stop arose in this phase:
- 0030 only adds things, and its downgrade refuses rather than drops;
- no CHECK on an existing table is widened;
- no role or permission changes: the reconcile uses the existing `domain:project` and the reads use `domain:read`;
- no real client data;
- the only deletions were in per-agent test databases (Q1).

**Not checked locally:** the full Playwright run (CI runs it on the PR), and `NEXT_PUBLIC_DOMAIN_LINKS_UI=off` in a browser (vitest only).

---

Files edited by the architect:
- `/home/user/MoodyMate-wt/f06-p31/docs/DELIVERY_PLAN.md`:
  - BE-31.1, BE-31.4 and FE-31.1 ticked, each with an evidence line;
  - BE-31.2, BE-31.3 and BE-31.5 left unticked, each with a "Not yet ticked" line;
  - BE-31.3's last bullet reworded (Q2) and FE-31.1's position bullet reworded (Q14).
- `/home/user/MoodyMate-wt/f06-p31/docs/DECISIONS.md`: a D-57 amendment (Q2) and a D-58 amendment (Q5), both dated 2026-10-24 and marked auto-resolved.

## Coordinator notes (2026-10-24)
- Placed docs/DOMAIN_IDENTITY.md and the two docs/RETENTION.md rows (legal_entity, domain_identity_link) from the engineer's drafts after checking them against D-58 §5 and for any regulatory or vendor-parity claim (none).
- Q15 follow-up accepted (auto-resolved): the cash-flow 'Linked identity' Playwright loop is removed, because an instrument lists only cash flows from its own source (D-29 §3). The cell stays covered by vitest.
- The domain-test downgrade hunk is accepted. It mirrors Q1 in the credit test.
