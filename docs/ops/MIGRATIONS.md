# Database migrations

**Scope:** how schema changes are written, checked and applied (BE-34.7, D-01, D-02, D-62 §5).
The migration head as of Phase 34 is `0030`.

## Where they live

- Alembic, configured by `backend/alembic.ini`. Scripts are in
  `backend/app/db/migrations/versions/`, named `<revision>_<slug>.py`, with a four-digit
  revision (`0030_identity_links.py`).
- The database comes from `DATABASE_URL` (settings, or the environment), unless
  `sqlalchemy.url` is set in the Alembic config.
- Every trigger a migration creates is listed in [`TRIGGERS.md`](TRIGGERS.md). The backend
  suite compares that list with the migrations' `CREATE TRIGGER` statements, so a migration
  that adds a trigger adds its row there in the same change.

## Rules for writing one

- **Additive only (D-01).** A forward migration adds tables, columns, constraints, indexes,
  triggers or seed rows. It does not drop or destructively alter anything. A named exception
  needs its own decision before merge, and is listed in `D01_EXCEPTIONS` in
  `backend/app/tests/test_committed_artifacts.py` (today only `0022`, D-38). Widening an
  existing CHECK is such an exception, and it goes to the user.
- **A tested downgrade (D-02).** CI runs `alembic upgrade head && alembic downgrade base &&
  alembic upgrade head` on an empty database. The test suite checks that every revision has
  a `downgrade()` and that `upgrade()` stays additive.
- **Downgrades refuse to lose governed data.** The downgrades of governed tables refuse while
  those tables hold rows (see `../RETENTION.md`). Dropping the audit trail needs an explicit
  `alembic -x allow_audit_loss=true downgrade ...` (0004). Seed rows are tagged with their
  migration, and only that migration's downgrade removes them (D-11).
- **Numbering in parallel work.** A later phase may start with a pre-numbered migration whose
  `down_revision` points at the last merged head. The coordinator re-chains it at merge time
  (CLAUDE.md, phase build protocol §6). Each piece of work migrates its own database copy,
  never the shared development database (D-03).

## Applying migrations in a deployment

- **A one-shot step before the API.** The backend image does not migrate on startup. In the
  compose `prod` profile, the `migrate` service runs `alembic upgrade head` with the backend
  image and exits. The `api` service starts only after it has exited 0
  (`depends_on: condition: service_completed_successfully`). Without compose, run the same
  image with `alembic upgrade head` as a job before rolling out the new API version.
- **Back up first.** Take a backup ([`BACKUP_RESTORE.md`](BACKUP_RESTORE.md)) before upgrading
  a database that holds data that matters.
- **Check the result.** `alembic current` prints the head with `(head)`, and the API's
  `GET /api/v1/health/ready` answers 200 with `migrations: ok`.

### Readiness and the migration head

`/api/v1/health/ready` reports `migrations: fail` (and answers 503) whenever the revisions in
the database's `alembic_version` differ from the heads of the code that is running. This has
two consequences for a rollout:

- A new API version stays out of rotation until its migration has run.
- Once the migration has run, instances of the **previous** version also report
  `migrations: fail`, even though an additive schema would still serve them. Start the new
  instances promptly after migrating, or migrate and switch in one step.

### Rolling back

- A downgrade is a development tool. On a database with data it is refused for governed
  tables, by design.
- Running the previous application version on the newer schema is not a ready rollback
  either: its readiness reports `migrations: fail` (above).
- The rollback for a production database is therefore a fix forward, or a restore of the
  backup taken before the upgrade ([`BACKUP_RESTORE.md`](BACKUP_RESTORE.md)).

## Processes and metrics (D-62 §2, Phase 34 Q3 d)

- The backend image runs one uvicorn process (no `--workers`). The compose `prod` profile runs
  one `api` container.
- **Metrics are per process.** The counters live in the memory of the process that served the
  requests. Each process with `METRICS_ENABLED=true` needs its own listener port
  (`METRICS_PORT`) and is scraped on its own. A process restart resets its counters.
  Prometheus multiprocess mode is out of scope.
- **Rate limits are per process too** (D-53 §5): N API processes allow N times the configured
  rate. A shared store is needed before more than one API instance runs.
- In the compose `prod` profile, the metrics listener binds only the API's address on the
  internal `metrics` network, and port 9100 is never published (`docker-compose.yml`).
