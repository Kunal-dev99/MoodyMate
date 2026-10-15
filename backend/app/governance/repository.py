"""governance.repository

The only code that touches the DB session for this module.

* `IdentityRepository`: lookups that run BEFORE a principal exists (they establish
  it), plus dev-session provisioning. These are the only unscoped reads; no endpoint
  exposes them directly.
* `UserRepository`: tenant-scoped reads for API callers (D-04, via ScopedRepository).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import ScopedRepository
from app.governance.constants import UserStatus
from app.governance.models import RoleModel, UserAccount, UserRole


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
        user = UserAccount(
            tenant_id=tenant_id,
            idp_issuer=issuer,
            idp_subject=subject,
            email=email,
            display_name=display_name,
            status=UserStatus.ACTIVE,
            created_by=actor,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user, ["roles"])
        return user

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
