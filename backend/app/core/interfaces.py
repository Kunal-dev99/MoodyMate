"""core.interfaces (BE-35.1)

What `app.core` needs from the identity and audit owner, `app.governance`, declared here and
implemented there. `app.core` imports only `app.db`; governance imports core and binds its
implementation once, when `app.governance.service` is imported (every app and every module
service imports it). So governance -> core is the only edge between the two.

* `resolve_principal`: a verified token identity to the server-side `Principal` (BE-2.3); a
  known account that may not sign in raises `AccountDenied`.
* `audit_denied`: one DENIED audit event in the caller's session (BE-3.2), every detail
  PUBLIC (codes, permission names and counts only, never a request value). The caller owns
  the session: auth and the rate limiter pass their own audit session (core.auth).
* `record_denial`: the audited refusal of a write, in its own session after a rollback; only
  `app.core.refusals.refuse` calls it (BE-29.3).

`governance()` fails closed: with nothing bound it raises, so a refusal never goes
unaudited silently (a 500, not a 403 without its trace).
"""

import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.tokens import VerifiedIdentity

DenialDetails = Mapping[str, str | int]


class AccountDenied(ApiError):
    """403 for a known account that may not sign in; carries what the audit row needs."""

    def __init__(self, user_id: uuid.UUID, tenant_id: uuid.UUID, code: str, message: str) -> None:
        super().__init__(403, code, message)
        self.user_id = user_id
        self.tenant_id = tenant_id


class ResolvePrincipal(Protocol):
    def __call__(
        self,
        session: AsyncSession,
        identity: VerifiedIdentity,
        *,
        can_self_provision: bool,
        masking_enabled: bool,
    ) -> Awaitable[Principal]: ...


class AuditDenied(Protocol):
    def __call__(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        actor: str,
        actor_user_id: uuid.UUID | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        details: DenialDetails,
    ) -> None: ...


class RecordDenial(Protocol):
    def __call__(
        self,
        session: AsyncSession,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str | None,
        reason: str,
    ) -> Awaitable[None]: ...


@dataclass(frozen=True)
class Governance:
    """The governance module's implementation of what core needs (bound at its import)."""

    resolve_principal: ResolvePrincipal
    audit_denied: AuditDenied
    record_denial: RecordDenial


_bound: list[Governance] = []


def bind_governance(implementation: Governance) -> None:
    """Called by app.governance.service when it is imported. Binding again replaces it."""
    _bound[:] = [implementation]


def governance() -> Governance:
    if not _bound:
        raise RuntimeError("app.core.interfaces: no governance implementation is bound")
    return _bound[0]


def audit_denied_by(
    session: AsyncSession,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: DenialDetails,
) -> None:
    """`audit_denied` for a signed-in principal (as governance's `audit_by` fills it)."""
    governance().audit_denied(
        session,
        tenant_id=principal.tenant_id,
        actor=principal.actor,
        actor_user_id=principal.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
    )
