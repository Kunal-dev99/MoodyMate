"""rules.service

Registry reads and pinning (BE-15.1, BE-15.4, BE-15.5, D-31 §4, D-32).

- `catalogue()`: every module version of the tenant with the rules it reads (each with its
  ACTIVE version), the ACTIVE baseline assumption set, and whether it can run now.
- `module_view(id)`: the same for one module version (None when absent or foreign, D-04).
- `rule_set_for(versions)`: the content-addressed rule set of these rule versions, keyed by
  the hash of their sorted ids (D-32 §4): an existing set is reused, a new one is written
  once with its members (in the caller's transaction).
- `bucket_definitions(rule_version_id)`: the materialised buckets of a DAY_BUCKETS version.

Rule version changes (propose, submit, approve) arrive with Phase 16.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.modules import registered
from app.core.principal import Principal
from app.rules.constants import BASELINE_ASSUMPTION_SET, ImplementationStatus
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
