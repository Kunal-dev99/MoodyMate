# Phase 11 — validation, quarantine and canonical records: checks and quick review

**Backend (BE-11.1 to BE-11.5).** A single-pass review of the code at `b4e0c27` ticked all
five tasks, with no BLOCKER or MAJOR. Fixes are in `477d594`:

- **P11-1:** `validation_issue.code` is now CHECK-constrained (migration 0015, unreleased,
  amended in place).
- **P11-6:** with `INGESTION_ENABLED` off, validate and disposition return 409 and are
  audited.
- **P11-2 to P11-5** are recorded as **D-27**: validate leaves the status unchanged on 409
  `mapping_required`; REJECTED is final and not linked to a retry; resolution needs a direct
  retry of the original import; for duplicate keys the first occurrence wins.

**Frontend (FE-11.1, FE-11.2)** is committed at `a0afbae`.

| Check | Result |
|---|---|
| Backend ruff, format, mypy | clean |
| Backend tests (D-26) | 44 collected, 44 passed in 83 s, including the ws1 matrix and audit check with the validate, quarantine and disposition routes |
| Migration | 0015 → 0014 → 0015 on `moodymate_ws2_p11` |
| openapi and samples checks | pass |
| Frontend typecheck, lint | clean |
| Frontend unit tests | 114/114 |
| Frontend build | ok |
| Playwright | 21/21 on a fresh DB copy |

The Playwright run covers both Done-whens:

- **FE-11.1:** on the mixed-errors fixture the page shows 20 accepted and 5 quarantined,
  equal to the API, with the counts adding up to the row count.
- **FE-11.2:** an item moves OPEN → UNDER_REVIEW → RETRY_READY → RESOLVED via a retry upload.
  `risk_analyst` sees no Quarantine tab and gets 403 on the direct call.

The first full Playwright run had 4 failures. It overlapped another Playwright run on the same
ports. A re-run with nothing else running passed 21/21.
