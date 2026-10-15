"""governance.repository

The only code that touches the DB session for this module.

* `IdentityRepository`: lookups that run BEFORE a principal exists (they establish
  it), plus dev-session provisioning. These are the only unscoped reads; no endpoint
  exposes them directly.
* `UserRepository`: tenant-scoped reads for API callers (D-04, via ScopedRepository).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import ScopedRepository
from app.governance.constants import UserStatus
from app.governance.models import RoleModel, Tenant, UserAccount, UserRole


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
