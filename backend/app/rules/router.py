"""rules.router

The registry read of Phase 15 (BE-15.5, the Feature 03 endpoint table, D-34):

- GET /calculation-modules (`rule:read`): every module version of the caller's tenant, with
  its declared contracts and what a run submitted now would pin (the ACTIVE rule versions,
  the ACTIVE baseline assumption set and the engine version), for the "New run" panel.

Phase 16 (BE-16.1 - BE-16.4, D-32):

- GET /rules, GET /rules/{rule_id} (`rule:read`): definitions with their versions, status,
  author, approver and the runs that pin each version.
- POST /rules/{rule_id}/versions (`rule:propose`): a DRAFT copied from the ACTIVE version plus
  a parameter patch, checked against the closed parameter kinds (422 invalid_parameters,
  value-free; 422 no_change).
- POST /rules/{rule_id}/versions/{version}/submit (`rule:propose`): DRAFT -> IN_REVIEW (a
  DAY_BUCKETS version's bucket definitions are materialised in the same transaction).
- POST /rules/{rule_id}/versions/{version}/approval (`rule:approve`): APPROVE activates and
  supersedes the previous ACTIVE version in one transaction; REJECT ends it. The author never
  decides (409 self_approval).

Every write and every refusal is audited (`rule.*`). RULE_CHANGE_ENABLED=false answers 409
rule_change_disabled to the three writes. Tenant scope comes from the principal (D-04): a
foreign id is the same 404 as a random one.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations.constants import CalculationRunStatus
from app.calculations.service import CalculationService
from app.core.auth import require_permission
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.rules.constants import (
    ILLUSTRATIVE_STATEMENT,
    ImplementationStatus,
    RuleCategory,
    validation_status_of,
)
from app.rules.constants import RuleVersionStatus as Status
from app.rules.models import AssumptionSet, RuleVersion
from app.rules.schemas import (
    AssumptionSetPin,
    ModuleInputContract,
    ModuleOutputContract,
    ModulePage,
    ModuleRuleOut,
    ModuleVersionOut,
    PinningRun,
    RuleDetail,
    RuleModuleRef,
    RulePage,
    RuleSummary,
    RuleVersionCreate,
    RuleVersionDecision,
    RuleVersionOut,
    RuleVersionPin,
    RuleVersionSubmit,
)
from app.rules.service import ModuleView, RuleRow, RulesService, author_user_id

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RuleReader = Annotated[Principal, Depends(require_permission(Permission.RULE_READ))]
RuleProposer = Annotated[Principal, Depends(require_permission(Permission.RULE_PROPOSE))]
RuleApprover = Annotated[Principal, Depends(require_permission(Permission.RULE_APPROVE))]
VersionNumber = Annotated[int, Path(ge=1, le=1_000_000)]
RECENT_RUNS = 20
DECIDED = frozenset({Status.ACTIVE, Status.SUPERSEDED, Status.RETIRED, Status.REJECTED})
ACTIVATED = frozenset({Status.ACTIVE, Status.SUPERSEDED, Status.RETIRED})

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
        validation_status=validation_status_of(m.code, v.version),
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


# --- Phase 16: rules and governed changes (BE-16.1, BE-16.2) ----------------------------------


def _summary(row: RuleRow) -> RuleSummary:
    d = row.definition
    active = next((v for v in row.versions if v.status == Status.ACTIVE.value), None)
    return RuleSummary(
        rule_id=d.id,
        code=d.code,
        name=d.name,
        category=RuleCategory(d.category),
        owner=d.owner,
        description=d.description,
        target_module=RuleModuleRef(
            module_id=row.module.id, code=row.module.code, name=row.module.name
        ),
        parameter_schema=d.parameter_schema,
        active_version=active.version if active else None,
        active_rule_version_id=active.id if active else None,
        version_count=len(row.versions),
        provisional=d.provisional,
        illustrative=d.illustrative,
    )


def _version_out(
    row: RuleRow,
    v: RuleVersion,
    pinning: Mapping[uuid.UUID, tuple[int, Sequence[PinningRun]]],
) -> RuleVersionOut:
    status = Status(v.status)
    count, runs = pinning.get(v.id, (0, []))
    return RuleVersionOut(
        rule_version_id=v.id,
        rule_id=row.definition.id,
        rule_code=row.definition.code,
        version=v.version,
        status=status,
        parameters=v.parameters,
        parameters_sha256=v.parameters_sha256,
        change_summary=v.change_summary,
        authored_by=v.authored_by,
        author_user_id=author_user_id(v.authored_by),
        approved_by=v.approved_by if status in ACTIVATED else None,
        approved_at=v.approved_at if status in ACTIVATED else None,
        decided_by=v.approved_by if status in DECIDED else None,
        decided_at=v.approved_at if status in DECIDED else None,
        decision_comment=v.decision_comment,
        superseded_at=v.updated_at if status == Status.SUPERSEDED else None,
        effective_from=v.effective_from,
        effective_to=v.effective_to,
        provisional=v.provisional,
        illustrative=v.illustrative,
        seeded=v.seeded_by is not None,
        created_at=v.created_at,
        run_count=count,
        runs=list(runs),
    )


async def _pinning(
    session: AsyncSession,
    principal: Principal,
    row: RuleRow,
    rule_sets: Mapping[uuid.UUID, list[uuid.UUID]],
) -> dict[uuid.UUID, tuple[int, list[PinningRun]]]:
    """Per version: how many runs pin it, and the newest of them."""
    calculations = CalculationService(session, principal)
    found: dict[uuid.UUID, tuple[int, list[PinningRun]]] = {}
    for v in row.versions:
        sets = rule_sets.get(v.id, [])
        if not sets:
            continue
        total, runs = await calculations.runs_in_rule_sets(sets, RECENT_RUNS)
        found[v.id] = (
            total,
            [
                PinningRun(
                    run_id=r.id,
                    status=CalculationRunStatus(r.status),
                    reporting_date=r.reporting_date,
                    rerun_of_run_id=r.rerun_of_run_id,
                    started_at=r.started_at,
                )
                for r in runs
            ],
        )
    return found


async def _detail(
    request: Request, session: AsyncSession, principal: Principal, row: RuleRow
) -> RuleDetail:
    service = RulesService(session, principal)
    rule_sets = await service.rule_sets_of([v.id for v in row.versions])
    pinning = await _pinning(session, principal, row, rule_sets)
    return RuleDetail(
        **_summary(row).model_dump(),
        versions=[_version_out(row, v, pinning) for v in row.versions],
        vocabulary=await service.vocabulary(row.definition),
        change_enabled=request.app.state.settings.RULE_CHANGE_ENABLED,
        statement=ILLUSTRATIVE_STATEMENT,
    )


async def _version_detail(
    session: AsyncSession, principal: Principal, rule_id: uuid.UUID, number: int
) -> RuleVersionOut:
    service = RulesService(session, principal)
    row = await service.rule(rule_id)
    [version] = [v for v in row.versions if v.version == number]
    rule_sets = await service.rule_sets_of([version.id])
    pinning = await _pinning(session, principal, row, rule_sets)
    return _version_out(row, version, pinning)


@router.get("/rules", response_model=RulePage, operation_id="listRules")
async def list_rules(principal: RuleReader, session: SessionDep) -> RulePage:
    """The rule definitions of the caller's tenant, by code, with their ACTIVE version. Every
    seeded rule is an illustrative prototype (D-35)."""
    rows = await RulesService(session, principal).rules()
    return RulePage(items=[_summary(r) for r in rows], total=len(rows))


@router.get("/rules/{rule_id}", response_model=RuleDetail, operation_id="getRule")
async def get_rule(
    request: Request, rule_id: uuid.UUID, principal: RuleReader, session: SessionDep
) -> RuleDetail:
    """One rule with every version (status, author, approver, parameters) and the runs that
    pin each version. 404 alike for an absent and a foreign id (D-04)."""
    row = await RulesService(session, principal).rule(rule_id)
    return await _detail(request, session, principal, row)


@router.post(
    "/rules/{rule_id}/versions",
    response_model=RuleVersionOut,
    status_code=201,
    operation_id="createRuleVersion",
)
async def create_rule_version(
    request: Request,
    rule_id: uuid.UUID,
    body: RuleVersionCreate,
    principal: RuleProposer,
    session: SessionDep,
) -> RuleVersionOut:
    """A new DRAFT version: the ACTIVE version's parameters with `parameters_patch` applied,
    checked against the rule's parameter kind (D-32 §1). Refusals, each audited DENIED
    (`rule.propose`): 404 `not_found`, 409 `rule_change_disabled`, 409 `no_active_version`,
    409 `invalid_parameter_schema`, 422 `invalid_parameters` (value-free problems), 422
    `no_change`."""
    enabled = request.app.state.settings.RULE_CHANGE_ENABLED
    version = await RulesService(session, principal).propose(
        rule_id, body.parameters_patch, body.change_summary, enabled
    )
    return await _version_detail(session, principal, rule_id, version.version)


@router.post(
    "/rules/{rule_id}/versions/{version}/submit",
    response_model=RuleVersionOut,
    operation_id="submitRuleVersion",
)
async def submit_rule_version(
    request: Request,
    rule_id: uuid.UUID,
    version: VersionNumber,
    body: RuleVersionSubmit,
    principal: RuleProposer,
    session: SessionDep,
) -> RuleVersionOut:
    """DRAFT -> IN_REVIEW; a DAY_BUCKETS version's bucket definitions are materialised in the
    same transaction (D-32 §5). Only the version's author submits it. Refusals, audited
    (`rule.submit`): 404 `not_found`, 409 `rule_change_disabled`, 409 `invalid_status`, 409
    `not_author`, 422 `invalid_parameters`."""
    enabled = request.app.state.settings.RULE_CHANGE_ENABLED
    await RulesService(session, principal).submit(rule_id, version, body.comment, enabled)
    return await _version_detail(session, principal, rule_id, version)


@router.post(
    "/rules/{rule_id}/versions/{version}/approval",
    response_model=RuleVersionOut,
    operation_id="decideRuleVersion",
)
async def decide_rule_version(
    request: Request,
    rule_id: uuid.UUID,
    version: VersionNumber,
    body: RuleVersionDecision,
    principal: RuleApprover,
    session: SessionDep,
) -> RuleVersionOut:
    """Maker-checker (D-32 §3). APPROVE makes an IN_REVIEW version ACTIVE and the previous
    ACTIVE version SUPERSEDED, in one transaction (one ACTIVE version per rule); REJECT ends
    it REJECTED. Refusals, audited (`rule.approve` / `rule.reject`): 404 `not_found`, 409
    `rule_change_disabled`, 409 `invalid_status` (e.g. a DRAFT that was never submitted), 409
    `self_approval` (the author), 409 `stale_version` (another version was activated after
    this one was proposed: propose again from the current ACTIVE version)."""
    enabled = request.app.state.settings.RULE_CHANGE_ENABLED
    await RulesService(session, principal).decide(
        rule_id, version, body.decision, body.comment, enabled
    )
    return await _version_detail(session, principal, rule_id, version)
