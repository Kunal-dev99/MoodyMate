# Phase 3 — engineer report (BE-3.1…3.5, FE-3.1…3.2)

Built in worktree `../MoodyMate-wt/ws1-phase3` (branch `feat/ws1-phase3`, from `master`
6b047d7) against DB copy `moodymate_ws1_p3`; tests used `moodymate_test` (D-03).

Evidence: `checks.txt` (121 backend tests run without `ENV`; 33 frontend unit tests; lint,
types, build; migration round trips) and `browser-phase3.json` with `browser-phase3.mjs` (the
real stack in dev mode) plus screenshots 01–04.

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| BE-3.1 | a test attempting `UPDATE` and `DELETE` on `audit_event` gets a DB error; the round trip passes. | Migration `0004` creates `audit_event` with triggers that reject UPDATE/DELETE (per row) and TRUNCATE (per statement), raising `insufficient_privilege` "append-only (D-10)". `test_audit_rows_cannot_be_updated_or_deleted[UPDATE…]` and `[DELETE…]` and `test_audit_table_cannot_be_truncated` PASSED; the row is unchanged afterwards. Round trip in checks.txt. |
| BE-3.2 | tests show a successful action and a denied action each produce exactly one event with the correct correlation ID. | `test_successful_action_produces_exactly_one_event` (`user.list`, SUCCESS) and `test_denied_action_produces_exactly_one_event` (`GET /api/v1/users`, DENIED, `required_permission`), each matched by its correlation ID, PASSED. `test_audit_joins_the_callers_transaction`: an event rolled back with its transaction leaves no row. `test_dev_session_creation_is_audited`. Browser: both correlation IDs appear in the timeline. |
| BE-3.3 | tests cover filters, pagination, 403 for `risk_analyst`, and the self-audit event. | `test_filters_and_pagination` (action, outcome, resource_type, actor, from/to, limit/offset with disjoint pages), `test_audit_read_requires_audit_read` (403 plus the required permission), `test_reading_audit_is_itself_audited` (one `audit.read` event with the filters, visible to the next read), `test_audit_reads_are_tenant_scoped`. All PASSED. |
| BE-3.4 | the round trip passes and seed classifications exist after upgrade. | Tables in `0004`; tagged PROVISIONAL seed in `0005` (public/internal/confidential/restricted, ranks 0–3; role clearances). `test_seed_classifications_exist_and_are_provisional` and `test_seed_clearances_match_constants` PASSED. Round trip in checks.txt, including a D-11 refusal while E2E users held seeded roles. |
| BE-3.5 | a test proves the same record returns masked to `risk_analyst` and clear to `data_steward` for a `restricted` field. | `test_same_record_masked_for_risk_analyst_clear_for_data_steward` PASSED. No real endpoint carries a restricted field until Phase 4, so the record is served by a test route with `schema:read` that uses the production `mask_model` and principal clearance. Real use today: audit `details` (`test_audit_details_masked_by_clearance`: email masked for auditor, clear for platform_admin). Logs: `test_log_filter_uses_the_same_classifications`. Setting: `test_masking_cannot_be_disabled_outside_local` and `test_masking_disabled_in_local_shows_everything`. |
| FE-3.1 | the timeline renders events created in BE-3.2 tests on the seeded environment, with the correlation ID visible. | browser-phase3.json: the success (`user.list`) and denied events created in the browser run appear with their correlation IDs (`timeline_shows_success_cid` and `timeline_shows_denied_cid` both true; screenshot 01). Outcome filter: all rows DENIED (03). risk_analyst has no Audit nav item and gets the 403 state. The component takes `resourceType`/`resourceId` for resource-scoped use. |
| FE-3.2 | a masked API value renders as masked text with a tooltip, never as an empty string. | browser-phase3.json: the auditor sees "masked" with the tooltip "Masked by policy: confidential data…", and the raw email is absent from the page (`auditor_sees_raw_email: false`, screenshot 02). platform_admin sees it in clear (04). Unit test `masked.test.ts`: only the server's marker counts as masked; an empty string or null is never treated as masked. |

## Design notes

- **What is audited now:** dev sign-in sessions, self-provisioning, user directory reads (`user.list`, `user.read`), audit reads (`audit.read`), and every 403 from a permission check. Mutating governance actions arrive in Phases 4–5 and must call `audit(...)` before their commit.
- **Not audited:** 401s (no actor), and the fail-closed "permission not declared" guard (a code defect, with no principal resolved).
- **Audit rows have no foreign keys** to tenant or user, so the trail never blocks, and is never cascaded by, changes to what it describes.
- **Database-level limits.** The triggers stop the application and ordinary SQL. A table owner can still disable them. Full immutability needs a separate, non-owner application role in deployment; that is noted for the infrastructure follow-up.
- **The E2E masked value** came from one `user.provision` audit row inserted into the DB copy (`correlation_id = e2e-provision-fixture`). Dev-mode sign-in never self-provisions, so no real row exists. The masking itself ran through the real API.

## Not done / follow-ups

- **Paging in the browser** was exercised only up to one page. Paging logic is covered by API tests.
- **Audit retention or archival is not implemented.** Deleting audit rows needs a deliberate, owner-level procedure; see `docs/RETENTION.md` in Phase 6.
