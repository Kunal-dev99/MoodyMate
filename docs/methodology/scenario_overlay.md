# Methodology: scenario overlay (stressed `maturity_ladder` runs)

Documented from the code as built (BE-30.1, D-56). Nothing here changes or adds methodology.
File references are `path:line` from the repository root, as of Phase 30.

## Intended use

An illustrative stress exploration on synthetic data (D-44, D-45, D-46). A scenario version
holds 1 to 5 shocks, each from a closed list of three templates. The overlay maps a baseline
`maturity_ladder` run's inputs to a stressed copy, the unchanged ladder module runs on it, and
the comparison reads baseline versus stressed figures per currency and bucket. It is a timing
and amount exploration tool, not a stress test of record, and not for client decisions.

## Status

- The overlay is not a registered module: a stressed run is a `maturity_ladder` v1 run that
  pins a scenario version and a shock subset (D-45 §4). Its validation status is that of
  `maturity_ladder` v1: **ILLUSTRATIVE** (`backend/app/rules/constants.py:145-151`), and the
  comparison's `context` is built from the baseline run's pins
  (`backend/app/orchestration/service.py`, `comparison`).
- Approval labels on versions and runs: "Exploration, not approved" or "Approved scenario
  (illustrative)" (`backend/app/orchestration/constants.py`, `ApprovalLabel`, D-44 §8).
- Statement carried by every scenario response (`backend/app/orchestration/constants.py:108-111`):
  "Illustrative stress exploration, not a regulatory stress test. Not a regulatory
  calculation; no parity with any vendor system is claimed." Stressed runs and the comparison's
  `context` also carry the module statement (`backend/app/rules/constants.py:98-101`).

## Formula

`apply` (`backend/app/calculations/scenario_overlay.py:365-457`) applies the run's shocks in
declared order to the parts of each in-scope flow; a part may be split again by a later shock.
For one matched part of amount `a` due on day `d` (`_apply_one`, `:338-362`):

- **`DELAYED_INFLOWS`** (INFLOW types): `moved = f x a` is due on `d + delay_days`;
  `stayed = a - moved` stays on `d`. With `f = 1` the whole part moves, no split (`:351-362`).
- **`DEPOSIT_WITHDRAWAL`** (OUTFLOW types): the part stays (`a`) and an ADDED part of `x x a`
  is due the same day, so the flow's outflow is `a x (1 + x)`. Stored as two parts, never one
  enlarged part. The only template that changes amounts (`:341-350`).
- **`FUNDING_UNAVAILABLE`** (INFLOW types): for parts due in days (0, `unavailable_days`],
  `moved = f x a` moves to day `unavailable_days + 1` and the rest stays; later parts are
  untouched (`:326`, `:351-362`).

Part ids are deterministic: the root part keeps its event id, every other part is
`uuid5(PART_NAMESPACE, "<event id>/<path>")` (`:67`, `:249-253`).

The comparison (D-46; `_comparison`, `backend/app/orchestration/service.py:1354-1546`) is
computed on read: per currency and bucket, baseline and stressed inflows, outflows, net gap
and cumulative gap, and `delta = stressed - baseline` by exact Decimal subtraction.
`turns_negative_net` / `turns_negative_cumulative` are `baseline >= 0 and stressed < 0`
(`:1445-1452`). `breaks_only_combined` is true when the combined run's net gap is negative while
the baseline's and every single-shock run's are not; it is null when a figure it reads is
masked or when it is not assessable (no combined run compared, or fewer single-shock runs
compared than the version has shocks, so always null for k = 1) (`:1469-1486`).

Two post-checks are recomputed independently (D-45 §6): `SCENARIO_OVERLAY` (the overlay
recomputed from the unstressed inputs equals what the run used, `scenario_overlay.py:477-484`)
and `SCENARIO_CONSERVATION` (per flow, parts sum to `a` for timing templates and to
`a x (1 + x)` per applied withdrawal; per currency and direction, the stressed total equals
the baseline total plus the declared added amounts, `:487-538`).

## Units

- Amounts: exact decimal text in the ISO 4217 currency of the row, never converted; no FX and
  no cross-currency total (`backend/app/core/decimals.py:55-57`).
- Fractions `f` and `x`: exact decimal text, 0 < value <= 1, at most 4 decimal places
  (`scenario_overlay.py:105-114`; `FRACTION_SCALE`, `DECIMAL_TEXT_PATTERN`,
  `backend/app/orchestration/constants.py:117`, `:124`).
- Days: calendar days after the reporting date (`delay_calendar` is `CALENDAR_DAYS` only,
  `scenario_overlay.py:137-138`).
- No ratio: the comparison's `context.units.ratio` is null. The comparison's `context` is
  its baseline (maturity_ladder) run's, so `context.units` reads, verbatim
  (`backend/app/calculations/constants.py:176-180`, `CONTEXT_*`):
  - `amount`: "Exact decimal, in the ISO 4217 currency of its row (no FX conversion)"
  - `ratio`: null
  - `days`: "Calendar days after the reporting date"

## Inputs

- The baseline run: a succeeded, non-stressed `maturity_ladder` run (`invalid_baseline`
  otherwise, `backend/app/orchestration/constants.py:232`). A stressed run copies its pins
  exactly: snapshot, reporting date, module version, rule set and assumption set (D-45 §1).
- From the pinned rule set: `cashflow_direction` (which template a type can match) and
  `maturity_buckets` (which flows are in scope) (`scenario_overlay.py:281-304`).
- The scenario version's shocks and the run's shock subset (`:176-186`). For k > 1 shocks a
  scenario run makes k + 1 member runs, each shock alone then all combined; for k = 1 one run
  (`:541-546`).
- Selectors (`:307-328`): the instrument type (required), and optionally `currency`, `origin`,
  a due window `due_from_day` / `due_to_day`, `instrument_keys` (at most 50, picked by the
  analyst) and `source_system_code`.

## Exclusions and warnings

- Only flows that are complete (due date, `amount >= 0`, currency, a known origin, an
  instrument of the snapshot with a mapped type) and placed in a bucket (not past due, not
  outside the buckets) are shocked. Others pass through unchanged and are counted
  (`scenario_overlay.py:281-304`, `:391-397`).
- Per shock, value-free counts (`ShockCount`, `:223-235`): matched flows, parts produced,
  `direction_mismatches` (a shock whose type has the other direction under the pinned map
  matches nothing), `skipped_past_due` (days <= 0) and `skipped_incomplete`.
- A shock that matches no flow of the baseline is refused `no_matching_cashflows`
  (`backend/app/orchestration/constants.py:234`).
- An overlay that cannot be applied (an invalid shock, more than 32 parts per flow, a delay
  past the last date) raises `OverlayError` and the run is BLOCKED with `PARAMETERS_INVALID`
  (`scenario_overlay.py:77-78`, `:416-417`, `:440-443`).
- The ladder module then applies its own exclusions to the stressed inputs
  (see `maturity_ladder.md`).

## Assumptions

The overlay seeds no rules of its own. It reads the baseline's pinned rule versions, whose
seeds are:

- **`maturity_buckets`** (`backend/app/db/migrations/versions/0018_calculations.py:158-177`):
  B01 (0,1], B02 (1,7], B03 (7,30], B04 (30,90], B05 (90,180], B06 (180,365], B07 (365,730],
  B08 (730,inf), calendar days after the reporting date.
- **`cashflow_direction`** (`0018_calculations.py:178-185`): `BOND` and `LOAN` -> INFLOW,
  `DEPOSIT` -> OUTFLOW. So `DELAYED_INFLOWS` and `FUNDING_UNAVAILABLE` can match `BOND` or `LOAN`
  flows, and `DEPOSIT_WITHDRAWAL` can match `DEPOSIT` flows (`TEMPLATE_DIRECTION`,
  `scenario_overlay.py:68-72`).
- **`past_due_treatment`** (`0018_calculations.py:186-193`): `EXCLUDE_WITH_WARNING`; past-due
  flows are never shocked under any treatment.

Template bounds are code constants (`backend/app/orchestration/constants.py:115-119`, D-44 §1-4):
1 to 5 shocks, at most 50 instrument keys, fractions of scale 4 in (0, 1], `delay_days` 1 to
3650, `unavailable_days` 1 to 365; at most 32 parts per flow (`:250`). The `+15%` style
`increase_fraction` bound is PROVISIONAL (D-44 §2).

## Rounding

None. `moved = f x a` and `stayed = a - moved` are exact under `exact_context()`: a fraction of
scale 4 times an amount is exact (`scenario_overlay.py:27-28`, `:557-560`;
`backend/app/core/decimals.py:31-37`). Comparison deltas are exact subtraction (D-46 §1).
Because parts may carry more decimal places than the source field's scale (e.g. `0.3333 x
100.00`), they are stored and read as exact decimal text, never re-quantized.

## Limitations

- Timing shocks only move money; lost funding (reducing an amount) is not modelled (D-44 §2).
- No cash-flow type field in `cashflow_event` v1: templates select by instrument type
  (`LOAN_REPAYMENT` maps to `LOAN`, D-44 §2).
- `source_system_code` matches all of a snapshot's flows or none in v1, since a snapshot holds
  one import per schema code (D-44 §3).
- Counterparty selection is not possible in v1 (D-44 §3).
- Past-due and out-of-bucket flows are never shocked.
- Comparison deltas changed by fewer than `CALC_MIN_AGGREGATE_COUNT` distinct non-zero flows
  are masked, and differencing scenarios against the baseline can still narrow down amounts
  (D-46 §3, §5).
- Engineering golden cases are hand-computed fixtures marked
  `ENGINEERING — awaiting domain-expert approval` (D-35 §4); none is an approved golden case.

## Open client decisions

These are client hard stops (D-56 §6, D-46 §5); engineering does not decide them:

- SME review of this document and of the templates.
- Independently approved golden packs.
- Which stress templates, selectors and bounds the client needs (including lost funding and
  counterparty or funding-facility selection).
- Day-0, past-due, missing, negative and out-of-horizon cash flows under stress.
- The FX method and whether any cross-currency figure is wanted.
- Residual differencing risk before production.
- Intended use, jurisdiction and reporting basis.
