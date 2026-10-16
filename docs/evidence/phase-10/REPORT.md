# Phase 10 — mapping workbench: checks and quick review

**Backend (BE-10.1…10.6).** A single-pass review of the code at `0d7b07a` ticked all six
tasks, with no BLOCKER or MAJOR. Two MINOR items were fixed in `07fffa1`:

- **P10-1:** validate now locks the version before setting `last_validation_id`, so a
  validate racing a submit no longer returns a 500.
- **P10-2:** the `mapping_version_checks` trigger refuses to leave DRAFT unless the version
  was submitted after a validation. It also refuses a decided status without a decision, and
  a decision made by the author. Migration 0014 is unreleased, so it was amended in place.

**Frontend (FE-10.1, FE-10.2)** is committed at `821ff80`.

## Results

| Check | Result |
|---|---|
| Backend ruff, format, mypy | clean |
| Backend tests (D-26) | 39 collected, 39 passed in 56 s, including the ws1 matrix and audit check with all mapping routes |
| Migration | 0014 → 0013 → 0014 round trip on `moodymate_ws2_p10` |
| Frontend typecheck, lint | clean |
| Frontend unit tests | 109/109 |
| Frontend build | ok |
| Playwright | 19/19, including FE-10.1 (BLOCKER until `notional` is mapped, then ok) and FE-10.2 (409 envelope on a forced early submit, then author → approver → Active v2) |

## Accepted deviations

- There is no edit-in-place for drafts: each save creates a new DRAFT version.
- The mapping version is pinned to an import in Phase 11 (D-18).
- There is one mapping per source contract.
- A mapping on a SUPERSEDED contract is refused.
- A dry run fails on any BLOCKER or ERROR.
- Transform params are checked with pydantic models.
- Mapping writes are gated by both `INGESTION_ENABLED` and `MAPPING_CHANGE_ENABLED`.

## Carried forward

- **Phase 11:** assert the `canonical_record` count in validate, and test the import_job
  mapping pin trigger.
