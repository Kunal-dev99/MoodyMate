# Methodology: `lcr_hqla` v1

Documented from the code as built (BE-30.1, D-56). Nothing here changes or adds methodology.
File references are `path:line` from the repository root, as of Phase 30.

## Intended use

An illustrative prototype of a per-currency liquidity coverage ratio on synthetic data
(D-35 §2, WS3-P4): high-quality liquid assets by level, run-off outflows and capped inflows
within a horizon, and their ratio. It demonstrates pinning, lineage and reproducibility. It is
not for client decisions (D-35, Consequences).

## Status

- Implementation status: AVAILABLE (`backend/app/db/migrations/versions/0020_lcr_results.py:58`
  tag `0020_lcr_hqla`; registered in code at
  `backend/app/calculations/modules/lcr_hqla.py:540`).
- Validation status: **ILLUSTRATIVE** (`MODULE_VALIDATION[("lcr_hqla", 1)]`,
  `backend/app/rules/constants.py:145-151`). No API sets it; promoting it is a client hard stop
  (D-56 §3).
- Every seeded rule version is PROVISIONAL and ILLUSTRATIVE.
- Statement carried by every run, read and result context
  (`backend/app/rules/constants.py:98-101`): "Illustrative prototype. Not a regulatory
  calculation; no parity with any vendor system is claimed."

## Formula

Per currency `c` (every currency with an asset row, an outflow or an inflow,
`backend/app/calculations/modules/lcr_hqla.py:470-493`), in `currency_row` (`:403-467`):

- Asset value of an included position: `balance x (1 - haircut)` (`:333-345`).
- `HQLA_L1`, `HQLA_L2A`, `HQLA_L2B` = sums of included asset values per level;
  `HQLA = HQLA_L1 + HQLA_L2A + HQLA_L2B` (`:412-421`).
- `outflows` = sum of `balance x run_off` over run-off positions (`:314-318`, `:423`).
- `inflows_before_cap` = sum of `amount x inflow_rate` over inflow cash flows (`:400`, `:425`).
- `cap_amount = inflow_cap x outflows` (`:426`).
- `inflows_after_cap = min(inflows_before_cap, cap_amount)`; the cap is applied only when
  `inflows_before_cap > cap_amount` (strictly), so inflows exactly at the cap are not flagged
  as capped (`:427-428`).
- `net_outflows = outflows - inflows_after_cap` (`:429`).
- `ratio = HQLA / net_outflows`, rounded once (see Rounding) when `net_outflows > 0`;
  otherwise `ratio` is null and the row carries `LCR_DENOMINATOR_ZERO` (`:431-435`).

## Units

- Amounts (balances, HQLA, outflows, inflows): exact decimal text in the ISO 4217 currency of
  the row, never converted (`backend/app/core/decimals.py:55-57`). There is no FX and no
  all-currency total (`backend/app/calculations/router.py:558-567`).
- Ratio: a dimensionless fraction (e.g. `1.2500`), 4 dp ROUND_HALF_EVEN (`lcr_hqla.py:96`).
- Rates, haircuts and the cap: fractions in [0, 1], exact decimal text (`lcr_hqla.py:178-186`).
- Days: calendar days after the reporting date (`lcr_hqla.py:397-399`; rule parameter schema
  unit, `backend/app/db/migrations/versions/0020_lcr_results.py:172`).
- The `context` block of every result read repeats these units and the pins
  (`backend/app/calculations/service.py:2336`, D-56 §5). `context.units` reads, verbatim
  (`backend/app/calculations/constants.py:176-180`, `CONTEXT_*`):
  - `amount`: "Exact decimal, in the ISO 4217 currency of its row (no FX conversion)"
  - `ratio`: "Dimensionless fraction, 4 dp ROUND_HALF_EVEN"
  - `days`: "Calendar days after the reporting date"

## Inputs

Input contract (`lcr_hqla.py:119-147`):

- `position` (required): `position_id`, `instrument_id`, `balance`, `currency`. `balance` is
  DECIMAL(20, 2) in the seeded schema
  (`backend/app/db/migrations/versions/0007_schema_seed.py:95`).
- `instrument` (required): `instrument_id`, `instrument_type`.
- `cashflow_event` (required): `cashflow_id`, `instrument_id`, `due_date`, `amount`,
  `currency` (`amount` DECIMAL(20, 2), `0007_schema_seed.py:104`).
- Rules: `hqla_level`, `hqla_haircut`, `lcr_outflow_run_off`, `lcr_inflow_rate`,
  `lcr_inflow_horizon_days`, `lcr_inflow_cap`.
- Parameters: `reporting_date`. No assumption keys.

## Exclusions and warnings

Each position, in this order (`lcr_hqla.py:275-345`):

| Order | Condition | Outcome | Where |
|---|---|---|---|
| 1 | `balance` or `currency` missing | excluded, `MISSING_VALUE` | `:280-292` |
| 2 | `balance < 0` | excluded, `NEGATIVE_AMOUNT` | `:293-296` |
| 3 | instrument key not in the snapshot | excluded, `INSTRUMENT_NOT_IN_SNAPSHOT` | `:297-304` |
| 4 | instrument has no type | excluded, `UNMAPPED_INSTRUMENT_TYPE` | `:305-313` |
| 5 | type has a run-off rate | an outflow, `balance x run_off` | `:314-318` |
| 6 | type has no HQLA level | asset row excluded, `NOT_HQLA_ELIGIBLE`, value 0 | `:319-332` |
| 7 | otherwise | asset row included at its level | `:333-345` |

Each cash flow, in this order (`lcr_hqla.py:348-400`):

| Order | Condition | Outcome | Where |
|---|---|---|---|
| 1 | instrument key not in the snapshot | excluded, `INSTRUMENT_NOT_IN_SNAPSHOT` | `:356-363` |
| 2 | instrument has no type | excluded, `UNMAPPED_INSTRUMENT_TYPE` | `:364-372` |
| 3 | type has no inflow rate | out of scope, no warning (counted in `out_of_scope`) | `:373-375` |
| 4 | `due_date`, `amount` or `currency` missing | excluded, `MISSING_VALUE` | `:376-392` |
| 5 | `amount < 0` | excluded, `NEGATIVE_AMOUNT` | `:393-396` |
| 6 | not due in (0, horizon] days | out of scope, no warning | `:397-399` |
| 7 | otherwise | an inflow, `amount x rate` | `:400` |

Per-currency warnings (`lcr_hqla.py:430-437`): `LCR_DENOMINATOR_ZERO` when `net_outflows <= 0`
(ratio null); `LCR_LEVEL_CAPS_NOT_APPLIED` whenever a Level 2A or 2B asset is included.

The run is BLOCKED with `PARAMETERS_INVALID` when a pinned rule is missing or invalid: a decimal
parameter that is not a JSON string of plain decimal text, a fraction above 1, a levelled type
without a haircut, or an instrument type given two roles (HQLA, run-off liability, inflow)
(`:178-252`, `:496-501`). It is BLOCKED with `AMBIGUOUS_INSTRUMENT_KEY` when a key names more
than one instrument version (`:502-507`).

## Assumptions

Each seeded rule (frozen in `backend/app/db/migrations/versions/0020_lcr_results.py`, equal to
`LCR_SEEDED_RULES` in `backend/app/rules/constants.py:254`):

- **`hqla_level`** (`0020_lcr_results.py:139-146`): `BOND` -> L1. Any type that is not a key is
  not HQLA.
- **`hqla_haircut`** (`0020_lcr_results.py:147-154`): `BOND` -> `0.0000`.
- **`lcr_outflow_run_off`** (`0020_lcr_results.py:155-161`): `DEPOSIT` -> `0.1000`.
- **`lcr_inflow_rate`** (`0020_lcr_results.py:162-168`): `LOAN` -> `0.5000`.
- **`lcr_inflow_horizon_days`** (`0020_lcr_results.py:169-176`): `30`; inflows are cash flows due
  (0, 30] calendar days after the reporting date.
- **`lcr_inflow_cap`** (`0020_lcr_results.py:177-184`): `0.7500` of outflows.
- The value of an asset is its position `balance`, because the schema has no market value
  (PROVISIONAL, `lcr_hqla.py:16`, `backend/app/calculations/router.py:561-562`).

Replacing any value is a rule-version change through the governed path (D-32 §3), not a code
change.

## Rounding

Only the ratio is rounded, once, to 4 decimal places with ROUND_HALF_EVEN
(`RATIO_ROUNDING`, `lcr_hqla.py:96`; declared on the module at `:540`). The division is exact
rational arithmetic on the operands' integer ratios with a sticky digit, so there is no double
rounding (`backend/app/calculations/modules/base.py:299-319`; `quantize`,
`backend/app/core/decimals.py:49-52`). Every product and sum runs under `exact_context()`,
which traps `Inexact` and `Rounded` (`decimals.py:31-37`). Amounts with more decimal places
than their field's scale are held at validation, never rounded
(`backend/app/mapping/transforms/engine.py:375-381`, `scale_exceeded`). A 3-dp amount whose third decimal is 0 (e.g. `1.230`) quantizes exactly to scale 2 and is accepted as `1.23`; only a non-zero digit beyond the scale (e.g. `1.235`) is held as `scale_exceeded`.

## Limitations

From the read's own limitations list (`backend/app/calculations/router.py:558-567`):

- Level 2A/2B caps are not applied (flagged `LCR_LEVEL_CAPS_NOT_APPLIED`).
- An asset's value is its position balance: there is no market value in the schema.
- Per currency, no FX conversion, no all-currency total.
- Inflows are cash flows due (0, horizon] days after the reporting date; outflows are position
  balances x run-off. No other categories are modelled.

Also:

- One role per instrument type, so a bond's coupons cannot be inflows while the bond is HQLA
  (`lcr_hqla.py:244-251`).
- Figures built from fewer than `CALC_MIN_AGGREGATE_COUNT` contributions are masked (D-33).
- Engineering golden cases are hand-computed fixtures marked
  `ENGINEERING — awaiting domain-expert approval` (D-35 §4); none is an approved golden case.

## Open client decisions

These are client hard stops (D-56 §6); engineering does not decide them:

- SME review of this document and of the module.
- Independently approved golden packs.
- L2A/L2B caps.
- The HQLA valuation basis (balance versus market value).
- Deposit classes and run-off rates.
- Inflow categories, rates, horizon and cap.
- Day-0, past-due, missing, negative and out-of-horizon cash flows.
- The FX method and whether any all-currency figure is wanted.
- Intended use, jurisdiction and reporting basis.
