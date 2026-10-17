# Phase 15 — snapshot, pinned run and maturity ladder: checks

**Scope.** BE-15.1 to BE-15.5, FE-15.1 and FE-15.2: the thin vertical slice of Feature 03.
Decisions D-30, D-31, D-32, D-33 (amended in this phase) and D-35 apply.

## What was built

- **BE-15.1** Rules registry, in migration `0018_calculations` (tag `0018_calculations`).
  - Rule and module versions outside DRAFT are immutable: UPDATE, DELETE and TRUNCATE are
    refused.
  - At most one ACTIVE version per rule, enforced by a partial unique index.
  - Bucket definitions can only be inserted under a DRAFT `DAY_BUCKETS` version.
  - Seed: maturity ladder v1, three ACTIVE rule versions, and the empty `baseline
    (illustrative)` assumption set. All of it is PROVISIONAL and illustrative, carrying the
    D-35 statement.
- **BE-15.2** Calculation tables.
  - Terminal runs are immutable.
  - Output rows are refused unless their run is RUNNING. The check locks the run row
    `FOR SHARE`, so no row can slip in while a run finishes.
  - Gap results must use a bucket of the run's pinned bucket version.
  - The downgrade refuses while any run or non-seed row exists.
- **BE-15.3** `POST /dataset-snapshots` and the reads. The same members always give the same
  snapshot, whatever their order; a repeat returns 200. The refusal codes and
  `content_sha256` are recomputed from the member manifests. Behind `CALCULATIONS_ENABLED`.
- **BE-15.4** The module interface and the synchronous engine, with Decimal-only arithmetic.
  - Maturity ladder v1, in 8 buckets: (0,1], (1,7], (7,30], (30,90], (90,180], (180,365],
    (365,730], (730,∞) calendar days.
  - Direction: LOAN and BOND are inflows; DEPOSIT is an outflow.
  - A past-due flow is excluded with `PAST_DUE_EXCLUDED`.
  - Run reproducibility:
    - `POST /calculation-runs` takes an `Idempotency-Key`;
    - a re-run on the same pins gives the same `manifest_sha256` and `result_sha256`.
  - Run failures:
    - any engine failure ends the run FAILED with `ENGINE_ERROR`;
    - a RUNNING run older than `CALC_RUN_STALE_SECONDS` is ended FAILED when its key is
      replayed.
- **BE-15.5** Run, results and maturity-ladder reads, with lineage down to the source row.
  Bucket totals equal the sum of their contributions.
  - **D-33 masking:** contributions are masked for `risk_analyst`.
  - **D-33 amendment:** a ladder figure built from fewer than `CALC_MIN_AGGREGATE_COUNT`
    (default 3, PROVISIONAL) contributions is masked too.
  - **Added:** `GET /imports?projected=&schema_code=` filters for the New-run panel.
- **FE-15.1** The `/calculations` run list and a New-run panel. The panel picks projected
  imports, builds or reuses a snapshot, shows the pins before submitting, sends an
  idempotency key, and shows refusal toasts with the correlation ID. Behind
  `NEXT_PUBLIC_CALCULATIONS_UI`.
- **FE-15.2** The run detail page.
  - Shows the status and post-checks, the manifest, warnings, and the ladder per currency
    with contractual and behavioural splits.
  - The drill-down links to the instrument, the import and the source row.
  - A masked figure gets a hint saying it has fewer than N contributions.
  - Every page carries the banner "Illustrative prototype — not regulatory".

## Checks (local)

| Check | Result |
|---|---|
| ruff, ruff format, mypy, OpenAPI drift | clean / 151 files / no issues / up to date |
| Backend suite (D-26) | 47 collected, 47 passed in 149 s |
| Migration round trip (scratch DB) | 0017 → 0018 → 0017 → 0018; seed restored with the same ids |
| Golden ladder | 12 hand-computed flows: 8 bucketed, 4 excluded; cumulative gap EUR 87.10, USD 700.00 |
| Frontend lint, tsc, vitest, build | clean, clean, 265 passed, passed |
| Playwright, full suite on a fresh DB | 33 passed (1.4 m); `calculations.spec.ts` covers both FE Done-whens |

**Review.** One quick pass found one major issue, now fixed: single cash-flow amounts were
visible to `risk_analyst` through a bucket chosen by its reporting date, which led to the
D-33 amendment. It also found two medium issues, both fixed: an output-insert race, and runs
that could stay RUNNING. The low-severity database integrity gaps are closed too.

## Open items

- **D-33 residual risk.** Comparing clear totals across two runs whose reporting dates move
  one flow between buckets can still reveal an amount. Closing this needs clearance option C
  or limits on reporting dates. The client must decide before production.
- **A flow due on the reporting date (day 0)** is excluded with `OUTSIDE_BUCKETS`, per the
  seed. The methodology owner should confirm this. Under `INCLUDE_IN_FIRST_BUCKET`, overdue
  flows go into B01 but a day-0 flow does not.
- **A cumulative gap of exactly zero after a masked net gap.** The masking rule and the
  "zeros stay clear" rule conflict here. The UI shows what the server sends; the backend
  rule needs stating in Phase 16.
- **D-31 §1.** There is no start-up registry check, because the app starts without a
  database. A run instead refuses with 409 `module_not_available`. D-31 should be reworded
  to match.
- **Phase 16 watch item.** JSONB fractions decode as floats. DECIMAL parameters must be
  stored as strings, or decoded with `parse_float=Decimal`.
- **Run detail** lists every warning inline, with no pagination.
