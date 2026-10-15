# Phase 2 hardening follow-up — engineer report (FE-2.3 re-fix, BE-2.13, BE-2.14)

These fix findings from the independent verification of the hardening work in PR #4. That
verification left FE-2.3 open and found defects in BE-2.10 and BE-2.12. All of them fall
within the security findings the user approved fixing (D-13 scope); no new decision is
needed. Built in worktree `../MoodyMate-wt/ws1-phase2h` (branch
`feat/ws1-phase2-hardening-2`, from `master` 5501996).

Evidence in this folder:
- `checks.txt`: 94 backend tests run without `ENV`, plus 26 frontend unit tests, lint, types and build.
- `prefix-bypass.txt`: the merged code reproduced as exploitable.
- `browser-redirects.json`: 11 payloads in a real browser.
- `browser-oidc-mode.json`: the real OIDC flow against the mock IdP; 3/3 fresh-user runs, 0 backend 500s.
- `kid-flood.txt`: 200 forged kids, all 401, in 0.59 s.

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| FE-2.3 (re-fix) | unit tests reject `//x`, `/\\x`, `/%5Cx`, absolute and `javascript:` URLs; a browser run with `next=/%5Cevil.example` lands on `/dashboard`. | The check now runs on the **normalised** result, which is the value returned. 6 new dot-segment cases plus an "output never resolves off-origin" property test **failed on the merged code (8 failures) and pass now** (26/26). prefix-bypass.txt shows the merged code returning `//evil.example` for `/.//evil.example`, `/..//…`, `/%2e//…` and `/a/..//…`. browser-redirects.json: all 11 payloads land on `/dashboard` with 0 external requests; the legitimate `/access` is kept. A bare `%` in a legitimate path is no longer rejected (verifier's minor note). |
| BE-2.13 | a test proves a cached-key lookup completes while a refresh is blocked; a test proves an unsafe discovered `jwks_uri` is not cached and the loader recovers when the provider fixes it. Both fail on the pre-fix code. | `test_cached_keys_served_while_a_refresh_is_in_flight` and `test_unsafe_discovered_jwks_uri_is_not_remembered`: **both FAILED against master's `tokens.py` and PASS now** (run shown in this session's log). Also `test_concurrent_first_requests_share_one_fetch` (6 concurrent cold requests make 1 fetch). Real OIDC still works (browser-oidc-mode.json). |
| BE-2.14 | a test proves mounts at `/api` and `/` are reported, and an unrelated mount (`/static`) is not. | `test_registry_reports_parent_path_mounts[/api]` and `[]` **failed on the pre-fix code** and pass now; `test_registry_ignores_unrelated_mounts` passes. |

## Also addressed

- **Restored coverage:** `test_short_dev_secret_refused` covers the `AUTH_DEV_SECRET` ≥ 32 characters rule, which lost its test when the Phase 2 tests were replaced.
- **Stale config comment:** `backend/.env.example` no longer says dev mode works outside local/test.

## Not changed: needs the user's decision

- **Residual exposure.** `AUTH_MODE` still defaults to `dev`, and `ENV=test` enables the dev sign-in endpoints with the public default secret. So a server that is reachable from the network but labelled `test` is open to anyone. Options:
  - make `AUTH_MODE` required too; or
  - require an explicit `AUTH_DEV_SECRET` whenever `ENV=test` runs anywhere but CI.

  Either changes D-13, so it is left for the user.
- **Runtime guard scope.** The runtime fail-closed guard still covers HTTP API routes only. Mounts and websockets are caught only by the CI registry check (BE-2.12 / BE-2.14), which is the intended gate.
