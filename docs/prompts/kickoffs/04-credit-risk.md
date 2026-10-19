# Kickoff 04 — Credit Risk (PD/LGD/EAD, ECL, rating workflow)

Instance of `docs/MASTER_PROMPT_FEATURE_KICKOFF.md`.

## 1. Requirement

- **Feature:** credit risk: PD, LGD and EAD factors, expected credit loss with IFRS 9-style
  staging and a CECL-style lifetime variant, plus an obligor rating workflow.
- **Workstream / feature IDs:** credit risk is not in the signed-off WS1–WS5 plans. This
  feature uses **added, provisional IDs CR-01…CR-06**, defined in the Feature 04 section of
  `docs/DELIVERY_PLAN.md`. They build on WS3 FC-01 (counterparties), FC-06 (collateral
  haircuts, simplified), FC-07 (registry) and FC-08 (pinning). Data Model §11 (spec :1231)
  leaves impairment scope unresolved, so scope confirmation is an open question.
- **Problem and user:**
  - Risk analysts need deterministic, reproducible expected-credit-loss figures on synthetic
    credit exposures, with every figure traceable to its obligor, rating, factors and source
    row.
  - Credit analysts propose obligor ratings, and someone other than the author approves
    them.
  - Approvers control every methodology change.
  - Auditors trace any ECL figure back to the rating version and the rule versions it pinned.

  The client's real credit methodology is unknown, so everything sits behind the D-31 module
  interface, with ILLUSTRATIVE seeded rules.
- **In scope:**
  - seeded `counterparty` and `credit_exposure` schemas, synthetic samples, and projection
    into append-only domain tables with lineage, plus credit vocabularies (neutral rating
    grades, segments, facility and collateral types);
  - an obligor rating workflow: propose (grade, effective date, rationale) and approve or
    reject by a different person, with immutable, effective-dated versions;
  - module `credit_ecl` v1:
    - PD from a master scale with a TTC→PIT scalar and a constant-hazard lifetime;
    - EAD = drawn + CCF × undrawn;
    - LGD from segment LGDs and collateral haircuts;
    - IFRS 9-style staging (Stage 1 12-month; Stage 2 on SICR notches or 30 DPD; Stage 3 on
      90 DPD, the default grade or the default flag);
    - annual discounting at the EIR;
  - module `credit_ecl_cecl` v1: a lifetime-for-all variant reading the same rules;
  - runs that pin the approved ratings in effect at the reporting date, alongside the
    existing snapshot, rule, assumption, module and engine pins;
  - a credit-loss view with drill-down, governed credit rule changes, golden engineering
    fixtures, a verification marker and a Playwright smoke.
- **Out of scope:** see "Deferred (out of scope for Feature 04)" in `docs/DELIVERY_PLAN.md`.
  In short:
  - real or approved methodology;
  - macro scenarios and weighting;
  - monthly granularity and amortisation;
  - full collateral modelling and capital;
  - rating models and external feeds;
  - committee approval;
  - FX;
  - accounting outputs;
  - AI.
- **Acceptance (business terms):**
  - A data steward imports and projects synthetic counterparty and exposure files. Anyone
    allowed can browse obligors and exposures, with amounts masked by classification and a
    link to the source row.
  - An analyst proposes a rating, and an approver (never the author) approves it. The
    history keeps every version, and nothing is overwritten.
  - An analyst runs ECL for a reporting date. Before submitting, the analyst sees which
    ratings and rule versions will be pinned, and which obligors are unrated and will be
    excluded.
  - The run shows ECL by currency and stage. Any figure drills down to the exposure's grade,
    rating version, PD, CCF, EAD, LGD amount, stage reason and yearly terms, then to the
    source row.
  - Re-running gives identical results. A later rating or rule approval changes new runs
    only.
  - The CECL-style variant runs on the same pins for comparison.
  - Every output carries the illustrative statement. Nothing claims IFRS 9 or CECL
    compliance, regulatory status, or parity with Moody's or any agency.

## 2. Pre-plan reading (done 2026-10-19, master `2db56b7`)

1. **Architecture and plan text.**
   - `docs/ARCHITECTURE.md`: the domain, rules and calculations rows.
   - `docs/architecture_spec.txt`:
     - Data Model §4 `counterparty` (:901), `collateral_asset`/`collateral_link`
       (:930-932) and `reference_data_item` rating categories (:936);
     - §5 `capital_result`/`collateral_result` (:978-980);
     - candidate attributes (:1050, :1078);
     - §11 unresolved items: rating providers (:1225), module rules (:1229) and impairment
       scope (:1231).
   - `docs/workstream_plans_spec.txt`: FC-01 counterparties (:673), FC-06 (:685) and the
     breadth caveat (:211). No PD, LGD, EAD, ECL, IFRS or CECL text exists in either spec.
2. **Constraining decisions.** D-01…D-04, D-06, D-10, D-11, D-13, D-14, D-24 §4, D-26,
   D-29…D-36. New proposals: D-37…D-43.
   - **D-38 and D-40 are hard stops:** a CHECK widening, and new permissions with who
     approves ratings.
3. **Project memory.** PCS is not installed (D-0), and `.project-memory/events.jsonl` was not
   needed.
4. **Codebase gaps.** They are listed under "Pre-plan findings" in the Feature 04 section of
   `docs/DELIVERY_PLAN.md`. The main points:
   - no credit fields at all; `instrument` v1 `counterparty_id` is unprojected;
   - `schema_code` is a closed CHECK on two tables;
   - the warning, input-ref and intermediate CHECKs are closed;
   - re-runs are gated on unchanged engine and manifest versions;
   - there is no rating permission;
   - 48 backend tests;
   - the migration head is 0021.

## 3. Standing rules

As in the master prompt (D-01…D-04), plus:
- synthetic data only outside production (D-14);
- no AI on the calculation or rating path;
- Decimal-only arithmetic with one declared rounding per module, and no float in
  `app/calculations/` or `app/domain/`;
- pure, deterministic modules, with immutable runs, ratings and rule versions;
- maker-checker on rule activation and on ratings;
- every finance parameter ILLUSTRATIVE / PROVISIONAL until the client's financial owner
  approves it;
- neutral grade codes, never agency symbols;
- no claim of regulatory, IFRS 9 or CECL compliance, or of Moody's or vendor parity;
- the backend suite stays under 50 tests (D-26): +1 for the whole feature, 49 after.

## 4. Plan

Delivered to `docs/DELIVERY_PLAN.md` as "Feature 04", Phases 19–23 (numbering per D-08):

- 19: counterparty and credit-exposure data in the domain (migration 0022);
- 20: the obligor rating workflow (0023, with the permissions seed);
- 21: one ECL run end to end with drill-down (`credit_ecl` v1, 0024);
- 22: the CECL variant, governed credit rule changes and pinning across rating changes
  (0025);
- 23: the validation pack, documentation and verification.

Decisions D-37…D-43 are proposed in `docs/DECISIONS.md`.

**Status: awaiting user approval of the plan and of D-37…D-43. D-38 (a D-01 exception) and
D-40 (permissions and who approves ratings) are hard stops and need explicit approval. No
code before approval.**

## 5–6

As in the master prompt, run through the CLAUDE.md phase build protocol (D-36) and
`.claude/workflows/phase-build.js`:
- one worktree per phase, e.g. `../MoodyMate-wt/credit-p19` with DB copy
  `createdb -T moodymate_dev moodymate_credit_p19`, and per-agent test DBs and e2e ports;
- the backend commits the OpenAPI contract first, and the frontend starts from
  `npm run gen:api`;
- migrations are pre-numbered 0022–0025, and the coordinator re-chains them at merge.
