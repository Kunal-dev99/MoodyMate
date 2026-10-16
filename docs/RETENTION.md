# Retention, deletion and backup — provisional assumptions (BE-6.2, DG-06)

**Status: PROVISIONAL.** Every row below needs confirmation from the client's business continuity, compliance and data-protection owners before it is implemented or relied on.

**Deliberately absent:** numeric retention periods and recovery objectives (RPO/RTO). Architecture §11 and §18 say not to set these until the client agrees them. Where a number would go, this document names who decides it.

**Scope:** the artifact classes that exist after Feature 01 (WS1 foundation). Later features (raw import artifacts, canonical records, calculation runs and results, reports, AI requests) add their own rows when they are built (Data Model "Define retention/deletion for raw artifacts, derived records, AI requests and logs").

## Principles that already hold in the code

- **Nothing governed is deleted by the application.** Lifecycle actions hide data through status or validity fields, and never delete it (D-02).
  - Audit events are append-only (D-10).
  - Schema versions are immutable once they leave DRAFT (BE-4.1).
  - Decided schema change proposals are read-only (BE-5.1).
  - DB triggers enforce all three, not only the application code.
- **Deletion is a deliberate, owner-level procedure.** Removing governed records means a database owner lifting the protective triggers inside one transaction, the way the seed downgrades do (D-11).
  - The application role cannot do this once the deployment uses a separate, non-owner role. That role is an infrastructure follow-up, and until it exists the table owner can still disable the triggers.
- **Migrations never drop governed data silently.**
  - The 0004 (audit), 0006 (registry) and 0008 (proposals) downgrades refuse while their tables hold data.
  - Dropping the audit trail needs an explicit `-x allow_audit_loss=true`.
- **Environments are separated by data class (BE-6.1, D-14).** Only `production` may hold client data. `local`, `test` and `staging` are synthetic only, and every response says which (`X-Data-Class`).

## Per artifact class

| Artifact class | Tables / location | Retention assumption | Deletion assumption | Backup assumption | Needs confirmation from |
|---|---|---|---|---|---|
| **Audit events** | `audit_event` | Kept at least as long as the longest-lived record they describe, and for the regulatory audit period. Reads of the audit trail are audited too. | Never deleted by the application. After the retention period, archived to protected long-term storage and then removed by an owner-level procedure that is itself recorded (for example as an archival manifest with counts and hashes). | Included in every database backup. An archive must be restorable on its own. | Compliance (audit period), Internal Audit (archive format), Infrastructure (archive storage) |
| **Schema registry** | `schema_definition`, `schema_version`, `schema_field`, `schema_dependency` | Kept for the life of the platform. Calculation runs (WS3) will pin schema versions, so any version referenced by a retained result must stay. | Versions are retired by status (DEPRECATED, RETIRED), never deleted. A schema with no retained dependents could be archived by the owner-level procedure. | Included in every database backup. | Data governance owner, Model risk |
| **Schema change proposals** | `schema_change_proposal` (impact analysis and decision held in the row) | Same as the registry. A proposal is the approval evidence for the version it activated. | Decided proposals are read-only and never deleted by the application. Superseded drafts are kept as history. | Included in every database backup. | Data governance owner, Compliance |
| **Tenants** | `tenant` | Kept while any row in the tenant is retained: every scoped table and audit event carries `tenant_id`. A tenant is suspended (`status`), not deleted. | Removing a tenant means removing or archiving all its scoped data first, by the owner-level procedure, with evidence per class. Not built. | Included in every database backup. | Client (offboarding terms), Compliance |
| **Identity and access** | `user_account`, `user_role`, `role`, `permission`, `role_permission` | User records stay while referenced by audit events (audit actors are user ids). Users are disabled (`status`), not deleted. | Personal fields (email, display name) can be erased or pseudonymised on a data-subject request, keeping the id so the audit trail stays readable. That procedure is not built yet. | Included in every database backup. | Data protection officer (erasure vs. audit), IAM owner |
| **Classification and access policy** | `data_classification`, `access_policy` | Kept for the life of the platform. Codes are referenced by schema fields and audit-detail classifications. | Seed rows are removed only by their migration's downgrade (D-11). | Included in every database backup. | Information security (classification scheme, PROVISIONAL) |
| **Seed and illustrative data** | Rows tagged `seeded_by` | Kept until the client confirms or replaces them. | Removed only by the seeding migration's downgrade, which touches tagged rows only. | Included in backups like any other rows. | Product owner (when to replace the illustrative schemas) |
| **Application logs** | Process stdout (JSON lines); destination decided at deployment | Short-lived operational data. Logs never carry classified values: they are redacted with the same codes as API masking (BE-3.5), and database driver messages are withheld. | Expire with the log platform's policy. | Not backed up by the application. | Operations (log platform and its retention), Information security |
| **Local and test databases** | `moodymate_dev`, `moodymate_test`, per-feature copies (D-03) | Synthetic data only (BE-6.1). Feature copies are dropped after their feature merges. | Disposable. | None. | n/a |

## Deletion evidence (all classes)

Whenever governed data is removed (archival, erasure, tenant offboarding), the procedure must leave evidence that is kept at least as long as the audit trail. That evidence is:
- **What was removed:** the class, the tenant, the selection criteria, the row counts, and a content hash of the archived export.
- **Who authorised it and who ran it:** two different people (maker-checker, as for schema changes).
- **When, and from which backup generation** the data will also age out.

For audit events the evidence is written *before* the rows are removed, so the trail never has a gap without an explanation. None of this is built: it is part of the archival procedure (open item 3).

## Backup and restore (assumptions)

- **Database backups.** Automated, encrypted at rest with approved key management, and including every table above (Arch §10, §11). Frequency and retention are not set: they follow from the RPO the client agrees.
- **Restore.** A restore must be demonstrated, not assumed (Arch §11, "Demonstrated restore and recovery objectives"). Restoring the triggers and functions is part of restoring the schema: a restore that loses them loses the immutability guarantees, so the restore test checks they are present.
- **Restore procedure (outline; to be written and rehearsed per environment):**
  1. Restore the database backup into an isolated instance.
  2. Run `alembic current` and confirm it shows the expected head.
  3. Confirm the protective triggers and functions exist: `audit_event_*`, `schema_version_immutable`, `schema_field_immutable`, `schema_dependency_same_tenant`, the `*_no_truncate` triggers and `schema_change_proposal_*`.
  4. Run the WS1 verification suite (`pytest -m ws1`) against it.
  5. Reconcile row counts per class against the backup manifest.
  6. Only then switch traffic.
  7. Record the restore as an operational event, with timings, so the agreed RTO can be measured.
- **Recovery objectives.** No RPO/RTO is stated here; they come from the business continuity owners.

## Open items

1. Retention periods per class (Compliance, Business continuity).
2. Recovery point and recovery time objectives (Business continuity).
3. The archival procedure and evidence format for audit events (Internal Audit).
4. Erasure versus audit readability for personal data (Data protection officer).
5. The non-owner application database role that makes the triggers binding (Infrastructure).
