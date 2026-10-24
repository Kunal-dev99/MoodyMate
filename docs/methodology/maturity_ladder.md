# Methodology: `maturity_ladder` v1

Documented from the code as built (BE-30.1, D-56). Nothing here changes or adds methodology.
File references are `path:line` from the repository root, as of Phase 30.

## Intended use

An illustrative prototype of a per-currency maturity ladder on synthetic data (D-35 §1,
WS3-P4). It shows how cash flows of a pinned dataset snapshot fall into day buckets after a
reporting date, with inflows, outflows, net gap and cumulative gap per bucket. It is a
demonstration of pinning, lineage and reproducibility. It is not for client decisions
(D-35, Consequences).

## Status

- Implementation status: AVAILABLE (`backend/app/db/migrations/versions/0018_calculations.py:64`
  tag `0018_calculations`; registered in code at
  `backend/app/calculations/modules/maturity_ladder.py:409`).
- Validation status: **ILLUSTRATIVE** (`MODULE_VALIDATION[("maturity_ladder", 1)]`,
  `backend/app/rules/constants.py:145-151`). No API sets it; promoting it is a client hard stop
  (D-56 §3).
- Every seeded rule version is PROVISIONAL and ILLUSTRATIVE.
- Statement carried by every run, read and result context
  (`backend/app/rules/constants.py:98-101`): "Illustrative prototype. Not a regulatory
  calculation; no parity with any vendor system is claimed."

## Formula

Per currency `c` and bucket `b` of the pinned `maturity_buckets` rule, over the placed cash
flows (`backend/app/calculations/modules/maturity_ladder.py:325-351`):

- `inflows_contractual(c, b)` = sum of `amount` of placed flows with direction INFLOW and
  origin CONTRACTUAL; likewise `inflows_behavioural`, `outflows_contractual`,
  `outflows_behavioural` (`:333-342`).
- `inflows = inflows_contractual + inflows_behavioural`;
  `outflows = outflows_contractual + outflows_behavioural` (`:343`).
- `net_gap = inflows - outflows` (`:344`).
- `cumulative_gap(c, b)` = running sum of `net_gap` over the buckets of `c` in bucket order
  (`:330`, `:345`).
- Every bucket of every currency that has at least one contribution gets a row, even when
  its sums are zero (`:329-350`).

Placement of one flow (`:218-322`): `days = due_date - reporting_date` in calendar days
(`:275`); the bucket is the one with `lower_exclusive < days <= upper_inclusive`, the last
bucket open above (`backend/app/calculations/modules/base.py:120-123`).

Direction comes from the instrument type through the pinned `cashflow_direction` map
(`maturity_ladder.py:264`), because `cashflow_event` v1 has no direction field.

## Units

- Amounts: exact decimal text in the ISO 4217 currency of the row, never converted
  (`backend/app/core/decimals.py:55-57`). There is no FX and no cross-currency total.
- Days: calendar days after the reporting date (`maturity_ladder.py:275`; rule parameter
  schema unit `calendar days after reporting date`,
  `backend/app/db/migrations/versions/0018_calculations.py:173`).
- No ratio: `context.units.ratio` is null for this module
  (`backend/app/calculations/constants.py`, `CONTEXT_RATIO_UNITS`).
- The `context` block of every result read repeats these units and the pins
  (`backend/app/calculations/service.py:2336`, D-56 §5). `context.units` reads, verbatim
  (`backend/app/calculations/constants.py:176-180`, `CONTEXT_*`):
  - `amount`: "Exact decimal, in the ISO 4217 currency of its row (no FX conversion)"
  - `ratio`: null
  - `days`: "Calendar days after the reporting date"

## Inputs

Input contract (`maturity_ladder.py:93-116`):

- `cashflow_event` (required): `cashflow_id`, `instrument_id`, `due_date`, `amount`,
  `currency`, `origin`. `amount` is DECIMAL(20, 2) in the seeded schema
  (`backend/app/db/migrations/versions/0007_schema_seed.py:104`).
- `instrument` (required): `instrument_id`, `instrument_type`.
- Rules: `maturity_buckets`, `cashflow_direction`, `past_due_treatment`.
- Parameters: `reporting_date`. No assumption keys (the `baseline` assumption set is empty).

## Exclusions and warnings

Each cash flow is checked in this order (`maturity_ladder.py:218-322`); the first that applies
excludes it with a WARNING-severity code:

| Order | Condition | Code | Where |
|---|---|---|---|
| 1 | `due_date`, `amount` or `currency` missing | `MISSING_VALUE` | `:229-244` |
| 2 | `origin` not CONTRACTUAL or BEHAVIOURAL | `UNKNOWN_ORIGIN` | `:245-249` |
| 3 | `amount < 0` | `NEGATIVE_AMOUNT` | `:250-256` |
| 4 | instrument key not in the snapshot | `INSTRUMENT_NOT_IN_SNAPSHOT` | `:257-263` |
| 5 | instrument type not a key of `cashflow_direction` | `UNMAPPED_INSTRUMENT_TYPE` | `:264-274` |
| 6 | past due (`days < 0`) under `EXCLUDE_WITH_WARNING` | `PAST_DUE_EXCLUDED` | `:284-291` |
| 6' | past due under `INCLUDE_IN_FIRST_BUCKET` | counted in the first bucket, `PAST_DUE_IN_FIRST_BUCKET` | `:292-300` |
| 7 | not past due and in no bucket (e.g. day 0 under the seed) | `OUTSIDE_BUCKETS` | `:301-310` |

The run is BLOCKED (ERROR severity, no results) when a rule the module reads is not pinned or
its parameters are invalid (`PARAMETERS_INVALID`, `:356-383`), or when an instrument key names
more than one version in the snapshot (`AMBIGUOUS_INSTRUMENT_KEY`, `:384-389`).

A zero amount is placed like any other flow (`:23-25`).

## Assumptions

Each seeded rule (frozen in `backend/app/db/migrations/versions/0018_calculations.py`, equal to
`SEEDED_RULES` in `backend/app/rules/constants.py:187`):

- **`maturity_buckets`** (`0018_calculations.py:158-177`): eight buckets, in calendar days
  after the reporting date, lower bound exclusive and upper inclusive:
  B01 (0,1], B02 (1,7], B03 (7,30], B04 (30,90], B05 (90,180], B06 (180,365], B07 (365,730],
  B08 (730,inf).
- **`cashflow_direction`** (`0018_calculations.py:178-185`): `BOND` -> INFLOW, `LOAN` ->
  INFLOW, `DEPOSIT` -> OUTFLOW. An illustrative asset/liability assumption.
- **`past_due_treatment`** (`0018_calculations.py:186-193`): `EXCLUDE_WITH_WARNING`. The other
  allowed value is `INCLUDE_IN_FIRST_BUCKET` (`backend/app/rules/constants.py:81-85`).

Both CONTRACTUAL and BEHAVIOURAL origins are included and shown separately. Replacing any value
is a rule-version change through the governed path (D-32 §3), not a code change.

## Rounding

None. Every operation is a sum or difference under `exact_context()`, which traps `Inexact` and
`Rounded` so any operation that would round raises instead
(`backend/app/core/decimals.py:31-37`; `maturity_ladder.py:409`, `rounding=None`). Figures are
written and read as exact decimal text (`decimals.py:55-57`). Amounts are held at validation if
they have more decimal places than the field's scale; they are never rounded on the way in
(`backend/app/mapping/transforms/engine.py:375-381`, `scale_exceeded`). A 3-dp amount whose third decimal is 0 (e.g. `1.230`) quantizes exactly to scale 2 and is accepted as `1.23`; only a non-zero digit beyond the scale (e.g. `1.235`) is held as `scale_exceeded`.

## Limitations

- Per currency, no FX conversion, no all-currency total.
- Direction is inferred from instrument type only; a signed amount is not interpreted.
- A flow due on the reporting date (day 0) falls outside the seeded first bucket (0,1] and is
  excluded with `OUTSIDE_BUCKETS`; under `INCLUDE_IN_FIRST_BUCKET` an overdue flow would be
  counted while a day-0 flow would not (`maturity_ladder.py:276-281`).
- Ladder figures built from fewer than `CALC_MIN_AGGREGATE_COUNT` contributions are masked
  like those contributions (D-33).
- Engineering golden cases are hand-computed fixtures marked
  `ENGINEERING — awaiting domain-expert approval` (D-35 §4); none is an approved golden case.

## Open client decisions

These are client hard stops (D-56 §6); engineering does not decide them:

- SME review of this document and of the module.
- Independently approved golden packs.
- The client's bucket set and day-count convention.
- Day-0, past-due, missing, negative and out-of-horizon cash flows.
- The cash-flow direction basis (an explicit direction field versus the instrument-type map).
- The FX method and whether any cross-currency total is wanted.
- Intended use, jurisdiction and reporting basis.
