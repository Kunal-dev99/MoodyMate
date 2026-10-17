# Kickoff 03 — WS3 Financial Domain Model & Calculation Services

Instance of `docs/MASTER_PROMPT_FEATURE_KICKOFF.md`.

## 1. Requirement

- **Feature:** WS3 financial domain model and calculation services
- **Workstream / feature IDs:** WS3 FC-01…FC-08, with the paired contracts FE-FC-01…05 and
  BE-FC-01…05. The Architecture §16 global phases are G-P2/G-P3 (domain → first calculation
  slice → rule/assumption registry and module interface). The WS3 plans call them WS3-P0…P6.
- **Problem and user:** WS2 now turns uncertain source files into validated canonical
  records. Risk analysts need to run deterministic, reproducible liquidity calculations on
  those records. Approvers need to control every change to methodology parameters. Auditors
  need to trace any figure back to its source row and to the exact versions that produced it.
  The client's real methodology is not known yet, so calculations sit behind a pluggable module
  interface. All finance parameters are versioned data, seeded as illustrative values.
- **In scope:**
  - projecting accepted canonical records into immutable domain versions (instrument,
    position, cash-flow event), with lineage to the canonical record, plus an instrument
    catalogue
  - a cross-import, content-addressed dataset snapshot that a run pins
  - a module and rule registry: module versions with declared input and output contracts,
    rule definitions with typed parameter schemas from a closed list of kinds, immutable rule
    versions with maker-checker activation, and read-only assumption sets
  - a deterministic calculation framework:
    - one run lifecycle;
    - an immutable manifest that pins snapshot, schema and mapping versions, the rule set,
      the assumption set, the module and the engine;
    - Decimal-only arithmetic with explicit rounding;
    - post-run checks, warnings, intermediate values, idempotency and reproducible re-runs.
  - an illustrative maturity ladder v1 and LCR/HQLA prototype v1 on synthetic data
  - ALM/IRRBB, collateral and capital as declared extension points only (never runnable)
  - the Financial Domain and Calculations workspaces in the frontend (they replace the
    "planned" placeholders)
  - carried over from Feature 02:
    - upload hardening: the D-25 §1 decoded-content cap, the XLSX row-number gap and openpyxl
      warnings;
    - the `/schemas` endless-loading fix.
- **Out of scope:** see "Deferred (out of scope for Feature 03)" in `docs/DELIVERY_PLAN.md`.
  In short:
  - real or approved methodology and golden cases, regulatory LCR features, FX and market-data
    feeds;
  - assumption authoring, schedule generation from contractual terms, reference entities;
  - ALM/IRRBB, collateral and capital calculations;
  - async runs, scenarios, the lineage graph, benchmarks and reports (WS4);
  - AI (WS5).
- **Acceptance (business terms):**
  - A data steward projects a validated synthetic import into the domain. Anyone allowed can
    browse instruments, their versions and cash flows, with amounts masked by classification
    and a link back to the import row.
  - A risk analyst picks projected imports to form a snapshot and runs the maturity ladder for
    a reporting date. Before submitting, the analyst sees exactly which rule, assumption,
    module and engine versions will be pinned.
  - The run shows its manifest, warnings and a per-currency ladder. Any bucket drills down to
    the cash flows, instruments and source rows behind it. Re-running with the same pins gives
    identical results, and nothing is ever overwritten.
  - An analyst proposes new bucket boundaries, and a different person (an approver) activates
    them. New runs use the new version, while old runs and their re-runs keep the old one.
  - The LCR/HQLA prototype shows HQLA components, outflows, capped inflows and a ratio per
    currency. It is clearly labelled illustrative. Planned modules are visible but cannot be
    run.
  - Every action is permission-checked on the server, tenant-scoped and audited. No result
    is described as regulatory or as matching any vendor system.

## 2. Pre-plan reading (done 2026-10-17)

1. **Architecture and plan text.**
   - `docs/ARCHITECTURE.md`: module rows for domain, rules and calculations.
   - `docs/architecture_spec.txt`:
     - §6.2 calculation flow (:190-206);
     - §7 component responsibilities (:223-253);
     - §12 correctness and reproducibility (:402-425);
     - §17 release gates (:577);
     - Data Model §4–5 entities (:896-984);
     - candidate attributes (:1038-1083);
     - §7 reproducibility (:1130-1141);
     - §9 enums (:1176-1178);
     - §10 manifest (:1188-1215).
   - `docs/workstream_plans_spec.txt`: the WS3 backend (:646-795) and frontend (:796-) plans.
2. **Constraining decisions.** D-01…D-08, D-10, D-11, D-13, D-14, D-19 (in spirit), D-21,
   D-22, D-25, D-26, D-27 and D-28 (still proposed). See the Feature 03 header in
   `docs/DELIVERY_PLAN.md`. New proposals: D-29…D-35.
3. **Project memory.** PCS is not installed (D-0), and `.project-memory/events.jsonl` was not
   needed for this plan.
4. **Codebase gaps.** They are listed under "Pre-plan findings" in the Feature 03 section of
   `docs/DELIVERY_PLAN.md`. The main points:
   - the WS3 modules are empty stubs, and the migration head is 0016;
   - the backend suite has 49 tests, at the D-26 cap;
   - D-25 §1 is not implemented;
   - `instrument` v1 `maturity_date` is free text, and `cashflow_event` has no direction.

## 3. Standing rules

As in the master prompt (D-01…D-04), plus:
- synthetic data only outside production (D-14);
- no AI anywhere on the calculation path;
- Decimal-only arithmetic with declared rounding, and no float anywhere in `app/calculations/`
  or `app/domain/`;
- deterministic, pure modules;
- immutable runs and versions;
- maker-checker on rule activation;
- every finance parameter PROVISIONAL or ILLUSTRATIVE until the client's financial owner
  approves it;
- no claim of regulatory certification or Moody's parity;
- the backend test suite stays under 50 tests (D-26), with the budget per phase in the plan.

## 4. Plan

Delivered to `docs/DELIVERY_PLAN.md` as "Feature 03", Phases 14–18 (numbering per D-08):

- 14: domain projection and the instrument catalogue (plus the carried-over WS2 hardening,
  which may ship first as its own PR);
- 15: snapshot, pinned run and the maturity ladder end to end;
- 16: governed rule changes and the registry;
- 17: the LCR/HQLA prototype;
- 18: extension points, the validation pack and WS3 verification.

Decisions D-29…D-35 are proposed in `docs/DECISIONS.md`.

**Status: awaiting user approval of the plan and of D-29…D-35 (and confirmation of D-28, which
Phase 15 relies on). No code before approval.**

## 5–6

As in the master prompt. The worktree is `../MoodyMate-wt/ws3-calculations` (one per phase if
preferred), with DB copy `moodymate_ws3_calculations` and its own artifact directory (D-15).
In each phase, the backend commits the OpenAPI contract first, and the frontend starts from
`npm run gen:api` against that commit.
