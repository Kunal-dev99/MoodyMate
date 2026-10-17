# Phase 14 — domain projection and instrument catalogue: checks

**Scope.** BE-14.1 to BE-14.5, FE-14.1 and FE-14.2 (FC-01, the data half of FC-02). BE-14.6
and FE-14.3 shipped earlier in PR #22. Decisions D-29, D-33 and D-34 apply.

## What was built

- **BE-14.1** Test headroom. Four test functions were folded into existing ones with every
  assertion kept:
  - `test_profiling` ×2 → `test_golden_profile_of_the_synthetic_instrument_csv`
  - `test_imports` ×1 → `test_over_the_byte_limit_is_413_with_nothing_left_behind`
  - `test_mappings` ×1 → `test_create_list_get_and_versions` (the dry run now reuses the
    mapping created above it)

  This took the suite from 49 to 45 tests, and `test_domain.py` brings it to 46.
- **BE-14.2** Migration `0017_domain_projection` adds nine tables.
  - Every table refuses UPDATE, DELETE and TRUNCATE.
  - Same-tenant insert checks:
    - lineage repeats the canonical record;
    - the instrument type belongs to the same tenant;
    - the projection's schema code matches the import's pinned schema;
    - version and event rows match their projection's schema code.
  - CHECKs cover the enums.
  - The downgrade refuses while any domain row exists.
  - Tagged seed `0017_domain`: 7 permissions, 19 role grants, and the instrument types LOAN,
    DEPOSIT and BOND.
- **BE-14.3** `POST /imports/{id}/projection`. It returns 201 on the first call and 200 on a
  replay that writes nothing. It is safe when two calls run at once: the import row is
  locked, plus unique indexes and a per-tenant advisory lock. Refusals are audited DENIED.
  It sits behind `DOMAIN_PROJECTION_ENABLED`. `maturity_date` (text in instrument v1) is not
  parsed.
- **BE-14.4** `GET /instruments` and `GET /instruments/{id}`, masked by classification
  (D-33).
  - `payload_sha256` reads "masked" unless the caller is cleared for the version's most
    sensitive field.
  - Cash flows are linked by tenant, source and business key.
  - Added during the build: `GET /imports/{id}/projection` (`domain:read`), the read-only
    projection summary.
- **BE-14.5** The OpenAPI export, `import.project` in `AUDITED`, the WS3 routes in the WS1
  matrix and isolation tests, the conftest cleanup, and the domain row in ARCHITECTURE.md.
  RETENTION.md also gets a domain projection row.
- **FE-14.1** The `/domain` catalogue (filters in the URL, paginated, provisional badge) and
  the instrument detail page (versions, typed values, attributes, lineage links, cash
  flows). Decimals are shown as the API's string. The switch `NEXT_PUBLIC_DOMAIN_UI=off`
  restores the Phase 13 placeholder.
- **FE-14.2** A projection section on the import Status tab.
  - It reads an existing projection on load (any role with `domain:read`).
  - The "Project to domain" button needs `domain:project`.
  - The summary shows conservation, `TYPE_NOT_PROJECTED`, `UNKNOWN_VALUE` and whether the
    response was a replay.

## Checks (local)

| Check | Result |
|---|---|
| ruff, ruff format, mypy, OpenAPI drift | clean / 146 files / no issues / up to date |
| Backend suite (D-26) | 46 collected, 46 passed in 129 s |
| Migration round trip on a scratch DB | 0016 → 0017 → 0016 → 0017 passed; seeds 7 / 19 / 3 |
| Frontend lint, tsc, vitest | clean, clean, 182 passed |
| `npm run build` | passed; also with `NEXT_PUBLIC_DOMAIN_UI=off` |
| Playwright, full suite on a fresh DB | 30 passed (1.3 m); `domain.spec.ts` covers both FE Done-whens |

**Review.** One quick security and correctness pass found no major issues. Four smaller
findings were fixed: the payload hash could reveal masked values; cash flows were linked
across sources; three insert checks were missing; and a date error message could carry a
raw value.

## Notes and open items

- **Projector v1 accepts only `instrument` schema v1 (D-29).** Once a later instrument
  version is active, instrument imports get 409 `projection_unsupported` until a new
  projector version exists. Because of this, `domain.spec.ts` runs before `smoke.spec.ts`,
  which activates a later version. Relaxing this needs a D-29 amendment.
- **Cash flows link to an instrument only when they come through the same source.** The
  stock samples sit on different sources, so they show no linked cash flows.
- **Frontend gaps:** the instrument detail fetches the type vocabulary with a second call,
  and the cash-flow vocabulary badge is fixed text. These could become an API field later.
