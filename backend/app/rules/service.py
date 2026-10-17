"""rules.service

Registry reads and pinning (BE-15.1, BE-15.4, BE-15.5, D-31 §4, D-32).

- `catalogue()`: every module version of the tenant with the rules it reads (each with its
  ACTIVE version), the ACTIVE baseline assumption set, and whether it can run now.
- `module_view(id)`: the same for one module version (None when absent or foreign, D-04).
- `rule_set_for(versions)`: the content-addressed rule set of these rule versions, keyed by
  the hash of their sorted ids (D-32 §4): an existing set is reused, a new one is written
  once with its members (in the caller's transaction).
- `bucket_definitions(rule_version_id)`: the materialised buckets of a DAY_BUCKETS version.

Phase 16 (BE-16.1 - BE-16.3, D-32 §1, §3, §5):

- The closed parameter kinds (`check_schema`, `check_parameters`): pure, value-free. A
  decimal is a JSON string of exact decimal text, normalised to its declared scale; a JSON
  number with a fraction or exponent is refused anywhere (asyncpg would decode it as a
  binary float), so no float ever reaches a stored rule parameter. `parameters_sha256` is
  the canonical JSON hash of the normalised parameters.
- `propose`: a DRAFT copied from the ACTIVE version plus a JSON merge patch (RFC 7386).
- `submit`: DRAFT -> IN_REVIEW. A DAY_BUCKETS version's `maturity_bucket_definition` rows are
  written in this transaction, before the status moves (the 0018 trigger accepts them only
  under a DRAFT version).
- `decide`: maker-checker. APPROVE supersedes the previous ACTIVE version and activates this
  one in one transaction (the partial unique index keeps one ACTIVE version per rule);
  REJECT ends it. The author never decides (409 self_approval); a version proposed before
  another one was activated is stale (409 stale_version: approving it would silently undo
  that change).

Every step and every refusal is audited (`rule.*`, refusals DENIED with their code). The
writes of one rule are serialised by a transaction-scoped lock on the rule.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, NoReturn

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.modules import registered
from app.core.decimals import decimal_text, exact_context
from app.core.errors import ApiError
from app.core.principal import Principal
from app.domain.service import DomainService
from app.governance.constants import ClassificationCode, ProposalDecision
from app.governance.service import audit_by, record_denial
from app.rules.constants import (
    ACTIVATE_RULE,
    APPROVE_RULE,
    BASELINE_ASSUMPTION_SET,
    MAX_BUCKETS,
    MAX_DAYS,
    MAX_DECIMAL_SCALE,
    MAX_MAP_ENTRIES,
    PARAMETER_VOCABULARIES,
    PROPOSE_RULE,
    REJECT_RULE,
    SUBMIT_RULE,
    SUPERSEDE_RULE,
    ImplementationStatus,
    ParameterKind,
    ParameterProblem,
    RuleRefusal,
    RuleVersionStatus,
)
from app.rules.models import (
    AssumptionSet,
    CalculationModule,
    MaturityBucketDefinition,
    ModuleVersion,
    RuleDefinition,
    RuleSet,
    RuleSetMember,
    RuleVersion,
)
from app.rules.repository import RulesRepository


def canonical_sha256(value: object) -> str:
    """SHA-256 of the canonical JSON of a value (sorted keys, no spaces)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def rule_set_sha256(version_ids: Sequence[uuid.UUID]) -> str:
    """The identity of a rule set: the hash of its sorted rule version ids (D-32 §4)."""
    return canonical_sha256(sorted(str(v) for v in version_ids))


# --- the closed parameter kinds (BE-16.1, D-32 §1): pure and value-free ------------------------

P = ParameterProblem
K = ParameterKind
_BUCKET_KEYS = frozenset({"code", "label", "lower_exclusive", "upper_inclusive"})
DECIMAL_TEXT = re.compile(r"^-?(0|[1-9][0-9]{0,29})(\.[0-9]{1,18})?$")
BUCKET_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")
ENUM_VALUE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
# kind -> (required schema keys, the one parameter key)
_KINDS: dict[ParameterKind, tuple[frozenset[str], str]] = {
    K.DECIMAL: (frozenset({"kind", "min", "max", "scale"}), "value"),
    K.INTEGER: (frozenset({"kind", "min", "max"}), "value"),
    K.ENUM: (frozenset({"kind", "values"}), "value"),
    K.DAY_BUCKETS: (frozenset({"kind"}), "buckets"),
    K.ENUM_MAP: (frozenset({"kind", "key_vocabulary", "values"}), "map"),
    K.DECIMAL_MAP: (frozenset({"kind", "key_vocabulary", "min", "max", "scale"}), "map"),
}
_OPTIONAL_SCHEMA_KEYS = frozenset({"unit"})
INVALID_PARAMETERS_MESSAGE = (
    "The parameters do not fit the rule's parameter kind; see details for the problems."
)


@dataclass(frozen=True, order=True)
class Problem:
    """One value-free problem: where (a structural path, never a value) and what."""

    path: str
    code: str


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _inexact(value: Any, path: str) -> list[Problem]:
    """Every JSON number with a fraction or exponent (decoded as a binary float)."""
    if isinstance(value, float):
        return [Problem(path, P.INEXACT_NUMBER)]
    if isinstance(value, Mapping):
        return [p for k, v in value.items() for p in _inexact(v, f"{path}.{_key_path(k)}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _inexact(v, f"{path}[{i}]")]
    return []


_STRUCTURE = frozenset({"value", "buckets", "map", *_BUCKET_KEYS})


def _key_path(key: Any) -> str:
    """A key outside the kinds' own structure is user text: never echoed (value-free)."""
    return key if key in _STRUCTURE else "key"


def exact_json(value: Any) -> bool:
    """Only JSON objects, arrays, strings, integers, booleans and null: no binary float."""
    if isinstance(value, Mapping):
        return all(isinstance(k, str) and exact_json(v) for k, v in value.items())
    if isinstance(value, list):
        return all(exact_json(v) for v in value)
    return value is None or isinstance(value, str | int | bool)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, str) and DECIMAL_TEXT.match(value):
        return Decimal(value)
    return None


def check_schema(schema: Any) -> list[Problem]:
    """A rule definition's `parameter_schema` against the closed kinds (D-32 §1)."""
    if not isinstance(schema, Mapping):
        return [Problem("", P.NOT_AN_OBJECT)]
    try:
        kind = ParameterKind(str(schema.get("kind")))
    except ValueError:
        return [Problem("kind", P.UNKNOWN_KIND)]
    required, _ = _KINDS[kind]
    problems = [
        Problem(k, P.UNKNOWN_KEY) for k in schema if k not in required | _OPTIONAL_SCHEMA_KEYS
    ]
    problems += [Problem(k, P.MISSING_KEY) for k in required if k not in schema]
    if problems:
        return sorted(set(problems))
    if "unit" in schema and not (
        isinstance(schema["unit"], str) and 0 < len(schema["unit"]) <= 100
    ):
        problems.append(Problem("unit", P.NOT_TEXT))
    if kind in (K.DECIMAL, K.DECIMAL_MAP):
        low, high, scale = _decimal(schema["min"]), _decimal(schema["max"]), schema["scale"]
        if low is None or high is None:
            problems.append(Problem("min", P.NOT_DECIMAL_TEXT))
        elif low > high:
            problems.append(Problem("max", P.BOUNDS_ORDER))
        if not _whole(scale) or not 0 <= scale <= MAX_DECIMAL_SCALE:
            problems.append(Problem("scale", P.OUT_OF_RANGE))
    if kind is K.INTEGER:
        if not _whole(schema["min"]) or not _whole(schema["max"]):
            problems.append(Problem("min", P.NOT_INTEGER))
        elif schema["min"] > schema["max"]:
            problems.append(Problem("max", P.BOUNDS_ORDER))
    if kind in (K.ENUM, K.ENUM_MAP):
        values = schema["values"]
        if not isinstance(values, list) or not values:
            problems.append(Problem("values", P.EMPTY))
        elif not all(isinstance(v, str) and ENUM_VALUE.match(v) for v in values):
            problems.append(Problem("values", P.INVALID_CODE))
        elif len(set(values)) != len(values):
            problems.append(Problem("values", P.DUPLICATE))
    if (
        kind in (K.ENUM_MAP, K.DECIMAL_MAP)
        and schema["key_vocabulary"] not in PARAMETER_VOCABULARIES
    ):
        problems.append(Problem("key_vocabulary", P.UNKNOWN_VOCABULARY))
    return sorted(set(problems))


def _decimal_value(value: Any, schema: Mapping[str, Any], path: str) -> tuple[str, list[Problem]]:
    """A DECIMAL value: decimal text within [min, max], at most `scale` places; normalised to
    exactly `scale` places (never rounded: more places are refused)."""
    number = _decimal(value)
    if number is None:
        return "", [Problem(path, P.NOT_DECIMAL_TEXT)]
    scale = int(schema["scale"])
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > scale:
        return "", [Problem(path, P.SCALE_EXCEEDED)]
    low, high = Decimal(schema["min"]), Decimal(schema["max"])
    if not low <= number <= high:
        return "", [Problem(path, P.OUT_OF_RANGE)]
    with localcontext(exact_context()):
        canonical = abs(number) if number == 0 else number
        return decimal_text(canonical.quantize(Decimal(1).scaleb(-scale))), []


def bucket_label(lower: int, upper: int | None) -> str:
    """The canonical label of a DAY_BUCKETS interval, as the seed writes it."""
    return f"({lower},{upper}]" if upper is not None else f"({lower},inf)"


def _buckets(value: Any) -> tuple[list[dict[str, Any]], list[Problem]]:
    """DAY_BUCKETS: ordered, contiguous half-open (lower, upper] calendar-day intervals, the
    last one open; at least one, codes distinct. The first lower bound is at least -1 (so a
    bucket may hold day 0). Labels are derived from the bounds."""
    if not isinstance(value, list) or not value:
        return [], [Problem("buckets", P.EMPTY)]
    if len(value) > MAX_BUCKETS:
        return [], [Problem("buckets", P.TOO_MANY)]
    problems: list[Problem] = []
    buckets: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        path = f"buckets[{i}]"
        if not isinstance(item, Mapping):
            problems.append(Problem(path, P.NOT_AN_OBJECT))
            continue
        problems += [
            Problem(f"{path}.{_key_path(k)}", P.UNKNOWN_KEY) for k in item if k not in _BUCKET_KEYS
        ]
        problems += [
            Problem(f"{path}.{k}", P.MISSING_KEY)
            for k in ("code", "lower_exclusive", "upper_inclusive")
            if k not in item
        ]
        code: Any
        lower: Any
        upper: Any
        code, lower, upper = (
            item.get("code"),
            item.get("lower_exclusive"),
            item.get("upper_inclusive"),
        )
        if not isinstance(code, str) or not BUCKET_CODE.match(code):
            problems.append(Problem(f"{path}.code", P.INVALID_CODE))
        if not _whole(lower) or (upper is not None and not _whole(upper)):
            problems.append(Problem(path, P.NOT_INTEGER))
            continue
        if not -1 <= lower <= MAX_DAYS or (upper is not None and upper > MAX_DAYS):
            problems.append(Problem(path, P.OUT_OF_RANGE))
            continue
        if upper is not None and upper <= lower:
            problems.append(Problem(path, P.BOUNDS_ORDER))
        if buckets and buckets[-1]["upper_inclusive"] != lower:
            problems.append(Problem(f"{path}.lower_exclusive", P.NOT_CONTIGUOUS))
        label = bucket_label(lower, upper)
        if "label" in item and item["label"] != label:
            problems.append(Problem(f"{path}.label", P.LABEL_MISMATCH))
        buckets.append(
            {"code": code, "label": label, "lower_exclusive": lower, "upper_inclusive": upper}
        )
    if not problems:
        if buckets[-1]["upper_inclusive"] is not None:
            problems.append(Problem(f"buckets[{len(buckets) - 1}]", P.LAST_NOT_OPEN))
        if len({b["code"] for b in buckets}) != len(buckets):
            problems.append(Problem("buckets", P.DUPLICATE))
    return buckets, problems


def _map(
    value: Any, kind: ParameterKind, schema: Mapping[str, Any], vocabulary: frozenset[str]
) -> tuple[dict[str, str], list[Problem]]:
    """ENUM_MAP / DECIMAL_MAP: keys from the key vocabulary, values from the value enum (or
    decimals per the schema); at least one entry."""
    if not isinstance(value, Mapping) or not value:
        return {}, [Problem("map", P.EMPTY)]
    if len(value) > MAX_MAP_ENTRIES:
        return {}, [Problem("map", P.TOO_MANY)]
    problems: list[Problem] = []
    normalised: dict[str, str] = {}
    for key in sorted(value, key=str):
        item = value[key]
        if key not in vocabulary:
            problems.append(Problem("map.key", P.NOT_IN_VOCABULARY))
        if kind is K.ENUM_MAP:
            if not isinstance(item, str) or item not in schema["values"]:
                problems.append(Problem("map.value", P.NOT_IN_VALUES))
            else:
                normalised[key] = item
        else:
            text, found = _decimal_value(item, schema, "map.value")
            problems += found
            normalised[key] = text
    return normalised, problems


def check_parameters(
    schema: Mapping[str, Any], parameters: Any, vocabulary: frozenset[str]
) -> tuple[dict[str, Any], list[Problem]]:
    """Proposed parameters against a valid parameter schema (BE-16.1): the normalised
    parameters, or the value-free problems. `vocabulary`: the codes of the schema's key
    vocabulary (map kinds)."""
    if not isinstance(parameters, Mapping):
        return {}, [Problem("", P.NOT_AN_OBJECT)]
    inexact = _inexact(parameters, "")
    if inexact:
        return {}, sorted({Problem(p.path.lstrip("."), p.code) for p in inexact})
    kind = ParameterKind(schema["kind"])
    _, key = _KINDS[kind]
    problems = [Problem(_key_path(k), P.UNKNOWN_KEY) for k in parameters if k != key]
    if key not in parameters:
        problems.append(Problem(key, P.MISSING_KEY))
    if problems:
        return {}, sorted(set(problems))
    value = parameters[key]
    normalised: Any
    if kind is K.DECIMAL:
        normalised, problems = _decimal_value(value, schema, key)
    elif kind is K.INTEGER:
        normalised = value
        if not _whole(value):
            problems = [Problem(key, P.NOT_INTEGER)]
        elif not schema["min"] <= value <= schema["max"]:
            problems = [Problem(key, P.OUT_OF_RANGE)]
    elif kind is K.ENUM:
        normalised = value
        if not isinstance(value, str) or value not in schema["values"]:
            problems = [Problem(key, P.NOT_IN_VALUES)]
    elif kind is K.DAY_BUCKETS:
        normalised, problems = _buckets(value)
    else:
        normalised, problems = _map(value, kind, schema, vocabulary)
    if problems:
        return {}, sorted(set(problems))
    return {key: normalised}, []


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 JSON merge patch: objects merge key by key, null removes, the rest replaces."""
    if not isinstance(patch, Mapping):
        return patch
    result = dict(target) if isinstance(target, Mapping) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def _bucket_rows(parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(b) for b in parameters.get("buckets", [])]


def author_user_id(authored_by: str) -> uuid.UUID | None:
    """The user id of an `user:<uuid>` actor (None for the seed tag or a service)."""
    prefix, _, rest = authored_by.partition(":")
    if prefix != "user":
        return None
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


@dataclass(frozen=True)
class RuleRow:
    definition: RuleDefinition
    module: CalculationModule
    versions: list[RuleVersion]  # by version number


@dataclass(frozen=True)
class RuleView:
    definition: RuleDefinition
    active: RuleVersion | None


@dataclass(frozen=True)
class ModuleView:
    module: CalculationModule
    version: ModuleVersion
    rules: list[RuleView]  # in the order of the input contract
    assumption_set: AssumptionSet | None
    implemented: bool  # registered in code (D-31 §1)

    @property
    def runnable(self) -> bool:
        return (
            self.version.implementation_status == ImplementationStatus.AVAILABLE.value
            and self.implemented
            and len(self.rules) == len(self.version.input_contract.get("rules", []))
            and all(r.active is not None for r in self.rules)
            and self.assumption_set is not None
        )

    @property
    def pinned_versions(self) -> list[RuleVersion]:
        return [r.active for r in self.rules if r.active is not None]


RULE_NOT_FOUND = "Rule not found."


class RulesService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = RulesRepository(session, principal.scope)

    async def _views(self, rows: list[tuple[CalculationModule, ModuleVersion]]) -> list[ModuleView]:
        codes = sorted({c for _, v in rows for c in v.input_contract.get("rules", [])})
        definitions = {d.code: d for d in await self.repo.definitions(codes)}
        active = {
            v.rule_definition_id: v
            for v in await self.repo.active_versions([d.id for d in definitions.values()])
        }
        baseline = await self.repo.active_assumption_set(BASELINE_ASSUMPTION_SET)
        views = []
        for module, version in rows:
            rules = [
                RuleView(definitions[code], active.get(definitions[code].id))
                for code in version.input_contract.get("rules", [])
                if code in definitions
            ]
            implemented = registered(module.code, version.version) is not None
            views.append(ModuleView(module, version, rules, baseline, implemented))
        return views

    async def catalogue(self) -> list[ModuleView]:
        return await self._views(await self.repo.modules())

    async def module_view(self, module_version_id: uuid.UUID) -> ModuleView | None:
        rows = await self.repo.modules(module_version_id)
        return (await self._views(rows))[0] if rows else None

    async def rule_set_for(self, versions: Sequence[RuleVersion]) -> RuleSet:
        """The rule set of exactly these versions, reused or written once (D-32 §4)."""
        sha = rule_set_sha256([v.id for v in versions])
        await self.repo.lock(f"rule_set:{sha}")
        existing = await self.repo.rule_set(sha)
        if existing is not None:
            return existing
        actor = self.principal.actor
        rule_set = RuleSet(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            content_sha256=sha,
            member_count=len(versions),
            created_by=actor,
        )
        self.repo.add(rule_set)
        await self.repo.flush()
        await self.repo.insert_rows(
            RuleSetMember,
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": self.principal.tenant_id,
                    "rule_set_id": rule_set.id,
                    "rule_version_id": v.id,
                    "rule_definition_id": v.rule_definition_id,
                    "precedence": position,
                    "created_by": actor,
                }
                for position, v in enumerate(sorted(versions, key=lambda v: str(v.id)), start=1)
            ],
        )
        return rule_set

    async def bucket_definitions(
        self, rule_version_id: uuid.UUID
    ) -> list[MaturityBucketDefinition]:
        return await self.repo.bucket_definitions(rule_version_id)

    # --- Phase 16: reads (BE-16.2) -----------------------------------------------------------

    async def _rows(self, definitions: list[RuleDefinition]) -> list[RuleRow]:
        modules = {m.id: m for m, _ in await self.repo.modules()}
        versions: dict[uuid.UUID, list[RuleVersion]] = {}
        for v in await self.repo.versions([d.id for d in definitions]):
            versions.setdefault(v.rule_definition_id, []).append(v)
        return [
            RuleRow(d, modules[d.target_module_id], versions.get(d.id, []))
            for d in definitions
            if d.target_module_id in modules
        ]

    async def rules(self) -> list[RuleRow]:
        return await self._rows(await self.repo.all_definitions())

    async def rule(self, rule_id: uuid.UUID) -> RuleRow:
        """404 alike for an absent and a foreign rule (D-04)."""
        definition = await self.repo.get(rule_id)
        if definition is None:
            raise ApiError(404, "not_found", RULE_NOT_FOUND)
        [row] = await self._rows([definition])
        return row

    async def rule_sets_of(
        self, version_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        """rule version id -> the rule sets that hold it (and so the runs that pin it)."""
        found: dict[uuid.UUID, list[uuid.UUID]] = {}
        for version_id, rule_set_id in await self.repo.rule_set_ids(version_ids):
            found.setdefault(version_id, []).append(rule_set_id)
        return found

    async def vocabulary(self, definition: RuleDefinition) -> list[str]:
        return sorted(await self._vocabulary(definition.parameter_schema))

    async def _vocabulary(self, schema: Mapping[str, Any]) -> frozenset[str]:
        if schema.get("key_vocabulary") == "instrument_type":
            codes = await DomainService(self.session, self.principal).instrument_type_codes()
            return frozenset(codes)
        return frozenset()

    async def pinned(self, rule_set_id: uuid.UUID) -> list[tuple[RuleDefinition, RuleVersion]]:
        """The rule versions of a rule set with their definitions (a run's exact pins)."""
        return await self.repo.rule_set_versions(rule_set_id)

    async def assumption_set_by_id(self, assumption_set_id: uuid.UUID) -> AssumptionSet | None:
        return await self.repo.assumption_set(assumption_set_id)

    # --- Phase 16: governed changes (BE-16.1, BE-16.2) --------------------------------------

    async def _refuse(self, action: str, rule_id: uuid.UUID, error: ApiError) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        await record_denial(
            self.session, self.principal, action, "rule_definition", str(rule_id), error.code
        )
        raise error

    async def _locked(
        self, action: str, rule_id: uuid.UUID, enabled: bool
    ) -> tuple[RuleDefinition, list[RuleVersion]]:
        """The switch, then the rule (locked for this transaction) and its versions."""
        if not enabled:
            await self._refuse(
                action,
                rule_id,
                ApiError(
                    409,
                    RuleRefusal.RULE_CHANGE_DISABLED,
                    "Rule changes are switched off in this environment; the ACTIVE versions "
                    "still apply and runs keep working.",
                ),
            )
        await self.repo.lock(f"rule:{rule_id}")
        definition = await self.repo.get(rule_id)
        if definition is None:
            await self._refuse(action, rule_id, ApiError(404, "not_found", RULE_NOT_FOUND))
        if check_schema(definition.parameter_schema):
            await self._refuse(
                action,
                rule_id,
                ApiError(
                    409,
                    RuleRefusal.INVALID_PARAMETER_SCHEMA,
                    "The rule's parameter schema is not one of the closed kinds (D-32 §1); "
                    "it takes no new version.",
                ),
            )
        return definition, await self.repo.versions([definition.id], fresh=True)

    async def _checked(
        self, action: str, definition: RuleDefinition, parameters: Any
    ) -> dict[str, Any]:
        schema = definition.parameter_schema
        vocabulary = await self._vocabulary(schema)
        normalised, problems = check_parameters(schema, parameters, vocabulary)
        if problems:
            await self._refuse(
                action,
                definition.id,
                ApiError(
                    422,
                    RuleRefusal.INVALID_PARAMETERS,
                    INVALID_PARAMETERS_MESSAGE,
                    {
                        "kind": schema["kind"],
                        "problems": [{"path": p.path, "code": p.code} for p in problems],
                    },
                ),
            )
        return normalised

    def _audit(
        self,
        action: str,
        definition: RuleDefinition,
        version: RuleVersion,
        approval_ref: str | None = None,
        **extra: Any,
    ) -> None:
        details: dict[str, Any] = {
            "rule_code": definition.code,
            "rule_version_id": str(version.id),
            "version": version.version,
            "parameters_sha256": version.parameters_sha256,
            **extra,
        }
        classes = dict.fromkeys(details, ClassificationCode.PUBLIC.value)
        if "comment" in details:
            classes["comment"] = ClassificationCode.INTERNAL.value
        audit_by(
            self.session,
            self.principal,
            action,
            "rule_definition",
            resource_id=str(definition.id),
            approval_ref=approval_ref,
            details=details,
            details_classification=classes,
        )

    async def propose(
        self, rule_id: uuid.UUID, patch: dict[str, Any], summary: str, enabled: bool
    ) -> RuleVersion:
        """A DRAFT: the ACTIVE version's parameters with the merge patch applied, checked
        and normalised (D-32 §1); refused when it changes nothing."""
        definition, versions = await self._locked(PROPOSE_RULE, rule_id, enabled)
        active = next((v for v in versions if v.status == RuleVersionStatus.ACTIVE.value), None)
        if active is None:
            await self._refuse(
                PROPOSE_RULE,
                rule_id,
                ApiError(
                    409,
                    RuleRefusal.NO_ACTIVE_VERSION,
                    "The rule has no ACTIVE version to copy a DRAFT from.",
                ),
            )
        parameters = await self._checked(
            PROPOSE_RULE, definition, merge_patch(active.parameters, patch)
        )
        sha = canonical_sha256(parameters)
        if sha == active.parameters_sha256:
            await self._refuse(
                PROPOSE_RULE,
                rule_id,
                ApiError(
                    422,
                    RuleRefusal.NO_CHANGE,
                    "The patch leaves the ACTIVE version's parameters unchanged.",
                    {"active_version": active.version},
                ),
            )
        version = RuleVersion(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            rule_definition_id=definition.id,
            version=max(v.version for v in versions) + 1,
            status=RuleVersionStatus.DRAFT.value,
            parameters=parameters,
            parameters_sha256=sha,
            authored_by=self.principal.actor,
            change_summary=summary,
            provisional=definition.provisional,
            illustrative=definition.illustrative,
            created_by=self.principal.actor,
            # The database clock after the rule lock: what stale_version compares.
            created_at=func.clock_timestamp(),
        )
        self.repo.add(version)
        await self.repo.flush()
        self._audit(PROPOSE_RULE, definition, version, based_on_version=active.version)
        await self.session.commit()
        return version

    async def _require(
        self,
        action: str,
        definition: RuleDefinition,
        versions: list[RuleVersion],
        number: int,
        status: RuleVersionStatus,
    ) -> RuleVersion:
        version = next((v for v in versions if v.version == number), None)
        if version is None:
            await self._refuse(
                action, definition.id, ApiError(404, "not_found", "Rule version not found.")
            )
        if version.status != status.value:
            await self._refuse(
                action,
                definition.id,
                ApiError(
                    409,
                    RuleRefusal.INVALID_STATUS,
                    f"Version {version.version} is {version.status}; this needs {status.value}.",
                    {"status": version.status},
                ),
            )
        return version

    async def submit(
        self, rule_id: uuid.UUID, number: int, comment: str | None, enabled: bool
    ) -> RuleVersion:
        """DRAFT -> IN_REVIEW, by the version's author only (409 not_author). The parameters
        are checked again; a DAY_BUCKETS version's bucket definitions are written first,
        while it is still DRAFT (D-32 §5)."""
        definition, versions = await self._locked(SUBMIT_RULE, rule_id, enabled)
        version = await self._require(
            SUBMIT_RULE, definition, versions, number, RuleVersionStatus.DRAFT
        )
        if version.authored_by != self.principal.actor:
            # Maker-checker: otherwise one person holding both roles could release someone
            # else's DRAFT and approve it alone.
            await self._refuse(
                SUBMIT_RULE,
                rule_id,
                ApiError(
                    409,
                    RuleRefusal.NOT_AUTHOR,
                    "Only the author of a DRAFT rule version can submit it for review.",
                ),
            )
        await self._checked(SUBMIT_RULE, definition, version.parameters)
        if definition.parameter_schema.get("kind") == ParameterKind.DAY_BUCKETS.value:
            await self.repo.insert_rows(
                MaturityBucketDefinition,
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": self.principal.tenant_id,
                        "rule_version_id": version.id,
                        "sequence": sequence,
                        "code": b["code"],
                        "label": b["label"],
                        "lower_exclusive_days": b["lower_exclusive"],
                        "upper_inclusive_days": b["upper_inclusive"],
                        "provisional": version.provisional,
                        "illustrative": version.illustrative,
                        "created_by": self.principal.actor,
                    }
                    for sequence, b in enumerate(_bucket_rows(version.parameters), start=1)
                ],
            )
        await self.repo.move(version.id, RuleVersionStatus.IN_REVIEW)
        self._audit(SUBMIT_RULE, definition, version, comment=comment)
        await self.session.commit()
        return version

    async def decide(
        self,
        rule_id: uuid.UUID,
        number: int,
        decision: ProposalDecision,
        comment: str,
        enabled: bool,
    ) -> RuleVersion:
        """Maker-checker (D-32 §3): APPROVE supersedes the ACTIVE version and activates this
        one in one transaction; REJECT ends it. Never by the version's author."""
        action = APPROVE_RULE if decision == ProposalDecision.APPROVE else REJECT_RULE
        definition, versions = await self._locked(action, rule_id, enabled)
        version = await self._require(
            action, definition, versions, number, RuleVersionStatus.IN_REVIEW
        )
        if version.authored_by == self.principal.actor:
            await self._refuse(
                action,
                rule_id,
                ApiError(
                    409,
                    RuleRefusal.SELF_APPROVAL,
                    "You cannot decide on a rule version you authored (maker-checker).",
                ),
            )
        if decision == ProposalDecision.REJECT:
            await self.repo.move(
                version.id, RuleVersionStatus.REJECTED, self.principal.actor, comment
            )
            self._audit(REJECT_RULE, definition, version, str(version.id), comment=comment)
            await self.session.commit()
            return version
        active = next((v for v in versions if v.status == RuleVersionStatus.ACTIVE.value), None)
        if (
            active is not None
            and active.approved_at is not None
            and (active.approved_at > version.created_at)
        ):
            await self._refuse(
                action,
                rule_id,
                ApiError(
                    409,
                    RuleRefusal.STALE_VERSION,
                    "Another version was activated after this one was proposed; approving it "
                    "would undo that change. Propose again from the current ACTIVE version.",
                    {"active_version": active.version},
                ),
            )
        if active is not None:  # first: one ACTIVE version per rule (partial unique index)
            await self.repo.move(active.id, RuleVersionStatus.SUPERSEDED)
        await self.repo.move(version.id, RuleVersionStatus.ACTIVE, self.principal.actor, comment)
        ref = str(version.id)
        self._audit(APPROVE_RULE, definition, version, ref, comment=comment)
        if active is not None:
            self._audit(SUPERSEDE_RULE, definition, active, ref)
        self._audit(ACTIVATE_RULE, definition, version, ref)
        await self.session.commit()
        return version
