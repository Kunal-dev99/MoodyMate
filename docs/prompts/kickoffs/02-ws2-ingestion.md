# Kickoff 02 — WS2 Data Ingestion, Mapping & Data Quality

Instance of `docs/MASTER_PROMPT_FEATURE_KICKOFF.md`.

## 1. Requirement

- **Feature:** WS2 ingestion, mapping and data quality
- **Workstream / feature IDs:** WS2 DI-01, DI-02, DI-03, DI-04, DI-05, DI-06; paired
  contracts FE-DI-01…05 / BE-DI-01…05 (global phase G-P1/G-P2 "configurable ingestion →
  mapping → quality" vertical slice in Architecture §16; WS2-P0…P5 in the WS2 plans)
- **Problem and user:** the client's real source files, attributes and feeds are not known
  yet (Data Model §11). Data stewards need to take an uncertain source shape safely
  through a configurable contract, a versioned mapping and validation, without anyone
  hardcoding a file format. Nothing may be dropped silently. Risk analysts, approvers and
  auditors need to see what was accepted, what was held and why, and whether the totals
  reconcile.
- **In scope:**
  - source registration: source system, source profile (format, delimiter, sheet,
    cadence, owner, default classification) and versioned source contracts that start
    PROVISIONAL
  - bounded CSV/XLSX/JSON uploads with file, row, column and cell limits, a checksum, a
    write-once raw artifact, idempotency and a synthetic-data attestation (D-14)
  - a source adapter interface; CSV/XLSX/JSON are its first adapters
  - deterministic field profiling and type suggestions (suggestions only)
  - versioned, immutable source-to-canonical mappings pinned to a schema version, built
    from an allowlist of deterministic transforms, with dry-run validation and
    maker-checker activation
  - validation against the pinned schema version, plus issues, quarantine, rejected rows,
    retry through a new import, and immutable canonical records with lineage
  - reconciliation of counts and control totals
  - shape and null-rate drift detection that creates review events only
  - an adapter contract test, the WS2 verification suite and Playwright smoke tests
  - the Ingestion Workbench in the frontend (it replaces the "planned" placeholder)
- **Out of scope:** see "Deferred (out of scope for Feature 02)" in
  `docs/DELIVERY_PLAN.md`. In short: live feeds and warehouse connectors, object storage,
  background jobs, WS3 domain projection, AI suggestions and purge jobs.
- **Acceptance (business terms):**
  - A data steward registers a source, uploads a synthetic CSV, XLSX or JSON sample, and
    sees its checksum, row count and profile with suggested types.
  - The steward maps source columns to the active canonical schema version and validates
    the mapping against the upload. A different person (an approver) activates it.
  - Validating the import shows every row as accepted, quarantined or rejected (counts
    always add up), field-level issues that contain no raw values, and a reconciliation
    outcome.
  - Quarantined rows can be reviewed and retried through a new import. Nothing is ever
    edited in place or deleted.
  - A changed source shape raises a drift alert that someone must review. It never changes
    the schema or the mapping by itself.
  - Every action is permission-checked on the server, tenant-scoped and audited. Values
    are masked by classification.

## 2. Pre-plan reading (done 2026-10-16)

1. `docs/ARCHITECTURE.md` (module table rows ingestion, mapping and reconciliation);
   `docs/architecture_spec.txt` §6.1 ingestion flow, §7, §9, §10, Appendix A/B, Data
   Model §2–3, §5–6, §9 enums and §10 manifest; `docs/workstream_plans_spec.txt` WS2
   backend (:370-507) and frontend (:509-644).
2. Constraining decisions: D-01…D-08 and D-10…D-14 (see the Feature 02 header in
   `docs/DELIVERY_PLAN.md`). New proposals: D-15…D-23.
3. PCS is not installed (D-0). `.project-memory/events.jsonl` has no WS2 entries.
4. Codebase gaps are listed under "Pre-plan findings" in the Feature 02 section of
   `docs/DELIVERY_PLAN.md`.

## 3. Standing rules

As in the master prompt (D-01…D-04). In addition: synthetic data only outside production
(D-14), no AI anywhere in WS2, fixed-precision decimals end to end (no floats), and no
raw values in logs or issue messages.

## 4. Plan

Delivered to `docs/DELIVERY_PLAN.md` as "Feature 02", Phases 7–13 (numbering per D-08).
Decisions D-15…D-23 are proposed in `docs/DECISIONS.md`.

**Status: awaiting user approval of the plan and of D-15…D-23. No code before approval.**

## 5–6

As in the master prompt. Worktree `../MoodyMate-wt/ws2-ingestion` (one per phase if
preferred), DB copy `moodymate_ws2_ingestion`. Each worktree also gets its own artifact
directory, paired with its DB copy (D-15).
