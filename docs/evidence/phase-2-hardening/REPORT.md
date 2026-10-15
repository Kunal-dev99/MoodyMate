# Phase 2 hardening — engineer report (BE-2.7…2.12, FE-2.3…2.4, D-13)

Follow-up to the independent security review of PR #3. Findings 1–8 were approved by the
user and are recorded as D-13. Built in worktree `../MoodyMate-wt/ws1-phase2h` (branch
`feat/ws1-phase2-hardening`, from `master` 0cb3348) against DB copy `moodymate_ws1_p2h`.

Evidence in this folder:
- `checks.txt`: 87 backend tests **run without `ENV` set**, plus 19 frontend unit tests, lint, types, build and contract checks.
- `startup-refusals.txt`: real `uvicorn` processes refusing each unsafe configuration.
- `browser-redirects.json.txt`: the pre-fix exploit reproduced, then the post-fix browser run.
- `browser-oidc-mode.json`: the real OIDC flow against the mock IdP through the new JWKS loader. It ran 5/5 times with a fresh user each time.
- `kid-flood.txt`: 200 forged tokens with random `kid`s.

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| BE-2.7 | tests prove startup is refused with `ENV` unset, and with `AUTH_MODE=dev` in `staging` and in `production`. | `test_hardening.py::test_startup_refused_without_env`, `::test_dev_auth_refused_outside_local_and_test[staging]` and `[production]` PASSED. Real processes: startup-refusals.txt. The whole suite now runs with `ENV` unset (`env -u ENV`), which proves nothing depends on a default. |
| BE-2.8 | tests prove an `http://` issuer is refused in staging, and an `http://` `jwks_uri` from discovery is rejected when insecure transport is not allowed. | `::test_http_issuer_refused_in_staging`, `::test_http_jwks_uri_from_discovery_rejected_when_insecure_not_allowed` (the JWKS URL is never fetched), and `::test_http_jwks_url_refused_in_production` PASSED. Also startup-refusals.txt. |
| BE-2.9 | a test proves the refusal. | `::test_audience_equal_to_client_id_refused` PASSED; startup-refusals.txt. |
| BE-2.10 | a test with N tokens carrying random `kid`s shows ≤ 1 key fetch per interval; a test with the provider unreachable shows 503 with the envelope. | `::test_unknown_kids_trigger_at_most_one_fetch_per_interval`: 50 forged kids cause 0 extra fetches, and 1 refresh happens after the interval. `::test_provider_unavailable_is_503_envelope`: 503 `auth_provider_unavailable`, `retryable: true`, correlation ID. Also `::test_provider_outage_backs_off` (20 attempts, 1 fetch) and `::test_key_rotation_is_picked_up_after_the_interval`. Live: kid-flood.txt (200 forged tokens give 200 × 401 in 0.52 s). The real JWKS path was exercised by browser-oidc-mode.json. |
| BE-2.11 | a test proves an active user in a SUSPENDED tenant gets 403. | `::test_user_of_suspended_tenant_is_denied` PASSED (403 `tenant_suspended`). |
| BE-2.12 | a test adds a mount and a websocket route under `/api/v1` and the registry lists both. | `::test_registry_reports_mounts_and_websockets` PASSED. |
| FE-2.3 | unit tests reject `//x`, `/\\x`, `/%5Cx`, absolute and `javascript:` URLs; a browser run with `next=/%5Cevil.example` lands on `/dashboard`. | `src/lib/__tests__/redirect.test.ts`: 14 cases (3 kept, 11 rejected incl. all listed) pass. Browser: every payload incl. `next=/%5Cevil.example`, literal `/\evil.example` and the tab variant lands on `/dashboard` with no external requests. The pre-fix check was reproduced first: `/\evil.example` resolved to `http://evil.example/`. The OIDC callback uses the same check. |
| FE-2.4 | a unit test proves a late 401 for an old token leaves a newer token in place, and a 401 for the current token clears it. | `src/api/__tests__/auth-middleware.test.ts`: "keeps a newer token when a late 401 answers an old one" and "clears the session when the 401 answers the current token" pass, plus 3 more cases. |

## Found while verifying (fixed in this change)

1. **Concurrent first sign-in returned 500.** The first OIDC browser run failed, and the backend log showed `UniqueViolation` on `user_account`. A new user's first page load sends `/me` and `/me/permissions` together, and both tried to self-provision. This existed in Phase 2 too; earlier runs got lucky.
   - Fixed: provisioning is now `INSERT … ON CONFLICT DO NOTHING` followed by a read.
   - `test_concurrent_first_sign_in_provisions_once` (8 concurrent resolves) **fails on the pre-fix code and passes with the fix.**
   - The browser flow then passed 5/5 with a fresh user each run, with zero 500s.
2. **Startup validation errors printed secrets.** Pydantic echoed the input settings, which partially showed `AUTH_DEV_SECRET`.
   - Fixed with `hide_input_in_errors=True`.
   - Covered by `test_settings_errors_do_not_echo_secrets`, plus the real-process check in startup-refusals.txt.

## Other changes

- **Factory startup.** The backend runs as `uvicorn app.main:create_app --factory`, and there is no module-level `app`, so importing never builds an app from implicit settings. The start scripts are updated to match.
- **Frontend unit tests.** Added with Vitest; CI runs `npm test`.
- **Two Phase 2 tests replaced, not weakened.** They encoded D-09's staging allowance, which D-13 removes; the new tests assert the stricter rule.

## Recorded, not fixed (design follow-ups from the review)

- **Token storage and revocation.** Tokens live in sessionStorage, where an injected script could read them, and there is no server-side revocation. Dev tokens last 8 hours.
- **SSO sign-out is local only:** it does not call the provider's end-session endpoint.
- **Roles are global** rather than per tenant.
- **Role enum vs database.** A role added in the database without a code change makes `/me` and `/users` fail response validation.
- **FE-2.2's Propose-button check** is to be re-verified when FE-5.1 adds that button.
