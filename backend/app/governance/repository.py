"""governance.repository

The only code that touches the DB session for this module.

* `IdentityRepository`: lookups that run BEFORE a principal exists (they establish
  it), plus dev-session provisioning. These are the only unscoped reads; no endpoint
  exposes them directly.
* `UserRepository`: tenant-scoped reads for API callers (D-04, via ScopedRepository).
"""

import uuid
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import DataScope, ScopedRepository
from app.governance.constants import UserStatus
from app.governance.models import (
    AccessPolicy,
    AuditEvent,
    DataClassification,
    RoleModel,
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
        result = await self.session.execute(
            stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars()), int(total or 0)
