"""governance.service

Identity resolution and user queries. Transactions are committed here, not in routers
or repositories.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.tokens import DevTokens, VerifiedIdentity
from app.governance.constants import DEFAULT_TENANT_ID, DEV_ISSUER, TenantStatus, UserStatus
from app.governance.models import RoleModel, UserAccount
from app.governance.repository import IdentityRepository, UserRepository


def _principal(user: UserAccount) -> Principal:
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        issuer=user.idp_issuer,
        subject=user.idp_subject,
        display_name=user.display_name,
        email=user.email,
        roles=frozenset(r.code for r in user.roles),
        permissions=frozenset(p.code for r in user.roles for p in r.permissions),
    )


@dataclass(frozen=True)
class DevSession:
    access_token: str
    expires_in: int


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = IdentityRepository(session)

    async def resolve_principal(
        self, identity: VerifiedIdentity, can_self_provision: bool
    ) -> Principal:
        user = await self.repo.find_user(identity.issuer, identity.subject)
        if user is None:
            if not can_self_provision:
                raise ApiError(
                    403,
                    "account_not_provisioned",
                    "Your account has not been set up on this platform. Contact an administrator.",
                )
            # Local development only: create the account with NO roles, so default deny
            # still applies until someone grants a role.
            user = await self.repo.create_user(
                DEFAULT_TENANT_ID,
                identity.issuer,
                identity.subject,
                identity.email,
                identity.name,
                actor="system:self-provision",
            )
            await self.session.commit()
        if user.status != UserStatus.ACTIVE:
            raise ApiError(403, "account_disabled", "Your account is disabled.")
        if await self.repo.tenant_status(user.tenant_id) != TenantStatus.ACTIVE:
            raise ApiError(403, "tenant_suspended", "Your organisation's access is suspended.")
        return _principal(user)

    async def dev_personas(self) -> list[RoleModel]:
        return await self.repo.list_roles()

    async def start_dev_session(self, role_code: str, tokens: DevTokens) -> DevSession:
        role = await self.repo.role_by_code(role_code)
        if role is None:
            raise ApiError(422, "unknown_role", "No such role.", {"role": role_code})
        subject = f"dev:{role.code}"
        email = f"{role.code}@dev.moodymate.local"
        name = f"Dev {role.name}"
        user = await self.repo.find_user(DEV_ISSUER, subject)
        if user is None:
            user = await self.repo.create_user(
                DEFAULT_TENANT_ID, DEV_ISSUER, subject, email, name, actor="system:dev-session"
            )
        if role.code not in {r.code for r in user.roles}:
            await self.repo.assign_role(user, role, actor="system:dev-session")
        await self.session.commit()
        return DevSession(
            access_token=tokens.issue(subject, email, name),
            expires_in=int(tokens.ttl.total_seconds()),
        )


class UserService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.repo = UserRepository(session, principal.scope)

    async def list_users(self, limit: int, offset: int) -> tuple[list[UserAccount], int]:
        return await self.repo.page(limit, offset)

    async def get_user(self, user_id: uuid.UUID) -> UserAccount:
        user = await self.repo.get(user_id)
        if user is None:
            # Same response whether the user is absent or in another tenant.
            raise ApiError(404, "not_found", "User not found.")
        return user
