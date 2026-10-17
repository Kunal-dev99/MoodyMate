# Phase 13 — adapter readiness and WS2 verification: checks

**Scope.** BE-13.1, BE-13.2, BE-13.3 and FE-13.1. Tests and docs only; no migration and no app
source change.

## What was built

- **BE-13.1** `docs/ADAPTERS.md`, the adapter contract, written from
  `backend/app/ingestion/adapters/`. `test_adapters.py::test_every_adapter_meets_the_contract`
  is one test that loops over CSV, XLSX, JSON and the test-only `FakeFeedAdapter`
  (`backend/app/tests/fake_feed.py`). It checks the interface, row and locator shape, exact
  string values with no floats, the limit refusals with no value leaks, and imports. It also
  greps in code that nothing in `app/` outside `app/tests` references the fake. A mutation
  check (the fake returning a float) made the test fail.
- **BE-13.2** `test_ws2_verification.py::test_ws2_end_to_end_matrix_isolation_and_audit`
  (`-m ws2`):
  - the end-to-end CSV → profile → mapping → approval → validate → reconcile → quarantine
    disposition → drift review run, with every other role refused on each write;
  - AUDITED completeness for WS2;
  - row conservation in the database;
  - the read route × role matrix;
  - cross-tenant isolation for sources, imports, mappings, quarantine and drift.

  The migration round trip reuses the ws1 test, which is now also marked `ws2`. CI has a new
  step, "WS2 verification suite", and the general step excludes `ws1` and `ws2`.
- **BE-13.3** `docs/RETENTION.md` gets 8 PROVISIONAL rows that cover all 21 tables from
  migrations 0009–0016. Restore step 4 checks each artifact's store hash and size against
  `import_artifact`.
- **FE-13.1** `frontend/e2e/ws2-smoke.spec.ts` is one serial journey in seven steps on its
  own source. Shared helpers moved from `reconciliation.spec.ts` into `e2e/support.ts`.

## Checks (local)

| Check | Result |
|---|---|
| ruff check / format | clean / 145 files formatted |
| mypy app scripts | no issues, 145 files |
| Backend suite (D-26) | 49 collected, 49 passed in 103 s |
| `pytest -m ws2 -v` | 2 passed; matrix printed (22 WS2 routes × 5 roles) |
| Frontend lint, `tsc --noEmit` | clean |
| Playwright, full suite on a fresh DB | 26 passed (1.1 m) |

BE-13.2 and FE-13.1 are ticked once CI is green on the Phase 13 PR (their Done-whens are CI
runs).

## Notes and open items

- The backend suite is at the D-26 maximum of 49. The next phase must fold tests in, not add
  them.
- `SourceFormat` has no FEED member, so the fake reports JSON and is never registered. A real
  feed adapter needs a new member and wider format CHECKs; this needs a named decision first
  (ADAPTERS.md §8).
- CSV `detect` claims any text that is not JSON. The next adapter must resolve that overlap.
- The script for restore step 4 (artifact hash check) is not built (RETENTION open item 7).
- The smoke test does not retry with a corrected file, because that would move the drift
  baseline. `validation.spec.ts` covers it.
