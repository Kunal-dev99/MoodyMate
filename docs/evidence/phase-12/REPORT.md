# Phase 12 — reconciliation and drift detection: checks and verification

**Scope.** BE-12.1 to BE-12.4 and FE-12.1, FE-12.2 (DI-05, the drift half of DI-06). The user
built it and merged it to master in `ffdf5fe`, `3bc019e`, `dd17dea`, `b17b963` and `f187f79`
(merge `cdbade9`). The architect verified it by reading the code at `cdbade9` in the
`feat/ws2-p13` worktree. All six tasks are ticked. The semantics as built are recorded as
**D-28** (proposed, waiting for the user's confirmation).

**Commit note.** `b17b963` is titled "WIP", but its content is final: it is part of the
merged Phase 12 and is covered by the checks below. No follow-up commit replaces it.

## What was built

- **Migration 0016** (`backend/app/db/migrations/versions/0016_reconciliation_and_drift.py`).
  It adds `reconciliation_run`, `reconciliation_item` and `data_drift_event`.
  - Runs and items are append-only: UPDATE, DELETE and TRUNCATE raise.
  - Insert triggers tie a run to a validated import of its own tenant. They refuse a
    non-MATCHED item under a MATCHED run, and an unexplained gap under a run that is not
    BROKEN.
  - Drift events are never deleted or truncated. Their content is frozen, and they move
    OPEN → ACKNOWLEDGED | ACTION_REQUIRED once.
  - CHECKs cover every enum, `variance = source - target`, and the status-of-variance rule.
  - The downgrade refuses while runs or events exist.
- **Reconciliation** (`backend/app/reconciliation/`). It runs inside validation
  (`ingestion/service.py:1101,1118-1163`). The figures come from the database
  (`ingestion/repository.py:441-489`). It has four checks: ROW_COUNT, EXPECTED_ROW_COUNT,
  CONTROL_TOTAL and DECLARED_CONTROL_TOTAL. The arithmetic is exact Decimal. The tolerance
  comes from `RECONCILIATION_TOLERANCE` (default 0, PROVISIONAL). The route is
  `GET /imports/{import_id}/reconciliation` (`import:read`), and figures are returned as exact
  text and masked by classification.
- **Drift** (`backend/app/mapping/`).
  - Detection runs after profiling (`ingestion/service.py:856-859`), against the latest
    COMPLETED / COMPLETED_WITH_WARNINGS import of the same source
    (`ingestion/repository.py:491-507`).
  - It detects five types (`mapping/service.py:1269-1314`). The thresholds are settings
    (`core/config.py:87-92`), and the feature sits behind `DRIFT_DETECTION_ENABLED`.
  - Routes: `GET /imports/{import_id}/drift` (`import:read`) and
    `POST /drift-events/{id}/review` (`quality:review`, `mapping/router.py:420-458`).
- **Frontend** (`frontend/src/components/ingestion/`).
  - `ImportReconciliationTab.tsx` and `reconciliation.ts`: per-check rows, an outcome pill,
    and figures shown as the API text.
  - `ImportDriftTab.tsx`, `DriftReviewDialog.tsx` and `drift.ts`: alerts, a once-only review
    dialog, and a "Propose schema change" link to the Phase 5 page with only the schema
    prefilled.
  - Playwright: `frontend/e2e/reconciliation.spec.ts`. Unit tests:
    `__tests__/reconciliation.test.ts` and `__tests__/drift.test.ts`.

## Done-when evidence

| Task | Evidence |
|---|---|
| BE-12.1 | `test_reconciliation.py::test_phase12_tables_are_guarded_in_the_database`: frozen enum copies equal the code; UPDATE/DELETE/TRUNCATE refused on runs and items; item insert refusals (MATCHED run, unexplained gap, status, variance, check code); run insert refused for a non-validated import; drift content frozen, DELETE/TRUNCATE refused, review once; event insert refused unless OPEN and against a completed baseline; downgrade to 0015 refused with counts unchanged, and 0015's refusal still holds afterwards. Round trip: `test_ws1_verification.py::test_migration_round_trip_and_downgrade_guards` (head = 0016 → base → head) and the CI step `Migration round trip (D-02)` (`ci.yml:48-49`). |
| BE-12.2 | `test_reconciliation.py::test_reconciliation_outcomes_and_exact_variances`: clean fixture MATCHED (row count 25/25/0/0, notional variance `"0.00"`); mixed-errors fixture REVIEW_REQUIRED, row count 25/20/5/5, notional variance `"4237607.14001"`, equal to the held rows' total computed in the test; `expected_row_count=24` BROKEN (variance `"-1"`), with a matching declared total MATCHED; risk_analyst and auditor see masked totals; 404 `reconciliation_not_found` before validation; a table test over 155 item combinations shows MATCHED iff every item is MATCHED and BROKEN iff a gap is unexplained; every stored run is checked; no `float` in the reconciler. |
| BE-12.3 | `test_reconciliation.py::test_drift_detection_setting_and_review`: a first import gets no events and no detection audit; the extra-column fixture after a clean baseline gets exactly one COLUMN_ADDED `legacy_flag`, with an `import.drift_detect` audit; schema, contract and mapping hashes and statuses, field count and proposal count are equal before and after; with `DRIFT_DETECTION_ENABLED=False` there are no events and no detection audit; the five types on synthetic profiles, with at-threshold values not counted as drift; no `float` in the detector. |
| BE-12.4 | The same test: risk_analyst and approver get 403 (`quality:review`); missing, blank or OPEN outcomes get 422; a foreign tenant gets the same 404 as a random id, audited DENIED; ACTION_REQUIRED is recorded with `reviewed_by`, the comment and the time, one SUCCESS `drift_event.review` event, and a `proposal_link` to `/schemas/proposals/new?schema={id}`; nothing governed changes (the proposal count included); a second review gets 409 `already_reviewed`, audited DENIED, with no change. |
| FE-12.1 | `reconciliation.spec.ts`, "FE-12.1: the quarantine fixture reconciles REVIEW_REQUIRED; …" (`:56-115`): validation in the browser, the outcome pill `REVIEW_REQUIRED`, every figure of every row equal to the API string, and the notional variance text equal to the API's. Masking: `:117-133`. Grep: `__tests__/reconciliation.test.ts:125-146`, over seven reconciliation and drift files, finds no `parseFloat`, `parseInt`, `Number(`, `Math.`, `toFixed` or unary `+`. An architect grep of those files also finds none. |
| FE-12.2 | `reconciliation.spec.ts`, "FE-12.2: a clean import, then the extra-column import: …" (`:135-194`): both uploads go through the wizard and the clean one is validated; exactly one `COLUMN_ADDED:legacy_flag` against the clean baseline; the steward acknowledges it with a comment; the alert shows `reviewed_by` and the comment, also after a reload. "Action required" (`:196-247`): the link opens the Phase 5 page with only the schema prefilled; the proposal total is unchanged; risk_analyst has no Review button and gets 403. |

## Checks

| Check | Result |
|---|---|
| CI run 43 on `cdbade9` | passed — https://github.com/Kunal-dev99/MoodyMate/actions/runs/39022542617 (backend: lint, mypy, migration round trip, tests, ws1 suite, OpenAPI check; frontend: types, lint, typecheck, unit tests, build; e2e: Playwright over all of `e2e/`, including `reconciliation.spec.ts`) |
| Backend ruff check (local, master) | clean |
| Backend ruff format (local, master) | clean, 143 files |
| Backend tests (local, master; D-26) | 47 passed in 112 s |

## Known gaps and notes (none blocks a tick)

- **Baseline key.** The baseline is matched by source system, not by source profile. This is
  the same while a source has one profile. It is recorded in D-28 §5 and should be revisited
  if multi-profile sources arrive.
- **Baseline timing.** The baseline is chosen at upload (profiling). An import compared
  before its baseline was validated has no events, and detection is not re-run later.
- **Untested refusals.** No backend test covers the `INGESTION_ENABLED` off refusal on
  `POST /drift-events/{id}/review`, or a BROKEN outcome from a differing
  DECLARED_CONTROL_TOTAL. These are not in the Done-whens, and D-26 caps the suite. They are
  candidates for the BE-13.2 matrix.
- **No local Playwright run** was done for this phase review; the e2e evidence is CI run 43.
  The spec can write screenshots with `RECONCILIATION_SCREENSHOT=<dir>`.
- **PROVISIONAL settings.** `RECONCILIATION_TOLERANCE` (0), `DRIFT_NULL_RATE_THRESHOLD`
  (0.10) and `DRIFT_RANGE_THRESHOLD` (0.25) are PROVISIONAL until the client sets them.
- **D-28 is proposed, not yet active.** It needs the user's confirmation.
