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

---

<!-- Never renumber existing IDs. New decisions are appended as D-05, D-06, … -->
