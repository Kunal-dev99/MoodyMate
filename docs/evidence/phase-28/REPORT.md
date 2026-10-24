# Phase 28 — Security and demo credibility (Workstream A, P0): phase report

**Status: complete; merged via the Phase 28 PR.**
Worktree: `/home/user/MoodyMate-wt/f06-p28` (branch `f06-p28`, based on 376c561).
Report date: 2026-10-24. Author: solution-architect (verification only).


## Coordinator completion (2026-10-24)

The build round left the remediation incomplete; the coordinator finished it:
- **postcss high (hard-stop Q13):** resolved by option B: `overrides.next.postcss = 8.5.30`. No high advisory is accepted.
- **Python advisories (17):** fastapi 0.162, starlette 1.9, pytest 9.1, pytest-asyncio 1.4, certifi 2026.10.23; pip-audit: no known vulnerabilities.
- **FastAPI 0.162 regression found and fixed:** included routers are now lazy, so `app.routes` hid every `/api/v1` route (the route-registry check went fail-open) and `scope["route"].path` lost its prefix (the D-04 public-path guard refused `/dev/sessions`). `app/core/routes.py` resolves served routes; the guard still fails closed.
- **CI wiring:** npm audit gate, `pip-audit`, `NEXT_PUBLIC_API_BASE_URL` on both builds.
- `docs/security/ADVISORIES.md`, the audit evidence JSON, the ARCHITECTURE.md security section, and plan ticks.
- Merged master (Phase 30); regenerated API types; the registry snapshot changed only in one lucide icon path.

**Checks on the merged tree:** backend 49 passed; ruff, mypy, openapi check clean; frontend tsc/lint clean, vitest 647 passed, build passes; build without the API URL fails as intended.

## Hard stops (user / client)
1. Advisory owners: every entry in ADVISORIES.md is UNASSIGNED (D-55 §4).
2. CSP enforcement in production, plus its prerequisites: OIDC issuer origin under connect-src, and `'unsafe-inline'` in script-src (nonce-based policy recommended). D-54 §5.
3. Production rate-limit values (defaults are PROVISIONAL).

## LOW / follow-ups
- Report-Only CSP has no `report-uri` / `report-to`: violations are not collected.
- `httpx2` not added: Starlette 1.9 warns that `httpx` with TestClient is deprecated; the tests narrow responses with `conftest.response()`.
- Keep `fastapi>=0.162,<0.163` pinned: `core/routes.py` uses `iter_route_contexts`.
- The rate limiter is per process; a shared store is needed before multi-instance deployment.
