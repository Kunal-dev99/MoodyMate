# Phase 2 — engineer report (BE-2.1…2.6, FE-2.1…2.2)

Built in worktree `../MoodyMate-wt/ws1-phase2` (branch `feat/ws1-phase2`, from `master`
1847c3b) against DB copy `moodymate_ws1_p2`; tests used `moodymate_test` (D-03).

Evidence in this folder:
- `checks.txt`: lint, types, 71 tests, OpenAPI drift, migration round trips on the DB copy and the compose container, frontend checks.
- `browser-dev-mode.json` + `p2-dev.mjs`: the full app in AUTH_MODE=dev, every seeded role.
- `browser-oidc-mode.json` + `p2-oidc.mjs`: the full app in AUTH_MODE=oidc against a real local OIDC provider (`ghcr.io/navikt/mock-oauth2-server:2.1.10`, authorization code + PKCE).
- Screenshots `01`–`09`.

| Task | Done-when (verbatim from plan) | Evidence |
|---|---|---|
| BE-2.1 | the migration round trip passes, and model tests create and read each entity. | Migration `0002_identity_tables` (6 tables). checks.txt: round trip base↔head twice on the DB copy and on the compose container. `test_identity_models.py::test_create_and_read_each_identity_entity` creates and reads tenant, user_account, role, permission, user_role and role_permission (PASSED). Also `test_idp_identity_is_unique`. |
| BE-2.2 | after upgrade, the role→permission matrix query matches the table in this plan; after downgrade, the tables are empty. | Migration `0003_identity_seed` (frozen values, rows tagged `seeded_by`). `test_identity_seed.py::test_seeded_matrix_matches_plan` (vs `ROLE_PERMISSIONS`, which mirrors the plan table) and `::test_downgrade_removes_seed_and_upgrade_restores_it` (all 6 tables count 0 at 0002) PASSED. Container query in checks.txt prints the same matrix. Extra: `::test_seed_downgrade_refuses_to_delete_user_data`, also seen for real in checks.txt when the DB copy still held E2E users. |
| BE-2.3 | tests cover a valid token, an expired token, a wrong issuer, no token, and `dev` mode being rejected in production. | `test_auth.py`, all PASSED: `test_valid_oidc_token_resolves_principal`, `test_expired_token_is_401`, `test_wrong_issuer_is_401`, `test_no_token_is_401_with_challenge`, `test_dev_mode_refused_in_production`. Also covered: wrong audience, wrong key, algorithm confusion (HS256 with the public key) and `alg=none`, not provisioned outside local, self-provisioned with no roles in local, disabled account, tampered dev token, dev token rejected in oidc mode, dev endpoints absent in oidc mode and outside local/test, settings validation. Real IdP: browser-oidc-mode.json (RS256 token from the mock provider, validated by the backend via JWKS discovery). |
| BE-2.4 | the registry test fails when a deliberately unguarded test route is added, and passes otherwise. | `test_route_registry.py::test_registry_catches_an_unguarded_route` (an added unguarded route is reported) and `::test_every_api_route_declares_a_permission` (real app: `[]`) PASSED. Runtime fail-closed, beyond the Done-when: `::test_unguarded_route_is_denied_at_runtime` (403 `permission_not_declared`, no data leaked). `PUBLIC_PATHS` is pinned by test. |
| BE-2.5 | a test with two tenants proves cross-tenant reads return nothing, even with a crafted ID in the URL. | `test_scoping.py::test_cross_tenant_reads_return_nothing_even_with_crafted_id` (list excludes them; `GET /users/{foreign-id}` gives 404, identical to a random ID). Also `::test_tenant_parameters_in_the_request_are_ignored` (query param and header) and `::test_repository_scope_filters_at_the_query` (the repository itself). All PASSED. |
| BE-2.6 | tests for each seeded role return exactly that role's permissions. | `test_me.py::test_me_and_permissions_per_role[<role>]`, 5 PASSED. Also browser-dev-mode.json, `shown_matches_backend: true` for every role. |
| FE-2.1 | manual test notes show sign-in as each seeded role in dev mode; the picker doesn't render when the mode is `oidc`. | browser-dev-mode.json: signed in as all 5 roles through the picker (`picker_personas: 5`, screenshot 01). browser-oidc-mode.json: `dev_picker_rendered: 0`, only "Sign in with SSO" (screenshot 06). Full PKCE flow (`code_challenge_method: S256`) through the provider's login page (07) to the dashboard. Unauthenticated deep link redirects to `/login?next=…`; a tampered token mid-session returns to sign-in; sign-out goes to `/login` with the token cleared. |
| FE-2.2 | `auditor` sees no Propose button; a direct URL to a forbidden page shows the 403 state, and the backend also returns 403 (cosmetic hiding is not relied on). | **Partly literal.** The direct-URL part is fully evidenced. Auditor opening `/access/users` sees the 403 state, and the browser saw the backend's own `403` for the same request (`auditor_direct_url_users`, screenshot 04). Risk analyst opening `/audit` gets the 403 state (05). Nav hides items per permission (`nav` per role). **There is no Propose button yet**: it arrives with the proposal workflow in FE-5.1. The hiding mechanism (`<RequirePermission fallback={null}>`) is built and evidenced on the admin-only "Users" section, which auditor does not see (`users_section_visible: false`) while platform_admin does. The literal "Propose button" check needs FE-5.1. |

## Additions beyond the plan (recorded for the architect)

- **New endpoints.** D-05 allows additions alongside the plan. All are recorded in ARCHITECTURE.md's governance row.
  - `GET /api/v1/users` and `GET /api/v1/users/{id}` (`admin:read`, read-only). These give BE-2.5 a real crafted-ID target and back the Access & Roles page.
  - `GET /api/v1/auth/config` (public). The frontend needs the auth mode and its OIDC client settings.
  - `GET /api/v1/dev/personas` and `POST /api/v1/dev/sessions` (public, mounted **only** when `AUTH_MODE=dev` and `ENV` is local or test). They mint dev tokens for seeded roles. This is a deliberate local-only capability. `PUBLIC_PATHS` is pinned by a test, so widening it is a reviewed change.
- **Fail-closed guard is app-level**, not router-level, so even a route attached directly to the app is denied unless it declares a permission.
- **Contract enums.** `Permission` and `Role` are enums in the OpenAPI contract, so the frontend's permission codes are generated, not hand-written.
- **Self-provisioning** happens in `ENV=local` only, and always with no roles (default deny).

## Known limitations / follow-ups

- **OIDC sign-out is local.** It clears the app session and the oidc-client user, but does not call the provider's end-session endpoint. Adding that is cheap, but needs the client's IdP logout behaviour confirmed.
- **No authenticated "last seen" tracking yet** (not in scope).
- **Denied (403) requests are not audited yet.** That is BE-3.2.
- **Role enum vs database.** A role added to the database without a code change would fail `/me` response validation. Roles are only added through migrations, so this is acceptable for now; noted for the admin tooling feature.
