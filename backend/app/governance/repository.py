"""governance.repository

The only code that touches the DB session for this module.

* `IdentityRepository`: lookups that run BEFORE a principal exists (they establish
  it), plus dev-session provisioning. These are the only unscoped reads; no endpoint
  exposes them directly.
* `UserRepository`: tenant-scoped reads for API callers (D-04, via ScopedRepository).
"""

import uuid
from datetime import datetime

from sqlalchemy import Select, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import DataScope, ScopedRepository
from app.governance.constants import UserStatus
from app.governance.models import (
    AccessPolicy,
    AuditEvent,
    DataClassification,
    RoleModel,
    SchemaChangeProposal,
    SchemaDefinition,
    SchemaDependency,
    SchemaField,
    SchemaVersion,
    Tenant,
    UserAccount,
    UserRole,
)


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_user(self, issuer: str, subject: str) -> UserAccount | None:
        result = await self.session.execute(
            select(UserAccount).where(
                UserAccount.idp_issuer == issuer, UserAccount.idp_subject == subject
            )
        )
        return result.scalar_one_or_none()

    async def create_user(
        self,
        tenant_id: uuid.UUID,
        issuer: str,
        subject: str,
        email: str | None,
        display_name: str,
        actor: str,
    ) -> UserAccount:
        """Create the account, or return the existing one if a concurrent request won.

        A user's first page load sends several requests at once, and each may try to
        provision the same identity. ON CONFLICT makes that race harmless.
        """
        await self.session.execute(
            insert(UserAccount)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                idp_issuer=issuer,
                idp_subject=subject,
                email=email,
                display_name=display_name,
                status=UserStatus.ACTIVE,
                created_by=actor,
            )
            .on_conflict_do_nothing(index_elements=["idp_issuer", "idp_subject"])
        )
        user = await self.find_user(issuer, subject)
        assert user is not None  # inserted now or by the concurrent winner
        return user

    async def tenant_status(self, tenant_id: uuid.UUID) -> str | None:
        status: str | None = await self.session.scalar(
            select(Tenant.status).where(Tenant.id == tenant_id)
        )
        return status

    async def clear_classifications(self, role_ids: list[uuid.UUID]) -> frozenset[str]:
        """Codes at or below the highest clearance among the roles (default: rank 0 only)."""
        max_rank = await self.session.scalar(
            select(func.max(DataClassification.rank))
            .join(AccessPolicy, AccessPolicy.max_clear_classification_id == DataClassification.id)
            .where(AccessPolicy.role_id.in_(role_ids))
        )
        limit = max_rank if max_rank is not None else 0
        result = await self.session.execute(
            select(DataClassification.code).where(DataClassification.rank <= limit)
        )
        return frozenset(result.scalars())

    async def all_classifications(self) -> frozenset[str]:
        result = await self.session.execute(select(DataClassification.code))
        return frozenset(result.scalars())

    async def list_roles(self) -> list[RoleModel]:
        result = await self.session.execute(select(RoleModel).order_by(RoleModel.code))
        return list(result.scalars())

    async def role_by_code(self, code: str) -> RoleModel | None:
        result = await self.session.execute(select(RoleModel).where(RoleModel.code == code))
        return result.scalar_one_or_none()

    async def assign_role(self, user: UserAccount, role: RoleModel, actor: str) -> None:
        self.session.add(UserRole(user_id=user.id, role_id=role.id, created_by=actor))
        await self.session.flush()
        await self.session.refresh(user, ["roles"])


class UserRepository(ScopedRepository[UserAccount]):
    model = UserAccount


class AuditRepository:
    """Audit trail access (BE-3.2, BE-3.3). Appends only; reads are tenant-scoped (D-04)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, event: AuditEvent) -> None:
        # No flush/commit here: the event joins the caller's transaction (BE-3.2).
        self.session.add(event)

    async def position(
        self, scope: DataScope, event_id: uuid.UUID
    ) -> tuple[datetime, uuid.UUID] | None:
        """Sort key of an event in the caller's tenant (paging cursor)."""
        row = (
            await self.session.execute(
                select(AuditEvent.occurred_at, AuditEvent.id).where(
                    AuditEvent.id == event_id, AuditEvent.tenant_id == scope.tenant_id
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    def _filtered(
        self,
        scope: DataScope,
        resource_type: str | None,
        resource_id: str | None,
        actor: str | None,
        action: str | None,
        outcome: str | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> Select[tuple[AuditEvent]]:
        stmt = select(AuditEvent).where(AuditEvent.tenant_id == scope.tenant_id)
        if resource_type is not None:
            stmt = stmt.where(AuditEvent.resource_type == resource_type)
        if resource_id is not None:
            stmt = stmt.where(AuditEvent.resource_id == resource_id)
        if actor is not None:
            stmt = stmt.where(AuditEvent.actor == actor)
        if action is not None:
            stmt = stmt.where(AuditEvent.action == action)
        if outcome is not None:
            stmt = stmt.where(AuditEvent.outcome == outcome)
        if occurred_from is not None:
            stmt = stmt.where(AuditEvent.occurred_at >= occurred_from)
        if occurred_to is not None:
            stmt = stmt.where(AuditEvent.occurred_at < occurred_to)
        return stmt

    async def page(
        self,
        scope: DataScope,
        *,
        limit: int,
        offset: int,
        before: tuple[datetime, uuid.UUID] | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> tuple[list[AuditEvent], int]:
        stmt = self._filtered(
            scope, resource_type, resource_id, actor, action, outcome, occurred_from, occurred_to
        )
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        if before is not None:
            key = tuple_(AuditEvent.occurred_at, AuditEvent.id)
            stmt = stmt.where(key < tuple_(literal(before[0]), literal(before[1])))
        result = await self.session.execute(
            stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)


class SchemaRepository(ScopedRepository[SchemaDefinition]):
    """Schema definitions in the caller's tenant; versions, fields and dependents load
    with them (D-04: versions are only reachable through a scoped definition)."""

    model = SchemaDefinition

    async def list_all(self) -> list[SchemaDefinition]:
        result = await self.session.execute(self.scoped().order_by(SchemaDefinition.code))
        return list(result.scalars())


class ProposalRepository(ScopedRepository[SchemaChangeProposal]):
    """Schema change proposals and the registry writes they make (BE-5.x), tenant-scoped."""

    model = SchemaChangeProposal

    async def page_filtered(
        self, status: str | None, schema_id: uuid.UUID | None, limit: int, offset: int
    ) -> tuple[list[SchemaChangeProposal], int]:
        stmt = self.scoped()
        if status is not None:
            stmt = stmt.where(SchemaChangeProposal.status == status)
        if schema_id is not None:
            stmt = stmt.where(SchemaChangeProposal.schema_id == schema_id)
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        result = await self.session.execute(
            stmt.order_by(SchemaChangeProposal.created_at.desc(), SchemaChangeProposal.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().unique()), int(total or 0)

    async def lock(self, proposal_id: uuid.UUID) -> SchemaChangeProposal | None:
        result = await self.session.execute(
            self.scoped()
            .where(SchemaChangeProposal.id == proposal_id)
            .with_for_update(of=SchemaChangeProposal)
            .execution_options(populate_existing=True)
        )
        return result.scalars().unique().one_or_none()

    async def lock_schema(self, schema_id: uuid.UUID) -> SchemaDefinition | None:
        """Serialises version numbering and activation per schema."""
        result = await self.session.execute(
            select(SchemaDefinition)
            .where(
                SchemaDefinition.id == schema_id,
                SchemaDefinition.tenant_id == self.scope.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def version(self, version_id: uuid.UUID) -> SchemaVersion:
        result = await self.session.execute(
            select(SchemaVersion)
            .where(SchemaVersion.id == version_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one()

    async def fields(self, version_id: uuid.UUID) -> list[SchemaField]:
        result = await self.session.execute(
            select(SchemaField)
            .where(SchemaField.schema_version_id == version_id)
            .order_by(SchemaField.position)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def next_version_number(self, schema_id: uuid.UUID) -> int:
        current = await self.session.scalar(
            select(func.max(SchemaVersion.version)).where(SchemaVersion.schema_id == schema_id)
        )
        return int(current or 0) + 1

    async def classification_codes(self) -> set[str]:
        return set((await self.session.execute(select(DataClassification.code))).scalars())

    async def dependencies_of(self, schema_ids: list[uuid.UUID]) -> list[SchemaDependency]:
        result = await self.session.execute(
            select(SchemaDependency)
            .join(SchemaDefinition, SchemaDefinition.id == SchemaDependency.schema_id)
            .where(
                SchemaDependency.schema_id.in_(schema_ids),
                SchemaDefinition.tenant_id == self.scope.tenant_id,
            )
            .order_by(SchemaDependency.dependent_type, SchemaDependency.dependent_ref)
        )
        return list(result.scalars())

    async def schema_codes(self, schema_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        result = await self.session.execute(
            select(SchemaDefinition.id, SchemaDefinition.code).where(
                SchemaDefinition.id.in_(schema_ids),
                SchemaDefinition.tenant_id == self.scope.tenant_id,
            )
        )
        return {row[0]: row[1] for row in result}

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)
