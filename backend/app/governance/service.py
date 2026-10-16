"""governance.service

Identity resolution and user queries. Transactions are committed here, not in routers
or repositories.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
    PROPOSAL_TRANSITIONS,
    AuditOutcome,
    ClassificationCode,
    Compatibility,
    DependentType,
    FieldType,
    ProposalDecision,
    ProposalStatus,
    SchemaVersionStatus,
    TenantStatus,
    UserStatus,
)
from app.governance.models import (
    AuditEvent,
    RoleModel,
    SchemaChangeProposal,
    SchemaDefinition,
    SchemaField,
    SchemaVersion,
    UserAccount,
)
from app.governance.repository import (
    AuditRepository,
    IdentityRepository,
    ProposalRepository,
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
    """Precision/scale on any type. A missing bound is unbounded (widest), except on a
    DECIMAL, where both bounds are part of the contract."""
    if (old.precision, old.scale) == (new.precision, new.scale):
        return None
    before = {"precision": old.precision, "scale": old.scale}
    after = {"precision": new.precision, "scale": new.scale}
    if new.data_type == FieldType.DECIMAL and None in (new.precision, new.scale):
        # Fixed-precision decimals (Arch non-negotiables): a missing bound is not "wider",
        # it is undefined (Postgres NUMERIC(p) even means scale 0).
        return FieldChange(
            new.name,
            "MODIFIED",
            "precision_scale",
            before,
            after,
            Compatibility.BREAKING,
            "A decimal needs explicit precision and scale.",
        )
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
        self.principal = principal
        self.repo = SchemaRepository(session, principal.scope)

    async def list_schemas(self) -> list[SchemaDefinition]:
        return await self.repo.list_all()

    async def lock_schema(self, schema_id: uuid.UUID) -> SchemaDefinition:
        """The schema locked for the caller's transaction (404 outside the tenant)."""
        schema = await self.repo.lock(schema_id)
        if schema is None:
            raise ApiError(404, "not_found", "Schema not found.")
        return schema

    async def register_dependent(
        self,
        schema_id: uuid.UUID,
        dependent_type: DependentType,
        dependent_ref: str,
        description: str | None,
    ) -> bool:
        """Upsert a `schema_dependency` row in the caller's transaction (D-18 §6), so Phase 5
        impact analysis lists the dependent. True when it was new. Not committed here."""
        await self.get_schema(schema_id)  # 404 outside the tenant
        return await self.repo.add_dependency(
            schema_id, dependent_type, dependent_ref, description, self.principal.actor
        )

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


async def known_classifications(session: AsyncSession) -> frozenset[str]:
    """Every data_classification code (BE-3.4). Global, not tenant data. For modules that
    store a classification code and must refuse unknown ones before the FK does."""
    return await IdentityRepository(session).all_classifications()


# --- Controlled schema change (Phase 5, DG-05) ------------------------------------------


def _public(*keys: str, **others: ClassificationCode) -> dict[str, str]:
    return {**dict.fromkeys(keys, ClassificationCode.PUBLIC), **others}


async def record_denial(
    session: AsyncSession,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str | None,
    reason: str,
) -> None:
    """Audit a refused attempt (BE-3.2) in its own session, as require_permission does: the
    request's own work is rolled back first and nothing else is committed with it."""
    await session.rollback()
    async with AsyncSession(session.bind) as audit_session:
        audit_by(
            audit_session,
            principal,
            action,
            resource_type,
            outcome=AuditOutcome.DENIED,
            resource_id=resource_id,
            details={"reason": reason},
            details_classification={"reason": ClassificationCode.PUBLIC},
        )
        await audit_session.commit()


@dataclass(frozen=True)
class ProposedField:
    """One field of a proposed contract, as submitted (validated by the API schema)."""

    spec: FieldSpec
    description: str | None


class SchemaChangeService:
    """Propose → impact analysis → submit → maker-checker approval → activation (BE-5.x).

    Every write is audited in the same transaction and committed here. Status moves follow
    PROPOSAL_TRANSITIONS (D-06); version immutability is also enforced by DB triggers."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = ProposalRepository(session, principal.scope)

    # --- reads --------------------------------------------------------------------------

    async def get(self, proposal_id: uuid.UUID) -> SchemaChangeProposal:
        proposal = await self.repo.get(proposal_id)
        if proposal is None:
            raise ApiError(404, "not_found", "Proposal not found.")
        return proposal

    async def page(
        self, status: str | None, schema_id: uuid.UUID | None, limit: int, offset: int
    ) -> tuple[list[SchemaChangeProposal], int]:
        return await self.repo.page_filtered(status, schema_id, limit, offset)

    async def fields(self, proposal: SchemaChangeProposal) -> list[SchemaField]:
        return await self.repo.fields(proposal.proposed_version_id)

    # --- create ---------------------------------------------------------------------------

    async def create(
        self,
        schema_id: uuid.UUID,
        title: str,
        rationale: str | None,
        fields: list[ProposedField],
        reverts: SchemaChangeProposal | None = None,
    ) -> SchemaChangeProposal:
        schema = await self.repo.lock_schema(schema_id)
        if schema is None:
            raise ApiError(404, "not_found", "Schema not found.")
        base = effective_version(schema)
        if base is None:
            raise ApiError(409, "no_active_version", "The schema has no version in effect.")
        names = [f.spec.name for f in fields]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ApiError(
                422, "duplicate_field", "Field names must be unique.", {"fields": duplicates}
            )
        known = await self.repo.classification_codes()
        unknown = sorted({f.spec.classification for f in fields} - known)
        if unknown:
            raise ApiError(
                422,
                "unknown_classification",
                "Unknown classification.",
                {"classifications": unknown},
            )
        base_specs = [FieldSpec.from_row(f) for f in await self.repo.fields(base.id)]
        proposed_specs = [f.spec for f in fields]
        diff = diff_fields(base_specs, proposed_specs)
        if diff.overall is None:
            raise ApiError(422, "no_change", "The proposed contract equals the version in effect.")

        version = SchemaVersion(
            schema_id=schema.id,
            version=await self.repo.next_version_number(schema.id),
            status=SchemaVersionStatus.DRAFT,
            compatibility=diff.overall,
            content_sha256=contract_sha256(proposed_specs),
            change_summary=title,
            created_by=self.principal.actor,
        )
        self.repo.add(version)
        await self.session.flush()
        self.repo.add(
            *(
                SchemaField(
                    schema_version_id=version.id,
                    position=i,
                    description=f.description,
                    created_by=self.principal.actor,
                    **{k: getattr(f.spec, k) for k in CONTRACT_KEYS},
                )
                for i, f in enumerate(fields, start=1)
            )
        )
        proposal = SchemaChangeProposal(
            tenant_id=self.principal.tenant_id,
            schema_id=schema.id,
            base_version_id=base.id,
            proposed_version_id=version.id,
            status=ProposalStatus.DRAFT,
            title=title,
            rationale=rationale,
            proposer_user_id=self.principal.user_id,
            reverts_proposal_id=reverts.id if reverts else None,
            created_by=self.principal.actor,
        )
        self.repo.add(proposal)
        await self.session.flush()
        audit_by(
            self.session,
            self.principal,
            "schema_change.revert" if reverts else "schema_change.create",
            "schema_change_proposal",
            resource_id=str(proposal.id),
            details={
                "schema": schema.code,
                "base_version": base.version,
                "proposed_version": version.version,
                "compatibility": diff.overall,
                "reverts": str(reverts.id) if reverts else None,
            },
            details_classification=_public(
                "schema", "base_version", "proposed_version", "compatibility", "reverts"
            ),
        )
        await self.session.commit()
        return await self.get(proposal.id)

    async def revert(self, proposal_id: uuid.UUID) -> SchemaChangeProposal:
        """BE-5.4: a NEW proposal whose contract is the one the applied change replaced."""
        applied = await self._locked(proposal_id)  # holds the schema lock from here on
        if applied.status != ProposalStatus.APPLIED:
            raise ApiError(409, "not_applied", "Only an applied change can be reverted.")
        schema = await self.repo.lock_schema(applied.schema_id)
        current = effective_version(schema) if schema else None
        if current is None or current.id != applied.proposed_version_id:
            raise ApiError(
                409, "not_current", "The version this change created is no longer in effect."
            )
        if await self.repo.reverts_of(applied.id):
            raise ApiError(409, "already_reverted", "This change already has a revert.")
        previous = await self.repo.fields(applied.base_version_id)
        return await self.create(
            applied.schema_id,
            f"Revert: {applied.title}"[:200],
            f"Reinstates the contract of v{applied.base_version.version}.",
            [ProposedField(FieldSpec.from_row(f), f.description) for f in previous],
            reverts=applied,
        )

    # --- impact analysis and submit -------------------------------------------------------

    async def _walk_dependents(self, schema: SchemaDefinition) -> list[dict[str, Any]]:
        """Everything that depends on `schema`, directly or through dependent schemas."""
        found: dict[tuple[str, str], dict[str, Any]] = {}
        visited = {schema.id}
        frontier = [(schema.id, [schema.code])]
        depth = 0
        while frontier:
            depth += 1
            paths = dict(frontier)
            next_frontier: list[tuple[uuid.UUID, list[str]]] = []
            for dep in await self.repo.dependencies_of(list(paths)):
                via = paths[dep.schema_id]
                key = (dep.dependent_type, dep.dependent_ref)
                if key in found:
                    found[key]["paths"].append(via)  # reached again by another route
                else:
                    found[key] = {
                        "dependent_type": dep.dependent_type,
                        "dependent_ref": dep.dependent_ref,
                        "description": dep.description,
                        "provisional": dep.provisional,
                        "depth": depth,  # breadth-first: the shortest route
                        "via": via,
                        "paths": [via],
                    }
                target = dep.dependent_schema_id
                if target is not None and target not in visited:
                    visited.add(target)
                    next_frontier.append((target, [*via, dep.dependent_ref]))
            frontier = next_frontier
        return list(found.values())

    async def analyse(self, proposal_id: uuid.UUID) -> SchemaChangeProposal:
        proposal = await self._locked(proposal_id)
        self._require_status(proposal, ProposalStatus.DRAFT)
        base_specs = [
            FieldSpec.from_row(f) for f in await self.repo.fields(proposal.base_version_id)
        ]
        proposed_specs = [
            FieldSpec.from_row(f) for f in await self.repo.fields(proposal.proposed_version_id)
        ]
        diff = diff_fields(base_specs, proposed_specs)
        dependents = await self._walk_dependents(proposal.schema)
        proposal.impact = {
            "overall": diff.overall,
            "changes": [vars(c) for c in diff.changes],
            "dependents": dependents,
            "base_sha256": contract_sha256(base_specs),
            "proposed_sha256": contract_sha256(proposed_specs),
            "analysed_at": datetime.now(UTC).isoformat(),
        }
        proposal.impact_analysed_at = datetime.now(UTC)
        audit_by(
            self.session,
            self.principal,
            "schema_change.impact_analysis",
            "schema_change_proposal",
            resource_id=str(proposal.id),
            details={
                "overall": diff.overall,
                "changes": len(diff.changes),
                "dependents": len(dependents),
            },
            details_classification=_public("overall", "changes", "dependents"),
        )
        await self.session.commit()
        return await self.get(proposal.id)

    async def submit(self, proposal_id: uuid.UUID, evidence: str | None) -> SchemaChangeProposal:
        proposal = await self._locked(proposal_id)
        if proposal.proposer_user_id != self.principal.user_id:
            await record_denial(
                self.session,
                self.principal,
                "schema_change.submit",
                "schema_change_proposal",
                str(proposal.id),
                "not_proposer",
            )
            raise ApiError(403, "not_proposer", "Only the proposer can submit this proposal.")
        self._require_status(proposal, ProposalStatus.DRAFT)
        if proposal.impact is None:
            raise ApiError(
                409, "impact_analysis_required", "Run the impact analysis before submitting."
            )
        self._move(proposal, ProposalStatus.SUBMITTED)
        proposal.submitted_at = datetime.now(UTC)
        proposal.regression_evidence_ref = evidence
        audit_by(
            self.session,
            self.principal,
            "schema_change.submit",
            "schema_change_proposal",
            resource_id=str(proposal.id),
            details={"overall": proposal.impact.get("overall"), "evidence": evidence},
            details_classification=_public("overall", evidence=ClassificationCode.INTERNAL),
        )
        await self.session.commit()
        return await self.get(proposal.id)

    # --- decision -------------------------------------------------------------------------

    async def decide(
        self, proposal_id: uuid.UUID, decision: ProposalDecision, comment: str | None
    ) -> SchemaChangeProposal:
        proposal = await self._locked(proposal_id)
        # Maker-checker (BE-5.3): the approver must be someone other than the proposer.
        if proposal.proposer_user_id == self.principal.user_id:
            await record_denial(
                self.session,
                self.principal,
                "schema_change.approve"
                if decision == ProposalDecision.APPROVE
                else "schema_change.reject",
                "schema_change_proposal",
                str(proposal.id),
                "self_approval",
            )
            raise ApiError(403, "self_approval", "You cannot decide on your own proposal.")
        self._require_status(proposal, ProposalStatus.SUBMITTED)
        if decision == ProposalDecision.REJECT:
            if not comment:
                raise ApiError(422, "comment_required", "Give a reason for the rejection.")
            return await self._close(proposal, ProposalStatus.REJECTED, comment)
        if proposal.impact is None:
            raise ApiError(
                409, "impact_analysis_required", "A recorded impact analysis is required."
            )
        schema = await self.repo.lock_schema(proposal.schema_id)
        assert schema is not None  # the proposal is in this tenant, so is its schema
        base = await self.repo.version(proposal.base_version_id)
        proposed = await self.repo.version(proposal.proposed_version_id)
        if base.status != SchemaVersionStatus.ACTIVE or effective_version(schema) != base:
            # Applying a change supersedes every other open proposal on the schema, so this
            # is only reachable through out-of-band edits. Refuse and change nothing.
            raise ApiError(
                409,
                "stale_base_version",
                "Another change took effect first. Propose again against the current version.",
            )
        specs = [FieldSpec.from_row(f) for f in await self.repo.fields(proposed.id)]
        if contract_sha256(specs) not in {proposed.content_sha256} or (
            proposal.impact.get("proposed_sha256") != proposed.content_sha256
        ):
            raise ApiError(
                409,
                "contract_changed",
                "The proposed contract changed after it was analysed. Propose it again.",
            )
        # One transaction: deprecate the old version, activate the new one, record it all.
        base.status = SchemaVersionStatus.DEPRECATED
        proposed.status = SchemaVersionStatus.ACTIVE
        proposed.effective_from = date.today()
        self._record_decision(proposal, ProposalStatus.APPLIED, comment)
        # Every other open proposal was made against the old version: close them now, in
        # this transaction, rather than letting an approver find out later.
        for other in await self.repo.open_for_schema(schema.id, exclude=proposal.id):
            await self._end(other, ProposalStatus.SUPERSEDED, f"Superseded by {proposal.id}.")
        overall = proposal.impact.get("overall")
        audit_by(
            self.session,
            self.principal,
            "schema_change.approve",
            "schema_change_proposal",
            resource_id=str(proposal.id),
            approval_ref=str(proposal.id),
            details={
                "overall": overall,
                "comment": comment,
                "proposer": str(proposal.proposer_user_id),
            },
            details_classification=_public(
                "overall", comment=ClassificationCode.INTERNAL, proposer=ClassificationCode.INTERNAL
            ),
        )
        for action, version in (
            ("schema_version.deprecate", base),
            ("schema_version.activate", proposed),
        ):
            audit_by(
                self.session,
                self.principal,
                action,
                "schema_version",
                resource_id=str(version.id),
                approval_ref=str(proposal.id),
                details={"schema": schema.code, "version": version.version},
                details_classification=_public("schema", "version"),
            )
        await self.session.commit()
        return await self.get(proposal.id)

    async def _close(
        self, proposal: SchemaChangeProposal, status: ProposalStatus, comment: str
    ) -> SchemaChangeProposal:
        await self._end(proposal, status, comment)
        await self.session.commit()
        return await self.get(proposal.id)

    async def _end(
        self, proposal: SchemaChangeProposal, status: ProposalStatus, comment: str
    ) -> None:
        """Reject or supersede: the proposal and its DRAFT version both end (D-06)."""
        proposed = await self.repo.version(proposal.proposed_version_id)
        proposed.status = SchemaVersionStatus.REJECTED
        self._record_decision(proposal, status, comment)
        audit_by(
            self.session,
            self.principal,
            "schema_change.reject"
            if status == ProposalStatus.REJECTED
            else "schema_change.supersede",
            "schema_change_proposal",
            resource_id=str(proposal.id),
            details={"comment": comment},
            details_classification={"comment": ClassificationCode.INTERNAL},
        )

    # --- helpers ---------------------------------------------------------------------------

    async def _locked(self, proposal_id: uuid.UUID) -> SchemaChangeProposal:
        """Locks the proposal's schema, then the proposal: one order everywhere (create and
        revert lock the schema too), so concurrent workflow actions cannot deadlock."""
        found = await self.repo.get(proposal_id)
        if found is None:
            raise ApiError(404, "not_found", "Proposal not found.")
        await self.repo.lock_schema(found.schema_id)
        proposal = await self.repo.lock(proposal_id)
        assert proposal is not None  # it existed in this tenant a moment ago; never deleted
        return proposal

    @staticmethod
    def _require_status(proposal: SchemaChangeProposal, expected: ProposalStatus) -> None:
        if proposal.status != expected:
            raise ApiError(
                409,
                "invalid_status",
                f"The proposal is {proposal.status}; this needs {expected}.",
                {"status": proposal.status},
            )

    @staticmethod
    def _move(proposal: SchemaChangeProposal, to: ProposalStatus) -> None:
        if to not in PROPOSAL_TRANSITIONS[ProposalStatus(proposal.status)]:
            raise ApiError(409, "invalid_status", f"Cannot move from {proposal.status} to {to}.")
        proposal.status = to

    def _record_decision(
        self, proposal: SchemaChangeProposal, status: ProposalStatus, comment: str | None
    ) -> None:
        self._move(proposal, status)
        proposal.decided_by = self.principal.actor
        proposal.decided_by_user_id = self.principal.user_id
        proposal.decided_at = datetime.now(UTC)
        proposal.decision_comment = comment
