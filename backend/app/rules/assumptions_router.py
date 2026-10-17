"""rules.assumptions_router

Read-only assumption sets (BE-16.4, D-32 §6, D-34):

- GET /assumption-sets (`rule:read`): every version in the caller's tenant.
- GET /assumption-sets/{assumption_set_id} (`rule:read`): one version (the id a run manifest
  pins) with every version of its code; 404 alike for an absent and a foreign id (D-04).

No write routes: authoring is deferred.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.rules.assumptions_schemas import (
    AssumptionSetDetailOut,
    AssumptionSetItem,
    AssumptionSetList,
    AssumptionSetVersionSummary,
)
from app.rules.assumptions_service import AssumptionSetService
from app.rules.constants import RuleVersionStatus
from app.rules.models import AssumptionSet

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RuleReader = Annotated[Principal, Depends(require_permission(Permission.RULE_READ))]

router = APIRouter(tags=["rules"])


def _item(a: AssumptionSet) -> AssumptionSetItem:
    return AssumptionSetItem(
        assumption_set_id=a.id,
        code=a.code,
        name=a.name,
        version=a.version,
        status=RuleVersionStatus(a.status),
        owner=a.owner,
        provenance=a.provenance,
        effective_from=a.effective_from,
        effective_to=a.effective_to,
        authored_by=a.authored_by,
        approved_by=a.approved_by,
        approved_at=a.approved_at,
        assumption_keys=sorted(a.assumptions),
        content_sha256=a.content_sha256,
        provisional=a.provisional,
        illustrative=a.illustrative,
        created_at=a.created_at,
    )


@router.get("/assumption-sets", response_model=AssumptionSetList, operation_id="listAssumptionSets")
async def list_assumption_sets(principal: RuleReader, session: SessionDep) -> AssumptionSetList:
    """Every assumption set version of the caller's tenant (read-only in Feature 03)."""
    sets = await AssumptionSetService(session, principal).assumption_sets()
    return AssumptionSetList(items=[_item(a) for a in sets], total=len(sets))


@router.get(
    "/assumption-sets/{assumption_set_id}",
    response_model=AssumptionSetDetailOut,
    operation_id="getAssumptionSet",
)
async def get_assumption_set(
    assumption_set_id: uuid.UUID, principal: RuleReader, session: SessionDep
) -> AssumptionSetDetailOut:
    """One assumption set version with its provenance, owner, effective dates, status and
    every version of the same code. 404 alike for an absent and a foreign id (D-04)."""
    found, versions = await AssumptionSetService(session, principal).assumption_set(
        assumption_set_id
    )
    return AssumptionSetDetailOut(
        **_item(found).model_dump(),
        versions=[
            AssumptionSetVersionSummary(
                assumption_set_id=v.id,
                version=v.version,
                status=RuleVersionStatus(v.status),
                effective_from=v.effective_from,
                effective_to=v.effective_to,
                approved_at=v.approved_at,
            )
            for v in versions
        ],
    )
