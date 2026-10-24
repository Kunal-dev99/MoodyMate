"""calculations.snapshots (BE-35.2: split from calculations.service, which re-exports it)

Dataset snapshots (BE-15.3, D-30): `POST /dataset-snapshots` and its reads. The members are
checked in a fixed order and the first failing check refuses (409, audited DENIED with its
code); `content_sha256` (`snapshot_sha256`) hashes the members' (schema_code, import_id,
manifest_sha256), so the same members return the existing snapshot and nothing is written.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.constants import CREATE_SNAPSHOT, SnapshotRefusal
from app.calculations.models import DatasetSnapshot, DatasetSnapshotMember
from app.calculations.repository import SnapshotRepository
from app.calculations.results import _public
from app.core.canonical import HashKind, sha256_for
from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.refusals import refuse
from app.domain.models import DomainProjection
from app.domain.service import DomainService
from app.governance.service import audit_by
from app.ingestion.models import ImportJob, ImportManifest
from app.ingestion.service import ImportService
from app.reconciliation.constants import ReconciliationOutcome
from app.reconciliation.models import ReconciliationRun
from app.reconciliation.service import ReconciliationService


def snapshot_sha256(members: Iterable[tuple[str, uuid.UUID, str]]) -> str:
    """A snapshot's identity (D-30 §1): the canonical JSON of its members' (schema_code,
    import_id, manifest_sha256), sorted by schema code."""
    entries = [
        {"schema_code": code, "import_id": str(import_id), "manifest_sha256": sha}
        for code, import_id, sha in members
    ]
    return sha256_for(
        HashKind.DATASET_SNAPSHOT,
        sorted(entries, key=lambda e: (e["schema_code"], e["import_id"])),
    )


# --- views returned to the router ------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotView:
    snapshot: DatasetSnapshot
    members: list[DatasetSnapshotMember]  # by schema code
    source_codes: Mapping[uuid.UUID, str]
    schema_versions: Mapping[uuid.UUID, int]


# --- dataset snapshots (BE-15.3) ------------------------------------------------------------


class SnapshotService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = SnapshotRepository(session, principal.scope)
        self.imports = ImportService(session, principal)
        self.domain = DomainService(session, principal)
        self.reconciliation = ReconciliationService(session, principal)

    async def _refuse(self, error: ApiError, resource_id: uuid.UUID | None = None) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await refuse(
            self.session,
            self.principal,
            error,
            action=CREATE_SNAPSHOT,
            resource_type="dataset_snapshot",
            resource_id=resource_id,
        )

    async def create(
        self, import_ids: Sequence[uuid.UUID], enabled: bool
    ) -> tuple[SnapshotView, bool]:
        """The snapshot of these imports and whether it was written now (False: the same
        members already form one, returned as is)."""
        if not enabled:
            await self._refuse(
                ApiError(
                    409,
                    SnapshotRefusal.CALCULATIONS_DISABLED,
                    "Calculations are switched off in this environment; existing snapshots "
                    "and runs stay readable.",
                )
            )
        jobs: list[ImportJob] = []
        for import_id in import_ids:
            job = await self.imports.find(import_id)
            if job is None:
                await self._refuse(ApiError(404, "not_found", "Import not found."), import_id)
            jobs.append(job)
        found: list[tuple[ImportJob, DomainProjection, ImportManifest]] = []
        for job in jobs:
            projection = await self.domain.projection_of(job)
            manifest = await self.imports.manifest(job)
            if projection is None or manifest is None:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.NOT_PROJECTED,
                        "Only projected imports can join a snapshot: project the import into "
                        "the domain first (D-29).",
                        {"import_id": str(job.id)},
                    ),
                    job.id,
                )
            found.append((job, projection, manifest))
        seen: dict[str, uuid.UUID] = {}
        for job, projection, _ in found:
            if projection.schema_code in seen:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.DUPLICATE_SCHEMA,
                        "A snapshot takes at most one import per schema code (D-30 §1).",
                        {
                            "schema_code": projection.schema_code,
                            "import_ids": [str(seen[projection.schema_code]), str(job.id)],
                        },
                    ),
                    job.id,
                )
            seen[projection.schema_code] = job.id
        reconciled: dict[uuid.UUID, ReconciliationRun] = {}
        for job, _, _ in found:
            run = await self.reconciliation.for_import(job)
            if run is None or run.outcome == ReconciliationOutcome.BROKEN:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.RECONCILIATION_BROKEN,
                        "The import's reconciliation is BROKEN (or missing): a gap that no held "
                        "row explains. It cannot be calculated on (D-28, D-30 §3).",
                        {"import_id": str(job.id), "outcome": run.outcome if run else None},
                    ),
                    job.id,
                )
            reconciled[job.id] = run
        for job, projection, _ in found:
            ambiguous = await self.domain.ambiguous_keys(projection)
            if ambiguous:
                await self._refuse(
                    ApiError(
                        409,
                        SnapshotRefusal.AMBIGUOUS_BUSINESS_KEY,
                        "A business key resolves to more than one version inside the snapshot "
                        "(D-30 §3).",
                        {
                            "import_id": str(job.id),
                            "schema_code": projection.schema_code,
                            "ambiguous_keys": ambiguous,
                        },
                    ),
                    job.id,
                )

        sha = snapshot_sha256((p.schema_code, j.id, m.manifest_sha256) for j, p, m in found)
        await self.repo.lock(f"dataset_snapshot:{sha}")
        existing = await self.repo.by_sha(sha)
        if existing is not None:
            return await self._view(existing), False

        actor, tenant = self.principal.actor, self.principal.tenant_id
        snapshot = DatasetSnapshot(
            id=uuid.uuid4(),
            tenant_id=tenant,
            content_sha256=sha,
            member_count=len(found),
            created_by=actor,
        )
        self.repo.add(snapshot)
        await self.repo.flush()
        await self.repo.insert_rows(
            DatasetSnapshotMember,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant,
                    "dataset_snapshot_id": snapshot.id,
                    "import_job_id": job.id,
                    "import_manifest_id": manifest.id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "domain_projection_id": projection.id,
                    "projector_version": projection.projector_version,
                    "schema_code": projection.schema_code,
                    "schema_version_id": manifest.schema_version_id,
                    "mapping_version_id": manifest.mapping_version_id,
                    "source_system_id": job.source_system_id,
                    "reconciliation_run_id": reconciled[job.id].id,
                    "reconciliation_outcome": reconciled[job.id].outcome,
                    "accepted_count": manifest.accepted_count,
                    "quarantined_count": manifest.quarantined_count,
                    "rejected_count": manifest.rejected_count,
                    "created_by": actor,
                }
                for job, projection, manifest in found
            ],
        )
        details: dict[str, Any] = {
            "dataset_snapshot_id": str(snapshot.id),
            "member_count": len(found),
            "schema_codes": sorted(seen),
            "content_sha256": sha,
        }
        audit_by(
            self.session,
            self.principal,
            CREATE_SNAPSHOT,
            "dataset_snapshot",
            resource_id=str(snapshot.id),
            details=details,
            details_classification=_public(details),
        )
        await self.session.commit()
        return await self._view(snapshot), True

    async def _views(self, snapshots: list[DatasetSnapshot]) -> list[SnapshotView]:
        members: dict[uuid.UUID, list[DatasetSnapshotMember]] = {}
        for m in await self.repo.members([s.id for s in snapshots]):
            members.setdefault(m.dataset_snapshot_id, []).append(m)
        sources = {m.source_system_id for ms in members.values() for m in ms}
        codes = await self.imports.source_codes(sources)
        pins = await self.domain.schema_pins()
        return [SnapshotView(s, members.get(s.id, []), codes, pins.versions) for s in snapshots]

    async def _view(self, snapshot: DatasetSnapshot) -> SnapshotView:
        [view] = await self._views([snapshot])
        return view

    async def page(self, limit: int, offset: int) -> tuple[list[SnapshotView], int]:
        snapshots, total = await self.repo.newest(limit, offset)
        return await self._views(snapshots), total

    async def get(self, snapshot_id: uuid.UUID) -> SnapshotView:
        """404 alike for an absent and a foreign snapshot (D-04)."""
        snapshot = await self.repo.get(snapshot_id)
        if snapshot is None:
            raise ApiError(404, "not_found", "Dataset snapshot not found.")
        return await self._view(snapshot)

    async def found(self, snapshot_id: uuid.UUID) -> DatasetSnapshot | None:
        return await self.repo.get(snapshot_id)

    async def members(self, snapshot_id: uuid.UUID) -> list[DatasetSnapshotMember]:
        return await self.repo.members([snapshot_id])
