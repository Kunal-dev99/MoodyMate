# Phase 5 — engineer report (BE-5.1…5.4, FE-5.1…5.2)

Built in worktree `../MoodyMate-wt/ws1-phase5` (branch `feat/ws1-phase5`, from `master` ac5effd) against DB copy `moodymate_ws1_p5`; tests on `moodymate_test` (D-03).

Evidence:
- `checks.txt`: 209 backend tests; 46 frontend tests; lint, types and build; migrations.
- `browser-phase5.json` and `browser-phase5.mjs`: the real stack with two seeded users.
- Screenshots 01–06.

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| BE-5.1 | the round trip passes. | Migration `0008` creates `schema_change_proposal` (tenant-scoped):<br>• `status` CHECK per D-06<br>• base and proposed version FKs<br>• the impact result (JSONB)<br>• the regression-evidence reference<br>• the decision: who, user id, when, comment<br>• `reverts_proposal_id` (the rollback reference)<br>The 0007 ⇄ head round trip runs on a fresh DB copy, and `test_migrations` runs the full round trip in the suite. The downgrade refuses while proposals exist (checks.txt). |
| BE-5.2 | a test proposal that removes a field reports BREAKING and lists the seeded dependent. | `test_removal_reports_breaking_and_lists_seeded_dependents`:<br>• removing `counterparty_id` reports `REMOVED`/BREAKING, overall BREAKING;<br>• the dependents include the seeded direct ones (SCHEMA position, MAPPING core_banking_instrument);<br>• the walk reaches transitive ones (MODULE liquidity_gap at depth 2 via `instrument → position`, REPORT maturity_ladder).<br>Also tested:<br>• `test_create_proposal_creates_a_draft_version`;<br>• `test_create_validates_the_contract`: duplicate names, an unknown classification, no change, and scale > precision;<br>• `test_proposing_needs_schema_propose`. |
| BE-5.3 | tests cover the proposer self-approving (403), approval without impact analysis (409), and a valid approval (new version active, old one deprecated, old contract unchanged, audit event present). | `test_proposer_cannot_approve_own_proposal` uses a user holding both data_steward and approver and gets 403 `self_approval`. `test_approval_without_impact_analysis_is_409` and `test_submit_without_impact_analysis_is_409` both return 409 `impact_analysis_required`. `test_valid_approval_activates_new_version`:<br>• v3 ACTIVE with an effective date, v1 DEPRECATED;<br>• v1's hash and field count are unchanged;<br>• `schema_change.approve`, `schema_version.activate` and `schema_version.deprecate` are audited under the request's correlation ID;<br>• the catalogue shows v3 in effect.<br>Also tested: rejection (a reason is required; the DRAFT version becomes REJECTED), a stale base (409 and SUPERSEDED), a tampered contract hash (409 `contract_changed`, the check deferred from Phase 4), platform_admin cannot approve (segregation of duties), and the `SCHEMA_CHANGE_ENABLED=false` read-only switch. |
| BE-5.4 | after a revert is approved, the active version's contract hash equals the original hash, and the version count increased. | `test_revert_reinstates_the_prior_contract_as_a_new_version`:<br>• the revert of the applied v1→v3 change is a new proposal and a new v4 (ADDITIVE, since counterparty_id returns as optional);<br>• after approval the one ACTIVE version's hash equals v1's, and the count rose by one.<br>`test_only_an_applied_proposal_can_be_reverted` returns 409. The browser run confirms it: `active_hash_equals_original: true`; versions v1 DEPRECATED, v2 DRAFT, v3 DEPRECATED, v4 ACTIVE. |
| FE-5.1 | manual test notes show the full flow with two seeded users; the Submit button stays disabled before analysis, and the backend also returns 409. | The manual test notes are below; browser-phase5.json records the same run.<br>• `submit_disabled_before_analysis: true`<br>• `backend_submit_before_analysis` returns status 409 with `impact_analysis_required`<br>• `submit_enabled_after_analysis: true`<br>• the proposer sees "You cannot approve your own proposal"<br>• the approver queue shows the proposal, and approval reaches APPLIED<br>Screenshots 01, 02, 04 and 05. Unit tests in `proposals.test.ts` cover `canSubmit` and `canDecide` (maker-checker). |
| FE-5.2 | the BREAKING fixture renders a red badge with the dependents listed. | browser-phase5.json: `impact_overall: BREAKING`, and the badge has `bg-destructive` (red). `impact_dependents` lists all 6 dependents, direct or via their path. Screenshot 03. |

## Manual test notes (FE-5.1, two seeded users, dev mode, DB copy `moodymate_ws1_p5`)

1. Sign in as **Data steward**, then open Source & Schemas → Instrument → **Propose change**. The editor opens with the 9 fields of v1 (in effect).
2. Enter the title "Drop counterparty_id from instrument" and a rationale. Remove `counterparty_id` (8 rows left), then **Create proposal**. You land on the proposal: status Draft, v1 Active → v3 Draft.
3. **Submit for approval** is disabled, with the note "Submit unlocks once the impact analysis is recorded". A direct `POST …/submit` returns **409** `impact_analysis_required`.
4. **Run impact analysis**. The compatibility badge is red **Breaking**, with 1 change (`counterparty_id` removed) and 6 affected dependents: 3 direct, plus liquidity_gap and maturity_ladder via position and cashflow_event.
5. Enter the regression evidence and **Submit for approval**. The status becomes "Awaiting approval", and the proposer sees "You cannot approve your own proposal."
6. Sign in as **Approver** in a second session. Proposals opens filtered to "Awaiting approval" and shows the proposal (v1 → v3, Breaking).
7. Open it, add a comment, then **Approve and activate v3**. The status becomes Applied; v3 is ACTIVE and v1 is DEPRECATED.
8. As Data steward, on the applied proposal, click **Propose revert**. A new draft opens; run the analysis (Additive) and submit.
9. As Approver, approve the revert. Versions are now v1 DEPRECATED, v2 DRAFT, v3 DEPRECATED, v4 ACTIVE, and v4's hash equals v1's (screenshot 06).

## Design notes

- **Proposal content:**
  - A proposal is the full proposed contract, stored as a new DRAFT `schema_version`. So the DB immutability triggers from Phase 4 protect it the moment it becomes ACTIVE, and history is never edited.
  - The next version number is taken under a lock on the schema row.
- **Workflow (D-06 transitions in `PROPOSAL_TRANSITIONS`):**
  - DRAFT → SUBMITTED → APPLIED, REJECTED or SUPERSEDED.
  - Submitting needs a recorded impact analysis, and only the proposer can submit.
  - Approval applies the change in the same transaction, so APPROVED and IN_REVIEW stay reserved.
- **Maker-checker:**
  - The approver needs `schema:approve` and must not be the proposer (403 `self_approval`).
  - platform_admin has no `schema:approve`.
  - The UI hides the decision panel from the proposer, but the server decides.
- **Activation checks, all in one transaction with the audit rows:**
  - the base version must still be the one in effect; otherwise 409, and the proposal is SUPERSEDED;
  - the proposed contract's hash must equal both the stored hash and the hash at analysis time, closing Phase 4 review item 4;
  - then the old version becomes DEPRECATED and the new one ACTIVE, with today as its effective date.
- **Impact walk:** a breadth-first walk over `schema_dependency` within the tenant. It follows SCHEMA dependents to their own dependents, never visits a schema twice, and records the depth and path. Dependents are still the seeded illustrations.
- **Audit:** create/revert, impact_analysis, submit, approve/reject/supersede, and version activate/deprecate. Every detail key is classified.
- **Setting `SCHEMA_CHANGE_ENABLED`** (default true): off makes every proposal write return 403 `schema_change_disabled`, leaving the Phase 4 read-only registry. Proposals can still be read.
- **Additions alongside the plan's endpoints (D-05):**
  - `POST /schema-change-proposals/{id}/submit`: FE-5.1 needs a submit step, and its 409 is what the Done-when asks for.
  - `GET /schema-change-proposals`: the approver's queue.

## Verification follow-ups

The independent review verified all 6 tasks and found no maker-checker bypass, deadlock, version-number race or tenant leak. It reported 10 lower-severity defects. The fixes below are in `app/tests/test_schema_change_hardening.py`, each test written first and failing before its fix. The results are in checks.txt, in the "Verification follow-ups" section.

| # | Finding | Resolution |
|---|---|---|
| 1 | A DECIMAL with no size passed as COMPATIBLE. | A DECIMAL needs both precision and scale (422). In the diff, a DECIMAL losing a bound is BREAKING; a missing bound on other types is still a widening. This replaces Phase 4's "a removed bound is a widening" for decimals, in line with the fixed-precision rule. |
| 2 | Refused maker-checker attempts were not audited. | `self_approval`, `not_proposer` and `schema_change_disabled` are audited as DENIED, in their own session after rolling back the request, as `require_permission` does. The switch is tested on all 4 remaining write routes. |
| 3 | A stale approval saved SUPERSEDED and then returned 409; other proposals were left open. | Applying a change supersedes every other DRAFT/SUBMITTED proposal on the schema in the same transaction. Their DRAFT versions become REJECTED. A later approve or analyse is a plain 409 that writes nothing. The stale-base guard is kept as a defence and writes nothing either. |
| 4 | Proposals could be edited or deleted in the database. | A trigger makes decided proposals read-only and rejects any DELETE or TRUNCATE (tests). The downgrade refusal is tested. |
| 5 | A revert raced a later change; an applied change could be reverted twice. | Every workflow action locks in the same order: schema, then proposal. Revert re-checks "still in effect" under that lock and allows one open or applied revert per change (409 `already_reverted`). |
| 6 | Dependents were counted once per path. | Each dependent is listed once, with its shortest route and every path (`paths`). The UI shows all paths. |
| 7 | The editor could mix schemas or lose edits. | A slower load for a previous schema is ignored. Switching schema asks before discarding edits. A failed load shows an error. |
| 8 | Bad numeric input became null. | Non-digit input stays invalid, and the editor reports it along with the 1–38 ranges and missing DECIMAL bounds (unit tests). |
| 9 | Omitting `extension_eligible` silently set false. | `extension_eligible` is now required (422 when omitted). |
| 10 | Weaker controls around proposals. | **Open, needs a decision:** should a BREAKING proposal require regression evidence? Also open: a withdraw action for proposers, and de-duplicating repeated proposals. The UI already guards against double submission. |

## Not done / follow-ups

- Proposals have no expiry or withdraw action. A proposal left in DRAFT stays there until a newer change supersedes it at approval time.
- Dependents are seeded illustrations until WS2–WS4 register real ones.
- The DB-owner trigger caveat from Phases 3 and 4 still applies.
