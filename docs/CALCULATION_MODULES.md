# Calculation modules

How calculation modules are declared, run and added (WS3; FC-03 to FC-07; D-31, D-32, D-35).
This file covers the module interface, the input and output contracts, the planned contracts,
the decimal and rounding policy, and each module's limitations.

## Statement: not regulatory, no Moody's parity

> **Illustrative prototype. Not a regulatory calculation; no parity with any vendor system is
> claimed.**

No module here is a regulatory calculation, and none claims parity with Moody's or any
other vendor system. Every seeded module, rule version and assumption set is PROVISIONAL
and ILLUSTRATIVE (D-35). This holds until a named financial owner approves the methodology
and the golden cases (Arch §12, §17). Do not use any result for client decisions.

## The module registry (seed rows)

Every module version is a `module_version` row under a `calculation_module` row. Both rows are
written by a tagged migration (D-11). There is no API to create a module (D-31 §1).
`GET /calculation-modules` lists every version, AVAILABLE and PLANNED.

| Module code | Version | `implementation_status` | Seeded by | Runnable |
|---|---|---|---|---|
| `maturity_ladder` | 1 | AVAILABLE | `0018_calculations` | yes |
| `lcr_hqla` | 1 | AVAILABLE | Phase 17 (BE-17.1) | yes, once Phase 17 is merged |
| `alm_irrbb` | 1 | PLANNED | `0021_planned_modules` | never |
| `collateral` | 1 | PLANNED | `0021_planned_modules` | never |
| `capital` | 1 | PLANNED | `0021_planned_modules` | never |

`lcr_hqla` is built in parallel in Phase 17. Its section below repeats the plan (BE-17.2,
D-35 §2). Check it against the Phase 17 seed at merge time.

A version can run only when **all** of these hold. Otherwise `POST /calculation-runs`
answers 409 `module_not_available`, writes no run, and audits a DENIED
`calculation_run.create` event. The `details.reason` field names which condition failed:

1. it is AVAILABLE (reason `planned` otherwise);
2. it is registered in code, in `app/calculations/modules/REGISTRY` (reason `not_implemented`);
3. every rule its input contract names has an ACTIVE rule version, and the baseline assumption
   set is ACTIVE (reason `configuration_incomplete`).

An id that is not in the caller's tenant registry gives reason `not_found`. The registry
response field `runnable` reports the same check.

## The module interface (D-31 §1)

The interface is in `backend/app/calculations/modules/base.py`. A module is a file in
the sub-package `backend/app/calculations/modules/` and exports these three things.

- **`SPEC: ModuleSpec`** declares the module:
  - `code`, `version`, `name` and `description`;
  - `engine_version`;
  - `input_contract` and `output_contract`, stored as they are on the seeded
    `module_version` row.
- **`run(inputs: ModuleInputs, parameters: PinnedParameters) -> ModuleResult`** is a pure
  function:
  - it does no database, file or network I/O;
  - it reads no clock, randomness or environment;
  - it holds no float, which a source grep in `test_calculations.py` enforces;
  - it does not import the AI layer.
- **`MODULE: RegisteredModule`** bundles the spec, the Decimal context the module runs under,
  its declared rounding (`None` means exact, so nothing is rounded) and the function.

The engine is `app/calculations/service.py`. It works in this order:

1. resolves the pins: the snapshot, the module version, the ACTIVE rule versions (as a
   content-addressed rule set), the ACTIVE assumption set and the schema and mapping versions;
2. writes the run as RUNNING in its own transaction;
3. feeds the frozen inputs in a stable order and runs the module under its context;
4. runs the post-checks and stores the outputs;
5. ends the run in one terminal status.

Every input is either used once or excluded with a warning code. Every aggregate must equal
the sum of its contributions, or the run is FAILED. A module whose contract is not met
returns `blocked=True`, and the run is BLOCKED.

### Input contract

`input_contract` is a JSON object, validated by `ModuleInputContract`:

| Key | Meaning |
|---|---|
| `schemas[]` | `{schema_code, required, fields[]}`: the canonical schemas a snapshot member provides, and the fields read. A `required` schema missing from the snapshot gives a BLOCKED run (`INPUT_SCHEMA_MISSING`). |
| `rules[]` | The rule definition codes the module reads. The run pins each one's ACTIVE version (D-32). |
| `assumption_keys[]` | The keys read from the pinned assumption set (none in v1). |
| `parameters[]` | The run parameters, for example `reporting_date`. |

### Output contract

`output_contract` is a JSON object, validated by `ModuleOutputContract`:

| Key | Meaning |
|---|---|
| `results[]` | The result tables written. They are append-only, and only inserted while the run is RUNNING. |
| `intermediates[]` | The `intermediate_value` codes. Each one references its domain row (D-31 §6). |
| `warning_codes[]` | The `calculation_warning` codes the module may raise. |

## Decimal and rounding policy (D-31 §2)

- Arithmetic is `Decimal` only, under the context the module declares.
  `app.core.decimals.exact_context()` traps Inexact, Rounded, InvalidOperation,
  DivisionByZero and Overflow, so any operation that would round raises instead (fail
  closed).
- The only rounding allowed is an explicit `quantize` with a `DeclaredRounding` that the
  module declares: an exponent and a mode. The default mode is ROUND_HALF_EVEN, which is
  PROVISIONAL.
- Values are stored as `NUMERIC` and returned as exact decimal text (`format(d, "f")`),
  never as a JSON number or a float.
- DECIMAL rule parameters must be stored as strings in JSONB (or decoded with
  `parse_float=Decimal`), because JSONB fractions decode as floats.
- Money and amounts carry an explicit currency. No module converts currencies (no FX in v1).

## Modules

### `maturity_ladder` v1 (AVAILABLE)

This is a per-currency maturity ladder (FC-04, D-35 §1).

**Contracts.**
- **Inputs:**
  - `cashflow_event` (`cashflow_id`, `instrument_id`, `due_date`, `amount`, `currency`,
    `origin`), required;
  - `instrument` (`instrument_id`, `instrument_type`), required.
- **Rules:** `maturity_buckets`, `cashflow_direction` and `past_due_treatment`.
- **Parameters:** `reporting_date`.
- **Outputs:** `liquidity_gap_result`, the intermediate `CASHFLOW_CONTRIBUTION`, and these
  warning codes: `AMBIGUOUS_INSTRUMENT_KEY`, `INSTRUMENT_NOT_IN_SNAPSHOT`, `MISSING_VALUE`,
  `NEGATIVE_AMOUNT`, `OUTSIDE_BUCKETS`, `PARAMETERS_INVALID`, `PAST_DUE_EXCLUDED`,
  `PAST_DUE_IN_FIRST_BUCKET`, `UNKNOWN_ORIGIN` and `UNMAPPED_INSTRUMENT_TYPE`.

**Rounding.** None: sums only, exact.

**Limitations.**
- It works per currency, with no FX and no cross-currency total.
- The direction comes from the instrument type (LOAN and BOND are inflows, DEPOSIT is an
  outflow). This is an illustrative asset and liability assumption, because
  `cashflow_event` v1 has no direction. A negative amount is excluded (`NEGATIVE_AMOUNT`).
- The buckets are illustrative calendar-day intervals: (0,1], (1,7], (7,30], (30,90],
  (90,180], (180,365], (365,730] and (730,∞). There are no business-day calendars.
- A flow due on the reporting date (day 0) is excluded with `OUTSIDE_BUCKETS`. The
  methodology owner must confirm this.
- Past-due flows are excluded with a warning by default (`EXCLUDE_WITH_WARNING`).
- Contractual and behavioural flows are both included and shown separately. No behavioural
  model generates flows.
- The D-33 masking rules apply on read. A figure built from fewer than
  `CALC_MIN_AGGREGATE_COUNT` contributions is masked for clearances below its inputs'.

### `lcr_hqla` v1 (AVAILABLE, Phase 17)

This is an illustrative LCR/HQLA module (FC-03, D-35 §2). It is built in parallel, so this
section repeats the plan, and Phase 17 owns the final contract.

**Contracts.**
- **Inputs:** position versions (`balance` as the value, PROVISIONAL), instrument versions
  (type) and cash flows within the horizon.
- **Outputs:** `hqla_asset_result` and `lcr_result`.

**Formulas.**
- HQLA = Σ eligible balance × (1 − haircut).
- Outflows = Σ balance × run-off.
- Inflows = min(Σ inflow × rate, cap × outflows).
- Ratio = HQLA / (outflows − inflows).

**Rounding.** The ratio is quantized to 4 dp with ROUND_HALF_EVEN (PROVISIONAL).

**Limitations.**
- It works per currency, with no FX.
- The value is the position balance, because the schema has no market value.
- BOND is the only HQLA type (Level 1, 0 haircut).
- There are no L2A or L2B caps. This is a documented limitation and raises a warning.
- A zero or negative denominator gives a NULL ratio and `LCR_DENOMINATOR_ZERO`.

### Planned contracts (FC-05, FC-06): `alm_irrbb`, `collateral`, `capital`

These are declared extension points only (WS3-P5). Each is seeded v1 PLANNED by migration
`0021_planned_modules`, with the input and output contracts below. Each one:
- has no code and no `REGISTRY` entry;
- has no rule definition or rule version;
- has no result table.

So they are never runnable and never presented as ready. Their contracts are a starting
point for the specification, not an agreed design. The schema codes, rule codes, result
tables and intermediate codes marked *(planned)* do not exist yet.

**`alm_irrbb` v1 (PLANNED).** Repricing gaps and rate-shock sensitivities of the banking book,
per currency (FC-05).
- **Inputs:**
  - `instrument` (`instrument_id`, `instrument_type`);
  - `position` (`instrument_id`, `balance`, `currency`);
  - `cashflow_event` (`cashflow_id`, `instrument_id`, `due_date`, `amount`, `currency`,
    `origin`);
  - `market_observation` *(planned)* (`curve_id`, `tenor`, `rate`, `currency`,
    `observed_at`).
- **Rules:** `repricing_buckets` and `rate_shock_scenarios` *(planned)*.
- **Assumption keys:** `behavioural_repricing` and `prepayment_rate`.
- **Parameters:** `reporting_date`.
- **Outputs:** `irrbb_repricing_gap_result` and `irrbb_sensitivity_result` *(planned)*, and
  the intermediates `REPRICING_CONTRIBUTION` and `SCENARIO_DISCOUNT_FACTOR`. No warning
  codes are declared yet.
- **Limitations:** not implemented. The following are later work and need a specification
  and reference cases:
  - rate scenarios;
  - curve construction;
  - behavioural repricing and prepayment models;
  - the full validated model.

**`collateral` v1 (PLANNED).** Haircut-adjusted collateral value allocated to secured
exposures, with eligibility and encumbrance treatment (FC-06).
- **Inputs:**
  - `collateral_asset` *(planned)* (`collateral_id`, `collateral_type`, `valuation`,
    `currency`, `eligible`, `encumbered`);
  - `collateral_link` *(planned)* (`collateral_id`, `instrument_id`, `allocated_amount`,
    `currency`);
  - `position` (`instrument_id`, `balance`, `currency`).
- **Rules:** `collateral_haircuts` and `collateral_eligibility` *(planned)*.
- **Parameters:** `reporting_date`.
- **Outputs:** `collateral_result` (Data Model §5) and the intermediate
  `COLLATERAL_ALLOCATION`. No warning codes are declared yet.
- **Limitations:** not implemented. Valuation sources, haircut schedules, allocation order
  and cross-currency treatment need specialist specifications.

**`capital` v1 (PLANNED).** Capital components and ratios over exposures, by capital
category (FC-06).
- **Inputs:**
  - `instrument` (`instrument_id`, `instrument_type`);
  - `position` (`instrument_id`, `balance`, `currency`);
  - `collateral_link` *(planned, optional)*.
- **Rules:** `capital_categories` and `risk_weights` *(planned)*.
- **Parameters:** `reporting_date`.
- **Outputs:** `capital_result` (Data Model §5) and the intermediate
  `RISK_WEIGHTED_EXPOSURE`. No warning codes are declared yet.
- **Limitations:** not implemented. The scope needs regulatory confirmation (Data Model §5),
  and production calculations need specialist specifications.

## How to add a module, or implement a planned one

A new module version is a code release plus a migration (D-31 §1). There is no API for it.

1. **Specify it.** Get a written methodology, the reference cases and a named domain-expert
   owner (WS3 backend plan: implement only after requirements and reference cases exist).
   Record any new rule parameter kind as an appended decision (D-32 §1).
2. **Write the code.** Add `backend/app/calculations/modules/<code>.py` with `SPEC`, a pure
   `run` and `MODULE`. Declare the context and any `DeclaredRounding`. Add new warning,
   intermediate and input-reference codes to `app/calculations/constants.py`. Register the
   module in `REGISTRY` in `modules/__init__.py`.
3. **Add a migration.** It must be additive and reversible (D-01, D-02) and tagged (D-11). It
   adds:
   - any result table, with the run guards of BE-15.2;
   - the `module_version` row as AVAILABLE, with the spec's contracts as a frozen copy;
   - its rule definitions and ACTIVE ILLUSTRATIVE rule versions (D-32 §7).

   `module_version` is append-only, so a PLANNED version is never switched to AVAILABLE.
   Implementing `alm_irrbb`, `collateral` or `capital` means seeding **v2** as AVAILABLE,
   and v1 stays PLANNED as the record.
4. **Write the tests (D-26 budget).** Fold them into `test_calculations.py`:
   - the frozen copy in the migration equals `SPEC`;
   - the golden loop gains engineering fixtures in `backend/app/calculations/golden/`
     (BE-18.2), marked `ENGINEERING — awaiting domain-expert approval`;
   - the guard loops cover the new tables.
5. **Update the docs.** Update this file (the registry table, the contracts and the
   limitations), `docs/ARCHITECTURE.md` and `docs/RETENTION.md`. If the endpoints change,
   update the OpenAPI contract too.
