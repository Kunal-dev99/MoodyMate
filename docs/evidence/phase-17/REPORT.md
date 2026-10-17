# Phase 17 — LCR/HQLA prototype module: checks

**Scope.** BE-17.1 to BE-17.3 and FE-17.1 (FC-03). Decisions D-31, D-32, D-33 (amended
again in this phase) and D-35 §2 apply. The phase was built by three parallel agents and
then integrated with Phase 16.

## What was built

- **BE-17.1 Migration and seed.** Migration `0020_lcr_results` adds `lcr_result` (one row per
  run and currency) and `hqla_asset_result` (one row per asset position, with lineage).
  - Both tables are protected by 0018's run-output guards: inserts only while the run is
    RUNNING, the run row locked FOR SHARE, append-only, TRUNCATE blocked.
  - CHECK constraints enforce the figure identities and the ratio, and the `*_nonzero_count`
    columns.
  - The downgrade refuses while any LCR results exist.
  - Tagged seed `0020_lcr_hqla`: `lcr_hqla` v1 is AVAILABLE, with six ACTIVE ILLUSTRATIVE
    rules. The decimals are strings padded to scale, so the rules can be governed through
    Phase 16.
- **BE-17.2 Module `lcr_hqla` v1.** It is pure and uses Decimal only.
  - HQLA is balance × (1 − haircut), by level.
  - Outflows are balance × run-off.
  - Inflows are flows due within (0, 30] days × rate, capped at 75% of outflows.
  - The ratio is rounded once, to 4 dp with ROUND_HALF_EVEN.
  - A zero denominator gives a NULL ratio and `LCR_DENOMINATOR_ZERO`.
  - Rules that would double count are refused: a type that is HQLA and a run-off liability,
    or either of those and an inflow.
- **BE-17.3** `GET /calculation-runs/{id}/liquidity` (`calc:read`) returns figures per
  currency, HQLA by level with its asset rows, and exclusions.
- **FE-17.1 Liquidity view.** It appears in the run detail for `lcr_hqla` runs and shows:
  - HQLA levels with their included and excluded rows;
  - outflows, gross inflows, and inflows after the cap;
  - the ratio, shown as "not computable" when it is NULL;
  - the LCR warnings.

  Masking hints follow the ladder's style. The view sits behind `NEXT_PUBLIC_LIQUIDITY_UI`.

## Checks (local; CI runs the full suites)

| Check | Result |
|---|---|
| ruff, ruff format, mypy, OpenAPI drift | clean / 157 files / no issues / up to date |
| Migration round trip (scratch DB) | 0018 → 0020 → 0018 → 0020; seed identical |
| Targeted backend tests (`test_calculations`, `test_rules`, WS1) | 6 passed; 48 collected (D-26) |
| Golden LCR case, hand-computed | EUR 1.2340 (cap binds; LOAN excluded); GBP 10.0000; USD NULL plus `LCR_DENOMINATOR_ZERO` |
| Governance | an LCR rule version is proposed, submitted and approved through Phase 16; a new run pins it and reproduces the golden values |
| Frontend lint, tsc, vitest | clean, clean, 344 passed |
| Playwright `liquidity.spec.ts` | 3 passed |

**Review.** One quick pass found no major issues. Two medium findings were fixed:
1. Rules that overlapped could double count a type. They are now refused.
2. Zero-value contributions counted toward the D-33 minimum. Masking now counts only
   non-zero contributions, for both LCR and the ladder. This is recorded as a D-33 amendment.

Because of fix 2, the maturity golden's EUR B03 outflows are now shown in clear: that cell's
only contribution is 0.00.

## Open items

- **LCR warnings live on `lcr_result.warning_codes`, not in `calculation_warning`.** Widening
  0018's CHECK would need a D-01 exception. The run's `warnings` list is therefore empty for
  LCR-only conditions, while the run status still says "with warnings".
- **An excluded asset shows `hqla_value` "0" in clear.** This follows D-33's "zeros stay
  clear"; its balance stays masked.
- **Inflows ignore `origin`, and past-due inflows are dropped silently.** Both are listed as
  limitations.
- **Level 2 caps are not applied.** `LCR_LEVEL_CAPS_NOT_APPLIED` is raised when a Level 2
  asset is included.
- **The liquidity read is not paginated.**
- **Duplicate e2e setup helpers.** `e2e/calc-support.ts` and `e2e/support.ts` both hold
  copies; cleaning this up is a follow-up.
