# Phase 20: obligor rating workflow (checks)

**Scope.** This report covers BE-20.1 to BE-20.3, FE-20.1 and FE-20.2 (CR-02; D-39, D-40, D-43). Five agents built the phase in parallel in the worktree `/home/user/MoodyMate-wt/cr-p20`: a backend-engineer, a test writer, two frontend-engineers (FE-20.1 "ratings-panel" and FE-20.2 "review queue") and one review agent. Nothing is committed yet. Migration 0023 was pre-numbered with down_revision 0022, as 0022's docstring reserves. Phase 21 takes 0024.

## What was built

- **BE-20.1 Migration `0023_credit_permissions`** (revision 0023, down_revision 0022):
  - **`obligor_rating` table:**
    - CHECKs on status, rationale length (1 to 2,000) and comment length (1 to 1,000).
    - CHECKs that keep the decision fields and the superseded fields consistent with the status.
  - **Lifecycle trigger `obligor_rating_guard`:**
    - A row is written IN_REVIEW with no decision, and its content never changes.
    - The only moves are IN_REVIEW to ACTIVE or REJECTED, and ACTIVE to SUPERSEDED or RETIRED. RETIRED is reserved.
    - The decision is set once, on the move out of IN_REVIEW. The superseded fields are set only on the move to SUPERSEDED, and must name a sibling version with the same counterparty, date and tenant.
    - On insert it checks that the counterparty and the proposer belong to the row's tenant, and that the grade is a `rating_grade` code.
    - DELETE and TRUNCATE are refused.
  - **Partial unique index** `uq_obligor_rating_one_active` on (counterparty_id, effective_from) WHERE status = 'ACTIVE'.
  - **Tagged seed `0023_credit_permissions`** (D-40, approved by the user): `rating:propose` goes to risk_analyst and `rating:approve` to approver. Its MATRIX (:61-64) equals the plan table and `governance/constants.py:129-135` (`CREDIT_ROLE_PERMISSIONS`). `ROLE_PERMISSIONS` now joins WS1, WS2, WS3 and the credit grants.
  - **Downgrade:** it refuses while any rating exists. Otherwise it drops the triggers, function, indexes and table, and removes only the tagged rows.
  - **ORM model:** `ObligorRating` is in `app/domain/models.py` and has no drift from the migrated table.
- **BE-20.2 Rating writes.**
  - Routes: `POST /counterparties/{id}/ratings` (needs `rating:propose`) and `POST /obligor-ratings/{id}/approval` (needs `rating:approve`). The services are `propose_rating` and `decide_rating`.
  - **Order of checks on propose:** switch off (409), then an absent or another tenant's counterparty (404), then an unknown grade (422 `invalid_grade`).
  - **Order of checks on decide:** switch off (409), then not found (404), then `invalid_status`, then `self_approval` (matched on `proposer_user_id`), then `stale_version`.
  - Approval moves the old ACTIVE version to SUPERSEDED and then activates the new one, in one transaction, under a per-counterparty lock.
  - Audit actions are `obligor_rating.propose`, `.approve`, `.reject` and `.supersede`, and every refusal is audited DENIED. The audit details tag the grade internal and the comment confidential, and never carry the rationale.
  - `RATING_CHANGE_ENABLED` is validated as a boolean when the app starts.
- **BE-20.3 Rating reads.**
  - Routes: `GET /obligor-ratings` (filters and newest first) and `GET /obligor-ratings/{id}`, both `domain:read`.
  - The counterparty detail now carries its `ratings` history, ordered by effective_from descending, then proposed_at descending.
  - Service functions: `rating_in_effect(id, at)` and the bulk form `ratings_in_effect(ids, at)`, which Phase 21 needs.
  - D-43 §2 masking: the grade is internal, and the rationale and decision comment are confidential. The counterparty key is masked like the counterparty's business key, failing closed when unknown.
- **Contract change.** `ObligorRatingOut.grade`, `rationale` and `decision_comment` changed from `str` to `Value` (`str | MaskedValue`). `grade_rank` is null when the grade is masked. `openapi.json` was re-exported and both frontend agents regenerated their types (rulings Q1 and Q8).
- **FE-20.1 Ratings panel** on the counterparty detail, behind `NEXT_PUBLIC_RATINGS_UI`:
  - **History:** each version shows grade and rank, effective date, status pill, proposer (with "(you)" on your own), decider and rationale. Masked values show as "masked". With no versions, the panel says exposures are excluded until a rating is approved (NO_APPROVED_RATING).
  - **"Propose rating" dialog,** shown only with `rating:propose` (cosmetic; the server still checks):
    - the grade picker loads from `GET /credit-vocabularies` and is labelled provisional;
    - an effective date, and a required rationale with a counter (up to 2,000);
    - errors show per field once touched, and all of them after a submit attempt;
    - the dialog closes only on the server's 201, and the status shown is the one the server returned;
    - server refusals are shown inline with the correlation ID.
  - **Navigation:** a Ratings tab to `/domain/ratings` (`domain:read`), shown only while both `NEXT_PUBLIC_RATINGS_UI` and `NEXT_PUBLIC_CREDIT_DOMAIN_UI` are on.
- **FE-20.2 Review queue** at `/domain/ratings`:
  - It lists IN_REVIEW by default. A `?status=` filter covers every status plus "All".
  - Approve and Reject appear only on IN_REVIEW versions, only for a holder of `rating:approve` who did not propose the version (cosmetic, D-04).
  - The decision needs a comment of 1 to 1,000 characters (limit from the contract), with a counter.
  - Server refusals are shown as sent. The status shown after a decision is the one the server returned.
  - The page returns 404 while either flag is off.
  - It reuses FE-20.1's `RatingStatusPill`, `RATING_STATUS_STYLE`, `ratingRefusal` and `RatingRefusalNotice`.

**Shared and existing files changed:**
- `conftest.py`:
  - `_RATING_CLEANUP` runs first in `_CLEANUP`. It lifts the triggers and deletes the rows inside one transaction, on the per-agent test DB only (Q6).
  - `"/obligor-ratings"` is added to `WS3_PREFIXES` (Q7).
- `test_credit.py`:
  - the BE-20.1 seed and frozen-copy checks;
  - new section 5 (ratings) and additions to section 6 (guards, downgrade).
- `test_ws1_verification.py`: matrix, isolation and audit hunks for the four routes.
- `credit.test.ts` (one `beforeEach`) and `registry.test.ts` (one `stubEnv`): both pin `NEXT_PUBLIC_RATINGS_UI=off`, so they keep asserting the Phase 19 tabs. I read both hunks. Neither weakens an assertion (Q13).

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration round trip (CLI, scratch `moodymate_scratch_p20`) | `Running upgrade 0022 -> 0023` / `Running downgrade 0023 -> 0022` / `0022` / `Running upgrade 0022 -> 0023` / `0023 (head)`; scratch DB dropped (`grep -c scratch_p20` -> 0) |
| DB guards (psql, scratch) | `ERROR: an obligor_rating grade is a rating_grade code of its tenant (D-39 §1)`; `...written IN_REVIEW, undecided`; `...content never changes`; `status move IN_REVIEW -> SUPERSEDED is not allowed`; `duplicate key value violates unique constraint "uq_obligor_rating_one_active"`; `...decision is recorded once`; `DELETE rejected`; `TRUNCATE rejected` |
| Downgrade while a rating exists | `asyncpg.exceptions.RaiseError: obligor ratings exist; they are kept (D-02, D-39), so this downgrade is refused` / `0023 (head)` |
| Seeded grants | `approver \| rating:approve \| 0023_credit_permissions`, `risk_analyst \| rating:propose \| 0023_credit_permissions` |
| ORM vs schema | `no obligor_rating drift` (3 diffs, all from earlier phases and unrelated) |
| Startup switch check | `ValidationError ... RATING_CHANGE_ENABLED ... Input should be a valid boolean [type=bool_parsing]` |
| WS1 + committed artifacts + domain (p20) | `7 passed, 1 warning in 40.56s` |
| `test_credit.py` (p20) | `1 passed, 1 warning in 13.84s` |
| `test_credit.py` + WS1 (p20b) | `5 passed, 1 warning in 40.74s` |
| credit, WS1, domain, auth, config (p20b) | `13 passed, 1 warning in 56.45s` |
| After the fixes (p20fix) | `test_credit.py`: `1 passed, 1 warning in 16.71s`; WS1: `4 passed, 1 warning in 29.05s` |
| WS1/WS3 matrix printout | `POST .../counterparties/{counterparty_id}/ratings rating:propose 403 403 422 403 403`; `GET /api/v1/obligor-ratings domain:read 200 200 200 200 200`; `GET /api/v1/obligor-ratings/{rating_id} domain:read 200 200 200 200 200`; `POST .../obligor-ratings/{rating_id}/approval rating:approve 403 403 403 422 403` (the WS3 printout includes all three `/obligor-ratings` routes after Q7) |
| Collected (D-26) | `49 tests collected in 0.46s` |
| ruff / format / mypy | `All checks passed!` / `159 files already formatted` / `Success: no issues found in 155 source files` |
| OpenAPI drift | `openapi.json is up to date` (exit 0) |
| `npm run gen:api` | `../backend/openapi.json → src/api/schema.d.ts` (run again after the contract change) |
| Frontend tsc, lint | `tsc exit 0` / `✔ No ESLint warnings or errors` |
| vitest (full) | `Test Files 29 passed (29)`, `Tests 467 passed (467)` |
| vitest (credit) | `review.test.ts (45 tests)`, `ratings.test.ts (19 tests)`, `credit.test.ts (45 tests)`: `109 passed` |
| `npm run build` | `Compiled successfully`; `/domain/ratings 6.93 kB` |
| Playwright `ratings-panel.spec.ts` + `ratings-review.spec.ts` | `4 passed (10.8s)` (`moodymate_test_p20fe`, ports 8020/3020, separate build copy), and again after the fixes `4 passed (11.7s)` (`moodymate_e2e_p20fe2`, ports 8720/3720) |
| Full backend suite | **not yet run** (the coordinator runs it once). It will fail as things stand; see MAJOR below. |
| Full Playwright | CI |

All runs used per-phase databases (`moodymate_scratch_p20`, `moodymate_test_p20`, `_p20b`, `_p20fix`, `_p20fe`, `moodymate_e2e_p20fe2`). `moodymate_dev` was never touched.

## Plan ticks

I checked each Done-when against the code and the test lines myself. Each tick has an evidence line in DELIVERY_PLAN.md.

- **BE-20.1** ticked:
  - `test_credit.py:352-382` covers the seeded join, the grants table and the frozen copies.
  - `:854-877` covers the content UPDATEs, illegal moves, DELETE, TRUNCATE and the UniqueViolation.
  - `:908-978` covers the downgrade refusal and the round trips.
  - The CLI round trip is in the checks table.
  - **The architect checked the matrix:** migration MATRIX, `CREDIT_ROLE_PERMISSIONS` and `PLAN_CREDIT_GRANTS` all match the plan table.
- **BE-20.2** ticked:
  - `test_credit.py:669-761` covers `invalid_grade`, `self_approval`, `invalid_status`, `stale_version`, supersede and one ACTIVE per date.
  - `:831-849` covers the switch-off 409.
  - Every refusal is checked with `assert_denied`.
  - `test_ws1_verification.py:623-624` and `:801-809` list and exercise both POST routes.
- **BE-20.3** ticked:
  - `test_credit.py:807-819` is the 7-date in-effect loop.
  - `:820-829` checks EXP-0008 at G05, 3 notches below G02.
  - `test_ws1_verification.py:304-307` checks both GET routes are `domain:read` with 200 for every role.
- **FE-20.1** ticked:
  - `ratings-panel.spec.ts:98` passed; it asserts IN_REVIEW at :140-150.
  - `ratings.test.ts:98-194` covers the form validation states.
- **FE-20.2** ticked:
  - `ratings-review.spec.ts:123` and `:156` passed: the 403 at :149, ACTIVE at :180, and the panel showing ACTIVE at :197-199.
  - `review.test.ts:51-59` checks the pill against the `openapi.json` `ObligorRatingStatus` enum.

These ticks rest on targeted runs. The MAJOR item below blocks the merge, not the ticks.

## Review outcome

- **MAJOR (open, architect finding; must be fixed before merge):** `backend/app/tests/test_calculations.py:1560` still asserts `head == "0022"`. With 0023 as head, `test_calculations.py` fails in the full suite. No agent ran that file this phase. The Phase 19 report (note 1) asked for this pin to move at the Phase 20 merge.
  - **Fix (test writer):** change the pin to `"0023"`, then run `test_calculations.py`. Its head -> 0017 -> head trip now crosses the 0023 downgrade; `_RATING_CLEANUP` keeps it free of ratings.
- **MEDIUM:** none.
- **LOW findings:**
  1. `0023_credit_permissions.py:118-127`: maker-checker is enforced only in `DomainService.decide_rating`. The trigger allows IN_REVIEW -> ACTIVE with any `decided_by`, so a direct `repo.move_rating` or a script could approve its own proposal. Optional defence in depth: refuse in the trigger when the decider is the proposer, ideally via a `decider_user_id` column. Until then, self-approval is refused by the service only, as for the Phase 16 rule versions.
  2. `0023_credit_permissions.py:133-141`: on the move to SUPERSEDED, the trigger does not check that the superseding sibling is ACTIVE or becoming ACTIVE. A buggy caller could leave a date with no rating in effect. Optional fixes: require `r.status IN ('IN_REVIEW','ACTIVE')` in the EXISTS, or add a deferred constraint trigger. The service order (supersede, then activate) works with either.
  3. `frontend/src/components/credit/ratings/ratings.ts:69` and `review.ts` `commentProblem`: the counters use UTF-16 `String.length`, while the server counts code points. A text with astral characters is wrongly shown as over the limit. This is safe, because the client is stricter. Fix: count `[...text.trim()].length` and add one vitest case with an astral character.
  4. (Q11, Q14) The two Phase 20 e2e specs copy about 45 to 60 lines of credit and projection setup from `counterparties.spec.ts`. Fold this into one shared helper in `frontend/e2e/support.ts` (with one owner) after merge, together with Phase 19 LOW 8 (`validatedCredit(code)`).
  5. (Architect) The review-queue vitest has no masked-value case. Masked grade, rationale and comment are covered there only by the type check (`ratings.test.ts` covers them for the panel).

## Rulings (auto-resolved)

**Recorded in DECISIONS.md** (D-39, dated amendment 2026-10-19, Phase 20 build, auto-resolved):
- **Q3 Stale:** a version is stale when ACTIVE(counterparty, effective_from).decided_at > proposal.proposed_at (strict). Both times come from `clock_timestamp()`, read after the per-counterparty lock. A proposal for another date is never stale.
- **Q9 Rejecting a stale version:** a stale version can be rejected; only approval is refused. Asserted at `test_credit.py:737-740`.
- **Q4 Audit resources:** a refused propose is audited on `resource_type 'counterparty'`. Every other rating event is on `obligor_rating`. Details never carry the rationale; the grade is tagged internal and the comment confidential.

**Report notes (auto-resolved):**
1. **Q1/Q8 Contract change.** `grade`, `rationale` and `decision_comment` are now `str | MaskedValue`, and `grade_rank` is null when the grade is masked (D-43 §2). `openapi.json` was re-exported and both frontends regenerated their types. tsc is clean.
2. **Q2 Order of checks.** Switch, then 404, then `invalid_status`, then `self_approval` on `proposer_user_id`, then `stale_version`. This follows D-32 §3 and its Phase 16 amendment; I confirmed it in `domain/service.py:963-1025`.
3. **Q5 Frozen copies.** `test_credit.py:375-382` now really compares 0023's STATUSES, TRANSITIONS, limits and MATRIX with the code. Applied.
4. **Q6 Cleanup hunk.** The `_RATING_CLEANUP` hunk in conftest.py is accepted. It is fixture cleanup on an isolated test DB, not a hard stop.
5. **Q7 WS3 prefixes.** `"/obligor-ratings"` was added to `WS3_PREFIXES` (`conftest.py:49`). Applied.
6. **Q10 CI specs.** CI picks up `ratings-panel.spec.ts` and `ratings-review.spec.ts` (testDir `./e2e`). Cite the CI run once it is green.
7. **Q12 Nav path.** The `/domain/ratings` tab, gated on `domain:read`, matches the FE-20.2 plan text.
8. **Q13 Older test hunks.** The `credit.test.ts` and `registry.test.ts` hunks only pin the flag off and keep the Phase 19 assertions. I checked both.
9. **Q15 Shared pill.** The coupling to the shared pill is accepted. `RATING_STATUS_STYLE` lives in `ratings/ratings.ts`. Any rename must update `review.test.ts` and `RatingsReviewQueue.tsx` in the same change.
10. **Q16 403 before self_approval.** A direct POST to the approval route by risk_analyst gets 403 `forbidden` from the `require_permission` dependency, before `self_approval` is ever checked. The WS1 matrix shows 403 for risk_analyst (`403 403 403 422 403`). Confirm again in the full suite run.
11. **Test budget.** 49 collected, 49 -> 49. No test function was added.

## Open items

**Blocking merge (coordinator):**
- Fix the MAJOR head pin at `test_calculations.py:1560` (test writer), then run the full backend suite once.
- Confirm the full Playwright run is green in CI and cite it here.

**Not blocking (architect, docs):**
- `docs/ARCHITECTURE.md` has no entry for D-39 or `obligor_rating` (grep count 0). It needs an update.
- `RETENTION.md` rows for `obligor_rating` are scheduled for the Phase 23 task "RETENTION.md rows for the Phase 20–22 tables".

**For the client before production (hard stops; not blocking the build):**
- **Role model:** the D-40 grants are PROVISIONAL (D-11). The final role model, including any credit-officer role or committee approval, needs the client (Arch §10).
- **Withdrawal:** RETIRED is reserved, and how a proposal is withdrawn is still to be decided.
- **Illustrative content:** the grades G01 to G08 and D are neutral codes. They imply no rating-agency or vendor parity, and no regulatory certification is claimed.
- **Retention period** for `obligor_rating`, set by Compliance (D-20 §4).

**Not checked:**
- a build with `NEXT_PUBLIC_RATINGS_UI=off` (covered by vitest only);
- the pages by hand, beyond what the two Playwright specs click through.

Files edited in this step:
- `/home/user/MoodyMate-wt/cr-p20/docs/DELIVERY_PLAN.md`
- `/home/user/MoodyMate-wt/cr-p20/docs/DECISIONS.md`
## Coordinator follow-up

- **Full backend suite.** One test failed on its first run. The `test_calculations.py`
  migration-head pin was still at `"0022"`, and the Phase 19 ruling already said it moves with
  each new head. The pin is now `"0023"`. Re-run: 49 passed.
