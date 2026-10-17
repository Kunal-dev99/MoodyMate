# Phase 18 — extension points, validation pack and WS3 verification: checks

**Scope.** BE-18.1 to BE-18.3, FE-18.1 and FE-18.2 (FC-05, FC-06, and the WS3 Definition of
Done). Four parallel agents built this phase. It was then integrated with Phases 16 and 17.

## What was built

- **BE-18.1 Planned modules.** Migration `0021_planned_modules` (down_revision 0020) seeds
  `alm_irrbb`, `collateral` and `capital` v1 as PLANNED, with declared contracts and tag
  `0021_planned_modules`.
  - They are listed by `GET /calculation-modules` and are never runnable: a run gets 409
    `module_not_available`, audited DENIED. The WS1 audit test asserts this.
  - `docs/CALCULATION_MODULES.md` covers the registry, the interface, contracts, rounding and
    how to add a module.
- **BE-18.2 Domain validation pack.** `backend/app/calculations/golden/*.json` holds the
  maturity ladder and `lcr_hqla` engineering cases, each "awaiting domain-expert approval".
  - The test loop reads the files, and each file runs under its own module.
  - Floats are refused when the files are loaded.
  - `golden/README.md` names the approver role and the tolerance policy.
- **BE-18.3 WS3 verification.** The `ws3` marker covers the WS1 matrix, isolation, audit and
  migration tests, plus `test_domain`, `test_calculations` and `test_rules`.
  - A CI step, "WS3 verification suite", prints the WS3 route × role matrix. WS3 routes are
    selected by path prefix.
  - Every WS3 table has a RETENTION.md row.
- **FE-18.1** PLANNED modules show a disabled, never-selectable entry in the New-run picker,
  with the badge, the reason and the contracts. The `/domain/modules` registry shows
  `PlannedModuleBadge` and `ModuleContracts`.
- **FE-18.2** `e2e/ws3-smoke.spec.ts` is one serial journey:
  1. project the imports;
  2. open the catalogue;
  3. build the snapshot (PLANNED modules are blocked, and a direct POST gets 409);
  4. run the maturity ladder, and the `lcr_hqla` run with the liquidity view;
  5. open the ladder and drill down to the source row;
  6. propose and approve a bucket rule change;
  7. a new run pins the new version, and the old run's hashes are unchanged;
  8. open the module registry.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| Migration chain | single head 0021: base → head → base → head and head → -1 → head pass |
| Targeted backend tests (calculations, rules, WS1, domain) | 7 passed |
| `pytest -m ws3` | 7 passed |
| Collected (D-26) | 48 |
| ruff, ruff format, mypy, OpenAPI drift | clean / 157 files / no issues / up to date |
| Frontend lint, tsc, vitest | clean, clean, 351 passed |
| Playwright `ws3-smoke.spec.ts` | 1 passed (all 8 steps, including 4b, 6, 7 and 8) |

## Open items for the client

- **Golden-case sign-off.** The cases need sign-off from the named financial owner for
  liquidity risk.
- **Planned contracts.** They name schemas, rules and tables that do not exist yet, so a
  domain owner must agree them.
- **Activating a planned module.** `module_version` is append-only, so implementing a planned
  module means seeding v2 as AVAILABLE.
