# Rotation follow-up — engineer report (BE-2.15, BE-2.16, FE-2.5)

These fix the verifier's last findings on PR #5. The user approved the work. Built on branch
`feat/ws1-phase2-hardening-3` from `master` 5bae7e3. The tests were written first and **failed
on the merged code** (checks.txt, "Before the fix").

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| BE-2.15 | a test with a rotated key proves the request made during the in-flight refresh gets `ProviderUnavailable` (not `InvalidToken`) and the next one succeeds, failing on the pre-fix code; a test proves an unknown kid after a completed refresh is still 401. | `test_rotated_key_during_inflight_refresh_is_retryable_not_401` (failed before, passes now) and `test_unknown_kid_after_a_completed_refresh_is_401` (control; passes on both). Also `test_unknown_kid_within_refresh_interval_is_retryable`. |
| BE-2.16 | tests prove the registry reports an unguarded catch-all route, and that a request to `/api/v1/anything` served by it is denied at runtime; both fail on the pre-fix code. | `test_registry_reports_unguarded_catch_all_route` and `test_catch_all_route_cannot_serve_api_paths_unguarded` (403 `permission_not_declared`; a non-API path is still served). Both failed before and pass now. The runtime guard now decides on the request path. |
| FE-2.5 | a browser run where `/api/v1/me` first returns a retryable 503 shows the Try again action, the token is still stored, and one click loads the app. | browser-retry.json: "Temporarily unavailable … Try again"; `session_kept: true`; after one click, `/access` loads with the auditor's permissions. Screenshots 01–02. The 503 was injected in the browser with the backend's exact envelope; the backend's real 503 path is covered by `test_provider_unavailable_is_503_envelope`. |

## Deliberate behaviour change

An unknown `kid` that cannot be checked yet now gets a **retryable 503**, not 401. This covers two cases: a refresh is in flight while keys are cached, or the refresh interval is running.

- Forged tokens therefore see 503 during the interval. They still never cause a fetch and never authenticate.
- 401 is returned only when a refresh has actually run and the kid is still unknown.
- Two existing assertions in `test_hardening.py` were updated to the new status; their fetch-count assertions are unchanged.

Results: 103 backend tests (run without `ENV`), 26 frontend unit tests; lint, types and build clean; OpenAPI unchanged.
