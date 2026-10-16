# Phase 7 — independent verification (BE-7.1…7.5, FE-7.1…7.2)

Reviewed 2026-10-16 by the solution architect in worktree `MoodyMate-wt/ws2-phase7`
(branch `feat/ws2-phase7`, base `d692b47`, checks at `8bdb0cd`). The review read the code
and tests directly and ran no commands. Pass/fail comes from `checks.txt`:

- ruff and mypy clean
- 352 passed plus the 8-test ws1 matrix
- openapi and sample `--check` pass
- migration round trip 0011→0008→0011 passes
- frontend: typecheck, lint, 66 vitest and build pass
- Playwright 11/11

All seven Done-whens are met, with no BLOCKER. Three MAJOR follow-ups (D1–D3) are to be
fixed or decided before Phase 8.

## Verdict per task

| Task | Verdict | Evidence |
|---|---|---|
| BE-7.1 | Met. Follow-ups D1, D5, D6, D11 | The `0009` guard trigger rejects row UPDATE/DELETE and has a TRUNCATE statement trigger; there is no session-settable bypass. CHECKs cover kind, format, encoding, delimiter and status. `test_sources.py` tests: `test_approved_contract_rejects_update_and_delete`, `test_approved_contract_rejects_truncate`, `test_enum_columns_are_checked`, `test_downgrade_refuses_while_a_user_source_exists`, `test_source_migrations_round_trip_without_user_sources`. |
| BE-7.2 | Met | The 0010 MATRIX, `WS2_ROLE_PERMISSIONS` and the plan table agree. Tests: `test_seeded_matrix_matches_plan`, `test_me.py::test_ws2_permissions_per_role` (written from the plan), `test_ws2_permission_downgrade_removes_only_its_rows`. |
| BE-7.3 | Met. Follow-up D4 | The switch is checked after the permission check, and refusals are audited in a separate session. `test_switch_off_refuses_writes_with_one_audit_event_and_reads_work`. |
| BE-7.4 | Met. Follow-ups D2, D3, D8, D9, D12 | `test_create_list_and_get`, `test_cross_tenant_get_is_the_same_404_as_a_random_id`, `test_post_without_source_manage_is_403_and_audited`, `test_new_contract_version_supersedes_the_previous_unchanged`. Both routes are in the ws1 matrix and the AUDITED set. |
| BE-7.5 | Met. Deviations 1–3 need a decision before Phase 8; follow-up D7 | `test_two_runs_give_identical_files`, `test_every_file_carries_the_marker`, `test_bad_variants_carry_their_defect`, `test_committed_samples_are_current`, `test_seeded_source_is_listed_as_provisional`. |
| FE-7.1 | Met. Evidence gap D13 | `e2e/ingestion.spec.ts` (steward vs auditor, the 403 state, a backend 403); `sources.test.ts`; `gate.test.ts` proves reversal (D-02). |
| FE-7.2 | Met. Follow-up D10; deviations 7–8 | `e2e/ingestion.spec.ts`: register, see it listed, add v2, v1 Superseded and v2 Provisional. The auditor sees 3 events with correlation IDs. Requests are cancelled with AbortController. |

## What held up

- **Fail closed and switch ordering.** Every route declares its permission, and the
  `INGESTION_ENABLED` check runs after it. Refusals are audited after a rollback.
- **Tenant scoping and the same 404.** All source queries go through `ScopedRepository`.
  The target schema is fetched through `SchemaService.get_schema`, and the DB trigger
  `source_contract_same_tenant` is a backstop.
- **Canonical hash.** Columns and keys are sorted and the JSON is compact. The copy frozen in
  0011 is kept equal by `test_seed_hash_matches_the_service`.
- **Concurrent new versions.** `SELECT … FOR UPDATE OF source_system` serialises them, with
  `UNIQUE(profile, version)` and `uq_source_contract_current` as DB backstops. Untested so
  far (D9).
- **Downgrades.**
  - 0009 refuses while sources exist.
  - 0010 and 0011 remove only tagged rows.
  - A downgrade runs in one transaction.
- **D-01, D-02 and D-14** are respected. `X-Data-Class: synthetic` is asserted on
  `POST /sources`.

## Defects

| ID | Severity | Where | Failure | Fix (test first) |
|---|---|---|---|---|
| D1 | MAJOR, needs a user decision | `0009` guard | PROVISIONAL contract content (columns, schema, hash, supersedes) can be rewritten by UPDATE. MVP contracts stay PROVISIONAL, so an import pinned in BE-8.1 could have its contract changed under it. | Migration 0009 is amended in place (never released, D-24) to freeze content in every status (only `status` and `updated_at` change). `test_provisional_contract_content_is_frozen` |
| D2 | MAJOR | `schemas.py` ProfileIn | `profile.provisional: false` is stored as sent, so one steward can mark a source client-approved. | Remove the field from create; it is always true. `test_registrant_cannot_mark_a_profile_approved` |
| D3 | MAJOR, plan wording | `service.py` | These business-rule refusals are not audited: `unsupported_source_kind`, `unknown_classification`, `no_active_version`, `duplicate_code`, `no_change`, `invalid_status`. | Audit each one, or narrow the constraint. `test_business_refusals_are_audited` |
| D4 | MINOR | `router.py` | The switch is an opt-in dependency, so a future WS2 write route could forget it. | Structural test `test_every_ws2_write_route_honours_ingestion_enabled` |
| D5 | MINOR | `0009` | `source_system.tenant_id` and `source_profile.source_system_id` can be UPDATEd. | Trigger rejecting those changes. `test_sources_cannot_move_tenant` |
| D6 | MINOR | `0009` vs `constants.py` | On APPROVED contracts, `created_by`, `created_at` and `id` can be rewritten, and the DB allows moves that `CONTRACT_TRANSITIONS` forbids. | Freeze those columns and enforce the transitions in the trigger (with D1). |
| D7 | MINOR | `0011` | Seed IDs come from `gen_random_uuid()`, so they change after a downgrade and upgrade. | `uuid5` IDs. `test_seed_ids_are_stable` |
| D8 | MINOR | `schemas.py` | Control characters (NUL gives a 500), whitespace-only text, and Excel-forbidden sheet-name characters are accepted. | Shared `SafeText` type plus a sheet-name pattern. `test_create_refuses_unsafe_text` |
| D9 | MINOR | repository/service | The race handling has no test. | `test_concurrent_new_versions_serialise` |
| D10 | MINOR (FE) | detail page, `ContractEditor` | A network failure leaves a loading block on screen forever. | Error state with "Try again"; a vitest case rejecting with a `TypeError`. |
| D11 | MINOR (tests) | `test_sources.py` | The downgrade-refusal and seed-downgrade tests cannot tell "nothing removed" from "removed and re-seeded". | Compare row IDs; add a separate user source that must survive. |
| D12 | MINOR | `service.py` | A profile-less source is filtered out after LIMIT/COUNT, so `total` can disagree with the items. | `EXISTS` filter in the query. |
| D13 | MINOR (evidence) | `checks.txt` | The Playwright capture does not list the FE-7.1 tests by name. | Re-capture the full list. |

**Carry-over, not a Phase 7 defect.** The app and the migrations use the same DB role, which
can disable triggers. A separate least-privilege runtime role belongs in pre-production
hardening.

**Design note.** `uq_source_contract_current` means a new PROVISIONAL version immediately
supersedes an APPROVED one. A future contract-approval flow will need to revisit this.

## Engineer-reported deviations: recommendations (the user decides)

1. **JSON envelope vs D-16.** The plan contradicts itself (BE-7.5 asks for a top-level key,
   D-16 for a top-level array).
   - *Recommended:* a new D-24 accepting either an array or exactly
     `{"marker": "ILLUSTRATIVE / SYNTHETIC…", "records": [...]}`.
2. **XLSX written with stdlib zip, not openpyxl.**
   - *Recommended:* accept. Add to the BE-8.3 Done-when that openpyxl reads every committed
     `position*.xlsx`.
3. **XLSX sheet name and the CSV `#` marker.** The sheet name is fine.
   - *Recommended:* in D-24, skip only line 1 of a CSV, and only when it starts with exactly
     `# ILLUSTRATIVE / SYNTHETIC`. Record a flag on the import. Never use a generic rule that
     skips `#` lines.
4. **One profile per source.**
   - *Recommended:* add a unique index now (additive).
5. **PROVISIONAL contracts editable.** See D1.
6. **Non-FILE_DROP kinds refused with 422.**
   - *Recommended:* accept (matches D-23).
7. **data_steward lacks `audit:read`.**
   - *Recommended:* accept as built (segregation of duties); revisit if stewards need it.
8. **Classification codes hard-coded in the frontend.**
   - *Recommended:* accept for now. Add `GET /classifications` before Phase 11.

## Deferred

- Contract review and approval actions, and clearing a profile's provisional flag.
- Editing or archiving sources and profiles.
- A least-privilege runtime DB role.

## Fixes after the review (2026-10-16)

The user decided the open points as D-24. Each fix was written test first; the full re-run
is in `checks-after-fixes.txt` (at `dbeb58f`).

| ID | Status | Proof |
|---|---|---|
| D1, D6 | Fixed: contract content frozen from creation in every status; `id`/`created_by`/`created_at` frozen; status moves only along `CONTRACT_TRANSITIONS` in the trigger (0009 amended in place, D-24) | `test_source_contract_guards.py` |
| D2 | Fixed: `provisional` removed from the create body; a sent value is ignored and the profile is always provisional | `test_registrant_cannot_mark_a_profile_approved`, `test_create_body_has_no_provisional_field` |
| D3 | Fixed: every refused WS2 write is audited with its reason code (D-24 §4); malformed bodies are not | `test_business_refusals_are_audited` (9 cases), `test_invalid_status_refusal_is_audited`, `test_malformed_bodies_are_not_audited` |
| D4 | Fixed | `test_every_ws2_write_route_honours_ingestion_enabled` |
| D5 | Fixed: `source_system_no_move` and `source_profile_no_move` triggers | `test_sources_cannot_move_tenant` |
| D7 | Fixed: uuid5 seed IDs | `test_seed_ids_are_stable`; the IDs are identical across the round trip |
| D8 | Fixed: `SafeText`, `SafeMultilineText`, `SheetName` (`app/core/text.py`) | `test_create_refuses_unsafe_text` (27 cases), `test_contract_route_refuses_unsafe_text` |
| D9 | Fixed (tested) | `test_concurrent_new_versions_serialise` (4 real concurrent apps) |
| D10 | Fixed: shared `startLoad` plus `LoadFailed` "Try again" state | `components/common/__tests__/load.test.ts` (6 tests) |
| D11 | Fixed | strengthened `test_downgrade_refuses_while_a_user_source_exists` and `test_seed_downgrade_removes_only_its_rows` |
| D12 | Fixed: `EXISTS` filter inside the paged query | `test_page_total_matches_items_with_a_profile_less_source` |
| D13 | Fixed: full Playwright list re-captured | `checks-after-fixes.txt` (11/11, FE-7.1 tests listed by name) |
| Deviation 4 | Fixed: unique `ix_source_profile_source_system_id` | `test_a_source_has_at_most_one_profile` |

**Follow-up for a later phase.** `/schemas` and `/schemas/proposals/new` have the same
endless-loading defect as D10 (Feature 01 pages). They should move to `startLoad`/`LoadFailed`.
**Note.** Any DB copy that ran the original 0009–0011 must be downgraded to 0008 and upgraded
again.
