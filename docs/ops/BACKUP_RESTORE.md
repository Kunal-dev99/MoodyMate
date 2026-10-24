# Backup and restore

**Scope:** the PostgreSQL database and the raw artifact store (BE-34.7, D-15, D-62 §5).
**Status:** the procedure below was exercised once, on scratch copies holding synthetic data
(Phase 34; the commands and output are in `docs/evidence/phase-34/REPORT.md`, "Restore
exercise"). It has not been run against a production system. Recovery objectives are the
client's to set (below). Retention periods are in [`../RETENTION.md`](../RETENTION.md), which
stays **PROVISIONAL**.

## What a backup holds

A backup has two parts, and both are needed:

1. **The database.** Every table, and the triggers and functions that enforce the append-only
   and immutability rules ([`TRIGGERS.md`](TRIGGERS.md)). A restore that loses the triggers
   loses those guarantees, so the restore checks them.
2. **The raw artifact store** (`ARTIFACT_STORE_DIR`; the `artifacts` volume in the compose
   `prod` profile). Uploaded files are stored outside the database, content-addressed as
   `sha256/<2 hex>/<64 hex>`, written once and never rewritten or deleted by the application
   (D-15, D-20). **A database backup does not include them.**

**Order: the database first, then the store.** The application writes an object before the
`import_artifact` row that refers to it is committed, and never removes an object. So a store
copy taken after the database dump holds every object the dump refers to. Objects added in
between (or left by a refused upload) show up as "object without a row", which is not a
failure.

## Recovery objectives (client decisions)

| Item | Value |
| --- | --- |
| Recovery point objective (RPO) | `<RPO: set by the client's business continuity owner>` |
| Recovery time objective (RTO) | `<RTO: set by the client's business continuity owner>` |
| Backup frequency | `<follows from the RPO>` |
| Generations kept, and for how long | `<client; see ../RETENTION.md>` |
| Encryption at rest and key management | `<client infrastructure>` |
| Off-site or second-region copy | `<client infrastructure>` |
| Point-in-time recovery (WAL archiving) | `<client infrastructure; not set up by this repository>` |

No number is stated here on purpose (`../RETENTION.md`, Architecture §11 and §18). The
Phase 34 exercise timed its restore step, but on a tiny synthetic database, so the time says
nothing about a production RTO.

## Taking a backup

Run with the database role's credentials from your secret store (`PGHOST`, `PGUSER`,
`PGPASSWORD`). Use `pg_dump` from PostgreSQL 16 or newer.

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
pg_dump -Fc -f "moodymate-${stamp}.dump" <db-name>                              # 1. database
tar -C "$ARTIFACT_STORE_DIR" -cf "moodymate-artifacts-${stamp}.tar" .              # 2. store
sha256sum "moodymate-${stamp}.dump" "moodymate-artifacts-${stamp}.tar" >"moodymate-${stamp}.sha256"
```

- `-Fc` (custom format) lets `pg_restore` stop on the first error and restore selectively.
- In production both files can hold client data (D-14). Store them encrypted, with access
  restricted as for the database itself.
- With the compose `prod` profile, the store is the `artifacts` volume, mounted at
  `/var/lib/moodymate/artifacts` in the `api` container. Copy it with any tool that keeps the
  files byte for byte. (Copying the volume was not part of the Phase 34 exercise.)

## Restoring

Restore into an **isolated** target that takes no traffic: a new, empty database and an empty
store directory.

```bash
sha256sum -c "moodymate-${stamp}.sha256"                                 # the files are intact
createdb <new-db>
pg_restore --exit-on-error -d <new-db> "moodymate-${stamp}.dump"
mkdir -p <new-store> && tar -C <new-store> -xf "moodymate-artifacts-${stamp}.tar"
```

- If the target's database role differs from the source's, add
  `--no-owner --role=<owner-role>` to `pg_restore`.
- The store must be readable by the API's user (uid 10001 in the backend image). Objects stay
  read-only (`0444`).

## Verifying a restore

Every check must pass before traffic moves to the restored system.

1. **Migration head.** `alembic current` against the restored database prints the code's head
   with `(head)` (`0030 (head)` as of Phase 34). From `backend/`:
   `DATABASE_URL=<new-db-url> alembic current`, or with compose:
   `docker compose --profile prod run --rm --no-deps migrate alembic current`.
2. **Triggers present and enabled.** The count equals the rows of `TRIGGERS.md` (177 as of
   Phase 34), and none is disabled:

   ```sql
   SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal;                         -- 177
   SELECT tgrelid::regclass, tgname FROM pg_trigger
    WHERE NOT tgisinternal AND tgenabled = 'D';                                    -- no rows
   ```

3. **Row counts.** Per table, the restored counts equal the source's (or the backup
   manifest's, once one exists).
4. **Artifact store re-hash.** For every `import_artifact` row, the object at `storage_ref`
   exists, its SHA-256 equals `sha256`, and its size equals `size_bytes`. Objects with no row
   are listed.

   ```bash
   scripts/verify-artifact-store.sh <new-db> <new-store>     # libpq settings from PG* variables
   ```

   It exits 0 only when every row has its object with the right hash and size. It prints refs
   and counts only, never content. **A missing or mismatched object is a failed restore.** Do
   not repair it silently: the application fails closed on it with `artifact_integrity_error`
   (D-15). Find a backup generation that holds the right object.
5. **Readiness.** Start the API against the restored database and store.
   `GET /api/v1/health/ready` answers 200 with every check `ok` (database, migrations,
   artifact_store, workers).
6. **Verification suites.** `../RETENTION.md` (restore step 5) lists the WS1 to WS3 suites.
   They create their own test data, so run them only against a disposable copy of the
   restored database, never against the one that will take traffic. This step was not part
   of the Phase 34 exercise.
7. **Record the restore** as an operational event: the backup generation used, who ran it,
   and the start and end times, so the agreed RTO can be measured.

Only then switch traffic.

## Not covered yet

- Point-in-time recovery, backup scheduling and monitoring of backup jobs (client
  infrastructure).
- Object storage replacing the filesystem store (D-12, D-15). The re-hash check stays the
  same, but the copy step changes.
- Archival and deletion procedures, and their evidence (`../RETENTION.md`, open item 3).
- Application logs are not backed up (`../RETENTION.md`).
