"""rules.router

The registry read of Phase 15 (BE-15.5, the Feature 03 endpoint table, D-34):

- GET /calculation-modules (`rule:read`): every module version of the caller's tenant, with
  its declared contracts and what a run submitted now would pin (the ACTIVE rule versions,
  the ACTIVE baseline assumption set and the engine version), for the "New run" panel.

Tenant scope comes from the principal (D-04). Rule changes arrive with Phase 16.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.rules.constants import ILLUSTRATIVE_STATEMENT, ImplementationStatus, RuleCategory
from app.rules.constants import RuleVersionStatus as Status
from app.rules.models import AssumptionSet, RuleVersion
from app.rules.schemas import (
    AssumptionSetPin,
    ModuleInputContract,
    ModuleOutputContract,
    ModulePage,
    ModuleRuleOut,
    ModuleVersionOut,
    RuleVersionPin,
)
from app.rules.service import ModuleView, RulesService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RuleReader = Annotated[Principal, Depends(require_permission(Permission.RULE_READ))]

router = APIRouter(tags=["rules"])


def rule_version_pin(v: RuleVersion) -> RuleVersionPin:
    return RuleVersionPin(
        rule_version_id=v.id,
        version=v.version,
        status=Status(v.status),
        parameters=v.parameters,
        parameters_sha256=v.parameters_sha256,
        effective_from=v.effective_from,
        authored_by=v.authored_by,
        approved_by=v.approved_by,
        approved_at=v.approved_at,
        provisional=v.provisional,
        illustrative=v.illustrative,
    )


def assumption_set_pin(a: AssumptionSet) -> AssumptionSetPin:
    return AssumptionSetPin(
        assumption_set_id=a.id,
        code=a.code,
        name=a.name,
        version=a.version,
        status=Status(a.status),
        owner=a.owner,
        provenance=a.provenance,
        effective_from=a.effective_from,
        effective_to=a.effective_to,
        assumptions=a.assumptions,
        content_sha256=a.content_sha256,
        provisional=a.provisional,
        illustrative=a.illustrative,
    )


def _module(view: ModuleView) -> ModuleVersionOut:
    m, v = view.module, view.version
    return ModuleVersionOut(
        module_id=m.id,
        module_version_id=v.id,
        code=m.code,
        name=m.name,
        description=v.description or m.description,
        version=v.version,
        implementation_status=ImplementationStatus(v.implementation_status),
        runnable=view.runnable,
        engine_version=v.engine_version,
        input_contract=ModuleInputContract.model_validate(v.input_contract),
        output_contract=ModuleOutputContract.model_validate(v.output_contract),
        rules=[
            ModuleRuleOut(
                rule_id=r.definition.id,
                code=r.definition.code,
                name=r.definition.name,
                category=RuleCategory(r.definition.category),
                owner=r.definition.owner,
                parameter_schema=r.definition.parameter_schema,
                description=r.definition.description,
                active_version=rule_version_pin(r.active) if r.active else None,
            )
            for r in view.rules
        ],
        assumption_set=assumption_set_pin(view.assumption_set) if view.assumption_set else None,
        provisional=v.provisional,
        illustrative=v.illustrative,
        statement=ILLUSTRATIVE_STATEMENT,
        created_at=v.created_at,
    )


@router.get(
    "/calculation-modules", response_model=ModulePage, operation_id="listCalculationModules"
)
async def list_calculation_modules(principal: RuleReader, session: SessionDep) -> ModulePage:
    """The calculation modules of the caller's tenant, AVAILABLE and PLANNED, with exactly
    the versions a run submitted now would pin. Every seeded module is an illustrative
    prototype: not a regulatory calculation, no vendor parity claimed (D-35)."""
    views = await RulesService(session, principal).catalogue()
    return ModulePage(items=[_module(v) for v in views], total=len(views))
