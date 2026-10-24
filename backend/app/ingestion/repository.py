"""ingestion.repository

The only code that touches the DB session for this module; every query applies the
principal's scope (D-04). Profiles and contracts are reached only through a source system
in the caller's tenant; artifacts, batches and records only through an import job in it.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, Row, Select, case, cast, exists, func, insert, or_, select, tuple_

from app.core.decimals import SOURCE
from app.db.base import Base
from app.db.repository import ScopedRepository
from app.ingestion.constants import ImportStatus
from app.ingestion.models import (
    CanonicalRecord,
    DataProfileSnapshot,
    ImportBatch,
    ImportJob,
    ImportManifest,
    QuarantineItem,
    SourceContract,
    SourceProfile,
    SourceRecord,
    SourceSystem,
    ValidationIssue,
    ValidationProfile,
)

# BE-12.2: a control-total value is "parseable" when, spaces trimmed, it is plain decimal
# text (no exponent, grouping or currency), as the profiler reads numbers (BE-9.2).
PLAIN_DECIMAL = SOURCE.pattern  # the SOURCE decimal text, as a PostgreSQL regex
# Per control-total column: (source total, accepted total, held total, parseable,
# unparseable, empty).
ColumnTotals = tuple[Decimal, Decimal, Decimal, int, int, int]

# BE-12.3: a drift baseline is the latest import in one of these statuses.
DRIFT_BASELINE_STATUSES = (ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_WARNINGS)

# One page of an import's records for validation: (id, batch id, batch number, ordinal,
# values, extras, flags).
RecordRow = Row[
    tuple[uuid.UUID, uuid.UUID, int, int, dict[str, str], list[str], list[dict[str, Any]]]
]


class SourceRepository(ScopedRepository[SourceSystem]):
    model = SourceSystem

    def _with_profile(self) -> Select[tuple[SourceSystem]]:
        """Scoped sources that have a profile. A source is registered with its profile in one
        transaction; the filter sits in the query so LIMIT and COUNT agree (D12)."""
        return self.scoped().where(
            exists().where(SourceProfile.source_system_id == SourceSystem.id)
        )

    async def page_by_code(self, limit: int, offset: int) -> tuple[list[SourceSystem], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(self._with_profile().subquery())
        )
        result = await self.session.execute(
            self._with_profile()
            .order_by(SourceSystem.code, SourceSystem.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def fresh(self, source_id: uuid.UUID) -> SourceSystem | None:
        """Reloaded with its profiles and contracts, so writes made in this session show."""
        result = await self.session.execute(
            self.scoped()
            .where(SourceSystem.id == source_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def code_taken(self, code: str) -> bool:
        found = await self.session.scalar(
            select(SourceSystem.id).where(
                SourceSystem.tenant_id == self.scope.tenant_id, SourceSystem.code == code
            )
        )
        return found is not None

    async def lock(self, source_id: uuid.UUID) -> SourceSystem | None:
        """Serialises contract versioning per source (one current contract per profile)."""
        result = await self.session.execute(
            self.scoped()
            .where(SourceSystem.id == source_id)
            .with_for_update(of=SourceSystem)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def contracts(self, profile_id: uuid.UUID) -> list[SourceContract]:
        """A profile's contracts, freshly loaded, oldest first. Callers reach the profile
        through a scoped source system first, so this never crosses tenants."""
        result = await self.session.execute(
            select(SourceContract)
            .where(SourceContract.source_profile_id == profile_id)
            .order_by(SourceContract.version)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def contract_source(self, contract_id: uuid.UUID) -> uuid.UUID | None:
        """The source of a contract of the caller's tenant (absent and foreign: None)."""
        found: uuid.UUID | None = await self.session.scalar(
            select(SourceSystem.id)
            .join(SourceProfile, SourceProfile.source_system_id == SourceSystem.id)
            .join(SourceContract, SourceContract.source_profile_id == SourceProfile.id)
            .where(SourceSystem.tenant_id == self.scope.tenant_id, SourceContract.id == contract_id)
        )
        return found

    async def contract_labels(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, int]]:
        """Contract id -> (source code, contract version), for the caller's tenant only."""
        if not ids:
            return {}
        result = await self.session.execute(
            select(SourceContract.id, SourceSystem.code, SourceContract.version)
            .join(SourceProfile, SourceProfile.id == SourceContract.source_profile_id)
            .join(SourceSystem, SourceSystem.id == SourceProfile.source_system_id)
            .where(SourceSystem.tenant_id == self.scope.tenant_id, SourceContract.id.in_(ids))
        )
        return {row.id: (row.code, row.version) for row in result}

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def flush(self) -> None:
        await self.session.flush()


class ImportRepository(ScopedRepository[ImportJob]):
    """Imports are tenant-scoped through `import_job.tenant_id`; artifacts, batches and
    records are reached only through a scoped job."""

    model = ImportJob

    def _filtered(
        self,
        source_id: uuid.UUID | None,
        status: str | None,
        schema_version_ids: Sequence[uuid.UUID] | None = None,
        ids: Sequence[uuid.UUID] | None = None,
        exclude_ids: bool = False,
    ) -> Select[tuple[ImportJob]]:
        query = self.scoped()
        if source_id is not None:
            query = query.where(ImportJob.source_system_id == source_id)
        if status is not None:
            query = query.where(ImportJob.status == status)
        if schema_version_ids is not None:
            query = query.where(ImportJob.schema_version_id.in_(schema_version_ids))
        if ids is not None:
            matches = ImportJob.id.in_(ids)
            query = query.where(~matches if exclude_ids else matches)
        return query

    async def page_filtered(
        self,
        limit: int,
        offset: int,
        source_id: uuid.UUID | None,
        status: str | None,
        schema_version_ids: Sequence[uuid.UUID] | None = None,
        ids: Sequence[uuid.UUID] | None = None,
        exclude_ids: bool = False,
    ) -> tuple[list[ImportJob], int]:
        query = self._filtered(source_id, status, schema_version_ids, ids, exclude_ids)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def fresh(self, import_id: uuid.UUID) -> ImportJob | None:
        result = await self.session.execute(
            self.scoped().where(ImportJob.id == import_id).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock(self, import_id: uuid.UUID) -> ImportJob | None:
        """The import, locked for this transaction: one profiling run at a time."""
        result = await self.session.execute(
            self.scoped()
            .where(ImportJob.id == import_id)
            .with_for_update(of=ImportJob)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def by_idempotency_key(self, actor: str, key: str) -> ImportJob | None:
        """D-21: a key is unique per tenant and actor."""
        result = await self.session.execute(
            self.scoped()
            .where(ImportJob.created_by == actor, ImportJob.idempotency_key == key)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def source_codes(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        result = await self.session.execute(
            select(SourceSystem.id, SourceSystem.code).where(
                SourceSystem.tenant_id == self.scope.tenant_id, SourceSystem.id.in_(ids)
            )
        )
        return {row.id: row.code for row in result}

    async def contract_versions(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Contract versions of contracts the caller's (scoped) imports pin."""
        if not ids:
            return {}
        result = await self.session.execute(
            select(SourceContract.id, SourceContract.version)
            .join(SourceProfile, SourceProfile.id == SourceContract.source_profile_id)
            .join(SourceSystem, SourceSystem.id == SourceProfile.source_system_id)
            .where(SourceSystem.tenant_id == self.scope.tenant_id, SourceContract.id.in_(ids))
        )
        return {row.id: row.version for row in result}

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)

    async def add_records(self, rows: list[dict[str, Any]]) -> None:
        """One multi-row INSERT for a batch's records (up to the D-16 row limit)."""
        if rows:
            await self.session.execute(insert(SourceRecord), rows)

    async def flush(self) -> None:
        await self.session.flush()

    # --- Phase 9: profiles (BE-9.1, BE-9.2) ----------------------------------------------------

    async def record_partitions(
        self, import_id: uuid.UUID, size: int
    ) -> AsyncIterator[Sequence[Row[tuple[dict[str, str], list[str], list[dict[str, Any]]]]]]:
        """An import's records (values, extras, flags) in file order, streamed from a
        server-side cursor in partitions of `size` rows, so that memory does not grow with
        the import. Scoped through the import's tenant."""
        query = (
            select(SourceRecord.raw_values, SourceRecord.extra_values, SourceRecord.flags)
            .join(ImportBatch, ImportBatch.id == SourceRecord.batch_id)
            .join(ImportJob, ImportJob.id == ImportBatch.import_job_id)
            .where(ImportJob.id == import_id, ImportJob.tenant_id == self.scope.tenant_id)
            .order_by(ImportBatch.batch_number, SourceRecord.ordinal)
            .execution_options(yield_per=size)
        )
        result = await self.session.stream(query)
        async for partition in result.partitions(size):
            yield partition

    async def snapshot(self, import_id: uuid.UUID) -> DataProfileSnapshot | None:
        """The profile of an import of the caller's tenant (absent and foreign look alike)."""
        result = await self.session.execute(
            select(DataProfileSnapshot)
            .join(ImportJob, ImportJob.id == DataProfileSnapshot.import_job_id)
            .where(
                DataProfileSnapshot.import_job_id == import_id,
                ImportJob.tenant_id == self.scope.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    # --- Phase 11: validation, issues, quarantine and manifests (BE-11.1 – BE-11.5) ---------

    async def record_page(
        self, import_id: uuid.UUID, after: tuple[int, int] | None, size: int
    ) -> Sequence[RecordRow]:
        """The next `size` records of an import after (batch number, ordinal), in file order.
        Keyset pages (no open cursor), so writes may run between pages. Scoped."""
        query = (
            select(
                SourceRecord.id,
                SourceRecord.batch_id,
                ImportBatch.batch_number,
                SourceRecord.ordinal,
                SourceRecord.raw_values,
                SourceRecord.extra_values,
                SourceRecord.flags,
            )
            .join(ImportBatch, ImportBatch.id == SourceRecord.batch_id)
            .join(ImportJob, ImportJob.id == ImportBatch.import_job_id)
            .where(ImportJob.id == import_id, ImportJob.tenant_id == self.scope.tenant_id)
        )
        if after is not None:
            query = query.where(tuple_(ImportBatch.batch_number, SourceRecord.ordinal) > after)
        result = await self.session.execute(
            query.order_by(ImportBatch.batch_number, SourceRecord.ordinal).limit(size)
        )
        return list(result.all())

    async def insert_rows(self, model: type[Base], rows: list[dict[str, Any]]) -> None:
        """One multi-row INSERT (rows built by the service for an import it holds locked)."""
        if rows:
            await self.session.execute(insert(model), rows)

    async def default_profile(self, code: str) -> ValidationProfile | None:
        """The newest version of the tenant's validation profile `code`."""
        result = await self.session.execute(
            select(ValidationProfile)
            .where(
                ValidationProfile.tenant_id == self.scope.tenant_id,
                ValidationProfile.code == code,
            )
            .order_by(ValidationProfile.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def manifest(self, import_id: uuid.UUID) -> ImportManifest | None:
        result = await self.session.execute(
            select(ImportManifest)
            .join(ImportJob, ImportJob.id == ImportManifest.import_job_id)
            .where(
                ImportManifest.import_job_id == import_id,
                ImportJob.tenant_id == self.scope.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def canonical_records(self, import_id: uuid.UUID) -> list[CanonicalRecord]:
        """An import's canonical records (its accepted rows) in source order, through an
        import of the caller's tenant (Phase 14 projection, D-29)."""
        result = await self.session.execute(
            select(CanonicalRecord)
            .join(ImportJob, ImportJob.id == CanonicalRecord.import_job_id)
            .where(
                CanonicalRecord.import_job_id == import_id,
                ImportJob.tenant_id == self.scope.tenant_id,
            )
            .order_by(CanonicalRecord.ordinal, CanonicalRecord.id)
        )
        return list(result.scalars())

    def _issues(
        self, import_id: uuid.UUID, severity: str | None, code: str | None, field: str | None
    ) -> Select[tuple[ValidationIssue]]:
        query = (
            select(ValidationIssue)
            .join(ImportJob, ImportJob.id == ValidationIssue.import_job_id)
            .where(
                ValidationIssue.import_job_id == import_id,
                ImportJob.tenant_id == self.scope.tenant_id,
            )
        )
        if severity is not None:
            query = query.where(ValidationIssue.severity == severity)
        if code is not None:
            query = query.where(ValidationIssue.code == code)
        if field is not None:
            query = query.where(
                or_(ValidationIssue.field == field, ValidationIssue.source_column == field)
            )
        return query

    async def issue_page(
        self,
        import_id: uuid.UUID,
        severity: str | None,
        code: str | None,
        field: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ValidationIssue], int]:
        query = self._issues(import_id, severity, code, field)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.order_by(ValidationIssue.sequence).limit(limit).offset(offset)
        )
        return list(result.scalars()), int(total or 0)

    async def severity_counts(self, import_id: uuid.UUID) -> dict[str, int]:
        query = self._issues(import_id, None, None, None).subquery()
        result = await self.session.execute(
            select(query.c.severity, func.count()).group_by(query.c.severity)
        )
        return {row[0]: int(row[1]) for row in result}

    async def batch(self, batch_id: uuid.UUID) -> tuple[ImportBatch, ImportJob] | None:
        """A batch of an import of the caller's tenant, with its import."""
        result = await self.session.execute(
            select(ImportBatch, ImportJob)
            .join(ImportJob, ImportJob.id == ImportBatch.import_job_id)
            .where(ImportBatch.id == batch_id, ImportJob.tenant_id == self.scope.tenant_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    def _items(self, batch_id: uuid.UUID, status: str | None) -> Select[tuple[QuarantineItem]]:
        query = (
            select(QuarantineItem)
            .join(ImportJob, ImportJob.id == QuarantineItem.import_job_id)
            .where(QuarantineItem.batch_id == batch_id, ImportJob.tenant_id == self.scope.tenant_id)
        )
        return query if status is None else query.where(QuarantineItem.status == status)

    async def quarantine_page(
        self, batch_id: uuid.UUID, status: str | None, limit: int, offset: int
    ) -> tuple[list[tuple[QuarantineItem, SourceRecord]], int]:
        """A batch's quarantine items with their (unedited) source records, by row."""
        query = self._items(batch_id, status)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        result = await self.session.execute(
            query.add_columns(SourceRecord)
            .join(SourceRecord, SourceRecord.id == QuarantineItem.source_record_id)
            .order_by(QuarantineItem.ordinal)
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        return [(row[0], row[1]) for row in result.all()], int(total or 0)

    async def status_counts(self, batch_id: uuid.UUID) -> dict[str, int]:
        query = self._items(batch_id, None).subquery()
        result = await self.session.execute(
            select(query.c.status, func.count()).group_by(query.c.status)
        )
        return {row[0]: int(row[1]) for row in result}

    async def lock_item(self, batch_id: uuid.UUID, item_id: uuid.UUID) -> QuarantineItem | None:
        """A quarantine item of this batch in the caller's tenant, locked."""
        result = await self.session.execute(
            self._items(batch_id, None)
            .where(QuarantineItem.id == item_id)
            .with_for_update(of=QuarantineItem)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def retry_ready_items(self, import_id: uuid.UUID) -> list[QuarantineItem]:
        """An import's RETRY_READY items, locked, by row (D-20 §2)."""
        result = await self.session.execute(
            select(QuarantineItem)
            .join(ImportJob, ImportJob.id == QuarantineItem.import_job_id)
            .where(
                QuarantineItem.import_job_id == import_id,
                ImportJob.tenant_id == self.scope.tenant_id,
                QuarantineItem.status == "RETRY_READY",
            )
            .order_by(QuarantineItem.ordinal)
            .with_for_update(of=QuarantineItem)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def batch_item(
        self, batch_id: uuid.UUID, item_id: uuid.UUID
    ) -> tuple[QuarantineItem, SourceRecord] | None:
        result = await self.session.execute(
            self._items(batch_id, None)
            .where(QuarantineItem.id == item_id)
            .add_columns(SourceRecord)
            .join(SourceRecord, SourceRecord.id == QuarantineItem.source_record_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    # --- Phase 12: reconciliation figures and the drift baseline (BE-12.2, BE-12.3) ---------

    async def reconciliation_figures(
        self, import_id: uuid.UUID, columns: Sequence[str]
    ) -> tuple[int, int, int, dict[str, ColumnTotals]]:
        """What validation stored for an import, read back in one scoped aggregate: source
        rows, rows with a canonical record, rows with a quarantine item and, per column, the
        exact NUMERIC totals of its parseable values over all, accepted and held rows."""
        accepted = CanonicalRecord.id.isnot(None)
        held = QuarantineItem.id.isnot(None)
        measures: list[Any] = [
            func.count(),
            func.count(CanonicalRecord.id),
            func.count(QuarantineItem.id),
        ]
        for column in columns:
            text_value = func.btrim(SourceRecord.raw_values[column].astext)
            parseable = text_value.op("~")(PLAIN_DECIMAL)
            number = case((parseable, cast(text_value, Numeric)), else_=None)
            empty = func.coalesce(text_value, "") == ""
            measures += [
                func.coalesce(func.sum(number), 0),
                func.coalesce(func.sum(number).filter(accepted), 0),
                func.coalesce(func.sum(number).filter(held), 0),
                func.count(number),
                func.count().filter(~empty, ~parseable),
                func.count().filter(empty),
            ]
        row = (
            await self.session.execute(
                select(*measures)
                .select_from(SourceRecord)
                .join(ImportBatch, ImportBatch.id == SourceRecord.batch_id)
                .join(ImportJob, ImportJob.id == ImportBatch.import_job_id)
                .outerjoin(CanonicalRecord, CanonicalRecord.source_record_id == SourceRecord.id)
                .outerjoin(QuarantineItem, QuarantineItem.source_record_id == SourceRecord.id)
                .where(ImportJob.id == import_id, ImportJob.tenant_id == self.scope.tenant_id)
            )
        ).one()
        totals: dict[str, ColumnTotals] = {}
        for i, column in enumerate(columns):
            src, acc, hld, n_ok, n_bad, n_empty = row[3 + 6 * i : 9 + 6 * i]
            totals[column] = (
                Decimal(src),
                Decimal(acc),
                Decimal(hld),
                int(n_ok),
                int(n_bad),
                int(n_empty),
            )
        return int(row[0]), int(row[1]), int(row[2]), totals

    async def drift_baseline(self, job: ImportJob) -> tuple[ImportJob, DataProfileSnapshot] | None:
        """BE-12.3: the latest COMPLETED or COMPLETED_WITH_WARNINGS import of the same source
        (one profile per source) other than `job`, that has a profile. Scoped."""
        result = await self.session.execute(
            select(ImportJob, DataProfileSnapshot)
            .join(DataProfileSnapshot, DataProfileSnapshot.import_job_id == ImportJob.id)
            .where(
                ImportJob.tenant_id == self.scope.tenant_id,
                ImportJob.source_system_id == job.source_system_id,
                ImportJob.id != job.id,
                ImportJob.status.in_(DRIFT_BASELINE_STATUSES),
            )
            .order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
            .limit(1)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None
