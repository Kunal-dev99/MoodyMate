"""governance.service

Identity resolution and user queries. Transactions are committed here, not in routers
or repositories.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.classification import UNCLASSIFIED, mask_mapping
from app.core.correlation import get_correlation_id
from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.tokens import DevTokens, VerifiedIdentity
from app.governance.constants import (
    COMPATIBILITY_RANK,
    DEFAULT_TENANT_ID,
    DEV_ISSUER,
    AuditOutcome,
    ClassificationCode,
    Compatibility,
    SchemaVersionStatus,
    TenantStatus,
    UserStatus,
)
from app.governance.models import (
    AuditEvent,
    RoleModel,
    SchemaDefinition,
    SchemaVersion,
    UserAccount,
)
from app.governance.repository import (
    AuditRepository,
    IdentityRepository,
    SchemaRepository,
    UserRepository,
)


def audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: str,
    action: str,
    resource_type: str,
    outcome: AuditOutcome,
    actor_user_id: uuid.UUID | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    details_classification: dict[str, str] | None = None,
    approval_ref: str | None = None,
) -> AuditEvent:
    """Record a privileged action (BE-3.2) in the caller's transaction.

    Nothing is committed here: the event is written atomically with the action it
    describes, or not at all. `details` must hold safe metadata only; classify any key
    and classify every key, so it is masked for readers without clearance (BE-3.5).
    """
    details = details or {}
    details_classification = details_classification or {}
    # Fail closed (BE-3.5): the trail is append-only, so a detail stored without a
    # classification could never be fixed later. Reject it when it is written.
    known = {c.value for c in ClassificationCode}
    unclassified = sorted(set(details) - set(details_classification))
    if unclassified:
        raise ValueError(f"audit details need a classification: {', '.join(unclassified)}")
    unknown = sorted(c for c in details_classification.values() if c not in known)
    if unknown:
        raise ValueError(f"unknown classification code(s): {', '.join(unknown)}")
    event = AuditEvent(
        tenant_id=tenant_id,
        actor=actor,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        correlation_id=get_correlation_id(),
        approval_ref=approval_ref,
        details=details,
        details_classification=details_classification,
    )
    AuditRepository(session).add(event)
    return event


def audit_by(
    session: AsyncSession,
    principal: Principal,
    action: str,
    resource_type: str,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    **kwargs: Any,
) -> AuditEvent:
    return audit(
        session,
        tenant_id=principal.tenant_id,
        actor=principal.actor,
        actor_user_id=principal.user_id,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
        **kwargs,
    )


async def _principal(
    user: UserAccount, repo: IdentityRepository, masking_enabled: bool
) -> Principal:
    clear = (
        await repo.clear_classifications([r.id for r in user.roles])
        if masking_enabled
        else await repo.all_classifications() | {UNCLASSIFIED}
    )
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        issuer=user.idp_issuer,
        subject=user.idp_subject,
        display_name=user.display_name,
        email=user.email,
        roles=frozenset(r.code for r in user.roles),
        permissions=frozenset(p.code for r in user.roles for p in r.permissions),
        clear_classifications=clear,
    )


class AccountDenied(ApiError):
    """403 for a known account that may not sign in; carries what the audit row needs."""

    def __init__(self, user: UserAccount, code: str, message: str) -> None:
        super().__init__(403, code, message)
        self.user_id = user.id
        self.tenant_id = user.tenant_id


@dataclass(frozen=True)
class DevSession:
    access_token: str
    expires_in: int


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = IdentityRepository(session)

    async def resolve_principal(
        self, identity: VerifiedIdentity, can_self_provision: bool, masking_enabled: bool = True
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
            audit(
                self.session,
                tenant_id=user.tenant_id,
                actor="system:self-provision",
                actor_user_id=user.id,
                action="user.provision",
                resource_type="user",
                resource_id=str(user.id),
                outcome=AuditOutcome.SUCCESS,
                details={"issuer": identity.issuer, "email": identity.email},
                details_classification={
                    "issuer": ClassificationCode.INTERNAL,
                    "email": ClassificationCode.CONFIDENTIAL,
                },
            )
            await self.session.commit()
        if user.status != UserStatus.ACTIVE:
            raise AccountDenied(user, "account_disabled", "Your account is disabled.")
        if await self.repo.tenant_status(user.tenant_id) != TenantStatus.ACTIVE:
            raise AccountDenied(
                user, "tenant_suspended", "Your organisation's access is suspended."
            )
        return await _principal(user, self.repo, masking_enabled)

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
        audit(
            self.session,
            tenant_id=user.tenant_id,
            actor="anonymous:dev-sign-in",
            actor_user_id=user.id,
            action="auth.dev_session.create",
            resource_type="user",
            resource_id=str(user.id),
            outcome=AuditOutcome.SUCCESS,
            details={"role": role.code},
            details_classification={"role": ClassificationCode.INTERNAL},
        )
        await self.session.commit()
        return DevSession(
            access_token=tokens.issue(subject, email, name),
            expires_in=int(tokens.ttl.total_seconds()),
        )


class UserService:
    """Read-only user directory; every read is a privileged access and is audited."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = UserRepository(session, principal.scope)

    async def list_users(self, limit: int, offset: int) -> tuple[list[UserAccount], int]:
        users, total = await self.repo.page(limit, offset)
        audit_by(
            self.session,
            self.principal,
            "user.list",
            "user",
            details={"limit": limit, "offset": offset, "returned": len(users)},
            details_classification=dict.fromkeys(
                ("limit", "offset", "returned"), ClassificationCode.PUBLIC
            ),
        )
        await self.session.commit()
        return users, total

    async def get_user(self, user_id: uuid.UUID) -> UserAccount:
        user = await self.repo.get(user_id)
        if user is None:
            # Same response whether the user is absent or in another tenant.
            raise ApiError(404, "not_found", "User not found.")
        audit_by(self.session, self.principal, "user.read", "user", resource_id=str(user.id))
        await self.session.commit()
        return user


@dataclass(frozen=True)
class AuditFilters:
    resource_type: str | None = None
    resource_id: str | None = None
    actor: str | None = None
    action: str | None = None
    outcome: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


class AuditQueryService:
    """Reads the audit trail (BE-3.3). Reading audit is itself audited."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = AuditRepository(session)

    async def page(
        self, filters: AuditFilters, limit: int, offset: int, before: uuid.UUID | None = None
    ) -> tuple[list[tuple[AuditEvent, dict[str, Any]]], int]:
        """One page, newest first. `before` (an event id) pages by key, not offset, so the
        `audit.read` event each read appends cannot shift the next page."""
        cursor = None
        if before is not None:
            cursor = await self.repo.position(self.principal.scope, before)
            if cursor is None:
                raise ApiError(422, "invalid_cursor", "Unknown 'before' event.")
        events, total = await self.repo.page(
            self.principal.scope,
            limit=limit,
            offset=offset,
            before=cursor,
            resource_type=filters.resource_type,
            resource_id=filters.resource_id,
            actor=filters.actor,
            action=filters.action,
            outcome=filters.outcome,
            occurred_from=filters.occurred_from,
            occurred_to=filters.occurred_to,
        )
        used = {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in vars(filters).items()
            if v is not None
        }
        audit_by(
            self.session,
            self.principal,
            "audit.read",
            "audit",
            details={
                "filters": used,
                "limit": limit,
                "offset": offset,
                "before": str(before) if before else None,
                "returned": len(events),
            },
            details_classification={
                # Filter values are typed by the reader and may name people or records.
                "filters": ClassificationCode.INTERNAL,
                **dict.fromkeys(
                    ("limit", "offset", "before", "returned"), ClassificationCode.PUBLIC
                ),
            },
        )
        await self.session.commit()
        clear = self.principal.clear_classifications
        return [
            (e, mask_mapping(e.details, e.details_classification, clear)) for e in events
        ], total


# --- Schema registry (Phase 4, DG-01) ---------------------------------------------------

CONTRACT_KEYS = (
    "name",
    "data_type",
    "required",
    "unit",
    "precision",
    "scale",
    "classification",
    "extension_eligible",
)


@dataclass(frozen=True)
class FieldSpec:
    """The contract-relevant attributes of a schema field (descriptions excluded)."""

    name: str
    data_type: str
    required: bool
    unit: str | None
    precision: int | None
    scale: int | None
    classification: str
    extension_eligible: bool

    @classmethod
    def from_row(cls, row: Any) -> "FieldSpec":
        """From a SchemaField or a mapping with the same keys."""
        values: dict[str, Any] = (
            {k: row[k] for k in CONTRACT_KEYS}
            if isinstance(row, dict)
            else {k: getattr(row, k) for k in CONTRACT_KEYS}
        )
        return cls(**values)


def contract_sha256(fields: list[FieldSpec]) -> str:
    """Content hash of a version's contract. Fields are hashed by name, so reordering them
    is not a contract change (the diff agrees). Migration 0007 holds a frozen copy of this
    algorithm; test_schema_registry keeps the two equal."""
    ordered = sorted(fields, key=lambda f: f.name)
    canonical = [{k: getattr(f, k) for k in CONTRACT_KEYS} for f in ordered]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class FieldChange:
    field: str
    change: str  # ADDED | REMOVED | MODIFIED
    attribute: str | None
    from_value: Any
    to_value: Any
    compatibility: Compatibility
    reason: str


@dataclass(frozen=True)
class SchemaDiff:
    changes: list[FieldChange]
    overall: Compatibility | None  # None: the contracts are identical


def _fits(old: int | None, new: int | None) -> bool:
    """Does a size bound `new` hold everything `old` held? None means unbounded."""
    return new is None or (old is not None and new >= old)


def _size_change(old: FieldSpec, new: FieldSpec) -> FieldChange | None:
    """Precision/scale on any type; a missing bound is unbounded (widest)."""
    if (old.precision, old.scale) == (new.precision, new.scale):
        return None
    before = {"precision": old.precision, "scale": old.scale}
    after = {"precision": new.precision, "scale": new.scale}
    integer_digits = (
        None if old.precision is None else old.precision - (old.scale or 0),
        None if new.precision is None else new.precision - (new.scale or 0),
    )
    if _fits(old.scale, new.scale) and _fits(*integer_digits):
        return FieldChange(
            new.name,
            "MODIFIED",
            "precision_scale",
            before,
            after,
            Compatibility.COMPATIBLE,
            "Precision/scale widened: every existing value still fits.",
        )
    return FieldChange(
        new.name,
        "MODIFIED",
        "precision_scale",
        before,
        after,
        Compatibility.BREAKING,
        "Precision/scale narrowed: existing values may no longer fit.",
    )


def _field_changes(old: FieldSpec, new: FieldSpec) -> list[FieldChange]:
    changes: list[FieldChange] = []

    def add(attr: str, compat: Compatibility, reason: str) -> None:
        changes.append(
            FieldChange(
                new.name, "MODIFIED", attr, getattr(old, attr), getattr(new, attr), compat, reason
            )
        )

    if old.data_type != new.data_type:
        add(
            "data_type",
            Compatibility.BREAKING,
            "Type changed: existing values and consumers may fail.",
        )
    if size := _size_change(old, new):
        changes.append(size)
    if old.required != new.required:
        if new.required:
            add("required", Compatibility.BREAKING, "Now required: existing records may lack it.")
        else:
            add("required", Compatibility.COMPATIBLE, "Now optional: consumers must allow absence.")
    if old.unit != new.unit:
        add("unit", Compatibility.BREAKING, "Unit changed: values would be misread.")
    if old.classification != new.classification:
        add(
            "classification", Compatibility.COMPATIBLE, "Sensitivity changed: who sees it in clear."
        )
    if old.extension_eligible != new.extension_eligible:
        add("extension_eligible", Compatibility.COMPATIBLE, "Extension eligibility changed.")
    return changes


def diff_fields(old: list[FieldSpec], new: list[FieldSpec]) -> SchemaDiff:
    """Classify every contract change from `old` to `new` (BE-4.3)."""
    before = {f.name: f for f in old}
    after = {f.name: f for f in new}
    changes: list[FieldChange] = []
    for f in old:
        if f.name not in after:
            changes.append(
                FieldChange(
                    f.name,
                    "REMOVED",
                    None,
                    f.data_type,
                    None,
                    Compatibility.BREAKING,
                    "Field removed: producers and consumers relying on it break.",
                )
            )
    for f in new:
        if f.name not in before:
            compat = Compatibility.BREAKING if f.required else Compatibility.ADDITIVE
            reason = (
                "New required field: existing records do not have it."
                if f.required
                else "New optional field: existing records stay valid."
            )
            changes.append(FieldChange(f.name, "ADDED", None, None, f.data_type, compat, reason))
        else:
            changes.extend(_field_changes(before[f.name], f))
    overall = max(
        (c.compatibility for c in changes), key=COMPATIBILITY_RANK.__getitem__, default=None
    )
    return SchemaDiff(changes=changes, overall=overall)


def effective_version(schema: SchemaDefinition, today: date | None = None) -> SchemaVersion | None:
    """The newest ACTIVE version already in effect."""
    today = today or date.today()
    active = [
        v
        for v in schema.versions
        if v.status == SchemaVersionStatus.ACTIVE
        and (v.effective_from is None or v.effective_from <= today)
    ]
    return max(active, key=lambda v: v.version, default=None)


class SchemaService:
    """Read-only schema registry (BE-4.3). Rows are tenant-scoped through the definition."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.repo = SchemaRepository(session, principal.scope)

    async def list_schemas(self) -> list[SchemaDefinition]:
        return await self.repo.list_all()

    async def get_schema(self, schema_id: uuid.UUID) -> SchemaDefinition:
        schema = await self.repo.get(schema_id)
        if schema is None:
            # Same response whether the schema is absent or in another tenant.
            raise ApiError(404, "not_found", "Schema not found.")
        return schema

    async def diff(
        self, schema_id: uuid.UUID, from_version: int, to_version: int
    ) -> tuple[SchemaDefinition, SchemaVersion, SchemaVersion, SchemaDiff]:
        schema = await self.get_schema(schema_id)
        by_number = {v.version: v for v in schema.versions}
        for number in (from_version, to_version):
            if number not in by_number:
                raise ApiError(404, "not_found", "Schema version not found.", {"version": number})
        old, new = by_number[from_version], by_number[to_version]
        result = diff_fields(
            [FieldSpec.from_row(f) for f in old.fields], [FieldSpec.from_row(f) for f in new.fields]
        )
        return schema, old, new, result
