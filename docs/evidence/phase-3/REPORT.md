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
| BE-3.4 | the round trip passes and seed classifications exist after upgrade. | Tables in `0004`; tagged PROVISIONAL seed in `0005` (public/internal/confidential/restricted, ranks 0–3; role clearances). `test_seed_classifications_exist_and_are_provisional` and `test_seed_clearances_match_constants` PASSED. Round trip in checks.txt. The first attempt on the DB copy stopped with a foreign-key error from the 0003 seed downgrade, because E2E users still held seeded roles. That is the Phase 2 protection of user role grants, not a handled D-11 refusal; it passed after the E2E users were cleared. |
| BE-3.5 | a test proves the same record returns masked to `risk_analyst` and clear to `data_steward` for a `restricted` field. | `test_same_record_masked_for_risk_analyst_clear_for_data_steward` PASSED. No real endpoint carries a restricted field until Phase 4, so the record is served by a test route with `schema:read` that uses the production `mask_model` and principal clearance. Real use today: audit `details` (`test_audit_details_masked_by_clearance`: email masked for auditor, clear for platform_admin). Logs: `test_log_filter_uses_the_same_classifications`. Setting: `test_masking_cannot_be_disabled_outside_local` and `test_masking_disabled_in_local_shows_everything`. |
| FE-3.1 | the timeline renders events created in BE-3.2 tests on the seeded environment, with the correlation ID visible. | browser-phase3.json: the success (`user.list`) and denied events created in the browser run appear with their correlation IDs (`timeline_shows_success_cid` and `timeline_shows_denied_cid` both true; screenshot 01). Outcome filter: all rows DENIED (03). risk_analyst has no Audit nav item and gets the 403 state. The component takes `resourceType`/`resourceId` for resource-scoped use. |
| FE-3.2 | a masked API value renders as masked text with a tooltip, never as an empty string. | browser-phase3.json: the auditor sees "masked" with the tooltip "Masked by policy: confidential data…", and the raw email is absent from the page (`auditor_sees_raw_email: false`, screenshot 02). platform_admin sees it in clear (04). Unit test `masked.test.ts`: only the server's marker counts as masked; an empty string or null is never treated as masked. |

## Design notes

- **What is audited now:** dev sign-in sessions, self-provisioning, user directory reads (`user.list`, `user.read`), audit reads (`audit.read`), and every 403 from a permission check. Mutating governance actions arrive in Phases 4–5 and must call `audit(...)` before their commit.
- **Not audited:** 401s (no actor); `account_not_provisioned` (no account, so no tenant to file the event under; it is logged); and the fail-closed "permission not declared" guard (a code defect, raised before any principal is resolved). `account_disabled` and `tenant_suspended` 403s ARE audited (`auth.access`, DENIED).
- **Audit rows have no foreign keys** to tenant or user, so the trail never blocks, and is never cascaded by, changes to what it describes.
- **Database-level limits.** The triggers stop the application and ordinary SQL. A table owner can still disable them. Full immutability needs a separate, non-owner application role in deployment; that is noted for the infrastructure follow-up.
- **The E2E masked value** came from one `user.provision` audit row inserted into the DB copy (`correlation_id = e2e-provision-fixture`). Dev-mode sign-in never self-provisions, so no real row exists. The masking itself ran through the real API.

## Verification follow-ups

The independent review verified 6 of 7 tasks, left BE-3.3 open (no `resource_id` filter test), and reported 10 findings. Every fix below had its test written first; all 12 tests failed before the fix (`app/tests/test_audit_hardening.py`). Results are in checks.txt under "Verification follow-ups".

| # | Finding | Resolution |
|---|---|---|
| 1 | Audit details without a classification were served in clear, and append-only rows can't be fixed later. | `audit()` rejects any detail key without a classification and any unknown code. `mask_mapping` masks an unclassified stored key as `unclassified`, which no role is cleared for. Existing callers now classify every key. |
| 2 | A downgrade could drop `audit_event` with its rows. | The 0004 downgrade refuses while `audit_event` has rows unless the operator opts in (`-x allow_audit_loss=true`); the refusal rolls back and leaves the DB at head (checks.txt). The table-owner limit is unchanged: it still needs a non-owner app role at deployment. |
| 3 | Database errors could put classified values in logs. | The engine uses `hide_parameters=True`, and the log formatter withholds DB driver messages (which echo bound values and key values), keeping the statement, SQLSTATE and constraint. |
| 4 | Self-audit shifted offset paging; filter values were unbounded. | Keyset paging via `before=<event id>` (tenant-checked; an unknown or foreign id returns 422). The timeline uses it. Filters are limited to 200 characters. Browser run: page 2 does not repeat page 1. |
| 5 | Not every 403 was audited. | `account_disabled` and `tenant_suspended` are audited. The remaining exceptions are listed under "Not audited". |
| 6 | The denial commit used the request session. | Denials are written in a separate session that commits only the audit row. Denied events still file under `endpoint` with the route template, so a resource-scoped timeline does not show them; the plain timeline does. |
| 7 | `mask_model` gaps. | It now walks lists, tuples and dicts at any depth, masks element-level markers (`list[Annotated[..., Classification]]`), and masks a classified `None`. Masking remains opt-in per handler: every Phase 4+ route returning a classified model must call `mask_model` and carry a masked/clear test. |
| 8 | Email was unclassified in the user directory. | `UserSummary.email` is `confidential` and masked by clearance (the Users page renders `MaskedValue`). `Me.email` is the caller's own address and stays clear. |
| 9 | Frontend rough edges. | Nested objects and arrays in details render masked markers as `MaskedValue`; an empty classification shows as "unclassified". The range label now follows the page actually shown. |
| 10 | Evidence caveats. | The 0003 wording above is corrected. checks.txt now records the committed tree. |

## Re-verification follow-ups

The second review verified BE-3.3 (so all 7 Phase 3 tasks are now verified), confirmed most fixes, and found new issues in them. Each fix below had its test written first, and each test failed before its fix.

| # | Finding | Resolution |
|---|---|---|
| N1 | A denial held the request's connection while taking a second one, so concurrent denials could exhaust the pool (500s, no audit). | The request session is rolled back, releasing its connection, before the denial's audit session opens. Test: a one-connection pool still returns 403. |
| N2 | The downgrade guard counted rows without a lock, and failed in `--sql` mode. | `LOCK TABLE audit_event IN ACCESS EXCLUSIVE MODE`, then a SQL `DO` block that raises if rows exist. It runs online and appears in offline scripts (test). |
| N3 | `mask_model` missed Optional, tuple, set and dict-value markers, and NamedTuples gave a 500. | Masking now follows each field's type annotation, including markers inside `Optional`/`Union`, element and value types, and fixed tuples. NamedTuples are rebuilt correctly. `MaskedValue` is frozen, so it can sit in a set. |
| N4 | With exactly one page of matches, "Older" led to an empty page with no way back. | The timeline asks for one extra row to decide whether "Older" exists. Browser: 20 matches give "1–20 of 20" with Older disabled. |
| N5 | The paging cursor survived a change of resource. | Cursors are keyed to the scope (resource and applied filters) and are dropped when it changes. |
| N6 | Grouped exceptions lost their members in logs. | The formatter walks exception groups, with the same rules for withholding database messages. |
| — | `audit.read` did not record the `before` cursor. | It is recorded now (test). `total` stays the count across all pages by design. |
| 10 | checks.txt did not name the commits. | Its header now states which tree each section ran on. |

Not changed: pydantic `ValidationError` text can include `input_value` if logged. Request validation errors are returned as a 422 envelope and are not logged with inputs today. A Phase 4 service that logs a `ValidationError` must not log it raw.

## Not done / follow-ups

- **Audit retention or archival is not implemented.** Deleting audit rows needs a deliberate, owner-level procedure; see `docs/RETENTION.md` in Phase 6.
