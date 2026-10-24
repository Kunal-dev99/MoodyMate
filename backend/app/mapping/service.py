"""mapping.service

The versioned mapping workbench (BE-10.3 – BE-10.5, DI-03, D-18, D-19), and drift
detection and review (BE-12.3, BE-12.4, DI-06) at the end of this module.

- A mapping maps one source contract to that contract's target schema. Each version pins
  the target's ACTIVE schema version when it is created and is immutable once it leaves
  DRAFT (DB triggers, migration 0014).
- A dry run (`validate`) applies a version's rules to every record of a profiled import and
  stores counts, codes and names only, never a value. It writes no canonical rows.
- Submit needs the author and a recorded ok validation of exactly this content; approval
  needs someone else and activates in one transaction: the previous ACTIVE version is
  DEPRECATED and a `schema_dependency` (MAPPING, code) row is upserted (D-18 §3, §6).

Every write is audited in the same transaction and committed here (D-10); every refused
write is audited DENIED with its reason code (D-24 §4) through `app.core.refusals.refuse`, after a
rollback. Other modules are reached through their services only.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Context as DecimalContext
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
from typing import Any, NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.canonical import HashKind, sha256_for
from app.core.classification import most_sensitive
from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.refusals import refuse
from app.governance.constants import (
    ClassificationCode,
    DependentType,
    FieldType,
    ProposalDecision,
    SchemaVersionStatus,
)
from app.governance.models import SchemaDefinition, SchemaVersion
from app.governance.service import SchemaService, audit_by, effective_version
from app.ingestion.constants import ContractStatus
from app.ingestion.models import DataProfileSnapshot, ImportJob, SourceContract
from app.ingestion.service import DriftThresholds, ImportService, SourceService
from app.mapping.constants import (
    DRIFT_DETECTOR_VERSION,
    DRIFT_TRANSITIONS,
    MAPPING_VERSION_TRANSITIONS,
    ColumnPolicy,
    DriftStatus,
    DriftType,
    IssueCode,
    IssueSeverity,
    MappingVersionStatus,
    NullPolicy,
    Refusal,
    TransformCode,
    ValidationOutcome,
)
from app.mapping.models import (
    DataDriftEvent,
    FieldMapping,
    MappingRule,
    MappingValidation,
    MappingVersion,
)
from app.mapping.repository import DriftRepository, MappingRepository
from app.mapping.transforms import (
    COMPATIBLE,
    TargetField,
    Transform,
    TransformError,
    apply,
    compile_transform,
)

# Audit actions; a refusal is recorded under the same action, DENIED.
CREATE_MAPPING = "mapping.create"
CREATE_VERSION = "mapping_version.create"
VALIDATE = "mapping.validate"
SUBMIT = "mapping_version.submit"
APPROVE = "mapping.approve"
REJECT = "mapping.reject"

VALIDATE_FETCH_ROWS = 1_000  # records per streamed partition of a dry run
# Most sensitive last (the seeded order, D-11). An unknown code ranks above all (fail closed).


def _public(*keys: str) -> dict[str, str]:
    return dict.fromkeys(keys, ClassificationCode.PUBLIC)


@dataclass(frozen=True)
class RuleSpec:
    """One rule as submitted (validated by the API schema; never trusted for scope)."""

    source_column: str | None
    target_field: str | None
    column_policy: ColumnPolicy
    transform_code: str
    transform_params: dict[str, Any]
    null_policy: NullPolicy
    default_value: str | None

    def canonical(self) -> dict[str, Any]:
        return {
            "source_column": self.source_column,
            "target_field": self.target_field,
            "column_policy": str(self.column_policy),
            "transform": {"code": self.transform_code, "params": self.transform_params},
            "null_policy": str(self.null_policy),
            "default_value": self.default_value,
        }

    @classmethod
    def of(cls, rule: MappingRule) -> "RuleSpec":
        return cls(
            rule.source_column,
            rule.target_field,
            ColumnPolicy(rule.column_policy),
            rule.transform_code,
            dict(rule.transform_params),
            NullPolicy(rule.null_policy),
            rule.default_value,
        )


@dataclass(frozen=True)
class MappingSpec:
    code: str
    name: str
    description: str | None
    source_contract_id: uuid.UUID
    schema_id: uuid.UUID | None
    rules: list[RuleSpec]
    change_summary: str | None


def mapping_sha256(schema_version_id: uuid.UUID | str, rules: Sequence[RuleSpec]) -> str:
    """Content hash of a version: the pinned schema version and the rules, in order."""
    payload = {
        "schema_version_id": str(schema_version_id),
        "rules": [r.canonical() for r in rules],
    }
    return sha256_for(HashKind.MAPPING_VERSION, payload)


def target_fields(version: SchemaVersion) -> dict[str, TargetField]:
    return {
        f.name: TargetField(
            name=f.name,
            data_type=FieldType(f.data_type),
            required=f.required,
            precision=f.precision,
            scale=f.scale,
            classification=f.classification,
        )
        for f in version.fields
    }


@dataclass(frozen=True)
class MappingLabels:
    """Names for the ids a mapping refers to, resolved for the caller's tenant."""

    contracts: dict[uuid.UUID, tuple[str, int]]  # contract id -> (source code, version)
    schema_codes: dict[uuid.UUID, str]
    schema_versions: dict[uuid.UUID, int]  # schema version id -> its number


# --- the dry run (BE-10.4) -----------------------------------------------------------------------


@dataclass
class _RuleRun:
    position: int
    spec: RuleSpec
    transform: Transform
    target: TargetField | None
    present: bool  # the source column is in the import (CONSTANT: always)
    runnable: bool  # evaluated per row (not when the definition is already a BLOCKER)
    mapped: int = 0
    empty: int = 0
    failures: dict[str, int] = field(default_factory=dict)

    @property
    def failed(self) -> int:
        return sum(self.failures.values())


def _issue(severity: IssueSeverity, code: IssueCode, message: str, **where: Any) -> dict[str, Any]:
    return {"severity": severity.value, "code": code.value, "message": message, **where}


# Transforms that pass source text through: the profile's types say whether it will fit.
_TEXT_PASSTHROUGH = frozenset(
    {TransformCode.COPY, TransformCode.TRIM, TransformCode.UPPER, TransformCode.LOWER}
)


class DryRun:
    """A version's rules applied to an import's records, in file order. Pure: it reads the
    records it is given and keeps counts, never values (a business key is kept only as a
    hash, to count duplicates)."""

    def __init__(
        self,
        rules: Sequence[RuleSpec],
        fields: Mapping[str, TargetField],
        columns: Sequence[str],
        profile_types: Mapping[str, Sequence[str]],
        business_keys: Sequence[str],
    ) -> None:
        self.issues: list[dict[str, Any]] = []
        self.rows = 0
        self.business_keys = list(business_keys)
        self._keys: set[str] = set()
        self._duplicates = 0
        self._missing_keys = 0
        present = set(columns)
        self.runs: list[_RuleRun] = []
        for position, spec in enumerate(rules, start=1):
            transform = compile_transform(spec.transform_code, spec.transform_params)
            target = fields.get(spec.target_field) if spec.target_field else None
            has_column = spec.source_column is None or spec.source_column in present
            runnable = self._check_rule(position, spec, transform, target, has_column, fields)
            if runnable:
                self._check_profile(position, spec, transform, target, profile_types)
            self.runs.append(_RuleRun(position, spec, transform, target, has_column, runnable))
        targeted = {s.target_field for s in rules if s.column_policy == ColumnPolicy.MAP}
        for name, target in fields.items():
            if target.required and name not in targeted:
                self.issues.append(
                    _issue(
                        IssueSeverity.BLOCKER,
                        IssueCode.REQUIRED_FIELD_UNMAPPED,
                        f"Required field '{name}' is not mapped.",
                        target_field=name,
                    )
                )
        covered = {s.source_column for s in rules if s.source_column is not None}
        for column in columns:
            if column not in covered:
                self.issues.append(
                    _issue(
                        IssueSeverity.ERROR,
                        IssueCode.COLUMN_WITHOUT_POLICY,
                        f"Column '{column}' is neither mapped nor preserved; every source "
                        "column needs MAP or PRESERVE.",
                        source_column=column,
                    )
                )

    def _check_rule(
        self,
        position: int,
        spec: RuleSpec,
        transform: Transform,
        target: TargetField | None,
        has_column: bool,
        fields: Mapping[str, TargetField],
    ) -> bool:
        where = {
            "rule_position": position,
            "target_field": spec.target_field,
            "source_column": spec.source_column,
        }
        if spec.column_policy == ColumnPolicy.MAP and target is None:
            self.issues.append(
                _issue(
                    IssueSeverity.BLOCKER,
                    IssueCode.UNKNOWN_TARGET_FIELD,
                    f"Rule {position} targets a field the pinned schema version does not have.",
                    **where,
                )
            )
            return False
        if not has_column:
            required = target is not None and target.required
            self.issues.append(
                _issue(
                    IssueSeverity.BLOCKER if required else IssueSeverity.WARNING,
                    IssueCode.SOURCE_COLUMN_MISSING,
                    f"Rule {position} reads column '{spec.source_column}', which this import "
                    "does not have.",
                    **where,
                )
            )
            if required:
                return False
        if target is not None and target.data_type not in COMPATIBLE[transform.output]:
            self.issues.append(
                _issue(
                    IssueSeverity.BLOCKER,
                    IssueCode.TRANSFORM_TARGET_INCOMPATIBLE,
                    f"Rule {position}: {transform.code} gives {transform.output} values, which "
                    f"a {target.data_type} field cannot hold.",
                    **where,
                )
            )
            return False
        return True

    def _check_profile(
        self,
        position: int,
        spec: RuleSpec,
        transform: Transform,
        target: TargetField | None,
        profile_types: Mapping[str, Sequence[str]],
    ) -> None:
        if target is None or target.data_type == FieldType.STRING or spec.source_column is None:
            return
        if transform.code not in _TEXT_PASSTHROUGH:
            return
        types = profile_types.get(spec.source_column)
        if types and target.data_type not in types:
            self.issues.append(
                _issue(
                    IssueSeverity.WARNING,
                    IssueCode.PROFILE_TYPE_MISMATCH,
                    f"Rule {position}: the profile of column '{spec.source_column}' does not "
                    f"fit {target.data_type} for every value; consider a parsing transform.",
                    rule_position=position,
                    target_field=spec.target_field,
                    source_column=spec.source_column,
                )
            )

    def add(self, values: Mapping[str, str]) -> None:
        self.rows += 1
        for run in self.runs:
            if not run.runnable:
                continue
            spec = run.spec
            raw = values.get(spec.source_column) if spec.source_column is not None else None
            outcome = apply(run.transform, raw, spec.null_policy, spec.default_value, run.target)
            if outcome.reason is not None:
                run.failures[outcome.reason] = run.failures.get(outcome.reason, 0) + 1
            elif outcome.text is None:
                run.empty += 1
            else:
                run.mapped += 1
        if self.business_keys:
            key = [(values.get(c) or "").strip() for c in self.business_keys]
            if not all(key):
                self._missing_keys += 1
                return
            digest = sha256_for(HashKind.DRY_RUN_BUSINESS_KEY, key)
            if digest in self._keys:
                self._duplicates += 1
            else:
                self._keys.add(digest)

    def add_all(self, rows: Sequence[Mapping[str, str]]) -> None:
        for values in rows:
            self.add(values)

    def result(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        issues = list(self.issues)
        for run in self.runs:
            if run.failed:
                issues.append(
                    _issue(
                        IssueSeverity.ERROR,
                        IssueCode.TRANSFORM_FAILED,
                        f"Rule {run.position} fails on {run.failed} of {self.rows} rows.",
                        rule_position=run.position,
                        target_field=run.spec.target_field,
                        source_column=run.spec.source_column,
                        count=run.failed,
                        reasons=dict(sorted(run.failures.items())),
                    )
                )
        columns = ", ".join(self.business_keys)
        if self._missing_keys:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    IssueCode.MISSING_BUSINESS_KEY,
                    f"{self._missing_keys} rows have an empty business key ({columns}).",
                    count=self._missing_keys,
                )
            )
        if self._duplicates:
            issues.append(
                _issue(
                    IssueSeverity.ERROR,
                    IssueCode.DUPLICATE_BUSINESS_KEY,
                    f"{self._duplicates} rows repeat the business key ({columns}) of an "
                    "earlier row.",
                    count=self._duplicates,
                )
            )
        results = [
            {
                "position": run.position,
                "source_column": run.spec.source_column,
                "target_field": run.spec.target_field,
                "target_type": run.target.data_type.value if run.target else None,
                "column_policy": run.spec.column_policy.value,
                "transform": run.transform.code.value,
                "rows": self.rows if run.runnable else 0,
                "mapped": run.mapped,
                "empty": run.empty,
                "failed": run.failed,
                "failures": dict(sorted(run.failures.items())),
            }
            for run in self.runs
        ]
        return issues, results


def outcome_of(issues: Sequence[Mapping[str, Any]]) -> ValidationOutcome:
    severities = {i["severity"] for i in issues}
    if severities & {IssueSeverity.BLOCKER, IssueSeverity.ERROR}:
        return ValidationOutcome.FAILED
    if IssueSeverity.WARNING in severities:
        return ValidationOutcome.WARNINGS
    return ValidationOutcome.OK


def is_ok(validation: MappingValidation | None, version: MappingVersion) -> bool:
    """A recorded ok dry run of exactly this version's content."""
    return (
        validation is not None
        and validation.outcome != ValidationOutcome.FAILED
        and validation.mapping_version_id == version.id
        and validation.mapping_sha256 == version.content_sha256
    )


# --- the service ---------------------------------------------------------------------------------


class MappingService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = MappingRepository(session, principal.scope)
        self.schemas = SchemaService(session, principal)
        self.sources = SourceService(session, principal)

    # --- reads ---------------------------------------------------------------------------

    async def page(
        self,
        limit: int,
        offset: int,
        source_id: uuid.UUID | None = None,
        schema_id: uuid.UUID | None = None,
    ) -> tuple[list[FieldMapping], int]:
        return await self.repo.page_filtered(limit, offset, source_id, schema_id)

    async def get(self, mapping_id: uuid.UUID) -> FieldMapping:
        mapping = await self.repo.fresh(mapping_id)
        if mapping is None:
            # Same response whether the mapping is absent or in another tenant (D-04).
            raise ApiError(404, "not_found", "Mapping not found.")
        return mapping

    async def labels(self, mappings: Sequence[FieldMapping]) -> MappingLabels:
        schemas = await self.schemas.list_schemas()
        return MappingLabels(
            await self.sources.contract_labels({m.source_contract_id for m in mappings}),
            {s.id: s.code for s in schemas},
            {v.id: v.version for s in schemas for v in s.versions},
        )

    async def validation(self, validation_id: uuid.UUID) -> MappingValidation | None:
        return await self.repo.validation(validation_id)

    async def usable_version(
        self, contract_id: uuid.UUID, schema_version_id: uuid.UUID
    ) -> MappingVersion | None:
        """D-21: the contract's ACTIVE mapping version, if it pins this schema version."""
        return await self.repo.active_for(contract_id, schema_version_id)

    async def usable_mapping(
        self, contract_id: uuid.UUID, schema_version_id: uuid.UUID
    ) -> tuple[FieldMapping, MappingVersion] | None:
        """The usable ACTIVE version with its mapping and rules, for validation (BE-11.3)."""
        version = await self.repo.active_for(contract_id, schema_version_id)
        return await self.repo.scoped_version(version.id) if version is not None else None

    async def column_classifications(
        self,
        contract_id: uuid.UUID,
        schema_version_id: uuid.UUID,
        pinned_version_id: uuid.UUID | None = None,
    ) -> dict[str, str]:
        """Source column -> the classification of the field its MAP rules target (the most
        sensitive, if several). PRESERVEd and unmapped columns are absent (BE-9.3)."""
        version: MappingVersion | None
        if pinned_version_id is not None:
            found = await self.repo.scoped_version(pinned_version_id)
            version = found[1] if found else None
        else:
            version = await self.usable_version(contract_id, schema_version_id)
        if version is None:
            return {}
        fields = {f.name: f.classification for f in await self._schema_fields(version)}
        result: dict[str, str] = {}
        for rule in version.rules:
            if rule.column_policy != ColumnPolicy.MAP or rule.source_column is None:
                continue
            code = fields.get(rule.target_field or "")
            if code is None:
                continue
            current = result.get(rule.source_column)
            result[rule.source_column] = (
                code if current is None else most_sensitive([current, code])
            )
        return result

    # --- writes: create and version (BE-10.3) ---------------------------------------------

    async def _refuse(
        self, action: str, resource_id: uuid.UUID | None, error: ApiError
    ) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await refuse(
            self.session,
            self.principal,
            error,
            action=action,
            resource_type="field_mapping",
            resource_id=resource_id,
        )

    async def _contract(
        self, contract_id: uuid.UUID, action: str, resource_id: uuid.UUID | None
    ) -> SourceContract:
        try:
            _, contract = await self.sources.get_contract(contract_id)
        except ApiError as exc:
            await self._refuse(action, resource_id, exc)
        if contract.status == ContractStatus.SUPERSEDED:
            await self._refuse(
                action,
                resource_id,
                ApiError(
                    409,
                    Refusal.CONTRACT_SUPERSEDED,
                    "The source contract is superseded; map its current version.",
                    {"source_contract_id": str(contract.id)},
                ),
            )
        return contract

    async def _active_schema_version(
        self, schema_id: uuid.UUID, action: str, resource_id: uuid.UUID | None
    ) -> tuple[SchemaDefinition, SchemaVersion]:
        try:
            schema = await self.schemas.get_schema(schema_id)
        except ApiError as exc:
            await self._refuse(action, resource_id, exc)
        version = effective_version(schema)
        if version is None:
            await self._refuse(
                action,
                resource_id,
                ApiError(
                    409,
                    Refusal.NO_ACTIVE_VERSION,
                    "The target schema has no ACTIVE version.",
                    {"schema_id": str(schema_id)},
                ),
            )
        return schema, version

    async def _check_rules(
        self,
        rules: Sequence[RuleSpec],
        version: SchemaVersion,
        action: str,
        resource_id: uuid.UUID | None,
    ) -> None:
        """What a draft may never hold: transforms off the allowlist or with bad params, a
        target the pinned schema version lacks, a target mapped twice, or a column both
        mapped and preserved (or preserved twice)."""
        for position, rule in enumerate(rules, start=1):
            try:
                compile_transform(rule.transform_code, rule.transform_params)
            except TransformError as exc:
                await self._refuse(
                    action,
                    resource_id,
                    ApiError(422, exc.code, exc.message, {"rule": position, "errors": exc.details}),
                )
        names = {f.name for f in version.fields}
        targets = [r.target_field for r in rules if r.target_field is not None]
        unknown = sorted(set(targets) - names)
        if unknown:
            await self._refuse(
                action,
                resource_id,
                ApiError(
                    422,
                    Refusal.UNKNOWN_TARGET_FIELD,
                    "Rules may only target fields of the pinned schema version.",
                    {"fields": unknown, "schema_version_id": str(version.id)},
                ),
            )
        duplicates = sorted({t for t in targets if targets.count(t) > 1})
        if duplicates:
            await self._refuse(
                action,
                resource_id,
                ApiError(
                    422,
                    Refusal.DUPLICATE_TARGET_FIELD,
                    "Each target field is mapped by one rule only.",
                    {"fields": duplicates},
                ),
            )
        preserved = [r.source_column for r in rules if r.column_policy == ColumnPolicy.PRESERVE]
        mapped = {r.source_column for r in rules if r.column_policy == ColumnPolicy.MAP}
        conflicts = sorted(
            {c for c in preserved if c is not None and (c in mapped or preserved.count(c) > 1)}
        )
        if conflicts:
            await self._refuse(
                action,
                resource_id,
                ApiError(
                    422,
                    Refusal.CONFLICTING_COLUMN_POLICY,
                    "A column is either mapped or preserved (once), not both.",
                    {"columns": conflicts},
                ),
            )

    def _new_version(
        self,
        mapping: FieldMapping,
        number: int,
        schema_version: SchemaVersion,
        rules: Sequence[RuleSpec],
        sha: str,
        change_summary: str | None,
    ) -> MappingVersion:
        actor = self.principal.actor
        version = MappingVersion(
            id=uuid.uuid4(),
            field_mapping_id=mapping.id,
            version=number,
            status=MappingVersionStatus.DRAFT,
            schema_version_id=schema_version.id,
            content_sha256=sha,
            author_user_id=self.principal.user_id,
            change_summary=change_summary,
            created_by=actor,
        )
        self.repo.add(version)
        self.repo.add(
            *(
                MappingRule(
                    mapping_version_id=version.id,
                    position=position,
                    source_column=r.source_column,
                    target_field=r.target_field,
                    transform_code=r.transform_code,
                    transform_params=compile_transform(r.transform_code, r.transform_params).params,
                    null_policy=r.null_policy,
                    default_value=r.default_value,
                    column_policy=r.column_policy,
                    created_by=actor,
                )
                for position, r in enumerate(rules, start=1)
            )
        )
        return version

    @staticmethod
    def _normalised(rules: Sequence[RuleSpec]) -> list[RuleSpec]:
        """Params as stored (defaults filled in), so equal rules hash equally."""
        return [
            RuleSpec(
                r.source_column,
                r.target_field,
                r.column_policy,
                r.transform_code,
                compile_transform(r.transform_code, r.transform_params).params,
                r.null_policy,
                r.default_value,
            )
            for r in rules
        ]

    async def create(self, spec: MappingSpec) -> FieldMapping:
        """The mapping and DRAFT v1 pinned to the target's ACTIVE schema version, in one
        transaction (audited `mapping.create`)."""
        contract = await self._contract(spec.source_contract_id, CREATE_MAPPING, None)
        if spec.schema_id is not None and spec.schema_id != contract.schema_id:
            await self._refuse(
                CREATE_MAPPING,
                None,
                ApiError(
                    422,
                    Refusal.SCHEMA_MISMATCH,
                    "A mapping targets its contract's schema.",
                    {"schema_id": str(contract.schema_id)},
                ),
            )
        schema, schema_version = await self._active_schema_version(
            contract.schema_id, CREATE_MAPPING, None
        )
        await self._check_rules(spec.rules, schema_version, CREATE_MAPPING, None)
        if await self.repo.code_taken(spec.code):
            await self._refuse(CREATE_MAPPING, None, _duplicate(spec.code))
        if await self.repo.for_contract(contract.id) is not None:
            await self._refuse(
                CREATE_MAPPING,
                None,
                ApiError(
                    409,
                    Refusal.CONTRACT_ALREADY_MAPPED,
                    "This contract already has a mapping; add a version to it.",
                    {"source_contract_id": str(contract.id)},
                ),
            )
        source, _ = await self.sources.get_contract(contract.id)
        mapping = FieldMapping(
            tenant_id=self.principal.tenant_id,
            code=spec.code,
            name=spec.name,
            description=spec.description,
            source_system_id=source.id,
            source_contract_id=contract.id,
            schema_id=schema.id,
            created_by=self.principal.actor,
        )
        self.repo.add(mapping)
        try:
            await self.repo.flush()
        except IntegrityError:
            # A concurrent create with the same code or contract lost the race.
            await self._refuse(CREATE_MAPPING, None, _duplicate(spec.code))
        rules = self._normalised(spec.rules)
        sha = mapping_sha256(schema_version.id, rules)
        v1 = self._new_version(mapping, 1, schema_version, rules, sha, spec.change_summary)
        await self.repo.flush()
        details: dict[str, Any] = {
            "code": spec.code,
            "source_contract_id": str(contract.id),
            "schema": schema.code,
            "schema_version": schema_version.version,
            "version_id": str(v1.id),
            "version": 1,
            "rules": len(rules),
            "content_sha256": sha,
        }
        audit_by(
            self.session,
            self.principal,
            CREATE_MAPPING,
            "field_mapping",
            resource_id=str(mapping.id),
            details=details,
            details_classification=_public(*details),
        )
        await self.session.commit()
        return await self.get(mapping.id)

    async def add_version(
        self, mapping_id: uuid.UUID, rules_in: Sequence[RuleSpec], change_summary: str | None
    ) -> MappingVersion:
        """The next DRAFT, pinned to the target's ACTIVE schema version now. Earlier
        versions are not touched (audited `mapping_version.create`)."""
        mapping = await self.repo.lock(mapping_id)
        if mapping is None:
            await self._refuse(
                CREATE_VERSION, mapping_id, ApiError(404, "not_found", "Mapping not found.")
            )
        await self._contract(mapping.source_contract_id, CREATE_VERSION, mapping.id)
        schema, schema_version = await self._active_schema_version(
            mapping.schema_id, CREATE_VERSION, mapping.id
        )
        await self._check_rules(rules_in, schema_version, CREATE_VERSION, mapping.id)
        rules = self._normalised(rules_in)
        sha = mapping_sha256(schema_version.id, rules)
        latest = max(mapping.versions, key=lambda v: v.version, default=None)
        if latest is not None and latest.content_sha256 == sha:
            await self._refuse(
                CREATE_VERSION,
                mapping.id,
                ApiError(
                    422,
                    Refusal.NO_CHANGE,
                    "The rules equal the latest version for the same schema version.",
                    {"version": latest.version},
                ),
            )
        number = (latest.version if latest else 0) + 1
        version = self._new_version(mapping, number, schema_version, rules, sha, change_summary)
        await self.repo.flush()
        details: dict[str, Any] = {
            "version_id": str(version.id),
            "version": number,
            "schema": schema.code,
            "schema_version": schema_version.version,
            "rules": len(rules),
            "content_sha256": sha,
        }
        audit_by(
            self.session,
            self.principal,
            CREATE_VERSION,
            "field_mapping",
            resource_id=str(mapping.id),
            details=details,
            details_classification=_public(*details),
        )
        await self.session.commit()
        return await self._version(mapping.id, number)

    # --- dry-run validation (BE-10.4) ------------------------------------------------------

    async def validate(
        self, import_id: uuid.UUID, version_id: uuid.UUID
    ) -> tuple[FieldMapping, MappingVersion, MappingValidation]:
        """Apply a version to every record of a profiled import and store the counts
        (audited `mapping.validate`). Writes no canonical rows and changes no import."""
        imports = ImportService(self.session, self.principal)
        try:
            job = await imports.get(import_id)
        except ApiError as exc:
            await self._refuse(VALIDATE, None, exc)
        found = await self.repo.scoped_version(version_id)
        if found is None:
            await self._refuse(
                VALIDATE, None, ApiError(404, "not_found", "Mapping version not found.")
            )
        mapping, version = found
        await self._check_pairing(job, mapping, version)
        snapshot = await imports.snapshot(job)
        if snapshot is None:
            await self._refuse(
                VALIDATE,
                mapping.id,
                ApiError(
                    409,
                    Refusal.PROFILE_REQUIRED,
                    "The import has no profile yet; a dry run needs one.",
                    {"import_id": str(job.id)},
                ),
            )
        schema_version = await self._pinned(mapping, version)
        _, contract = await self.sources.get_contract(mapping.source_contract_id)
        rules = [RuleSpec.of(r) for r in version.rules]
        columns = list(dict.fromkeys(c for batch in job.batches for c in batch.columns))
        run = DryRun(
            rules,
            target_fields(schema_version),
            columns,
            {c["name"]: c["candidate_types"] for c in snapshot.columns},
            contract.business_key_columns,
        )
        async for partition in imports.record_partitions(job, VALIDATE_FETCH_ROWS):
            await run_in_threadpool(run.add_all, [row[0] for row in partition])
        issues, results = run.result()
        outcome = outcome_of(issues)
        counts = {s: sum(1 for i in issues if i["severity"] == s) for s in IssueSeverity}
        validation = MappingValidation(
            id=uuid.uuid4(),
            mapping_version_id=version.id,
            import_job_id=job.id,
            mapping_sha256=version.content_sha256,
            outcome=outcome,
            row_count=run.rows,
            blocker_count=counts[IssueSeverity.BLOCKER],
            error_count=counts[IssueSeverity.ERROR],
            warning_count=counts[IssueSeverity.WARNING],
            issues=issues,
            rule_results=results,
            created_by=self.principal.actor,
        )
        self.repo.add(validation)
        await self.repo.flush()
        # Review P10-1: re-read the version under its row lock, so a submit that committed
        # meanwhile is seen (a submitted version's pointer is frozen; the run still stands).
        locked = await self.repo.version_of(version.field_mapping_id, version.version)
        if locked is not None and locked.status == MappingVersionStatus.DRAFT:
            locked.last_validation_id = validation.id  # frozen once the draft is submitted
            await self.repo.flush()
        details: dict[str, Any] = {
            "import_id": str(job.id),
            "validation_id": str(validation.id),
            "version_id": str(version.id),
            "version": version.version,
            "outcome": outcome.value,
            "row_count": run.rows,
            "blockers": validation.blocker_count,
            "errors": validation.error_count,
            "warnings": validation.warning_count,
        }
        audit_by(
            self.session,
            self.principal,
            VALIDATE,
            "field_mapping",
            resource_id=str(mapping.id),
            details=details,
            details_classification=_public(*details),
        )
        await self.session.commit()
        return mapping, version, validation

    async def _check_pairing(
        self, job: ImportJob, mapping: FieldMapping, version: MappingVersion
    ) -> None:
        """A dry run only makes sense for an import of the mapped contract that pins the
        version's schema version (D-18 §4: mappings are never rebased)."""
        if job.source_contract_id != mapping.source_contract_id:
            await self._refuse(
                VALIDATE,
                mapping.id,
                ApiError(
                    409,
                    Refusal.CONTRACT_MISMATCH,
                    "The import was received under another source contract than the one "
                    "this mapping maps.",
                    {"import_id": str(job.id)},
                ),
            )
        if job.schema_version_id != version.schema_version_id:
            await self._refuse(
                VALIDATE,
                mapping.id,
                ApiError(
                    409,
                    Refusal.SCHEMA_VERSION_MISMATCH,
                    "The import pins another schema version than this mapping version.",
                    {"import_id": str(job.id), "version": version.version},
                ),
            )

    # --- submit and approval (BE-10.5) ------------------------------------------------------

    async def _locked_version(
        self, mapping_id: uuid.UUID, number: int, action: str
    ) -> tuple[FieldMapping, MappingVersion]:
        mapping = await self.repo.lock(mapping_id)
        if mapping is None:
            await self._refuse(action, mapping_id, ApiError(404, "not_found", "Mapping not found."))
        version = await self.repo.version_of(mapping.id, number)
        if version is None:
            await self._refuse(
                action,
                mapping.id,
                ApiError(404, "not_found", "Mapping version not found.", {"version": number}),
            )
        return mapping, version

    async def _require_status(
        self,
        mapping: FieldMapping,
        version: MappingVersion,
        expected: MappingVersionStatus,
        action: str,
    ) -> None:
        if version.status != expected:
            await self._refuse(
                action,
                mapping.id,
                ApiError(
                    409,
                    Refusal.INVALID_STATUS,
                    f"Version {version.version} is {version.status}; this needs {expected}.",
                    {"status": version.status},
                ),
            )

    async def _require_validation(
        self, mapping: FieldMapping, version: MappingVersion, action: str
    ) -> None:
        validation = (
            await self.repo.validation(version.last_validation_id)
            if version.last_validation_id
            else None
        )
        rules_sha = mapping_sha256(
            version.schema_version_id, [RuleSpec.of(r) for r in version.rules]
        )
        if not is_ok(validation, version) or rules_sha != version.content_sha256:
            await self._refuse(
                action,
                mapping.id,
                ApiError(
                    409,
                    Refusal.MAPPING_VALIDATION_REQUIRED,
                    "Run a dry-run validation of this version against an import first; its "
                    "latest one must be ok (OK or WARNINGS).",
                    {"version": version.version},
                ),
            )

    async def _require_active_schema_version(
        self, mapping: FieldMapping, schema_version: SchemaVersion, action: str
    ) -> None:
        if schema_version.status != SchemaVersionStatus.ACTIVE:
            await self._refuse(
                action,
                mapping.id,
                ApiError(
                    409,
                    Refusal.SCHEMA_VERSION_NOT_ACTIVE,
                    "The schema version this mapping version pins is no longer ACTIVE; "
                    "create a new version against the current one (D-18 §4).",
                    {"schema_version_id": str(schema_version.id), "status": schema_version.status},
                ),
            )

    def _move(self, version: MappingVersion, to: MappingVersionStatus) -> None:
        current = MappingVersionStatus(version.status)
        if to not in MAPPING_VERSION_TRANSITIONS[current]:
            raise ApiError(409, Refusal.INVALID_STATUS, f"Cannot move from {current} to {to}.")
        version.status = to

    async def submit(
        self, mapping_id: uuid.UUID, number: int, comment: str | None
    ) -> MappingVersion:
        """DRAFT -> IN_REVIEW: the author only, with a recorded ok validation of exactly this
        content, while the pinned schema version is ACTIVE (audited)."""
        mapping, version = await self._locked_version(mapping_id, number, SUBMIT)
        if version.author_user_id != self.principal.user_id:
            await self._refuse(
                SUBMIT,
                mapping.id,
                ApiError(403, Refusal.NOT_AUTHOR, "Only the author can submit this version."),
            )
        await self._require_status(mapping, version, MappingVersionStatus.DRAFT, SUBMIT)
        await self._require_validation(mapping, version, SUBMIT)
        schema_version = await self._pinned(mapping, version)
        await self._require_active_schema_version(mapping, schema_version, SUBMIT)
        self._move(version, MappingVersionStatus.IN_REVIEW)
        version.submitted_at = datetime.now(UTC)
        await self.repo.flush()
        details: dict[str, Any] = {
            "version_id": str(version.id),
            "version": version.version,
            "validation_id": str(version.last_validation_id),
            "comment": comment,
        }
        audit_by(
            self.session,
            self.principal,
            SUBMIT,
            "field_mapping",
            resource_id=str(mapping.id),
            details=details,
            details_classification={
                **_public("version_id", "version", "validation_id"),
                "comment": ClassificationCode.INTERNAL,
            },
        )
        await self.session.commit()
        return await self._version(mapping.id, version.version)

    async def decide(
        self,
        mapping_id: uuid.UUID,
        number: int,
        decision: ProposalDecision,
        comment: str | None,
    ) -> MappingVersion:
        """Maker-checker (D-18 §3). APPROVE activates the version and DEPRECATES the
        previous ACTIVE one, and registers the mapping as a dependent of its schema, in one
        transaction; REJECT ends the version. Both need someone other than the author."""
        action = APPROVE if decision == ProposalDecision.APPROVE else REJECT
        mapping, version = await self._locked_version(mapping_id, number, action)
        if version.author_user_id == self.principal.user_id:
            await self._refuse(
                action,
                mapping.id,
                ApiError(403, Refusal.SELF_APPROVAL, "You cannot decide on your own mapping."),
            )
        await self._require_status(mapping, version, MappingVersionStatus.IN_REVIEW, action)
        if decision == ProposalDecision.REJECT:
            if not comment:
                await self._refuse(
                    action,
                    mapping.id,
                    ApiError(422, Refusal.COMMENT_REQUIRED, "Give a reason for the rejection."),
                )
            self._move(version, MappingVersionStatus.REJECTED)
            self._record_decision(version, comment)
            await self.repo.flush()
            self._audit_decision(REJECT, mapping, version, comment)
            await self.session.commit()
            return await self._version(mapping.id, version.version)

        # The schema lock serialises this with a Phase 5 approval that would deprecate the
        # pinned schema version.
        schema = await self.schemas.lock_schema(mapping.schema_id)
        schema_version = next(v for v in schema.versions if v.id == version.schema_version_id)
        await self._require_active_schema_version(mapping, schema_version, action)
        await self._require_validation(mapping, version, action)
        previous = [v for v in mapping.versions if v.status == MappingVersionStatus.ACTIVE]
        for old in previous:
            self._move(old, MappingVersionStatus.DEPRECATED)
        await self.repo.flush()  # before the activation: one ACTIVE version per mapping
        self._move(version, MappingVersionStatus.ACTIVE)
        self._record_decision(version, comment)
        await self.repo.flush()
        registered = await self.schemas.register_dependent(
            mapping.schema_id,
            DependentType.MAPPING,
            mapping.code,
            f"Field mapping '{mapping.name}' (WS2).",
        )
        self._audit_decision(APPROVE, mapping, version, comment, registered=registered)
        for action_name, item in [
            *(("mapping_version.deprecate", old) for old in previous),
            ("mapping_version.activate", version),
        ]:
            audit_by(
                self.session,
                self.principal,
                action_name,
                "field_mapping",
                resource_id=str(mapping.id),
                approval_ref=str(version.id),
                details={
                    "version_id": str(item.id),
                    "version": item.version,
                    "content_sha256": item.content_sha256,
                },
                details_classification=_public("version_id", "version", "content_sha256"),
            )
        await self.session.commit()
        return await self._version(mapping.id, version.version)

    def _record_decision(self, version: MappingVersion, comment: str | None) -> None:
        version.decided_by = self.principal.actor
        version.decided_by_user_id = self.principal.user_id
        version.decided_at = datetime.now(UTC)
        version.decision_comment = comment

    def _audit_decision(
        self,
        action: str,
        mapping: FieldMapping,
        version: MappingVersion,
        comment: str | None,
        registered: bool | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "version_id": str(version.id),
            "version": version.version,
            "author": str(version.author_user_id),
            "comment": comment,
        }
        classification = {
            **_public("version_id", "version"),
            "author": ClassificationCode.INTERNAL,
            "comment": ClassificationCode.INTERNAL,
        }
        if registered is not None:
            details["schema_dependency_created"] = registered
            classification["schema_dependency_created"] = ClassificationCode.PUBLIC
        audit_by(
            self.session,
            self.principal,
            action,
            "field_mapping",
            resource_id=str(mapping.id),
            approval_ref=str(version.id),
            details=details,
            details_classification=classification,
        )

    # --- helpers ---------------------------------------------------------------------------

    async def _schema_fields(self, version: MappingVersion) -> list[Any]:
        found = await self.repo.scoped_version(version.id)
        if found is None:
            return []
        schema = await self.schemas.get_schema(found[0].schema_id)
        pinned = next((v for v in schema.versions if v.id == version.schema_version_id), None)
        return list(pinned.fields) if pinned else []

    async def _pinned(self, mapping: FieldMapping, version: MappingVersion) -> SchemaVersion:
        schema = await self.schemas.get_schema(mapping.schema_id)
        return next(v for v in schema.versions if v.id == version.schema_version_id)

    async def _version(self, mapping_id: uuid.UUID, number: int) -> MappingVersion:
        mapping = await self.get(mapping_id)
        return next(v for v in mapping.versions if v.version == number)


def _duplicate(code: str) -> ApiError:
    return ApiError(409, Refusal.DUPLICATE_CODE, "A mapping with this code exists.", {"code": code})


# --- Phase 12: drift detection and review (BE-12.3, BE-12.4, DI-06) ------------------------------
#
# Detection is a pure, deterministic comparison of two stored profiles (BE-9.2): the new
# import's and its baseline's (the latest COMPLETED or COMPLETED_WITH_WARNINGS import of the
# same source). It writes OPEN events for a person to review and changes nothing else: no
# schema, contract or mapping (Arch §6.1 step 7). Decimal only, never float.

DETECT_DRIFT = "import.drift_detect"  # audited with the profile it compares
REVIEW_DRIFT = "drift_event.review"  # a reviewer's decision; refusals under the same action
# The Phase 5 new-proposal page (frontend/src/app/(platform)/schemas/proposals/new), with the
# import's target schema prefilled: the only follow-up ACTION_REQUIRED points to.
PROPOSAL_PAGE = "/schemas/proposals/new"
_DRIFT_EXACT = DecimalContext(prec=100_000, traps=[Inexact, Rounded, InvalidOperation])
# The profile statistics a drift summary keeps (never top values). `min` and `max` are data
# and are masked by the column's classification on read, as on the profile (BE-9.3).
SUMMARY_KEYS = (
    "position",
    "value_count",
    "null_count",
    "null_rate",
    "distinct_count",
    "candidate_types",
    "min",
    "max",
)


def drift_summary(column: Mapping[str, Any]) -> dict[str, Any]:
    """The statistics of one profiled column that a drift event keeps."""
    summary = {key: column.get(key) for key in SUMMARY_KEYS}
    types = column.get("candidate_types") or []
    summary["suggested_type"] = types[0] if types else None
    return summary


def _null_rate(column: Mapping[str, Any]) -> Decimal | None:
    rate = column.get("null_rate")
    return Decimal(rate) if isinstance(rate, str) else None


def _numeric_range(column: Mapping[str, Any]) -> tuple[Decimal, Decimal] | None:
    """A numeric column's (min, max); None when the column is not numeric throughout."""
    if FieldType.DECIMAL.value not in (column.get("candidate_types") or []):
        return None
    low, high = column.get("min"), column.get("max")
    if not isinstance(low, str) or not isinstance(high, str):
        return None
    try:
        return Decimal(low), Decimal(high)
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class DriftFinding:
    """One drift event before it has ids."""

    drift_type: DriftType
    column: str
    observed: dict[str, Any] | None
    baseline: dict[str, Any] | None


def detect_drift(
    observed: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
    null_rate_threshold: Decimal,
    range_threshold: Decimal,
) -> list[DriftFinding]:
    """Compare two profiles' columns (BE-12.3), in the observed file order, then the removed
    columns in the baseline's order:

    - COLUMN_ADDED / COLUMN_REMOVED: a column only one side has (exact name);
    - TYPE_CHANGED: the suggested (most specific) type differs, both sides having values;
    - NULL_RATE_SHIFT: the null rate moved by more than `null_rate_threshold`;
    - RANGE_SHIFT: a numeric column's min or max lies beyond the baseline's by more than
      `range_threshold` x the baseline's range (x |baseline max| when the range is zero)."""
    before = {str(c["name"]): c for c in baseline}
    after = {str(c["name"]): c for c in observed}
    found: list[DriftFinding] = []
    with localcontext(_DRIFT_EXACT):
        for name, column in after.items():
            old = before.get(name)
            if old is None:
                found.append(
                    DriftFinding(DriftType.COLUMN_ADDED, name, drift_summary(column), None)
                )
                continue
            now, then = drift_summary(column), drift_summary(old)
            if now["suggested_type"] and then["suggested_type"]:
                if now["suggested_type"] != then["suggested_type"]:
                    found.append(DriftFinding(DriftType.TYPE_CHANGED, name, now, then))
            rate_now, rate_then = _null_rate(column), _null_rate(old)
            if rate_now is not None and rate_then is not None:
                if (rate_now - rate_then).copy_abs() > null_rate_threshold:
                    found.append(DriftFinding(DriftType.NULL_RATE_SHIFT, name, now, then))
            range_now, range_then = _numeric_range(column), _numeric_range(old)
            if range_now is not None and range_then is not None:
                low, high = range_then
                span = high - low if high != low else high.copy_abs()
                margin = range_threshold * span
                if range_now[0] < low - margin or range_now[1] > high + margin:
                    found.append(DriftFinding(DriftType.RANGE_SHIFT, name, now, then))
        for name, column in before.items():
            if name not in after:
                found.append(
                    DriftFinding(DriftType.COLUMN_REMOVED, name, None, drift_summary(column))
                )
    return found


# The detector's parts, for the test that proves it holds no float (BE-12.3).
DRIFT_PARTS = ("drift_summary", "_null_rate", "_numeric_range", "detect_drift")


@dataclass(frozen=True)
class DriftView:
    """An import's drift events with what masking and the follow-up link need."""

    job: ImportJob
    events: list[DataDriftEvent]
    classifications: dict[str, str]  # column -> classification of its mapped field
    default_classification: str
    schema_id: uuid.UUID  # the import's target schema (the proposal page's prefill)

    def classification(self, column: str) -> str:
        """BE-9.3: the mapped field's classification, else the source profile's default."""
        return self.classifications.get(column, self.default_classification)

    def proposal_href(self) -> str:
        return f"{PROPOSAL_PAGE}?schema={self.schema_id}"


class DriftService:
    """Detect drift after profiling (BE-12.3) and record a person's review (BE-12.4).
    Nothing here changes a schema, a contract or a mapping."""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = DriftRepository(session, principal.scope)
        self.imports = ImportService(session, principal)

    async def detect(
        self, job: ImportJob, snapshot: DataProfileSnapshot, thresholds: DriftThresholds
    ) -> list[DriftFinding]:
        """Compare a new profile with the source's baseline, in the profiling transaction
        (the caller commits), audited `import.drift_detect` with the baseline, counts and
        types only. A first import has no baseline: nothing is compared or written."""
        found = await self.imports.drift_baseline(job)
        if found is None:
            return []
        baseline, base_snapshot = found
        findings = await run_in_threadpool(
            detect_drift,
            snapshot.columns,
            base_snapshot.columns,
            thresholds.null_rate,
            thresholds.range_share,
        )
        applied = {
            "null_rate_threshold": format(thresholds.null_rate, "f"),
            "range_threshold": format(thresholds.range_share, "f"),
            "provisional": True,
        }
        await self.repo.insert_events(
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": job.tenant_id,
                    "import_job_id": job.id,
                    "sequence": position,
                    "baseline_import_id": baseline.id,
                    "observed_snapshot_id": snapshot.id,
                    "baseline_snapshot_id": base_snapshot.id,
                    "drift_type": f.drift_type.value,
                    "column_name": f.column,
                    "observed_summary": f.observed,
                    "baseline_summary": f.baseline,
                    "thresholds": applied,
                    "detector_version": DRIFT_DETECTOR_VERSION,
                    "status": DriftStatus.OPEN.value,
                    "created_by": self.principal.actor,
                }
                for position, f in enumerate(findings, start=1)
            ]
        )
        types: dict[str, int] = {}
        for f in findings:
            types[f.drift_type.value] = types.get(f.drift_type.value, 0) + 1
        details: dict[str, Any] = {
            "baseline_import_id": str(baseline.id),
            "event_count": len(findings),
            "drift_types": dict(sorted(types.items())),
            "detector_version": DRIFT_DETECTOR_VERSION,
        }
        audit_by(
            self.session,
            self.principal,
            DETECT_DRIFT,
            "import_job",
            resource_id=str(job.id),
            details=details,
            details_classification=_public(*details),
        )
        return findings

    async def _view(self, job: ImportJob, events: list[DataDriftEvent]) -> DriftView:
        source, contract = await self.sources.get_contract(job.source_contract_id)
        return DriftView(
            job=job,
            events=events,
            classifications=await self.imports.mapped_classifications(job),
            default_classification=source.profiles[0].default_classification,
            schema_id=contract.schema_id,
        )

    @property
    def sources(self) -> SourceService:
        return self.imports.sources

    async def view(self, import_id: uuid.UUID) -> DriftView:
        """An import's drift events (GET /imports/{id}/drift); 404 alike for an absent and
        a foreign import (D-04)."""
        job = await self.imports.get(import_id)
        return await self._view(job, await self.repo.for_import(job.id))

    async def _refuse(self, resource_id: uuid.UUID, error: ApiError) -> NoReturn:
        await refuse(
            self.session,
            self.principal,
            error,
            action=REVIEW_DRIFT,
            resource_type="data_drift_event",
            resource_id=resource_id,
        )

    async def review(
        self, event_id: uuid.UUID, outcome: DriftStatus, comment: str
    ) -> tuple[DriftView, DataDriftEvent]:
        """A person's review (BE-12.4): OPEN -> ACKNOWLEDGED | ACTION_REQUIRED, once, with
        the reviewer, the comment and the time, audited `drift_event.review`. A second
        review is 409 `already_reviewed` (audited). ACTION_REQUIRED creates nothing: the
        response points to the Phase 5 proposal page."""
        event = await self.repo.lock(event_id)
        if event is None:
            # Absent and foreign look alike (D-04); the requested id is audited.
            await self._refuse(event_id, ApiError(404, "not_found", "Drift event not found."))
        current = DriftStatus(event.status)
        if outcome not in DRIFT_TRANSITIONS[current]:
            await self._refuse(
                event.id,
                ApiError(
                    409,
                    "already_reviewed",
                    f"This drift event was reviewed already ({current}); a review is final.",
                    {"status": current.value},
                ),
            )
        event.status = outcome
        event.reviewed_by = self.principal.actor
        event.review_comment = comment
        event.reviewed_at = datetime.now(UTC)
        await self.repo.flush()
        details: dict[str, Any] = {
            "import_id": str(event.import_job_id),
            "drift_type": event.drift_type,
            "from": current.value,
            "to": outcome.value,
            "comment": comment,
        }
        audit_by(
            self.session,
            self.principal,
            REVIEW_DRIFT,
            "data_drift_event",
            resource_id=str(event.id),
            details=details,
            details_classification={
                **_public("import_id", "drift_type", "from", "to"),
                "comment": ClassificationCode.INTERNAL,
            },
        )
        await self.session.commit()
        job = await self.imports.get(event.import_job_id)
        events = await self.repo.for_import(job.id)
        return await self._view(job, events), next(e for e in events if e.id == event_id)
