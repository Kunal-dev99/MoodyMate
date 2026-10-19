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
  2. **Ratings.** The grade, rationale and decision comment are `internal`, so the proposing
     analyst (clearance `internal`) can read their own rationale. This is an open item for the
     client.
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
