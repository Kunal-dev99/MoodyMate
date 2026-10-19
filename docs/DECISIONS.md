# Architecture Decision Log

Each entry: ID, title, status, date, decision, reason, consequences. IDs are permanent.

---

## D-0 Project context manager

- **Status:** none installed, plan to add
- **Date:** 2026-10-15
- **Decision:** The Project Context System (PCS) for durable, append-only project memory
  (`.project-memory/events.jsonl` + sqlite search index) is not installed. It was not
  found on PyPI, npm, or among the GitHub repositories reachable from the bootstrap
  session.
- **Interim:** `.project-memory/` exists and is gitignored. Checkpoints are appended by
  hand as JSON lines to `.project-memory/events.jsonl` in the PCS event shape so they
  can be ingested once PCS is installed. `docs/DECISIONS.md` and `docs/DELIVERY_PLAN.md`
  remain the committed system of record.
- **To close:** install PCS from its source, run its init in the repo root, ingest the
  existing `events.jsonl`, and update this entry's status.

## D-01 Additive, never destructive

- **Status:** active
- **Date:** 2026-10-15
- **Decision:** Gaps are closed by ADDING columns, tables, endpoints, pages and settings
  alongside what exists. Forward migrations are additive-only: no `DROP` (table, column,
  constraint, index) and no destructive `ALTER` in a forward migration.
- **Exceptions:** must be named in their own decision entry, with the reason, the data
  affected and the recovery path, before the change is merged.
- **Consequences:** superseded columns or tables are deprecated (and documented) rather
  than removed. Immutable versioned records from the data model (schema, mapping, rule,
  assumption, module versions, calculation runs) are never updated in place.

## D-02 Reversibility as a design constraint

- **Status:** active
- **Date:** 2026-10-15
- **Decision:** Every schema change ships a tested `downgrade`; CI runs the
  upgrade → downgrade → upgrade round trip. Lifecycle actions (retire, deprecate,
  archive, reject, cancel) HIDE data through status or validity fields, never delete
  it. New behaviour ships behind a migration or a setting, so that turning it off
  restores the previous behaviour exactly.
- **Consequences:** every phase in the delivery plan carries a reversibility note. The
  AI layer in particular must be switchable off without affecting ingestion or
  calculations (Architecture §13, §20).

## D-03 Isolation of in-progress work

- **Status:** active
- **Date:** 2026-10-15
- **Decision:** Every feature is built in a separate git worktree OUTSIDE the running
  app folder (e.g. `../MoodyMate-wt/<feature>`), against a COPY of the database, never
  the live dev DB.
- **Consequences:** the main checkout always runs; a feature is merged only after its
  checks pass in the worktree. Database copies are created per feature (e.g.
  `createdb -T moodymate_dev moodymate_<feature>`) and dropped after merge.

## D-04 Fail closed on authorization

- **Status:** active
- **Date:** 2026-10-15
- **Decision:** A request with no matching permission is denied (default deny). Row
  scoping (tenant / legal entity / dataset / portfolio) runs as a repository-layer query
  filter derived from the authenticated principal. It can't be bypassed by a crafted
  request: scope never comes from request parameters.
- **Consequences:** every repository query receives the principal's scope. An endpoint
  without a declared permission fails tests. Frontend visibility is cosmetic only
  (WS1 cross-cutting rule: server-side authorization is authoritative).

## D-05 Endpoint naming source of truth

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** The paired WS plans' endpoint names are canonical. Architecture §9 names
  are treated as illustrative. Endpoints that appear only in §9 (e.g. `GET /me`) are
  added alongside. Everything is finalised in `backend/openapi.json`, which is
  generated from code and drift-checked in CI.
- **Reason:** the WS plans are the FE/BE pairing contract, and their Definition of Done
  requires matching feature IDs and endpoints.

## D-06 Lifecycle state enums

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** The Data Model §9 UPPERCASE enums are canonical. Where Architecture
  Appendix B has a state the data model lacks, it is added in UPPERCASE (e.g.
  `schema_version` gains `REJECTED`; `schema_change_proposal` uses `DRAFT, SUBMITTED,
  IN_REVIEW, APPROVED, REJECTED, APPLIED, SUPERSEDED`). Each object gets one transition
  table in code, defining the authorised actor, preconditions, side effects and audit
  event.
- **Reason:** one spelling per object (Arch Appendix B note); the data model is the
  persistence contract.

## D-07 Error envelope includes `retryable`

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** `{code, message, details, correlation_id, retryable}`. This is a
  superset of the WS plans' envelope (adding a field is compatible).

## D-08 Phase and task numbering

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** `docs/DELIVERY_PLAN.md` phase numbers are global and sequential across
  all features (Feature 01 uses Phases 1–6; the next feature starts at 7), so
  `BE-n.m`/`FE-n.m` never collide. Architecture global phases are cited as `G-P0…G-P6`,
  and WS-internal phases as `WS<k>-P<n>`.
- **Reason:** the documents reuse P0–P6 with different meanings.

## D-09 Authentication modes

- **Status:** active (approved 2026-10-15, Feature 01); **amended by D-13**
- **Date:** 2026-10-15
- **Decision:** Setting `AUTH_MODE=oidc|dev`. `oidc` validates JWTs against the
  configured issuer/JWKS. `dev` validates JWTs signed by a local dev key, and is refused
  at startup when `ENV=production`. There is no password store in either mode (Arch §10).
- **Reason:** the client IdP is unknown. Development and testing need real token
  validation paths without depending on the client's IAM.

## D-10 Audit immutability

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** `audit_event` is append-only, enforced by a DB trigger that rejects
  `UPDATE`/`DELETE` (plus no update/delete repository methods). Audit is written in the
  same transaction as the action. Reads of audit are themselves audited.
- **Reason:** Arch §10 says to protect audit records against ordinary modification.

## D-11 Tenancy and provisional seed data

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** Every scoped table carries `tenant_id` from day one, with one tenant
  seeded. Seed roles, permissions, classifications and synthetic schemas are inserted
  by data migrations, tagged with the migration ID, and marked PROVISIONAL. Their
  downgrade removes only tagged seed rows. This is a named exception to D-01 for seed
  data only, never user data.
- **Reason:** the role model, classifications and schemas need client confirmation
  (Arch §10, Data Model §11).

## D-12 Local development infrastructure

- **Status:** active (approved 2026-10-15, Feature 01)
- **Date:** 2026-10-15
- **Decision:** `docker-compose.yml` provides PostgreSQL 16 for local work and CI. Each
  feature worktree uses its own database copy
  (`createdb -T moodymate_dev moodymate_<feature>`) per D-03. Object storage and a job
  broker are not added until a feature needs them.
- **Reason:** hosting is undecided (Arch §18). Keep the dependency footprint minimal.

## D-13 Fail-safe authentication configuration (amends D-09)

- **Status:** active (approved by the user 2026-10-15, Phase 2 hardening)
- **Date:** 2026-10-15
- **Context:** the independent security review of PR #3 found that a deployment which
  forgot `ENV` started as `local` with `AUTH_MODE=dev`. Anyone could then mint an admin
  token through `POST /api/v1/dev/sessions`.
- **Decision:**
  1. `ENV` has no default: startup fails if it is not set. `AUTH_MODE=dev` is allowed only
     when `ENV` is `local` or `test`. This removes D-09's allowance for dev mode in staging.
  2. Outside local/test, OIDC requires `https://` for the issuer, a configured JWKS URL and
     any `jwks_uri` obtained by discovery.
  3. Startup is refused when `OIDC_AUDIENCE` equals `OIDC_CLIENT_ID`, so ID tokens issued to
     the frontend cannot be replayed as API access tokens.
- **Consequences:** every environment must set `ENV` (see `backend/.env.example`), and
  staging must use a real or test OIDC provider. The backend runs as a factory
  (`uvicorn app.main:create_app --factory`), so importing the module never builds an app
  from implicit settings.
- **Reversal:** restore the defaults in `app/core/config.py`. Not recommended.

## D-14 Client data only in production

- **Status:** active (decided by the user 2026-10-16, Phase 6 review)
- **Date:** 2026-10-16
- **Context:** the Phase 6 plan text (BE-6.1) could be read two ways: "synthetic is required outside production" and "refuse client unless ENV is staging or production". The first build allowed client data in staging, and the independent review flagged that.
- **Decision:**
  1. `DATA_CLASS=client` is allowed only when `ENV=production`. local, test and staging hold synthetic data only, and startup refuses anything else.
  2. `DATA_CLASS` must be set explicitly in staging and production, never defaulted (fail closed, like D-13).
- **Consequences:** user-acceptance testing in staging uses synthetic or anonymised data. If staging ever needs real client data, that is a new decision with its own controls (masking, access, retention).
- **Reversal:** change the one condition in `app/core/config.py` and the `MATRIX` in `test_environment_guard.py`. Not without a new decision.

## D-15 Raw artifact storage before object storage exists

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Context:** WS2 must keep every uploaded file as an immutable raw artifact (Arch §6.1
  step 2, Data Model `import_artifact`). No object storage exists yet (D-12), and hosting is
  undecided.
- **Question:** where do uploaded sample files live?
- **Options:**
  - **A (recommended):** an `ArtifactStore` interface with a local filesystem implementation.
    - Objects are content-addressed by SHA-256 and write-once; an existing object is never
      overwritten.
    - Every read re-verifies the hash, and a mismatch fails closed with
      `artifact_integrity_error`.
    - The root is `ARTIFACT_STORE_DIR`. It must be outside the repository and is required
      outside local/test (D-13).
    - Each worktree pairs its own directory with its DB copy (D-03).
    - An S3-compatible implementation is added later behind the same interface.
  - **B:** store the bytes in PostgreSQL (`bytea`). This is transactional and already
    covered by database backups. It bloats the database, though, and differs from the
    architecture's object-storage boundary.
  - **C:** add MinIO to `docker-compose.yml` now. This is closest to the target, but it
    adds infrastructure before hosting is known, against D-12.
- **Consequences of A:**
  - The DB row and the file can diverge (for example, a lost disk). Restore checks
    therefore include a hash match (BE-13.3).
  - The application never deletes artifacts (D-20).

## D-16 Upload bounds, accepted formats and the synthetic-data attestation

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** which limits and formats does the MVP accept, and how does an upload honour
  D-14?
- **Recommended:**
  1. **Size and count limits.**
     - Per file: at most 10 MiB, enforced while streaming (413 `payload_too_large`).
     - At most 50,000 data rows, 200 columns and 4,000 characters per cell.
     - The ceilings are hard-coded. Settings (`IMPORT_MAX_*`) may lower them, never raise
       them.
  2. **CSV.** UTF-8 only (a BOM is allowed). The delimiter comes from the source profile
     (`,` `;` tab `|`), and a header row is required. All values are read as strings.
  3. **XLSX.** Only `.xlsx`; `.xls` and macro-enabled `.xlsm` are refused, with the type
     detected from content, not from the extension. One sheet is read: the profile's named
     sheet, otherwise the first. The total uncompressed size must be at most 50 MiB, with a
     compression ratio of at most 100:1 (zip-bomb guard). Formulas are never evaluated;
     only cached values are read.
  4. **JSON.** UTF-8; a top-level array of flat objects with scalar values only. Parsed
     with `parse_float=Decimal`. Nested values are refused.
  5. **Synthetic-data attestation (D-14).** While the effective `DATA_CLASS` is
     `synthetic`, `POST /imports` requires `synthetic_attested=true` (otherwise 422
     `synthetic_attestation_required`). The import records `data_class` and the
     attestation, and both are audited. The server cannot detect real data, so this makes
     the uploader accountable for it.
- **Alternatives:** larger limits with async processing (deferred with D-21); also
  accepting other encodings (deferred).
- **Consequences:** these limits are PROVISIONAL and will be revisited once client sample
  sizes are known.

## D-17 XLSX parsing library

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** which library reads XLSX safely without breaking fixed-precision rules?
- **Options:**
  - **A (recommended):** `openpyxl` in `read_only=True, data_only=True` mode, with
    `defusedxml` installed, which openpyxl then uses against XML entity attacks.
    - XLSX stores numbers as IEEE doubles. A numeric cell is therefore converted with
      `Decimal(repr(value))` (shortest round-trip), and values beyond 15 significant digits
      raise a precision warning.
    - Dates are converted to ISO strings.
    - It is pure Python, mature, and also writes the synthetic samples.
  - **B:** `python-calamine`, a Rust reader. It is faster and has a smaller XML attack
    surface in Python, but it is a binary dependency with a younger API.
  - **C:** `pandas`. Rejected: it coerces to float, which breaks the fixed-precision rule,
    and it is heavy.
- **Consequences:** two new runtime dependencies (`openpyxl`, `defusedxml`) plus
  `python-multipart` for uploads. Steering users to CSV for high-precision values is
  documented in `docs/ADAPTERS.md`.

## D-18 Mapping versions and their binding to schema versions

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Context:** Data Model §2 says "the canonical model and source mapping are versioned
  separately" and "each import records the exact mapping version applied". Arch §10 asks
  for maker-checker on production mapping changes.
- **Question:** how do mapping versions relate to schema versions, and how does one become
  active?
- **Recommended:**
  1. **Pinning.** A `mapping_version` pins exactly one `schema_version_id` (the target's
     ACTIVE version when the draft is created) and one `source_contract_id`.
  2. **Lifecycle and immutability.** The lifecycle is the `schema_version` set (D-06). A
     version is immutable once it leaves DRAFT (DB triggers, TRUNCATE included).
  3. **Activation.** It needs a recorded successful dry-run validation and an approver
     holding `mapping:approve` who is not the author. Each mapping has one ACTIVE version;
     activating a new one DEPRECATES the previous one in the same transaction.
  4. **Schema changes.** When a Phase 5 approval deprecates the pinned schema version, the
     mapping version stays as history. It is no longer usable for new imports, whose pinned
     schema version differs: they show MAPPING_REQUIRED until a new mapping version for the
     new schema version is approved. Mappings are never migrated automatically.
  5. **Imports.** They pin `mapping_version_id` permanently when validated.
  6. **Dependencies.** Activation upserts a `schema_dependency` (`MAPPING`, mapping code)
     row, so Phase 5 impact analysis lists real mappings.
- **Alternatives:**
  - Bind mappings to the schema rather than to a version. Rejected: it is not
    reproducible.
  - Auto-rebase mappings on schema change. Rejected: that is a silent change.
  - Activation without maker-checker. Rejected: Arch §10.

## D-19 Mapping transforms are a closed allowlist

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** what may a mapping rule do to a value?
- **Recommended:**
  - Only named, pure, deterministic transforms with JSON-schema-validated parameters:
    `COPY`, `TRIM`, `UPPER`, `LOWER`, `PARSE_DECIMAL(decimal_sep, thousands_sep)`,
    `SCALE(power_of_ten)`, `PARSE_DATE(format)`, `PARSE_BOOLEAN(true_values,
    false_values)`, `MAP_VALUES(table, on_missing)`, `DEFAULT(value)` and
    `CONSTANT(value)`.
  - No expressions, eval, templating, regex or user code. Decimal arithmetic only.
  - Adding a transform needs code, tests and an appended decision.
- **Reason:** no AI-generated or user-authored code is executed (WS cross-cutting rules),
  results must be reproducible, and regex risks ReDoS.

## D-20 Quarantine, rejected rows, retry and retention

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** how are invalid rows held, corrected and retained?
- **Recommended:**
  1. **Row outcomes.** Every source row ends in exactly one outcome:
     - **ACCEPTED:** a `canonical_record` is written.
     - **QUARANTINED:** the row has an ERROR-severity issue and gets a `quarantine_item`
       with status OPEN.
     - **REJECTED:** the row has a row-level BLOCKER, such as an unparseable row, and gets a
       `quarantine_item` with status REJECTED.

     A file-level BLOCKER fails the import and no rows are accepted. Rows are never dropped.
  2. **Quarantine lifecycle.** The `quarantine_item` statuses are those of Data Model §9.
     Transitions:
     - OPEN→UNDER_REVIEW
     - UNDER_REVIEW→{REJECTED, RETRY_READY, OPEN}
     - RETRY_READY→CORRECTED, automatically when a retry import contains the business key
     - CORRECTED→{RESOLVED, RETRY_READY}, automatically on the retry's validation outcome

     Every transition is audited, and `audit_event` is the disposition history.
  3. **Corrections never edit `source_record`.** A correction is a new import with
     `retry_of_import_id`, uploaded with the corrected file.
  4. **Retention (PROVISIONAL).** Raw artifacts, source records, quarantine items and
     issues are kept indefinitely until the client sets retention (RETENTION.md rows,
     BE-13.3). The application has no delete or purge path, and downgrades refuse while
     rows exist. Displayed quarantined values are masked by classification. Unmapped columns
     use the source profile's default classification.
- **Alternatives:**
  - Edit-in-place corrections. Rejected by D-01/D-02 and lineage.
  - A separate `quarantine_history` table. Not needed while `audit_event` is append-only;
    it can be added later.

## D-21 Import processing model, idempotency and status set

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** is import processing synchronous or job-based, and which statuses does
  `import_job` use?
- **Recommended:**
  1. **Processing.** The MVP processes synchronously within the request, as D-16 bounds
     allow: upload → parse → profile, and later validate → reconcile → drift. Each step is
     a service function that a WS4 `workflow_job` runner can later call unchanged.
  2. **Idempotency.** `POST /imports` requires an `Idempotency-Key` header (Arch §9),
     unique per tenant and actor.
     - A replay with the same bytes returns the original import.
     - A replay with different bytes returns 409 `idempotency_conflict`.
     - A validate runs once per import. A re-run is a new import.
  3. **Status set.** The Data Model §9 `import_job` set (CREATED, RECEIVED, PROFILING,
     MAPPING_REQUIRED, VALIDATING, COMPLETED, COMPLETED_WITH_WARNINGS, FAILED, CANCELLED),
     plus QUARANTINED from Appendix B per D-06, meaning validation finished with at least
     one quarantined or rejected row.
     - The transition table lives in `ingestion/constants.py`.
     - Appendix B's `uploaded` maps to RECEIVED and `ready` to COMPLETED.
     - After profiling, the import is RECEIVED (ready to validate) or MAPPING_REQUIRED (no
       usable ACTIVE mapping version).
- **Alternatives:** introduce `workflow_job` now. Rejected: it duplicates WS4 RO-01, and the
  current limits do not need it.

## D-22 Canonical records and the import as the MVP dataset snapshot

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** where do accepted records go before the WS3 domain tables exist, and what
  will a calculation run pin?
- **Recommended:**
  - **Storage.** A generic, append-only `canonical_record` holds the accepted records. Its
    `payload` JSONB is keyed by schema field name, with typed strings:
    - decimals as strings at the field's scale, never JSON floats;
    - dates as ISO-8601.

    Each record also carries `schema_version_id`, `mapping_version_id`,
    `source_record_id` and `payload_sha256`, and has `record_lineage` back to its source.
  - **Snapshot.** For the MVP, one validated import is one immutable dataset snapshot, and
    its manifest (BE-11.5) is what a run pins.
  - **Later.** WS3 projects these records into `instrument_version`/`position_version` and
    may introduce a cross-import `dataset_snapshot`.
- **Alternatives:** write directly into domain tables (they do not exist yet, and that would
  couple WS2 to WS3).

## D-23 WS2 enums not defined in Data Model §9

- **Status:** active (approved by the user 2026-10-16, Feature 02 plan)
- **Date:** 2026-10-16
- **Question:** what values do the WS2 status/type columns that the data model leaves open
  take? All are UPPERCASE per D-06, CHECK-constrained, and each lifecycle has a transition
  table in code.
- **Recommended:**
  - `source_system.kind`: FILE_DROP, WAREHOUSE, APPLICATION, FEED. Only FILE_DROP is
    usable in the MVP.
  - `source_profile.format`: CSV, XLSX, JSON.
  - `mapping_version.status`: the `schema_version` set (DRAFT, IN_REVIEW, APPROVED, ACTIVE,
    DEPRECATED, RETIRED, REJECTED).
  - `mapping_rule.column_policy`: MAP, PRESERVE.
  - `source_record.outcome`: PENDING, ACCEPTED, QUARANTINED, REJECTED.
  - `validation_issue.disposition`: ACCEPTED_WITH_WARNING, QUARANTINED, REJECTED,
    FILE_BLOCKED.
  - `reconciliation_run.outcome`: MATCHED, REVIEW_REQUIRED (Data Model §10 example),
    BROKEN.
  - `reconciliation_item.status`: MATCHED, WITHIN_TOLERANCE, VARIANCE.
  - `data_drift_event.drift_type`: COLUMN_ADDED, COLUMN_REMOVED, TYPE_CHANGED,
    NULL_RATE_SHIFT, RANGE_SHIFT.
  - `data_drift_event.status`: OPEN, ACKNOWLEDGED, ACTION_REQUIRED.
- **Consequences:** the enums appear in OpenAPI, and the FE status pills are tested against
  the generated enum sets.

## D-24 In-file synthetic markers, frozen source contracts and audited business refusals

- **Status:** active (decided by the user 2026-10-16, Phase 7 review)
- **Date:** 2026-10-16
- **Context:** the Phase 7 review (`docs/evidence/phase-7/REPORT.md`) found the plan in
  conflict with itself. BE-7.5 asks for the "ILLUSTRATIVE / SYNTHETIC" marker inside each
  sample file, but D-16 accepts only a top-level JSON array. The review also found that
  PROVISIONAL contracts stayed editable (D1) and that business-rule refusals were not
  audited (D3).
- **Decision:**
  1. **JSON uploads** are either a top-level array of flat objects or exactly
     `{"marker": "<starts with ILLUSTRATIVE / SYNTHETIC>", "records": [flat objects]}`, with
     no other keys. Anything else is refused. Amends D-16 §4.
  2. **CSV uploads** skip line 1 only when it starts with exactly
     `# ILLUSTRATIVE / SYNTHETIC`, and the import records that the marker was present.
     There is no general rule that skips `#` lines (D-20: no row is silently dropped).
     XLSX marks the data in the sheet name and the workbook title.
  3. **Source contract content is frozen from creation, in every status.** Only `status` and
     `updated_at` may change, along the transition table. Changes are always a new version.
     Amends BE-7.1's "immutable once not PROVISIONAL".
  4. **Every refused WS2 write is audited** with its reason code: authorization, policy,
     the switch, and business-rule refusals (for example `duplicate_code`, `no_change`,
     `unsupported_source_kind`). Malformed request bodies (422 `validation_error`) are not.
  5. **data_steward keeps no `audit:read`.** Source history stays with auditors
     (segregation of duties).
- **Consequences:** the Phase 8 CSV and JSON adapters implement points 1–2. Phase 7's unreleased
  migrations (0009–0011, never merged or deployed) are amended in place for point 3, and
  a shared refusal-audit helper covers point 4.
- **Reversal:** a new decision. Points 3 and 4 only ever tighten controls.

## D-25 Upload content cap, refused uploads and Phase 8 clarifications

- **Status:** active (decided by the user 2026-10-16, Phase 8 review)
- **Date:** 2026-10-16
- **Context:** the Phase 8 review (`docs/evidence/phase-8/REPORT.md`) found that an XLSX can
  expand far beyond its file size (D1). It also raised points the plan left open.
- **Decision:**
  1. **Decoded content cap (D-16 addendum).** A running total of decoded cell characters is
     kept for every format. An upload above **50 MiB** of decoded content is refused with 422
     `content_too_large`. It is a hard ceiling that settings may only lower.
  2. **Code layout.** A module may hold one sub-package for pluggable format adapters
     (`app/ingestion/adapters/`). Shared storage plumbing lives in `app/core/artifacts.py`.
  3. **A refused upload persists nothing.** A refusal at upload time (limits, format,
     encoding, shape) is audited DENIED with its reason (D-24 §4). No import is created and
     no file is kept. D-20's "a file-level BLOCKER fails the import" applies once an import
     exists, at validation (Phase 11), which ends FAILED with its artifact kept.
  4. **Retention state.** `import_artifact.retention_state` has one value, RETAINED. More
     states only come with BE-13.3 and a decision.
  5. **Row semantics** (D-20: never drop, never invent):
     - ragged rows keep the cells they have and are flagged `MISSING_CELLS`;
     - extra cells go to `extra_values`, flagged `EXTRA_CELLS`;
     - interior blank rows are kept, flagged `BLANK_ROW`, and trailing blank rows are not
       rows;
     - a JSON null leaves the key out and is flagged `NULL_VALUE`;
     - the XLSX marker is recognised from the sheet name or the workbook title (it is
       informational only);
     - a bare JSON scalar for a JSON source is `json_invalid_shape`.
  6. **Idempotency (D-21 clarification).** The fingerprint covers source, file SHA-256,
     attestation, expected row count and control totals, but not the file name. A replay
     changes nothing and is not audited; a replay conflict is.
- **Reversal:** a new decision. Point 1 only ever tightens.

## D-26 Essential backend tests only

- **Status:** active (decided by the user 2026-10-16)
- **Date:** 2026-10-16
- **Context:** the backend suite had grown to 1,013 tests and took about 9.5 minutes per run,
  mostly through per-combination parametrisation. That slowed every phase and every CI run.
- **Decision:**
  1. The backend suite stays **under 50 tests** in total (ws1 included), covering
     security and core flows only:
     - authentication and RBAC, with the route×role matrix;
     - tenant isolation;
     - the audit trail and its immutability;
     - environment guards;
     - DB immutability triggers;
     - one happy path per feature;
     - no raw-value leaks;
     - migrations.
  2. Each new phase adds at most a handful of essential tests. Loops inside one test replace
     parametrised variants.
  3. Frontend unit tests and Playwright are not limited by this decision.
  4. A plan Done-when may cite a consolidated test. Earlier ticks that name removed tests
     stay as history.
- **Consequences:** a full backend run takes about 50 s instead of about 9.5 min. Detailed
  edge cases are no longer guarded by tests. The removed areas are listed in the commit
  `Trim the backend test suite to 33 essential tests` (3cc448c), and can be re-added one at a time if a
  regression shows up.
- **Reversal:** a new decision.

## D-27 Phase 11 validation semantics

- **Status:** active (recorded 2026-10-17 from the Phase 11 review; PROVISIONAL where noted)
- **Date:** 2026-10-17
- **Context:** the Phase 11 review (`docs/evidence/phase-11/REPORT.md`) asked for these
  behaviours to be written down. They go beyond the plan text.
- **Decision:**
  1. **Import manifest table.** `import_manifest` is written once with the validation
     outcome and is append-only. It is what a calculation pins (D-22). Its unique import id
     enforces "validate once".
  2. **No usable mapping at validate.** Validate answers 409 `mapping_required` and leaves the
     status unchanged. Profiling sets MAPPING_REQUIRED (Phase 9); validate does not.
  3. **REJECTED is final** (PROVISIONAL). Rows rejected by the default profile (BLANK_ROW,
     EXTRA_CELLS) and every row of a FAILED import have REJECTED quarantine items. A
     corrected retry is accepted on its own, but those original items are not linked to it
     (`resolved_by_import_id` stays empty). Only RETRY_READY items are resolved.
  4. **Retry the original import.** Items are resolved only by a direct retry
     (`retry_of_import_id` = the item's import). The retry wizard always targets the import
     that holds the items.
  5. **Duplicate business keys: the first occurrence wins.** The first row carrying a key owns
     it, even if that row is itself quarantined. Later rows with the same key are quarantined
     as DUPLICATE_BUSINESS_KEY.
  6. **Default validation profile v1** (PROVISIONAL, tagged seed). BLANK_ROW and EXTRA_CELLS
     are BLOCKER/REJECTED. MISSING_CELLS is a WARNING. Cell, formula and precision errors are
     ERROR/QUARANTINED. Field errors (required, type, date, scale, precision, currency,
     unmapped value, missing or duplicate key) are ERROR/QUARANTINED. File-level problems are
     FILE_BLOCKED, and the import is FAILED.
- **Reversal:** a new decision, or a new validation_profile version for point 6.

## D-28 Phase 12 reconciliation and drift semantics

- **Status:** active (confirmed by the user 2026-10-17). Recorded from the Phase 12 code at
  `cdbade9`. PROVISIONAL where noted.
- **Date:** 2026-10-17
- **Context:** Phase 12 (`docs/evidence/phase-12/REPORT.md`) implemented behaviours that go
  beyond the plan text of BE-12.1 to BE-12.4. They are written down here as built, so later
  phases rely on them knowingly.
- **Decision:**
  1. **One reconciliation per validated import.** Validation writes it in its own transaction,
     for every final status (COMPLETED, COMPLETED_WITH_WARNINGS, QUARANTINED, FAILED). The
     run and its items are append-only, with a unique import id. Changing the checks or the
     rules means a new `reconciler_version` (now 1). `GET /imports/{id}/reconciliation`
     (`import:read`) answers 404 `reconciliation_not_found` before validation. There is no
     write route and no off switch: reconciliation is a control.
  2. **Checks, in a fixed order.** For every check, `variance = source - target`, and `held`
     is the part of the gap that held rows explain.
     - ROW_COUNT: source rows against rows with a canonical record. `held` is the rows with a
       quarantine item (quarantined or rejected).
     - EXPECTED_ROW_COUNT, only when the upload declared one: the declared count against the
       source rows. `held` is none.
     - CONTROL_TOTAL, per contract control-total column: the source total against the
       accepted rows' total. `held` is the held rows' total.
     - DECLARED_CONTROL_TOTAL, only when the upload declared that column's total: the
       declared total against the source total. `held` is none.

     A value counts towards a total only when it is plain decimal text after trimming
     spaces. The counts of parseable, unparseable and empty values are kept in `details`.
  3. **Status and outcome rules.**
     - An item is MATCHED when its variance is 0, WITHIN_TOLERANCE when
       |variance| ≤ tolerance, and otherwise VARIANCE.
     - An item is *unexplained* when |variance − held| > tolerance, with a missing `held`
       counting as 0.
     - The run is BROKEN if any item is unexplained, otherwise REVIEW_REQUIRED if any item is
       not MATCHED, otherwise MATCHED.

     So a quarantine import whose gaps equal its held rows is REVIEW_REQUIRED, and any
     declared figure that differs is BROKEN. Insert triggers refuse a non-MATCHED item under
     a MATCHED run, and an unexplained item under a run that is not BROKEN. CHECKs hold the
     variance and the status-of-variance rule.
  4. **Tolerance and exact decimals** (PROVISIONAL). `RECONCILIATION_TOLERANCE` is one
     absolute tolerance, default `0`, a finite Decimal of at least 0. It applies to
     CONTROL_TOTAL and DECLARED_CONTROL_TOTAL only. Row counts always use 0 (CHECK). Each run
     stores the policy it applied (`kind ABSOLUTE`, `tolerance`, `count_tolerance "0"`,
     `provisional true`, `setting`). The arithmetic is Decimal under a context that traps any
     rounding, never float, and is stored as NUMERIC. The API returns every figure as exact
     decimal text (`format(d, "f")`, never in exponent form). A role that is not cleared sees
     a control total's source, target, held and variance masked, by the more sensitive of the
     column's and the artifact's classification. Counts are always public.
  5. **Drift baseline.** Detection runs after profiling, at upload, in the profiling
     transaction. The baseline is the most recent import (by `created_at`, then id) of the same
     tenant and source system, other than this one, that is COMPLETED or
     COMPLETED_WITH_WARNINGS and has a profile. This equals "same source profile" while a
     source has one profile. An insert trigger also enforces it. The baseline is fixed when the
     event is written. With no baseline (a first import), nothing is compared, written or
     audited.
  6. **Drift types and thresholds as settings** (PROVISIONAL).
     - COLUMN_ADDED and COLUMN_REMOVED match on the exact column name.
     - TYPE_CHANGED: the suggested (most specific) candidate type differs, when both sides
       have one.
     - NULL_RATE_SHIFT: the null rate moved by more than `DRIFT_NULL_RATE_THRESHOLD`
       (default `0.10`, from 0 to 1).
     - RANGE_SHIFT: for a column numeric on both sides, the min or max lies beyond the
       baseline's by more than `DRIFT_RANGE_THRESHOLD` (default `0.25`) × the baseline's
       range, or × |baseline max| when the range is zero.

     A value exactly at a threshold is not drift. Each event stores the thresholds it applied
     and `detector_version` 1. Summaries keep profile statistics only (never top values), and
     min and max are masked by the column's classification on read. One event per
     (import, type, column).
  7. **`DRIFT_DETECTION_ENABLED`** (default `true`). When it is off, no detection runs and no
     event or `import.drift_detect` audit is written. Existing events stay readable and
     reviewable, so this switch is the D-02 reversal for drift.
  8. **Review outcomes.**
     - `POST /drift-events/{id}/review` needs `quality:review` and is also refused while
       `INGESTION_ENABLED` is off.
     - The outcome is ACKNOWLEDGED or ACTION_REQUIRED. A comment of 1 to 1,000 characters is
       required.
     - The move is OPEN → one of those two, once. The review records the reviewer, the
       comment and the time, and is audited `drift_event.review`.
     - A second review is 409 `already_reviewed`, audited DENIED, and changes nothing.
       Absent and foreign events give the same 404, also audited.
     - The database freezes the event content and allows only that one review move. Events
       are never deleted or truncated.
  9. **No automatic proposals.** Neither detection nor review creates or changes a schema,
     contract, mapping or schema change proposal. ACTION_REQUIRED only returns a
     `proposal_link` to the Phase 5 page `/schemas/proposals/new?schema={id}`, which prefills
     the import's target schema and nothing else.
- **Consequences:** the downgrade of 0016 refuses while any run or drift event exists
  (D-20 §4). Phase 13 (BE-13.2, BE-13.3) covers these tables in the WS2 matrix and in
  RETENTION.md. The client must set the real tolerance and thresholds before production use.
- **Reversal:** a new decision. A change to point 2 or 3 also bumps `reconciler_version`, and
  a change to point 6 bumps `detector_version`.

## D-29 Projecting canonical records into the WS3 domain tables

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17)
- **Date:** 2026-10-17
- **Context:** D-22 deferred this projection to WS3. Data Model §4 needs stable identities
  plus immutable versions for reproducible runs. `canonical_record` has no `tenant_id`
  column, and the seeded `instrument` v1 types `maturity_date` as free text.
- **Question:** when and how do accepted canonical records become domain rows?
- **Options:**
  - **A (recommended):** an explicit, idempotent per-import step,
    `POST /imports/{id}/projection` (`domain:project`).
  - **B:** project automatically at the end of validation. Rejected: it couples WS2 to WS3,
    and an off switch would leave WS2 changed.
  - **C:** project lazily when a snapshot is created. Rejected: the catalogue would be empty
    until someone calculates.
- **Recommended (A):**
  1. **Eligibility.** An import is eligible when it has an `import_manifest`, its status is
     COMPLETED, COMPLETED_WITH_WARNINGS or QUARANTINED, and its schema code is `instrument`,
     `position` or `cashflow_event`. Only accepted rows are projected.
  2. **Rows.** Each accepted canonical record gives exactly one `instrument_version`,
     `position_version` or `cashflow_event` row, so rows are conserved. One
     `domain_projection` row per import holds a value-free summary. Calling the step again
     returns that row and writes nothing.
  3. **Identity.**
     - `instrument` and `position` get a stable UUID per (tenant, source system, business
       key).
     - A later import of the same key adds a new version and never updates an old one.
     - "Current" is not defined. Time is chosen by pinning a snapshot (D-30).
     - Effective dating is PROVISIONAL until the client defines business-date semantics.
  4. **Field projection.**
     - A frozen spec per schema code (`projector_version` 1) maps schema fields to typed
       columns.
     - Amounts are stored as exact `NUMERIC`, never rounded, and dates as `DATE`.
     - A STRING schema field is never parsed into a typed column. For example, `instrument`
       v1 `maturity_date` stays NULL and is counted as `TYPE_NOT_PROJECTED`.
     - Extension-eligible instrument fields go to `instrument_attribute_value`.
     - Every other field stays in `canonical_record`, where lineage reaches it. Nothing is
       dropped.
  5. **Instrument types.** These are a seeded PROVISIONAL vocabulary. An unknown type is
     still projected with `instrument_type_id` NULL and counted, and modules warn on it.
  6. **Integrity.**
     - Every row stores `canonical_record_id` (unique), `import_job_id`,
       `schema_version_id` and `payload_sha256`.
     - Rows are append-only (UPDATE, DELETE and TRUNCATE are refused).
     - `tenant_id` is copied from `import_job`, and a same-tenant insert trigger checks it.
  7. **Switch and audit.** `DOMAIN_PROJECTION_ENABLED` (default `true`): when off, the step
     answers 409 `domain_projection_disabled`, which is audited. Reads still work.
- **Consequences:** a new schema version (e.g. `instrument` v2) needs a new
  `projector_version` before its imports can be projected. The downgrade refuses while any
  projection exists.

## D-30 Cross-import dataset snapshot

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17)
- **Date:** 2026-10-17
- **Question:** what exactly does a calculation run pin as its dataset (Data Model §7: a
  `dataset_snapshot_id` or an immutable input manifest)?
- **Recommended:**
  1. **What a snapshot is.** A `dataset_snapshot` is an immutable set of projected imports,
     with **at most one import per schema code** in the MVP. Each member pins the import,
     its `import_manifest.manifest_sha256` and its `domain_projection`.
     `content_sha256` hashes the sorted member list.
  2. **Content-addressed.** The same member set returns the existing snapshot, so no
     idempotency key is needed. `POST /dataset-snapshots` needs `calc:run`, and the create
     is audited.
  3. **Refusals** (409, each audited DENIED):
     - `not_projected`;
     - `duplicate_schema`;
     - `reconciliation_broken`: a member's D-28 outcome is BROKEN (fail closed, Arch §6.2
       step 5);
     - `ambiguous_business_key`: one business key resolves to two instruments inside the
       snapshot.

     QUARANTINED members are allowed. Their held counts are carried into the run manifest so
     coverage is visible (PROVISIONAL).
  4. **Cross-source references.** A `cashflow_event` or `position` resolves its
     `instrument_id` by business key within the snapshot and the tenant. An unresolved
     reference is a run warning, never a silent drop. This is PROVISIONAL until the client
     sets source-of-truth precedence (Data Model §11).
- **Alternatives:**
  - Pin the imports directly on the run. Rejected: there is no reusable or comparable dataset
    identity.
  - Allow several imports per schema. Deferred: it needs merge and precedence rules.
- **Consequences:** this depends on D-28 §3 (reconciliation outcome), which is still
  *proposed*.

## D-31 Calculation framework: module interface, run lifecycle and reproducibility

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17)
- **Date:** 2026-10-17
- **Question:** how are modules plugged in, executed and made reproducible?
- **Recommended:**
  1. **Module interface.**
     - A module lives in `app/calculations/modules/`, a sub-package as D-25 §2 allows.
     - It declares `code`, `version`, its input contract (the schema codes and fields it
       needs), the rule definitions it reads, the assumption keys it reads, and its output
       tables.
     - `run(inputs, parameters) -> ModuleResult(results, intermediates, warnings)` is a
       **pure function** over frozen typed inputs. It does no DB or network I/O and reads no
       clock, randomness or environment. It holds no float, and a source grep enforces
       this, as in BE-11.2.
     - A module is registered in code and seeded as a `module_version` row. Startup refuses
       a registered module that has no row. There is no API to create a module: a new module
       version means a code release plus a migration.
  2. **Decimal policy.**
     - Every module runs under a declared Decimal context that traps Inexact, Rounded and
       InvalidOperation.
     - Any rounding is an explicit `quantize` that the module declares (scale and mode,
       default ROUND_HALF_EVEN, PROVISIONAL).
     - Values are stored as `NUMERIC` and returned as exact decimal text
       (`format(d, "f")`).
  3. **Run lifecycle.**
     - The statuses are the Data Model §9 set (D-06): QUEUED, RUNNING, SUCCEEDED,
       SUCCEEDED_WITH_WARNINGS, FAILED, CANCELLED, BLOCKED. Execution is synchronous in the
       MVP, as in D-21, so QUEUED and CANCELLED are reserved for WS4.
     - The run row is written as RUNNING in its own transaction, so a crash leaves evidence.
     - BLOCKED means the input contract was not met, for example a required schema is
       missing from the snapshot.
     - FAILED means a post-check failed (Arch §6.2 step 5), and never counts as a silent
       success. Post-checks include: every in-scope input is used once or excluded with a
       warning code, and every aggregate equals the sum of its contributions.
     - Pre-run refusals (permission, switch, a PLANNED module, a bad body) are 4xx and write
       no run.
  4. **Pins and manifest.**
     - A run pins the snapshot, `reporting_date`, the module version, `engine_version`, the
       rule set (D-32 §4) and the assumption set. It also pins, per member, the schema and
       mapping versions (via `import_manifest`) and its parameters.
     - `calculation_input_ref` rows list each pin.
     - `manifest_sha256` covers the pinned content only, not the run id or times.
       `result_sha256` covers the canonical serialisation of all outputs.
  5. **Immutability and reproducibility.**
     - The status moves only RUNNING → one terminal status, once, enforced by trigger.
     - Outputs are inserted only while RUNNING and are append-only afterwards. Nothing is
       ever updated or deleted.
     - A re-run (`rerun_of`) is a new run on the same pins and must reproduce
       `result_sha256`.
     - `POST /calculation-runs` takes an `Idempotency-Key`, as in D-21 §2.
  6. **Lineage in this feature.** Every per-input contribution is an `intermediate_value`
     that references its domain row. The chain then runs through `canonical_record_id` →
     `record_lineage` → `source_record` → import. The generic `lineage_node`/`lineage_edge`
     graph is WS4 RO-03.
  7. **Switch.** `CALCULATIONS_ENABLED` (default `true`): when off, new snapshots and runs get
     409 `calculations_disabled` (audited). Existing runs stay readable.
  8. **No AI.** Nothing on the run path calls the AI layer.
- **Consequences:** datasets are small enough for synchronous runs under the D-16 bounds.
  Async execution comes with WS4.

- **Amendment (2026-10-17, Phases 15–16 build):**
  - **§1 module check:** there is no start-up registry check, because the app starts without
    a database. A module version that is unregistered or not AVAILABLE is refused when a run
    is submitted (409 `module_not_available`, audited).
  - **PLANNED versions:** these are seeded with no code. The seeded rows are a superset of
    the code registry.
  - **§5 re-runs:** a re-run (`rerun_of`) must reproduce the original `result_sha256`.
    Otherwise it ends FAILED with a `POST_CHECK_FAILED` warning
    (`{"check": "RERUN_REPRODUCED"}`) and stores no results. A re-run whose pins can no
    longer be resolved gets 409 `rerun_not_reproducible`.

## D-32 Rule, module and assumption registry: parameters, lifecycle and maker-checker

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17)
- **Date:** 2026-10-17
- **Question:** how are methodology parameters represented and changed?
- **Recommended:**
  1. **Rules are data.** A `rule_definition` has a code, a category, a target module, an
     owner and a `parameter_schema` built only from a closed list of kinds:
     - `DECIMAL(min, max, scale)`;
     - `INTEGER(min, max)`;
     - `ENUM(values)`;
     - `DAY_BUCKETS`: ordered, contiguous half-open day intervals, with an open last bucket;
     - `ENUM_MAP`;
     - `DECIMAL_MAP`.

     There are no expressions, formulas, templates or user code (as in D-19). Adding a kind
     needs code, tests and an appended decision.
  2. **Versions.**
     - A `rule_version` holds immutable `parameters` plus `parameters_sha256`.
     - The lifecycle is the Data Model §9 set plus REJECTED (D-06): DRAFT → IN_REVIEW →
       ACTIVE (approval) or REJECTED, then ACTIVE → SUPERSEDED or RETIRED.
     - A version is immutable once it leaves DRAFT, enforced by a DB trigger that also covers
       TRUNCATE.
     - At most one ACTIVE version exists per definition. Activating a new version
       supersedes the previous one in the same transaction.
  3. **Maker-checker.**
     - Proposing and submitting need `rule:propose`.
     - Approving needs `rule:approve`, and the approver is not the author (409
       `self_approval`).
     - Every step and every refusal is audited.
     - `RULE_CHANGE_ENABLED=false` freezes the rules at their ACTIVE versions.
  4. **Content-addressed rule sets.** Each run's set of pinned rule versions is written once
     as a `rule_set` (plus its members), keyed by the hash of the sorted version ids. So a
     "rule-set version" exists without a separate rule-set authoring workflow.
  5. **Bucket definitions.** `maturity_bucket_definition` rows are the immutable
     materialisation of a `DAY_BUCKETS` rule version. There is one lifecycle, not two.
  6. **Assumption sets.** They are versioned, with provenance, owner, effective dates and
     status. They are seeded and read-only in Feature 03, and every run pins one. Authoring
     is deferred.
  7. **Seeded versions.** These are ACTIVE by a tagged migration (D-11). They record
     `approved_by = <seed tag>` and are marked PROVISIONAL and ILLUSTRATIVE. They are
     replaced only through point 3.
- **Alternatives:**
  - Free-form JSON parameters. Rejected: they cannot be governed or validated.
  - Formula strings. Rejected: Arch §7 and the WS cross-cutting rules forbid them.
  - Hand-authored rule sets. Deferred: content addressing is enough for pinning.

- **Amendment (2026-10-17, Phase 16 build):**
  - **DECIMAL storage:** DECIMAL and DECIMAL_MAP values are stored as JSON strings of exact
    decimal text, padded to the declared scale; extra places are refused, never rounded.
    Any fractional or exponent JSON number in parameters is refused (`inexact_number`).
  - **Bucket boundaries:** DAY_BUCKETS may start at a lower bound of -1, so the first bucket
    can hold a flow due on the reporting date (day 0). The seeded v1 starts at 0, which
    leaves day 0 open as a methodology item.
  - **Bucket definitions:** these are written when a version is submitted, while it is
    still DRAFT.
  - **Maker-checker:**
    - only the author may submit a version (409 `not_author`);
    - a decider who is the author gets 409 `self_approval`;
    - approving a version proposed before another activation gets 409 `stale_version`.

## D-33 Classification and masking of calculation outputs

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17). PROVISIONAL.
- **Date:** 2026-10-17
- **Context:** the inputs that drive results are `confidential`: cash-flow `amount`, position
  `balance` and instrument `notional`. `risk_analyst` has clearance `internal` (BE-3.5 seed).
  Under input-level masking, the role that runs calculations would see masked results.
- **Question:** what classification do outputs carry?
- **Options:**
  - **A (recommended):**
    - aggregates (bucket and currency totals, gaps, HQLA, outflow and inflow totals,
      ratios) are `internal`;
    - per-input contributions, intermediate values and asset rows carry the most sensitive
      classification among their input fields;
    - the API masks by the caller's clearance, as in BE-3.5.
  - **B:** every output inherits its inputs' classification. The analyst then sees masked
    totals unless clearances change.
  - **C:** raise `risk_analyst` clearance to `confidential`. That is a change to the role
    model, which the client must approve.
- **Consequences of A:** an analyst sees aggregates and runs the module, but not
  instrument-level amounts. The client must confirm this before production.
- **Amendment (2026-10-17, Phase 15 review):** an aggregate built from very few inputs
  would show an instrument-level amount. For example, a reporting date chosen so that one
  cash flow sits alone in a bucket makes that bucket's total equal to the flow's
  confidential amount. So a minimum-aggregation rule applies:
  - **The rule:** a ladder figure built from fewer than `CALC_MIN_AGGREGATE_COUNT`
    contributions takes its contributions' classification. The setting defaults to 3,
    is PROVISIONAL, and turns the rule off at 1.
  - **Derived figures:** inflows, outflows and net take the most sensitive part they are
    built from.
  - **Cumulative gap:** once one net gap in a currency is masked, the cumulative gap stays
    masked for the rest of that currency.
  - **Zeros:** zero figures stay clear.
  - **Where it applies:** on read. Stored results and `result_sha256` do not change.
  - **Residual risk:** comparing clear totals from two runs whose reporting dates move one
    flow between buckets can still reveal an amount. Closing that needs option C, or a
    limit on reporting dates. This is an open item for the client before production.
- **Amendment (2026-10-17, Phase 17 review):**
  - **Non-zero contributions only:** only non-zero contributions count toward
    `CALC_MIN_AGGREGATE_COUNT`. Dormant zero-balance or zero-amount rows would otherwise let
    one real amount show in clear. This applies to the maturity ladder and to LCR
    (`lcr_result.*_nonzero_count`).
  - **LCR figures:** they follow the same rule. An HQLA level, outflows or inflows built from
    fewer than N non-zero contributions are masked, and so is every figure derived from
    them. Per-asset rows are masked by their own classification.

## D-34 WS3 permissions and role grants

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17). PROVISIONAL (D-11).
- **Date:** 2026-10-17
- **Decision (proposed):** seven new permissions, seeded by a tagged migration:
  - `domain:read`, `rule:read` and `calc:read` go to all five roles;
  - `domain:project` goes to data_steward;
  - `rule:propose` and `calc:run` go to risk_analyst;
  - `rule:approve` goes to approver.
- **Segregation of duties:**
  - stewards curate data and analysts calculate;
  - analysts propose methodology changes and approvers activate them;
  - platform_admin and auditor read only.
- **Consequences:** the matrix is in the Feature 03 section of `docs/DELIVERY_PLAN.md`. The
  WS1 matrix test covers every WS3 route.

## D-35 Illustrative methodology seeds for the prototype modules

- **Status:** active (Feature 03 plan approved by the user, 2026-10-17). PROVISIONAL / ILLUSTRATIVE.
- **Date:** 2026-10-17
- **Context:** the client's bucket sets, cash-flow conventions and liquidity parameters are
  unknown (Data Model §11). WS3-P4 still asks for a bounded LCR/HQLA and maturity-ladder
  demonstration on synthetic data.
- **Decision (proposed):**
  1. **Maturity ladder v1** works per currency, with no FX.
     - Its seeded rules:
       - `maturity_buckets`: illustrative day intervals (0,1], (1,7], (7,30], (30,90],
         (90,180], (180,365], (365,730], (730,∞), counted as calendar days after the
         reporting date;
       - `cashflow_direction`: LOAN and BOND → INFLOW, DEPOSIT → OUTFLOW, as an
         illustrative asset/liability assumption, because `cashflow_event` v1 has no
         direction;
       - `past_due_treatment`: EXCLUDE_WITH_WARNING.
     - Both CONTRACTUAL and BEHAVIOURAL origins are included and shown separately.
  2. **LCR/HQLA v1** also works per currency, and its parameters are illustrative:
     - BOND is Level 1 with a 0 haircut, and other types are not HQLA;
     - DEPOSIT has a 0.10 run-off;
     - LOAN inflows have a 0.50 rate, a 30-day horizon and a 0.75 inflow cap;
     - the value is the position `balance`, because the schema has no market value;
     - there are no L2A/L2B caps;
     - the ratio is quantized to 4 dp ROUND_HALF_EVEN.
  3. **Labelling.** Every seeded rule version, module and UI result says "Illustrative
     prototype. Not a regulatory calculation; no parity with any vendor system is claimed."
  4. **Golden cases.** Engineering golden cases are hand-computed fixtures marked
     `ENGINEERING — awaiting domain-expert approval`. They are not "approved golden cases"
     until a named financial owner signs them off (Arch §12, §17).
- **Consequences:** replacing these values is a rule-version change through D-32 §3 and needs
  no code. Nothing here may be used for client decisions.

---

<!-- Never renumber existing IDs. New decisions are appended as D-05, D-06, … -->

## D-36 Phase build protocol: parallel agent swarm and auto-resolved questions

- **Status:** active (requested by the user, 2026-10-17)
- **Context:** in Feature 03, phases moved fastest with parallel agents, the API contract
  landing first, CI as the full e2e run, and parallel phases built in separate worktrees.
  The user asked for this to be the default, and for in-phase questions not to stop the
  build.
- **Decision:**
  - **Protocol.** Every phase follows the "Phase build protocol (parallel agent swarm)" in
    `CLAUDE.md`:
    - contract first;
    - a one-message fan-out, with each agent owning its own files;
    - isolation per D-03;
    - targeted tests per agent, the full backend suite run once by the coordinator, and CI as
      the full Playwright run;
    - D-26 still applies;
    - one review alongside e2e: MAJOR and MEDIUM findings fixed before merge, LOW ones logged;
    - pre-numbered migrations for parallel phases, which the coordinator re-chains.

    `.claude/workflows/phase-build.js` runs it.
  - **Open questions.** They go to the read-only `decision-resolver` agent. It follows
    existing decisions; where none applies, it takes the safer or stricter option. Each
    ruling is recorded as **auto-resolved**: as a dated amendment here, or as a phase-report
    note.
  - **Hard stops,** sent to the user in one message at the end of a phase:
    - deleting data, or anything irreversible on shared systems;
    - a D-01 exception;
    - a change to roles, permissions or who approves what;
    - real client data, regulatory claims or vendor-parity claims;
    - client decisions needed before production (listed as open items; they don't block the
      build).
  - **Standing approval.** "Build Phase N" means run the workflow, merge on green CI, and
    report once at the end.
- **Consequences:** fewer interruptions, and every auto-resolved ruling stays traceable. Hard
  stops never get resolved automatically.

## D-37 Credit risk data in the domain: counterparties and credit exposures

- **Status:** active (Feature 04 plan approved by the user, 2026-10-19). PROVISIONAL (D-11).
- **Date:** 2026-10-19
- **Context:** Feature 04 needs obligors, exposures and credit attributes. At master `2db56b7`
  the domain has none of them:
  - the 0017 tables hold instrument, position and cash flow only (`domain/models.py`);
  - `instrument` v1 has `counterparty_id` (STRING, confidential, 0007:62), but projector v1
    does not project it: it is neither a typed column nor extension-eligible, so it stays in
    `canonical_record` (`domain/constants.py:133-147`);
  - `rating_category` exists only in the DRAFT `instrument` v2 (0007:85);
  - there is no days-past-due, default flag, undrawn amount or limit, facility type,
    collateral, origination grade or effective interest rate anywhere, and `instrument` v1
    `maturity_date` is free text (D-29 §4).

  Data Model §4 lists `counterparty` (spec :901) and `collateral_asset`/`collateral_link`
  (:930-932), but no credit-exposure entity. §11 (:1231) leaves "whether … impairment … is in
  scope" unresolved.
- **Question:** how do credit attributes reach the domain with lineage to a source row?
- **Options:**
  - **A (recommended):** two new seeded ILLUSTRATIVE schemas, `counterparty` v1 and
    `credit_exposure` v1. They go through the unchanged WS2 pipeline (source, import,
    mapping, validation, reconciliation) and are projected (D-29) into new append-only
    tables.
  - **B:** a `position` v2 carrying the credit fields. Rejected:
    - Feature 03 snapshots and mappings pin `position` v1 (D-18);
    - activating v2 changes WS2 behaviour for every position source;
    - a snapshot holds one import per schema code (D-30 §1), so liquidity positions and
      credit exposures could not sit in one snapshot.
  - **C:** credit data typed in the app. Rejected: there would be no lineage to a source row.
- **Recommended (A):**
  1. **Schemas** (seeded ACTIVE by a tagged migration, as 0007):
     - `counterparty` v1: `counterparty_id` (key), `counterparty_name` (confidential),
       `segment`, `country`;
     - `credit_exposure` v1: `exposure_id` (key), `counterparty_id`, `instrument_id`
       (optional, not read by the v1 modules), `as_of_date`, `currency`, `facility_type`,
       `drawn_amount` and `undrawn_amount` (confidential), `days_past_due` (INTEGER),
       `default_flag` (BOOLEAN), `origination_date`, `origination_grade`, `maturity_date`
       (DATE, required), `effective_interest_rate` (DECIMAL, a fraction), `collateral_type`
       and `collateral_value` (confidential).
  2. **Identity.** A stable UUID per (tenant, source system, business key), as in D-29 §3.
     A later import adds a version and never updates an old one.
  3. **Projection.** The specs for the two new codes are added under `projector_version` 1.
     The three existing specs stay frozen. `ColumnKind` gains INTEGER and BOOLEAN. A STRING
     is still never parsed. Vocabulary fields (segment, facility type, collateral type,
     origination grade) are kept as text, as given. The module maps them, and an unmapped
     value is a reasoned exclusion or warning, never silent.
  4. **References.** An exposure resolves its counterparty by business key within the
     snapshot and the tenant, as in D-30 §4. An unresolved reference is an exclusion with a
     reason.
  5. **Vocabularies.** A seeded `credit_vocabulary` table (tagged, PROVISIONAL, append-only)
     holds:
     - `rating_grade`, with a rank and a default-grade flag;
     - `credit_segment`;
     - `facility_type`;
     - `collateral_type`.

     Grade codes are neutral (`G01`…`G08`, `D`). Rating-agency symbols are never used, so no
     vendor or agency parity is implied.
  6. **Collateral** is simplified to one type and one value per exposure. `collateral_asset`,
     `collateral_link` and the PLANNED `collateral` module (0021) stay deferred (FC-06).
  7. **Switch.** `CREDIT_PROJECTION_ENABLED` (default `true`). When it is off, the two new codes
     get 409 `projection_unsupported` (audited), which is exactly the pre-Feature-04
     behaviour. Projected rows stay readable.
- **Consequences:**
  - This depends on D-38, the CHECK widening.
  - A credit snapshot holds one `counterparty` import and one `credit_exposure` import.
  - `ambiguous_business_key` (D-30 §3) also covers counterparty keys.

## D-38 D-01 exception: widen the two `schema_code` CHECKs

- **Status:** active. **D-01 exception approved by the user (2026-10-19):** widen the CHECKs;
  fallback B is not used.
- **Date:** 2026-10-19
- **Context:** two columns are CHECK-constrained to `instrument`, `position` and
  `cashflow_event` (`DomainSchemaCode`):
  - `domain_projection.schema_code` (0017, `domain/models.py:67`);
  - `dataset_snapshot_member.schema_code` (0018, `calculations/models.py:109`).

  D-37 A needs `counterparty` and `credit_exposure` in both. CLAUDE.md lists widening an
  existing CHECK as a D-01 exception. Phase 17 avoided one by keeping the LCR warnings on
  `lcr_result` rather than widening `calculation_warning.code`.
- **Options:**
  - **A (recommended):** migration 0022 replaces both CHECKs with wider ones. The constraint
    is dropped and re-added in one transaction, under the same name.
    - No row changes, and every existing row satisfies the wider CHECK.
    - The downgrade refuses while any row carries a new code, then restores the narrow
      CHECK.
    - A test proves the round trip, and that an unknown sixth code is still refused.
  - **B (fallback, no exception):** parallel tables `credit_domain_projection` and
    `dataset_snapshot_credit_member`, with the snapshot content hash extended only when credit
    members exist, so Feature 03 hashes are unchanged. This avoids the exception, but it
    duplicates the projection and snapshot machinery (two refusal paths and two hash paths).
- **Not widened, by design** (the Phase 17 pattern): `calculation_warning.code`,
  `calculation_input_ref.ref_type`, `intermediate_value` (bound to ladder rows) and
  `rule_definition.category`. Credit exclusions and warnings live on the new result tables
  (D-41 §4), and rating pins live in a new table (D-39 §5).
- **If declined:** Phase 19 builds option B. BE-19.1 and BE-19.4 name the fallback.
- **Amendment (2026-10-19, Phase 19 build, auto-resolved):** how an approved D-01 exception
  is recorded and checked.
  - It is recorded in two places that must match: an entry in `D01_EXCEPTIONS` in
    `backend/app/tests/test_committed_artifacts.py` (revision → decision ID and the one allowed
    operation; for 0022, `("D-38", "drop_constraint")`), and the migration's own
    `D01_EXCEPTION` constant (`0022_credit_domain.py:48`).
  - The additive-only check scans the whole migration module minus its docstring and
    `downgrade()` (not only `upgrade()`), so a destructive call in a helper is still caught.
  - The resolver can never add a `D01_EXCEPTIONS` entry. Each new entry needs its own
    user-approved decision (a hard stop under CLAUDE.md).

## D-39 Obligor rating workflow: versioned, maker-checker, effective-dated, pinned

- **Status:** active (Feature 04 plan approved by the user, 2026-10-19). PROVISIONAL.
- **Date:** 2026-10-19
- **Question:** how does an obligor get a grade that a calculation can rely on and reproduce?
- **Recommended:**
  1. **Object.** `obligor_rating` holds one immutable version per proposal, for one
     counterparty identity. It records:
     - the grade (`rating_grade` vocabulary);
     - `effective_from` (DATE);
     - a rationale of 1 to 2,000 characters (required);
     - the proposer and the time.

     The decision fields (decider, time, comment) are set once.
  2. **Lifecycle** (a D-06 subset).
     - A proposal is written IN_REVIEW. There is no DRAFT, because a proposal has nothing to
       edit; a correction is a new proposal.
     - IN_REVIEW → ACTIVE (approve) or REJECTED.
     - ACTIVE → SUPERSEDED, in the same transaction, when another version for the same
       counterparty and `effective_from` is approved.
     - RETIRED is reserved, because withdrawal is deferred.

     A DB trigger freezes the content, allows only these moves (once each) and refuses
     DELETE and TRUNCATE. A partial unique index allows one ACTIVE version per (counterparty,
     `effective_from`).
  3. **Maker-checker.**
     - Proposing needs `rating:propose`. Approving or rejecting needs `rating:approve`, plus
       a comment of 1 to 1,000 characters.
     - The decider is not the proposer (409 `self_approval`).
     - Approving a version that was proposed before another activation for the same
       counterparty and date gives 409 `stale_version`.
     - Deciding a version that is not IN_REVIEW gives 409 `invalid_status`.
     - An unknown grade gives 422 `invalid_grade`.
     - An absent or foreign counterparty gives the same 404.
     - Every step and refusal is audited (`obligor_rating.propose`, `.approve`, `.reject`,
       `.supersede`), and refusals are audited DENIED.
  4. **In effect at a date.** For counterparty *c* and date *d*, the rating in effect is the
     ACTIVE version with the greatest `effective_from` ≤ *d*. History stays: an ACTIVE version
     with an earlier `effective_from` still governs its own period.
  5. **Pinning.**
     - At run submission, the engine resolves §4 at the reporting date for every
       counterparty in the snapshot. It writes `calculation_rating_pin` rows and a sorted
       `ratings` manifest section, which `manifest_sha256` covers.
     - A re-run uses the pinned rating versions, even if they have since been SUPERSEDED.
     - With no rating in effect, the exposure is excluded as `NO_APPROVED_RATING`. The
       source `origination_grade` is never used as a fallback.
  6. **Switch.** `RATING_CHANGE_ENABLED` (default `true`). When it is off, proposing and
     deciding get 409 `rating_change_disabled` (audited). Reads and runs keep working and pin
     the ACTIVE ratings as they stand.
  7. **No AI.** Grades are human judgements. Nothing suggests a grade (WS5).
- **Alternatives:**
  - A rating as a rule version. Rejected: ratings are per-obligor data, not methodology.
  - Source-supplied current ratings only. Rejected: the requirement asks for an in-app
    workflow. The source `origination_grade` stays as data for the SICR test.
- **Amendment (2026-10-19, Phase 20 build, auto-resolved):** §3 made precise.
  - **Stale.** A version is stale when the ACTIVE version for the same counterparty and
    `effective_from` has `decided_at` > this version's `proposed_at` (strict). Both times come
    from the DB clock (`clock_timestamp()`), read after the per-counterparty transaction
    lock. A proposal for another `effective_from` is never stale (each date governs its own
    period, §4).
  - **Stale versions can be rejected.** Only approval is refused with `stale_version`;
    REJECT of a stale version succeeds (otherwise it would sit IN_REVIEW for ever, as
    withdrawal is deferred). Asserted in `test_credit.py:737-740`.
  - **Audit resources.** A refused proposal (no rating id yet) is audited on
    `resource_type = 'counterparty'`. Every other rating event, successes and decide
    refusals, is on `obligor_rating` with the rating id. Audit details never carry the
    rationale; the grade is tagged internal and the decision comment confidential in
    `details_classification` (D-43 §2).

## D-40 Credit permissions and role grants

- **Status:** active. **Permission change approved by the user (2026-10-19):** `rating:propose`
  → risk_analyst, `rating:approve` → approver. PROVISIONAL (D-11).
- **Date:** 2026-10-19
- **Decision (proposed):** two new permissions, seeded by the tagged migration
  `0023_credit_permissions`:
  - `rating:propose` goes to risk_analyst;
  - `rating:approve` goes to approver.

  Nothing else changes:
  - reads of counterparties, exposures, ratings and credit vocabularies reuse `domain:read`
    (all five roles);
  - ECL runs reuse `calc:run` (risk_analyst) and `calc:read`;
  - credit rule changes reuse `rule:propose` and `rule:approve` (D-34);
  - there is no new role and no clearance change. platform_admin, data_steward and auditor
    gain nothing.
- **Segregation of duties:** analysts propose ratings and approvers decide them. A user who
  holds both roles cannot decide their own proposal (409 `self_approval`, D-39 §3).
- **Open for the client:** whether a separate credit-officer role, a credit committee (more
  than one approver) or approval limits by exposure size are needed. None of these is built.
- **Consequences:**
  - `governance/constants.py` gains `CREDIT_ROLE_PERMISSIONS`, and `ROLE_PERMISSIONS` becomes
    WS1 ∪ WS2 ∪ WS3 ∪ credit.
  - The WS1 matrix test covers every new route.

## D-41 Credit ECL modules and engine extensions

- **Status:** active (Feature 04 plan approved by the user, 2026-10-19).
- **Date:** 2026-10-19
- **Recommended:**
  1. **Module `credit_ecl` v1** follows the D-31 interface: pure, Decimal only, no I/O.
     - Its inputs are counterparty versions, credit-exposure versions, the pinned ratings and
       the grade vocabulary (rank, default grade).
     - Its required schemas are `counterparty` and `credit_exposure`.
     - It reads its rules by code from its input contract. Rules already resolve by code
       (`rules/service.py:434-451`), not by target module.
  2. **Variant module `credit_ecl_cecl` v1** (Phase 22). It reads the same rule definitions by
     code (owned by `credit_ecl`) and measures every exposure over its lifetime. Both modules
     can run on the same snapshot and pins, so they can be compared.
     - Rejected alternative: an `ecl_measurement_basis` rule parameter. `rule_definition` is
       append-only, and a comparison would need two maker-checker cycles to flip the ACTIVE
       version.
  3. **Engine.**
     - `ModuleInputs` and `ModuleResult` gain optional credit fields, which stay empty for
       Feature 03 modules.
     - Rating pins and the `ratings` manifest section exist only when a module's input
       contract declares `ratings`.
     - `ENGINE_VERSION` stays `"1"` and `MANIFEST_VERSION` stays `1`.
       `calculations/service.py:1040-1044` refuses re-runs once either changes, so bumping
       them would make every Feature 03 run unreproducible. A Feature 03 manifest keeps
       exactly its key set.
  4. **Outputs.** These are new tables under 0018's run-output guards (inserted only while the
     run is RUNNING, then append-only):
     - `calculation_rating_pin`;
     - `ecl_exposure_result`: one row per in-scope exposure version, INCLUDED or EXCLUDED with
       a reason, carrying the factors, the stage and its reason, the horizon, EAD, the LGD
       amounts, the ECL and `warning_codes`;
     - `ecl_term_result`: per year, for the lifetime drill-down;
     - `ecl_result`: per currency and stage, the counts, non-zero counts, EAD and ECL totals
       and `warning_codes`.

     The figure identities are CHECK-constrained. Exclusion reasons, stage reasons and warning
     codes are closed lists that live on these rows (D-38). A run with any of them ends
     SUCCEEDED_WITH_WARNINGS.
  5. **Post-checks** (`PostCheck` gains them in code only):
     - `ECL_COVERAGE`: every exposure version is in exactly one row;
     - `ECL_TERMS`: an exposure's ECL is the sum of its terms;
     - `ECL_TOTALS`: each stage total is the sum of its included rows;
     - `STAGE_RULES`: each stage is recomputed from the row's own fields.
  6. **Rounding.** There is one declared rounding: 0.01 ROUND_HALF_EVEN (PROVISIONAL), applied
     once per discounted term through `modules/base.py` `divide`. Everything else is exact.
     No LGD or coverage ratio is computed, because that would need a second rounding. Amounts
     are shown instead.
  7. **Rule vocabularies.** `PARAMETER_VOCABULARIES` gains `rating_grade`, `credit_segment`,
     `facility_type` and `collateral_type`. This adds no parameter kind (the D-32 §1 list is
     unchanged) and uses only existing rule categories (no CHECK widening).
  8. **No AI** on the run path (D-31 §8).
- **Consequences:** retiring a module version hides it from "New run", as in Phase 17.
  Existing runs stay readable.

## D-42 Illustrative credit methodology seeds

- **Status:** active (Feature 04 plan approved by the user, 2026-10-19). PROVISIONAL / ILLUSTRATIVE.
- **Date:** 2026-10-19
- **Context:** the client's master scale, LGD, CCF, staging criteria and discounting
  conventions are unknown (Data Model §11). Every value below is chosen for arithmetic
  coverage. None is calibrated, and none is taken from any agency or vendor scale.
- **Decision (proposed):**
  1. **Master scale.** `pd_master_scale` is a DECIMAL_MAP from `rating_grade` to the 12-month
     TTC PD, in [0, 1] at scale 6:
     - G01 0.000300, G02 0.000800, G03 0.002000, G04 0.005000;
     - G05 0.012000, G06 0.030000, G07 0.080000, G08 0.200000;
     - D 1.000000.
  2. **TTC → PIT.** `pd_pit_scalar` is a DECIMAL in [0, 5] at scale 4, seeded 1.0000. PIT PD =
     min(1, TTC × scalar), with `PD_CAPPED` when the cap binds.
  3. **Lifetime term structure.** The annual PD is constant: marginal PD_k = (1 − PD)^(k−1) ×
     PD, for k = 1…n.
     - n = ceil(days to maturity / 365), at least 1 and at most `lifetime_max_years`
       (INTEGER in [1, 50], seeded 30). `LIFETIME_CAPPED` is raised when the cap binds.
     - A maturity on or before the reporting date gives n = 1 with `MATURITY_PASSED`.
  4. **EAD** = drawn + CCF × undrawn. `ccf` is a DECIMAL_MAP over `facility_type`, in [0, 1] at
     4 dp: TERM_LOAN 0.0000, REVOLVER 0.7500, GUARANTEE 0.5000. EAD stays constant over the
     lifetime, with no amortisation (a documented limitation).
  5. **LGD.**
     - `lgd_unsecured` is a DECIMAL_MAP over `credit_segment`, 4 dp: CORPORATE 0.4500,
       SME 0.5000, RETAIL 0.6000, SOVEREIGN 0.4500.
     - `collateral_haircut` is a DECIMAL_MAP over `collateral_type`, 4 dp: CASH 0.0000,
       SECURITIES 0.2000, REAL_ESTATE 0.3000.
     - Collateral after haircut = value × (1 − haircut).
     - Uncovered EAD = max(0, EAD − collateral after haircut).
     - LGD amount = unsecured LGD × uncovered EAD.
     - No collateral counts as 0. An unmapped collateral type is ignored, which is the
       conservative choice, and raises `COLLATERAL_TYPE_UNMAPPED`.
  6. **Staging** (IFRS 9-style, seeded as rules). The thresholds are INTEGER rules:
     `stage2_dpd_backstop` 30, `stage3_dpd_backstop` 90 and `sicr_notch_threshold` 3.
     - **Stage 3** when DPD ≥ the Stage 3 threshold, the pinned grade is the default grade,
       or `default_flag` is true.
     - **Stage 2** otherwise, when DPD ≥ the Stage 2 threshold, or current rank − origination
       rank ≥ the notch threshold. An unknown origination grade also gives Stage 2, which is
       the conservative choice (`ORIGINATION_GRADE_UNKNOWN`).
     - **Stage 1** otherwise.
     - A Stage 2 threshold that is not below the Stage 3 threshold BLOCKS the run with
       `PARAMETERS_INVALID`.
  7. **Measurement.**
     - Stage 1: a 12-month horizon, i.e. one term (k = 1) with no pro-rating below one year (a
       limitation).
     - Stage 2: the lifetime sum of the terms.
     - Stage 3: ECL = the LGD amount (PD 1, undiscounted, PROVISIONAL).
     - The CECL-style variant measures Stages 1 and 2 over the lifetime alike, and Stage 3 as
       above.
  8. **Discounting.** Each term is PD_k × LGD amount / (1 + EIR)^k, annual, using the
     exposure's EIR (a fraction ≥ 0). Each term is rounded once to 0.01 (D-41 §6).
  9. **Labelling.** Every rule, module, response and page carries the D-35 §3 statement.
     Credit pages add: "IFRS 9-style and CECL-style logic for demonstration only. Not an
     IFRS 9 or CECL compliant measurement."
  10. **Golden cases** are `ENGINEERING — awaiting domain-expert approval` (D-35 §4).
- **Consequences:** values change only through rule versions (D-32 §3). Changing the grade
  vocabulary or the shape of the term structure needs code and a migration.

## D-43 Classification and masking of credit data and outputs

- **Status:** active (Feature 04 plan approved by the user, 2026-10-19). PROVISIONAL. Extends D-33.
- **Date:** 2026-10-19
- **Decision (proposed):**
  1. **Input fields.**
     - Confidential: `counterparty_name`, `drawn_amount`, `undrawn_amount` and
       `collateral_value`.
     - Internal: segment, grades, DPD, default flag, EIR, facility type, collateral type and
       business keys.
     - Public: country and currency.
  2. **Ratings.** The grade is `internal`. The rationale and the decision comment are
     `confidential`, so they are masked for risk_analyst, including the analyst's own
     rationale once submitted. Auto-resolved 2026-10-19 (D-36): this is the safer default,
     and an open item for the client.
  3. **Exposure result rows.**
     - Amounts take their inputs' classification (confidential), so they are masked for
       risk_analyst: drawn, undrawn, EAD, collateral after haircut, uncovered EAD, LGD amount,
       ECL and the term amounts.
     - Factors are `internal` and shown in clear: PD, CCF, unsecured LGD, haircut, stage and
       its reason, grade, notches and years.
  4. **Aggregates.**
     - `ecl_result` EAD and ECL totals per currency and stage are `internal`, under the D-33
       minimum-aggregation rule, which counts non-zero contributions only (non-zero EAD for
       the EAD total, non-zero ECL for the ECL total).
     - A currency total across stages is a derived figure: it is masked when any stage part is
       masked, so a masked stage cannot be recovered by subtraction.
     - Zeros stay clear and counts are public. There is no cross-currency total (no FX).
  5. **Where it applies:** on read. Stored results and `result_sha256` do not change.
  6. **Residual risk:** as in D-33, differencing two runs can still reveal an amount (for
     example, when a rating change or a reporting date moves one exposure between stages).
     This is an open item for the client before production.

## D-44 Stress scenarios: three templates, versioned data, exploration and promotion

- **Status:** proposed (Feature 05 plan, revised 2026-10-19 to follow the user's design
  `docs/design/agentic-stress-explorer.md`). PROVISIONAL / ILLUSTRATIVE. The approval model in
  §5–6 was **approved by the user on 2026-10-19** (see D-47).
- **Date:** 2026-10-19
- **Context:**
  - The user's design: §4 "A scenario is data, not generated code"; §8 three MVP templates;
    §5 the worked example.
  - WS4 RO-02 and Data Model §6 (`scenario`, `scenario_change`, `scenario_run`, spec
    :991-995) and §9 `scenario.status` (:1180).
  - `backend/app/orchestration/` is an empty skeleton (ARCHITECTURE.md:59).
- **Recommended:**
  1. **Data, never code.** A `scenario_version` holds 1 to 5 shocks. Each shock is a template
     from a closed list, with typed parameters and a selector. There are no expressions,
     formulas or user code (D-19, D-32 §1). Adding a template needs code, tests and an
     appended decision. Amounts and fractions are exact decimal text, never binary floats.
  2. **Templates (v1, design §8).**
     - **`DELAYED_INFLOWS`** (delayed loan repayments).
       - `cashflow_type`: an instrument type whose pinned direction is INFLOW. The design's
         `LOAN_REPAYMENT` maps to `LOAN`, because `cashflow_event` v1 has no cash-flow type
         field.
       - `affected_fraction`: DECIMAL, 0 < f ≤ 1, scale 4.
       - `delay_days`: INTEGER, 1 to 3650.
       - `delay_calendar`: ENUM, `CALENDAR_DAYS` only in v1.

       Each matching flow is split: f × amount moves `delay_days` later and (1 − f) × amount
       stays. With f = 1 the whole flow moves.
     - **`DEPOSIT_WITHDRAWAL`** (increased deposit withdrawals).
       - `cashflow_type`: an OUTFLOW instrument type, for example `DEPOSIT`.
       - `increase_fraction`: DECIMAL, 0 < x ≤ 1, scale 4 (for example "+15%" is `0.1500`;
         the bound is PROVISIONAL).

       Each matching outflow amount is multiplied by (1 + x). This is the only template that
       changes amounts.
     - **`FUNDING_UNAVAILABLE`** (reduced availability of a specified funding source).
       - A funding-source selector (§3).
       - `unavailable_days`: INTEGER, 1 to 365.
       - `affected_fraction`: DECIMAL, 0 < f ≤ 1, scale 4 (1 means fully unavailable).
       - `delay_calendar`: `CALENDAR_DAYS`.

       For matching INFLOW flows due in days (0, `unavailable_days`], f × amount moves to day
       `unavailable_days` + 1 and the rest stays. Flows due later are untouched. It is a
       timing shock only; lost funding (reducing the amount) is deferred.
  3. **Selectors.**
     - **Common optional fields:** `currency`; `origin` (CONTRACTUAL or BEHAVIOURAL); and a due
       window `due_from_day` / `due_to_day`, counted in days after the reporting date (for
       example, "repayments expected this month").
     - **Funding source.** There is no funding-facility attribute in the domain. For now the
       source is selected by:
       - `instrument_type` (required, INFLOW);
       - optional `instrument_keys`: at most 50 instrument business keys, picked by the
         analyst in the UI and never by the model (D-49 §3);
       - optional `source_system_code`.
     - **Limits of these selectors at master `0e443eb`:**
       - A snapshot holds one import per schema code (D-30 §1). So `source_system_code`
         matches either all of a snapshot's cash flows or none in v1. It is accepted for
         forward compatibility, and validation reports the match count.
       - Counterparty selection is **not possible in v1**: `instrument` v1 `counterparty_id`
         is not projected (the D-37 context). A proper funding-source attribute and
         counterparty selection are deferred.
  4. **Validation (design §3 C).**
     - Bodies use `extra="forbid"`.
     - Exact decimal text is padded to scale 4. More places are refused, never rounded, and
       JSON fractions or exponents are refused (`inexact_number`).
     - Bounds and units: `delay_calendar` must be `CALENDAR_DAYS`.
     - `cashflow_type` and `instrument_type` must be keys of the ACTIVE `cashflow_direction`
       rule, with the direction the template needs.
     - Every refusal is 422 `invalid_shock`, with value-free details: the shock index, the
       field and a code.
     - **Data availability:** validating against a baseline run returns the number of
       matching cash flows per shock, which are public counts. A run is refused while any
       shock matches nothing (409 `no_matching_cashflows`, D-45 §10).
  5. **Lifecycle** (D-06: the §9 `scenario.status` set plus `CONFIRMED_FOR_EXPLORATION`):
     - **DRAFT**: editable by its author only.
     - **DRAFT → CONFIRMED_FOR_EXPLORATION**: the author confirms their own version.
       Exploration runs are then allowed.
     - **DRAFT or CONFIRMED_FOR_EXPLORATION → IN_REVIEW**: the author submits it for
       promotion.
     - **IN_REVIEW → APPROVED_FOR_TEST** (a shared, approved scenario) **or REJECTED.** A
       rejected version is not runnable; its author may copy it into a new version.
     - **Any status other than DRAFT → CLOSED**: hidden from pickers. Its runs stay readable
       and re-runnable.
     - RUNNING and TESTED are reserved, because runs are synchronous.
     - Content freezes when a version leaves DRAFT, and its shocks are materialised as
       `scenario_change` rows. A DB trigger enforces the moves (once each) and refuses DELETE
       and TRUNCATE.
     - Only CONFIRMED_FOR_EXPLORATION and APPROVED_FOR_TEST versions can run. The run pins
       the approval state at that moment (D-45 §4).
  6. **Exploration versus approval (user-approved 2026-10-19).**
     - The author's own confirmation enables **exploration runs only**. They are labelled
       "Exploration, not approved" and are never authoritative: they are not listed in the
       shared library and not usable as an approved scenario.
     - Promotion is maker-checker:
       - the approver is not the author (409 `self_approval`);
       - a decision needs a comment of 1 to 1,000 characters;
       - only the author may edit, confirm or submit (409 `not_author`);
       - a version in the wrong state gives 409 `invalid_status`.
     - For an `AI_PROPOSED` version, confirming and submitting need
       `acknowledge_ai_origin: true` and acknowledgement of every `PROPOSED_DEFAULT`
       parameter (D-48 §4). Otherwise the answer is 422 `ai_review_required` or
       `unacknowledged_defaults`.
     - **The AI never confirms, submits, approves or closes.**
     - Every step and every refusal is audited, with the shocks' sha256 and never their text.
  7. **Provenance.**
     - `origin` is `MANUAL` or `AI_PROPOSED`.
     - `parent_version_id` records adaptations (design §6.4: "what if 30 days?" is a new
       version).
     - `parameter_sources` records `STATED` or `PROPOSED_DEFAULT` per AI-filled parameter,
       and is null for manual versions.
     All of these exist from 0026 on, so later phases widen nothing.
  8. **Labelling.** Every response and page carries: "Illustrative stress exploration, not a
     regulatory stress test. Not a regulatory calculation; no parity with any vendor system is
     claimed." (D-35 §3). Runs also carry "Exploration, not approved" or "Approved scenario
     (illustrative)".
  9. **Switch.** `SCENARIOS_ENABLED` (default `true`). When it is off, writes and stressed runs
     get 409 `scenarios_disabled` (audited). Reads still work.
- **Alternatives:**
  - Free-form rule overrides. Rejected: they cannot be validated, and they would change
    methodology.
  - Scenarios as rule versions. Rejected: they would flip the ACTIVE methodology for every
    run (D-32 §2).
  - An earlier draft of this plan had four non-splitting shock kinds. It was replaced by the
    user's templates.
- **Amendment (2026-10-19, Phase 24 build, auto-resolved):** §4–6 made precise.
  - **§4 vocabulary (Q3).** `invalid_shock` problem codes are `orchestration.constants.
    ShockProblem`: `not_a_list`, `too_few_shocks`, `too_many_shocks`, `not_an_object`,
    `unknown_field`, `required`, `unknown_template`, `inexact_number` (a JSON fraction, or an
    exponent including exponent text such as `"2E-1"`), `not_decimal`, `too_many_places`
    (refused, never rounded), `not_integer`, `not_text`, `out_of_range`, `unsupported_value`
    (outside a field's enum, e.g. `BUSINESS_DAYS`), `unknown_instrument_type`,
    `direction_mismatch` (both against the ACTIVE `cashflow_direction` rule),
    `invalid_window`, `too_many_items` (more than 50 keys), `duplicate_item` and
    `unknown_instrument` (a picked key that is not an instrument of the tenant with a version
    of the selector's `instrument_type` that the author may see; absent and hidden answer
    alike, D-33). An unknown key's name is never echoed (`unknown_field` on its container
    path). Patterns are matched whole (`fullmatch`), so trailing newlines are refused.
  - **§4 shape (Q9).** `details = {"problems": [{"shock_index": int | null, "field": str,
    "code": ShockProblem}]}`, the same object wrapper as `rules/service.py`. `field` is a
    structural path (e.g. `parameters.affected_fraction`); `shock_index` is 0-based, the same
    convention as `scenario_change.shock_index`, and null with `field = "shocks"` for
    list-level problems. A reversed window is reported on `selector.due_from_day`.
  - **§4 order (Q8).** On every route that names a scenario or version (and the Phase 25
    routes): permission (403, the dependency) → the tenant-scoped lookup of the path's
    scenario/version (404, absent and foreign alike) → body and state checks. Body validation
    (422 `invalid_shock`) never runs before the scoped 404. As built, `PUT …/versions/{n}`
    checks authorship and status (409 `not_author` / `invalid_status`) before the body (422),
    which is stricter (a non-author learns nothing about validation); a missing
    `parent_version` on `POST …/versions` is a 404 after the body check, inside an already
    scoped scenario.
  - **§5–6 DB guards (Q6).** The 0026 trigger also refuses `decided_by = authored_by`
    (maker-checker in the DB as well as the service's 409 `self_approval`), and `scenario` rows
    are fully append-only (no UPDATE). Renaming a scenario later needs an additive change
    (e.g. a name on the version).
- **Amendment (2026-10-19, Phase 25 build, auto-resolved):** §8 labels on stressed runs (Q21).
  The FE-25.1 badges on the run list and run detail ("Exploration, not approved" or "Approved
  scenario (illustrative)", plus the applied shocks, the baseline link and the scenario link)
  are **not** behind `NEXT_PUBLIC_STRESS_COMPARE_UI`. This is a narrow exception to D-02's
  "new behaviour sits behind a setting". Stressed runs can exist through the API while the UI
  switch is off, and showing them without their §8 label would be the unsafe option. Runs that
  are not stressed render exactly as in Phase 24, so for all earlier data the switch still
  restores the previous UI. When the explorer switch is off, the scenario link is plain text.

## D-45 Deterministic scenario engine: split-aware overlay, own contribution table, conservation

- **Status:** proposed (Feature 05 plan, revised 2026-10-19 to the user's design §2.4–2.5, §5
  and §7).
- **Date:** 2026-10-19
- **Question:** how does a scenario run "through the existing maturity ladder methodology"
  when a partial delay splits one cash flow? `intermediate_value` allows one contribution
  per cash flow and run (`UNIQUE (cashflow_event_id, calculation_run_id)`,
  `calculations/models.py:257`), and dropping that constraint would be a D-01 exception.
- **Recommended:**
  1. **Same module, same pins.**
     - A stressed run is a normal `calculation_run` of `maturity_ladder` v1.
     - It copies the baseline run's pins exactly: snapshot, reporting date, module version,
       rule set (even SUPERSEDED versions) and assumption set. It reuses the `rerun_of` pin
       resolution (`calculations/service.py:1033-1069`).
     - It adds the scenario version and the shock subset (§7). The snapshot and the baseline
       records are never changed (design §7).
  2. **Overlay (pure).** `app/calculations/scenario_overlay.py` maps
     `(ModuleInputs, pinned directions, shocks) -> (stressed ModuleInputs, parts, applications)`.
     It does no I/O and reads no clock, randomness or environment. It is Decimal only under
     `exact_context()`, and the no-float grep covers it.
     - **Parts.** Each matching flow may be split into parts. Each part is a `CashFlowInput`
       with a deterministic id (`uuid5` of the event id and the part path). So the unchanged
       module and the COVERAGE post-check see unique ids, and the engine maps each part back
       to its `cashflow_event_id`.
     - **Splitting is exact.** moved = f × amount and stayed = amount − moved. Both are exact,
       so nothing is rounded.
     - **Order.** Shocks apply in their declared order, to parts: a part can be split again.
       There are at most 5 shocks and at most 32 parts per flow.
     - **Scope.** Only flows that are complete and not past due are shocked. Others pass
       through unchanged.
     - **Direction.** It comes from the pinned `cashflow_direction` version. A shock whose
       type has the wrong direction matches nothing, and this is counted.
     - **Failure.** An overlay that cannot be applied BLOCKS the run with the existing
       `PARAMETERS_INVALID` code. No CHECK is widened.
  3. **Module untouched.** `maturity_ladder.run` runs on the stressed parts. The ladder's own
     arithmetic is the existing methodology (design §2.5).
  4. **Pins and manifest.**
     - Stressed runs only get a `scenario` section: scenario id, version id and number,
       `shocks_sha256`, the shocks, `shock_subset`, `approval_state` (EXPLORATION or
       APPROVED) and `baseline_run_id`.
     - `ENGINE_VERSION` stays `"1"` and `MANIFEST_VERSION` stays `1` (the D-41 §3 reasoning).
     - Non-stressed manifests and result documents keep exactly their Feature 03 key sets.
     - The pin row lives in the new `calculation_scenario_pin` table, because the
       `calculation_input_ref.ref_type` CHECK is closed.
  5. **Outputs: no D-01 exception.**
     - Stressed figures go to `liquidity_gap_result`, which has no per-flow constraint.
     - Per-part contributions go to a **new `scenario_contribution` table** (instead of
       `intermediate_value`). Its key is `UNIQUE (calculation_run_id, cashflow_event_id,
       part_path)`. It carries `shock_sequences`, the same lineage columns as
       `intermediate_value` (canonical record, import, source record, ordinal) and the
       contribution classification. Stressed runs write no `intermediate_value` rows.
     - `scenario_shock_application` holds one row per (run, flow, shock): days and amounts
       moved or added.
     - Both tables sit under 0018's run-output guards.
     - The ladder results and drill-down reads serve stressed runs from
       `scenario_contribution`.
     - `result_sha256` covers both tables for stressed runs only.
  6. **Post-checks** (`PostCheck`, in code only):
     - `SCENARIO_OVERLAY`: the overlay is recomputed from the unstressed inputs and equals the
       stored parts and applications.
     - `SCENARIO_CONSERVATION`:
       - per flow, the parts sum exactly to the original amount for the timing templates
         (`DELAYED_INFLOWS`, `FUNDING_UNAVAILABLE`), and to amount × (1 + x) for
         `DEPOSIT_WITHDRAWAL`;
       - per currency and direction, the stressed total over all buckets equals the baseline
         total plus the declared amount deltas. A timing-only scenario changes when money
         arrives, not how much (design §5).
     The existing post-checks run on the stressed parts.
  7. **Each shock alone and combined (design §5).** Running a version with k > 1 shocks
     creates k + 1 stressed runs: one per shock alone, and one with all of them. Each pins its
     `shock_subset`, and one `scenario_run` groups them. With k = 1 there is one run. All of
     them are synchronous and bounded (k ≤ 5).
  8. **Reproducibility.** A re-run (`rerun_of`) of any stressed run re-pins the same version
     and subset, even when the version is CLOSED, and must reproduce `result_sha256`.
  9. **Pairing.** `scenario_run` (Data Model §6) is append-only. It holds the baseline run,
     the scenario version, the approval state, the member runs and value-free counts per
     shock (matched flows, parts, direction mismatches).
  10. **Request.** `POST scenarios/{scenario_id}/run` takes `{version, baseline_run_id}` and
      an `Idempotency-Key`. Refusals are 409, audited DENIED, with nothing written:
      - `scenario_not_runnable` (the version is not CONFIRMED_FOR_EXPLORATION or
        APPROVED_FOR_TEST);
      - `invalid_baseline` (not a succeeded, non-stressed `maturity_ladder` run);
      - `baseline_not_reproducible`;
      - `no_matching_cashflows`;
      - `scenarios_disabled`;
      - `calculations_disabled`.
  11. **No AI** on this path. `app/calculations/**` and `app/orchestration/**` never import
      `app.ai` (grep).
- **Alternatives:**
  - Drop the `intermediate_value` uniqueness. Rejected: it is a D-01 exception.
  - A separate stress module. Rejected: it duplicates the methodology.
  - Whole-flow moves only (the earlier draft). Rejected: the user's design needs partial
    delays.
- **Amendment (2026-10-19, Phase 25 build, auto-resolved):** §2, §6 and §10 made precise.
  - **§6 the two post-checks (Q8).** `SCENARIO_OVERLAY` recomputes the overlay through the
    `scenario_overlay.run_overlay` seam and compares the result with the stored parts and
    applications. It checks that storage and the part-id mapping are faithful.
    `SCENARIO_CONSERVATION` checks the overlay's arithmetic. In production `run_overlay` is
    `apply()` under `exact_context()`, so both checks call the same function. The seam is the
    only production path, and no flag bypasses it. A test double that tampers with a part
    therefore fails `SCENARIO_CONSERVATION`, as the BE-25.3 Done-when requires.
  - **§2 part shape and ids (Q9).** A flow no shock touches keeps its own event id and part
    path `"0"` (`UNCHANGED`). Every part of a shocked flow, its `STAYED` part included, gets a
    `uuid5` id of the event id and the part path, under a fixed namespace constant. Part paths
    are deterministic. `DEPOSIT_WITHDRAWAL` is stored as a `STAYED` part of the original
    amount plus an `ADDED` part of exactly x × amount on the same day: conservation is exact
    (the parts sum to amount × (1 + x)) and the drill-down has its "added" row. The `ADDED`
    part counts toward the 32-part cap. Nothing is rounded.
  - **§2 and §9 per-shock counts (Q10).** The counts are each shock applied alone to the
    baseline inputs, which matches the single-shock member runs and the "refused while any
    shock matches nothing" rule (D-44 §4, D-45 §10). Flows due on day 0 are never shocked: the
    ladder excludes them as `OUTSIDE_BUCKETS`, so moving one into a bucket would break the
    per-currency total in `SCENARIO_CONSERVATION`. The key `skipped_past_due` keeps its name,
    so the contract does not change, but it is defined as "due on or before the reporting
    date (days <= 0)" wherever it is described.
  - **§10 refusal order and replay (Q13).** A re-run of a stressed run is a new stressed run,
    so it gets 409 `scenarios_disabled` while `SCENARIOS_ENABLED` is off. `RunRefusal` is an
    application enum, not a DB CHECK, so this is not a D-01 matter. The order on
    `POST scenarios/{scenario_id}/run`, as built (`orchestration/router.py:320-328`), is:
    1. the permission (403, the route dependency);
    2. the global switches, `scenarios_disabled` then `calculations_disabled` (they reveal
       nothing);
    3. the scoped 404 for the scenario or version;
    4. the Idempotency-Key (409 `idempotency_conflict` for a different request), so a replay
       returns the same `scenario_run` even after the version has closed;
    5. for a new key only: `scenario_not_runnable`, the baseline's 404, `invalid_baseline` or
       `baseline_not_reproducible`, and `no_matching_cashflows`.

    This fits the Phase 24 amendment to D-44 §4 (permission, then the scoped 404, then the
    state checks). A replay may complete member runs that a crash left missing. Each
    completed member pins the `scenario_run`'s recorded version, `shocks_sha256`, subset and baseline pins without
    resolving them again (§8), and each is audited `calculation_run.create`. The route
    description states this order.

## D-46 Baseline-versus-stress comparison, per-shock and combined, and its masking (extends D-33)

- **Status:** proposed (Feature 05 plan, revised 2026-10-19 to the user's design §2.6 and §5).
  PROVISIONAL.
- **Date:** 2026-10-19
- **Decision (proposed):**
  1. **Computed on read** from the immutable `liquidity_gap_result` rows of the baseline and
     of every member run of a `scenario_run`. Rows are joined on (currency,
     `bucket_definition_id`), which match because the pins are identical. There is no
     comparison table. Arithmetic is exact Decimal subtraction.
  2. **Figures** per currency and bucket:
     - baseline and stressed inflows, outflows, net gap and cumulative gap, plus the deltas;
     - these are given for each shock alone and for the combined run;
     - `turns_negative_net` and `turns_negative_cumulative`, meaning baseline ≥ 0 and
       stressed < 0;
     - `breaks_only_combined`, meaning the combined run is negative while the baseline and
       every single-shock run are not;
     - the largest changes, ranked by absolute net delta among clear deltas only (design
       §2.6);
     - the first negative cumulative bucket, baseline versus stressed (labelled
       illustrative).

     There is no cross-currency total. A deterministic, templated (non-AI) summary of these
     facts is part of the response. It is the fallback for D-51.
  3. **Masking.**
     - Each side is masked as its own ladder (D-33).
     - **The minimum aggregation counts distinct non-zero cash flows, not parts.** Two parts
       of one flow are one amount.
     - A delta is masked when either side is masked, or when it was changed by fewer than
       `CALC_MIN_AGGREGATE_COUNT` distinct non-zero flows.
     - Once a cumulative delta in a currency is masked, the rest of that currency's cumulative
       deltas stay masked.
     - Flags are `null` when they read a masked figure.
     - Masked deltas are never ranked, so the ranking cannot leak their size.
     - Zeros stay clear and counts are public.
  4. **Drill-down** per bucket and per shock subset lists the changed parts: cash flow,
     instrument key, shock sequences, days and bucket before and after, and the amount moved,
     stayed or added.
     - Amounts are confidential and masked for risk_analyst.
     - Days, buckets and templates are internal.
     - Every row links to the source-row lineage (D-31 §6). This is the evidence that D-51
       cites.
  5. **Residual risk:** as D-33. Differencing scenarios and the baseline can narrow down
     amounts. This is an open item for the client before production.
- **Amendment (2026-10-19, Phase 25 build, auto-resolved):** §2 and §3 made precise.
  - **§3 counting and zeros (Q11).** Inflow and outflow deltas count the distinct, non-zero
    changed flows of their own direction. The net delta counts the union of the two. A
    cumulative delta carries the classification of every earlier net delta in its currency and
    member run, and **stays masked even at zero**: here "stays masked" takes precedence over
    "zeros stay clear", because a clear zero cumulative after a masked one would give the
    masked net delta away by differencing. Zero net, inflow and outflow deltas stay clear. A
    few-flows delta takes its changed flows' classification (D-33, Phase 15 amendment), and
    is then masked by the caller's clearance: clear for data_steward, masked for
    risk_analyst.
  - **§3 "the cumulative deltas after it" (Q17).** Changed flows are counted per bucket, not
    cumulatively. Once a bucket's net delta is masked, the cumulative delta of **that** bucket
    and every later bucket in the currency is masked, separately for each member run.
  - **§2 comparison scope (Q12).**
    - (a) "Succeeded" members means SUCCEEDED and SUCCEEDED_WITH_WARNINGS. FAILED and BLOCKED
      members are listed with their status only and never compared.
    - (b) `breaks_only_combined` is `null` (not assessable) when there is no compared
      combined run, or when fewer single-shock members were compared than the version has
      shocks. It is therefore always `null` for k = 1. It is never computed from the singles
      that remain. So a `null` flag now means either "reads a masked figure" or "not
      assessable".
    - (c) The largest changes are ranked across every member's cells, but within each
      currency: the rank restarts at 1 for each currency. There is no FX and no
      cross-currency figure (D-35 §1). Only clear deltas are ranked.
- **Amendment (2026-10-19, Phase 25 build, auto-resolved; review follow-up R1, R4):** §2, §3
  and §5.
  - **§3 few-changed-flows on stressed figures (R1, review MEDIUM).** Masking only the delta
    left it recoverable as stressed minus baseline. So a stressed figure carries the changed
    flows' classification whenever 0 < its distinct non-zero changed flows <
    `CALC_MIN_AGGREGATE_COUNT`. This applies per ladder row and per direction × origin part.
    Such a figure **stays masked even at zero**, and the cumulative carry (Q11, Q17)
    applies to it. The rule lives in the stressed run's own ladder classification. So the
    comparison, the stressed run's `GET …/maturity-ladder` and every other reader mask it
    the same way. Baseline figures are unchanged.
  - **§5 residual risk, named (R1).** Two routes remain, and both can still narrow down a
    masked amount:
    - conservation across buckets in timing-only scenarios: per currency and direction the
      stressed total equals the baseline total (D-45 §6);
    - differencing across members: the combined delta minus a clear single-shock delta.

    Fully closing them needs complementary suppression, or D-33 option C. Option C is a
    clearance change, so it is a hard stop and a client decision. This stays an open item
    for the client before production.
  - **§2 summary versioning (R4).** `SUMMARY_TEMPLATE_VERSION` stays `"1"`, because no
    version was released before this merge (the Q12 per-currency sentence is part of "1").
    From merge onward, any change to the sentence templates or to how they are chosen bumps
    it. Masking changes alone do not.

## D-47 Scenario and AI permissions, and the exploration/approval model

- **Status:** **active. Approved by the user on 2026-10-19** (a hard stop: permissions, and who
  approves what). PROVISIONAL (D-11).
- **Date:** 2026-10-19
- **Decision (user-approved, option A):**
  1. **New permissions**, seeded by tagged migrations:
     - `scenario:propose` → risk_analyst (0026). It covers create, edit, **confirm for
       exploration** (own versions only), submit for promotion, and close of own
       non-approved versions.
     - `scenario:approve` → approver (0026). It covers **promotion**: approve or reject an
       IN_REVIEW version, and close an approved version.
     - `ai:use` → risk_analyst (0028). It covers agent conversations and explanations. The
       service also requires `scenario:propose`, because the agent only creates drafts.
  2. **Reused permissions.**
     - Reads of scenarios, runs, comparisons, AI conversation metadata, tool-call logs and
       explanations use `calc:read` (all five roles).
     - Stressed runs, including the agent's `run_exploration` tool on the user's behalf, use
       `calc:run` (risk_analyst).
  3. **Who approves what (user-approved).**
     - The analyst confirms their own scenario for exploration runs. Those runs are labelled
       "Exploration, not approved" and are never authoritative.
     - Promoting a scenario to an approved, shared one needs a second person, an approver who
       is not the author (409 `self_approval`).
     - **The AI never confirms or approves anything.**
  4. Nothing else changes: no new role and no clearance change. platform_admin, data_steward
     and auditor gain nothing.
- **Open for the client before production:** whether self-confirmed exploration is acceptable
  in production, and whether promotion needs a committee or an ALM role.
- **Consequences:**
  - `governance/constants.py` gains `SCENARIO_ROLE_PERMISSIONS` and `AI_ROLE_PERMISSIONS`.
  - `ROLE_PERMISSIONS` becomes WS1 ∪ WS2 ∪ WS3 ∪ credit ∪ scenario ∪ AI.
  - The WS1 matrix test covers every new route.
- **Amendment (2026-10-19, Phase 24 build, auto-resolved):** §1 close, made precise (Q5). No
  change to who can do what; this restates §1 and the Feature 05 endpoint table.
  - **Route gate:** `POST …/versions/{n}/close` is declared any-of
    `scenario:propose|scenario:approve` (`core/auth.py` `require_any_permission`); either
    permission passes the dependency, which still fails closed (403) for everyone else. A
    single-permission declaration behaves exactly as before.
  - **Service check by status:** CONFIRMED_FOR_EXPLORATION, IN_REVIEW and REJECTED need
    `scenario:propose` and authorship (else 409 `not_author`); APPROVED_FOR_TEST needs
    `scenario:approve`, with no authorship requirement (closing hides, it does not promote).
    The wrong permission is 403 `forbidden` with `details.required_permission` naming only the
    one applicable permission.
  - DRAFT, an already CLOSED version and the reserved RUNNING and TESTED are 409
    `invalid_status`. The AI never closes (checked by grep in Phase 26).

## D-48 The agent: a controlled workflow of intent, clarification and closed tools

- **Status:** proposed (Feature 05 plan, revised 2026-10-19 to the user's design §3 A–D and
  §6).
- **Date:** 2026-10-19
- **Context:**
  - The user's design: "a controlled workflow, not one unrestricted LLM call".
  - WS5: AI-01, AI-04 ("allowlisted APIs through scoped permissions, approval gates and logged
    tool calls") and AI-08.
  - Arch §13 forbids an agent that runs arbitrary SQL, changes history, or approves its own
    proposal.
  - `backend/app/ai/` is an empty skeleton.
- **Recommended:**
  1. **Provider interface.** `app/ai/providers/` defines `AgentProvider.complete(ProviderRequest)
     -> ProviderResponse`. It returns the content, model id, tokens and latency.
     - `MockProvider` is deterministic and offline. It runs from scripted fixtures, records
       its requests, and is the default (`LLM_PROVIDER=mock`). CI uses only this one.
     - `GroqProvider` calls Groq's OpenAI-compatible Chat Completions API through the
       existing `httpx` (`pyproject.toml:26`), with no SDK. It uses JSON-object mode,
       temperature 0, a timeout, a token cap, and a retry on timeout or 429 only.
  2. **A turn.** `POST ai/assist` (the WS5 name) takes `task: "SCENARIO_PLANNING"`,
     `baseline_run_id`, an optional `conversation_id`, an optional free-text `message`
     (pre-filtered per D-49 §4) and optional structured `answers`. The model must return
     exactly one closed outcome, validated with `extra="forbid"` and parsed with
     `parse_float=Decimal`:
     - **`DRAFT_SPEC`**: 1 to `AI_MAX_DRAFTS_PER_TURN` (default 3) scenario specs. Each has
       a template, parameters (each tagged `STATED` or `PROPOSED_DEFAULT`), a name of at most
       80 characters and a rationale of at most 500;
     - **`NEEDS_CLARIFICATION`**: questions, each `{shock_index, template, parameter,
       reason: MISSING | AMBIGUOUS | OUT_OF_BOUNDS, options?}`. Parameters must exist in the
       template. The UI renders the question text from templates, never from model prose;
     - **`TOOL_CALL`**: one call from the closed list (§6);
     - **`UNSUPPORTED`**: a reason code.

     Anything else is refused as `INVALID_OUTPUT`, and nothing is written.
  3. **Intent and selection (design §3 A–B).** Specs go through the D-44 validators.
     - "Delay 20% of loan inflows by two weeks" becomes `DELAYED_INFLOWS {cashflow_type:
       LOAN, affected_fraction: "0.2000", delay_days: 14, delay_calendar: CALENDAR_DAYS}`.
     - A spec that is missing a required parameter is turned into `NEEDS_CLARIFICATION` by
       the server, whatever the model returned.
  4. **Never guess silently.**
     - A `STATED` numeric parameter is cross-checked deterministically. Its number, or its
       percent form, must appear in the user's message or answers. Otherwise it is
       downgraded to `PROPOSED_DEFAULT`.
     - `PROPOSED_DEFAULT` values are highlighted, and each must be acknowledged at confirm
       (D-44 §6).
     - Funding-source `instrument_keys` are always asked for, and answered through the UI
       picker. They never come from the model, and are never sent to it.
  5. **A bounded conversation.**
     - A conversation has at most `AI_MAX_TURNS` turns (default 6) and expires after
       `AI_CONVERSATION_TTL_MINUTES` (default 60).
     - State is stored structurally in `ai_conversation`: the templates, parameters and their
       sources, open questions, the handle map and the status (OPEN, COMPLETED, EXPIRED,
       CLOSED).
     - Each turn is an `ai_request` that stores hashes only. **Raw user text, prompts and
       responses are never stored, and never replayed.** The next turn sends the structured
       state plus the new message.
     - Structured answers that complete a spec build the draft deterministically, with no
       model call.
  6. **Closed tools (design §3 D).** `app/ai/tools.py` defines typed functions that call the
     application's own service layer in-process, under the requesting user's principal.
     Permissions apply and fail closed. The tools are:
     - **`create_scenario_draft`**: a DRAFT version, `origin = AI_PROPOSED`, optionally with
       `parent_version_id` (to adapt);
     - **`validate`**: the D-44 validators plus the match counts against the baseline;
     - **`run_exploration`**: refused unless a **human** has already confirmed the version
       (CONFIRMED_FOR_EXPLORATION) or it is APPROVED_FOR_TEST. Confirming is a user route,
       never a tool;
     - **`get_result`**: run status and the masked summary;
     - **`compare`**: the masked D-46 facts.

     The rules for tool calls:
     - There is no DB, SQL, HTTP, file or approval tool.
     - Arguments use per-conversation handles (`S1`, `V2`, `R1`), never UUIDs.
     - Results passed back to the model are filtered to the D-49 schema.
     - At most `AI_MAX_TOOL_CALLS` per turn (default 6). An unknown tool or bad arguments end
       the turn as REFUSED.
     - Every call writes an `ai_tool_call` row (Data Model §7): the tool, the arguments'
       sha256, the authorization result, an outcome code and the latency. It is also audited
       as `ai_tool_call.<tool>`.
  7. **Observe and adapt (design §6.3–6.4).** The agent sees run status through
     `get_result`. "What if the delay were 30 days?" creates a new version with
     `parent_version_id`. The user confirms it, and the agent runs it and compares it side by
     side with the parent run. A bounded threshold search is deferred to v2.
  8. **Provenance.**
     - `ai_request` (per turn): task, requester, conversation and turn, provider, model id,
       prompt template version (`scenario_planning.v1`), `context_sha256`, `prompt_sha256`,
       `response_sha256`, the message's sha256 and length, the status (PENDING, COMPLETED,
       NEEDS_CLARIFICATION, INVALID_OUTPUT, FAILED, REFUSED), latency, tokens and an error
       code.
     - `ai_proposal` (per spec): status VALIDATED or REJECTED (a §9 subset), the structured
       shocks and their sha256, the `scenario_version_id` and the codes.
     - `ai_tool_call` and `ai_conversation`.
     - There are no raw-text columns. Rows are append-only, with trigger-enforced status
       moves.
  9. **No authority.** The agent never confirms, submits, approves, closes, edits history or
     executes funding transactions (design §6). A source grep on `app/ai/` enforces this: no
     reference to the confirm, submit, approval or close services, and no repository or
     SQLAlchemy import outside `app/ai/repository.py`, which holds the AI tables only.
  10. **Limits and switches, fail closed.**
      - `AI_RATE_LIMIT_PER_HOUR`: 429 `ai_rate_limited`, retryable.
      - The timeout, token, response-size and retry caps of D-50. A provider timeout is 504
        `ai_provider_timeout` and a provider error is 502 `ai_provider_error`. Both carry the
        status class only.
      - `AI_ENABLED` and `AI_SCENARIO_PROPOSALS_ENABLED` must both be on, else 409
        `ai_disabled`.
      - `groq` without a key gives 503 `ai_not_configured`.
      - With AI off, every scenario, run and comparison route works, and the D-46 templated
        summary replaces explanations (Arch §13).

## D-49 AI data boundary: context, free text, pre-filter and where a live provider may run

- **Status:** proposed. **§4 (sending the analyst's free text) and §5 (live Groq in local and
  staging only) were approved by the user on 2026-10-19** (a hard stop: data leaves the
  platform for a third party). Production use stays an open item for the client (Arch §13).
- **Date:** 2026-10-19
- **Decision:**
  1. **The context is a closed schema** (`AgentContext`, `extra="forbid"`). It holds only:
     - the D-44 statement and the template catalogue with bounds;
     - the bucket codes, labels and day bounds of the pinned bucket rule;
     - the currencies present;
     - the instrument types and their directions, from the pinned `cashflow_direction`;
     - per currency and bucket: inflows, outflows, net and cumulative gap as exact text, or
       `"MASKED"`;
     - the structured conversation state (D-48 §5);
     - masked tool results, with handles instead of ids.
  2. **Masked as the least-cleared reader would see it.** Figures go through D-33/D-46 at
     clearance `internal`, whoever the caller is. So no figure built from fewer than
     `CALC_MIN_AGGREGATE_COUNT` distinct non-zero flows is ever sent.
  3. **Never sent:**
     - row-level values, business keys (including the funding `instrument_keys`), UUIDs,
       names or source rows;
     - counterparty or credit data;
     - anything classified `confidential` or `restricted`.

     Context strings are codes matching `^[A-Z0-9_]{1,40}$`, or server constants.
  4. **Free text (user-approved).** The analyst's message is sent to the provider, under
     these controls:
     - It is capped at `AI_MAX_REQUEST_CHARS` (default 1000).
     - The UI shows a warning next to the input: "Do not paste client data, names, account
       or instrument identifiers, or row-level figures."
     - **A deterministic pre-filter runs before any call.** It refuses the message with 422
       `request_looks_like_row_data` (audited with codes only, nothing sent) when the text
       contains:
       - a run of 6 or more digits, ignoring spaces, commas and dots between digits;
       - more than `AI_MAX_NUMBERS_IN_REQUEST` numeric tokens (default 6);
       - a UUID, an email address, or an IBAN-like token;
       - an identifier-shaped token (`[A-Za-z]{2,}[-_]?\d{3,}`, the shape of the sample keys
         such as `CP-0008`);
       - any token equal to a business key in the baseline snapshot (checked server-side).
     - Only the message's sha256 and length are stored.
     - The prompt marks the message as untrusted data. The closed outcomes and closed tools
       (D-48) bound what injected text could do.
  5. **Where a live provider may run (user-approved).**
     - `LLM_PROVIDER=groq` is refused at startup when `ENV` is `test` (so CI never calls Groq)
       or `production`, or when `DATA_CLASS=client`.
     - It is allowed in `local` and `staging`, which hold synthetic data only (D-14).
     - At request time it is refused (409 `ai_data_class_refused`) unless the effective data
       class is `synthetic`.
     - Production needs a new decision after the client's approval (residency, terms,
       privacy, retention).
  6. **Egress.** HTTPS to `api.groq.com` must be allowed by the environment's network policy.
     `GROQ_BASE_URL` must be `https://` and, outside `local`, its host must be
     `api.groq.com`. TLS verification is never disabled.
  7. **Enforced by tests:**
     - a sentinel amount alone in a bucket and a sentinel business key never appear in any
       recorded provider request;
     - the request contains no UUID;
     - its top-level keys equal the `AgentContext` fields;
     - a loop over pre-filter cases refuses each one before any provider call.

## D-50 LLM provider configuration and secret handling

- **Status:** proposed (Feature 05 plan, 2026-10-19).
- **Date:** 2026-10-19
- **Decision (proposed):**
  1. **Settings** (`core/config.py`, validated at startup per D-13):

     | Variable | Default | Rule |
     |---|---|---|
     | `AI_ENABLED` | `false` | exists (`config.py:66`) |
     | `AI_SCENARIO_PROPOSALS_ENABLED` | `true` | boolean |
     | `LLM_PROVIDER` | `mock` | `mock` or `groq`; `groq` refused per D-49 §5 |
     | `GROQ_API_KEY` | unset | `SecretStr`; required only for live calls |
     | `GROQ_MODEL` | unset | required when `LLM_PROVIDER=groq`; e.g. `llama-3.3-70b-versatile` (to be confirmed by the user in the Groq console) |
     | `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | https; host per D-49 §6 |
     | `LLM_TIMEOUT_SECONDS` | `20` | 1–60 |
     | `LLM_MAX_OUTPUT_TOKENS` | `1500` | 1–4000 |
     | `LLM_MAX_RESPONSE_BYTES` | `32768` | 1–65536 |
     | `LLM_MAX_RETRIES` | `1` | 0–2 |
     | `AI_RATE_LIMIT_PER_HOUR` | `20` | ≥ 1 |
     | `AI_MAX_REQUEST_CHARS` | `1000` | 1–4000 (D-49 §4) |
     | `AI_MAX_NUMBERS_IN_REQUEST` | `6` | 1–20 (D-49 §4) |
     | `AI_MAX_DRAFTS_PER_TURN` | `3` | 1–5 |
     | `AI_MAX_TURNS` | `6` | 1–10 |
     | `AI_MAX_TOOL_CALLS` | `6` | 1–10 per turn |
     | `AI_CONVERSATION_TTL_MINUTES` | `60` | 5–1440 |
     | `SCENARIOS_ENABLED` | `true` | boolean (D-44 §9) |

  2. **Where the key lives.**
     - Locally: `backend/.env`, which is git-ignored (`.gitignore:5-7`).
       `backend/.env.example` gains commented placeholders only, never a value.
     - For a cloud demo: the environment's secret store, injected as an environment
       variable.
     - The key is never pasted into chat, issues, PRs, docs, fixtures or test files. A key
       that has been exposed is rotated in the Groq console.
  3. **Never leaked.**
     - The key is held as `SecretStr` and read only inside `GroqProvider` to build the
       `Authorization` header.
     - Settings errors hide input (`hide_input_in_errors=True` already, `config.py:61`).
     - The log formatter redacts `gsk_[A-Za-z0-9]+` as defence in depth.
     - Provider errors map to value-free codes, never to provider bodies or headers.
     - Audit details carry the provider and model id only.
     - No setting appears in `openapi.json`.
     - Tests fold these checks into `test_config.py` and `test_committed_artifacts.py`, using
       a sentinel key and `httpx.MockTransport`. No test makes a network call.
  4. **`GET ai/status`** shows whether AI is enabled, the provider kind, the model id and
     `configured: true/false`. It never shows the key, any part of it, or its length.

## D-51 Evidence-backed explanations and the deterministic verifier

- **Status:** proposed (Feature 05 plan, 2026-10-19, from the user's design §3 E, §5: "The
  explanation has to come from the actual run output, never from invented figures").
- **Date:** 2026-10-19
- **Recommended:**
  1. **Routes** (the WS5 names): `POST ai/explanations` (`ai:use`) takes `{scenario_run_id,
     conversation_id?}`. `GET ai/explanations/{explanation_id}` uses `calc:read`. The agent
     may also request one after `compare`.
  2. **Facts in.** The model receives a numbered fact list (`F1`, `F2`, …) built from the D-46
     comparison at clearance `internal`. Each fact is: kind, currency, bucket code and label,
     figure, shock subset, and value as exact text or `MASKED`.
     - The scenario parameters are facts too, carrying both forms (for example
       `affected_fraction 0.2000` and `20%`).
     - Lineage facts carry counts only: the number of changed flows and parts per bucket and
       shock.
     - Nothing row-level is included (D-49 §3).
  3. **Structured out.** The output is `{sentences: [{text (at most 300 characters),
     citations: [fact ids, at least 1]}]}`, with at most 8 sentences.
  4. **Deterministic verifier.** The explanation is accepted only if:
     - every sentence cites existing facts;
     - every numeric token in the text equals (as normalised decimal text) the value, or the
       absolute value, of one of its cited facts;
     - every bucket code or label in the text belongs to a cited fact;
     - no `MASKED` fact is given a value;
     - no forbidden claim appears ("regulatory stress test" outside the negating statement,
       compliant, certified, guaranteed, Moody's, vendor parity).
  5. **Failure.** Any mismatch or uncited figure **refuses the whole explanation**
     (`REFUSED`, with value-free codes). The UI then shows the D-46 templated, non-AI summary,
     labelled as such.
  6. **Stored.** An `ai_explanation` row (Data Model §7 family, append-only) holds:
     - the status (VERIFIED or REFUSED);
     - the verified sentences and their citations (generated text, verified against facts
       the reader may see);
     - the verifier codes;
     - the hashes of the facts, the prompt and the response.

     The raw response is not stored.
  7. **Evidence links.** Each citation links to the drill-down for that fact's currency,
     bucket and shock subset (D-46 §4), and from there to the source-row lineage.
  8. **Masking.** The agent sees, and so can say, only `internal`-clear facts. The same
     verified text is shown to every role.
- **Consequences:** an explanation can never introduce a figure the run did not produce. The
  "share of explanations backed by evidence" metric (design §8) is `VERIFIED / all` from
  these rows.
