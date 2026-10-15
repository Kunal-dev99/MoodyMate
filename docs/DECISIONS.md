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

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** The paired WS plans' endpoint names are canonical. Architecture §9 names
  are treated as illustrative. Endpoints that appear only in §9 (e.g. `GET /me`) are
  added alongside. Everything is finalised in `backend/openapi.json`, which is
  generated from code and drift-checked in CI.
- **Reason:** the WS plans are the FE/BE pairing contract, and their Definition of Done
  requires matching feature IDs and endpoints.

## D-06 Lifecycle state enums

- **Status:** proposed (Feature 01)
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

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** `{code, message, details, correlation_id, retryable}`. This is a
  superset of the WS plans' envelope (adding a field is compatible).

## D-08 Phase and task numbering

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** `docs/DELIVERY_PLAN.md` phase numbers are global and sequential across
  all features (Feature 01 uses Phases 1–6; the next feature starts at 7), so
  `BE-n.m`/`FE-n.m` never collide. Architecture global phases are cited as `G-P0…G-P6`,
  and WS-internal phases as `WS<k>-P<n>`.
- **Reason:** the documents reuse P0–P6 with different meanings.

## D-09 Authentication modes

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** Setting `AUTH_MODE=oidc|dev`. `oidc` validates JWTs against the
  configured issuer/JWKS. `dev` validates JWTs signed by a local dev key, and is refused
  at startup when `ENV=production`. There is no password store in either mode (Arch §10).
- **Reason:** the client IdP is unknown. Development and testing need real token
  validation paths without depending on the client's IAM.

## D-10 Audit immutability

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** `audit_event` is append-only, enforced by a DB trigger that rejects
  `UPDATE`/`DELETE` (plus no update/delete repository methods). Audit is written in the
  same transaction as the action. Reads of audit are themselves audited.
- **Reason:** Arch §10 says to protect audit records against ordinary modification.

## D-11 Tenancy and provisional seed data

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** Every scoped table carries `tenant_id` from day one, with one tenant
  seeded. Seed roles, permissions, classifications and synthetic schemas are inserted
  by data migrations, tagged with the migration ID, and marked PROVISIONAL. Their
  downgrade removes only tagged seed rows. This is a named exception to D-01 for seed
  data only, never user data.
- **Reason:** the role model, classifications and schemas need client confirmation
  (Arch §10, Data Model §11).

## D-12 Local development infrastructure

- **Status:** proposed (Feature 01)
- **Date:** 2026-10-15
- **Decision:** `docker-compose.yml` provides PostgreSQL 16 for local work and CI. Each
  feature worktree uses its own database copy
  (`createdb -T moodymate_dev moodymate_<feature>`) per D-03. Object storage and a job
  broker are not added until a feature needs them.
- **Reason:** hosting is undecided (Arch §18). Keep the dependency footprint minimal.

---

<!-- Never renumber existing IDs. New decisions are appended as D-05, D-06, … -->
