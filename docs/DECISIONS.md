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

---

<!-- Never renumber existing IDs. New decisions are appended as D-05, D-06, … -->
