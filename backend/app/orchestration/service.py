"""orchestration.service

Business rules, orchestration, transactions and event emission; calls repositories only.

Phase 24 (BE-24.2 - BE-24.4): stress scenarios (D-44 §1-9, D-47 §3).

- `templates`: the three v1 templates with their bounds, and the instrument types of the
  ACTIVE `cashflow_direction` rule each instrument-type field accepts.
- Writes, each audited `scenario.*` with the shocks' sha256 and never their text:
  - `create` (`scenario.create`): a scenario and its first DRAFT version, origin MANUAL;
  - `new_version` (`scenario.version`): a new DRAFT, optionally adapting a parent version;
  - `edit` (`scenario.edit`): replace a DRAFT's shocks, the author only;
  - `confirm` (`scenario.confirm`): DRAFT -> CONFIRMED_FOR_EXPLORATION by the author alone
    (exploration only, "Exploration, not approved");
  - `submit` (`scenario.submit`): DRAFT or CONFIRMED_FOR_EXPLORATION -> IN_REVIEW, the
    author only;
  - `decide` (`scenario.approve` / `scenario.reject`): IN_REVIEW -> APPROVED_FOR_TEST or
    REJECTED, by an approver who is not the author (maker-checker);
  - `close` (`scenario.close`): any status but DRAFT -> CLOSED; the author closes a
    non-approved version (scenario:propose), an approver an approved one (scenario:approve).
  When a version leaves DRAFT its shocks are materialised as `scenario_change` rows.
- Every refusal is audited DENIED with its code and nothing else is written: 404 not_found
  (absent and foreign alike, D-04), 409 scenarios_disabled (SCENARIOS_ENABLED off),
  duplicate_code, not_author, self_approval, invalid_status, 403 forbidden (close), 422
  invalid_shock (value-free details), ai_review_required, unacknowledged_defaults.
- Reads: the list defaults to the shared library (APPROVED_FOR_TEST only, never an
  exploration version); a scenario by id shows every version, CLOSED and REJECTED included.
  Every response carries `labels.statement` (D-44 §8).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NoReturn

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.calculations import scenario_overlay
from app.calculations.constants import AGGREGATE_CLASSIFICATION, CalculationRunStatus
from app.calculations.service import (
    SUCCEEDED as RUN_SUCCEEDED,
)
from app.calculations.service import (
    CalculationService,
    LadderView,
    ScenarioMember,
    ScenarioRunSpec,
    most_sensitive,
)
from app.core.classification import UNCLASSIFIED, MaskedValue
from app.core.correlation import get_correlation_id
from app.core.decimals import decimal_text
from app.core.errors import ApiError
from app.core.principal import Principal
from app.domain.service import DomainService
from app.governance.constants import ClassificationCode, Permission, ProposalDecision
from app.governance.service import audit_by, record_denial
from app.orchestration.constants import (
    APPROVAL_LABELS,
    AUDIT_RUN_RESOURCE,
    DIRECTION_RULE,
    MAX_LARGEST_CHANGES,
    MAX_SHOCKS,
    SCENARIO_STATEMENT,
    ApprovalLabel,
    ApprovalState,
    FlowDirection,
    FlowOrigin,
    ParameterKind,
    PartChange,
    ScenarioAction,
    ScenarioOrigin,
    ScenarioRefusal,
    ScenarioRunAction,
    ScenarioRunRefusal,
    ScenarioTemplate,
    ScenarioVersionStatus,
    ShockProblem,
)
from app.orchestration.models import Scenario, ScenarioChange, ScenarioRun, ScenarioVersion
from app.orchestration.repository import ScenarioRepository
from app.orchestration.scenario_templates import (
    TEMPLATES,
    FieldSpec,
    Problem,
    instrument_types,
    proposed_defaults,
    shocks_sha256,
    validate_shocks,
)
from app.orchestration.schemas import (
    AiAcknowledgement,
    ComparisonBucket,
    ComparisonCell,
    ComparisonCurrency,
    ComparisonFigure,
    ComparisonRow,
    ComparisonSummary,
    ContributionSourceLineage,
    FirstNegativeCumulative,
    LargestChange,
    ScenarioComparisonOut,
    ScenarioContributionOut,
    ScenarioContributionPage,
    ScenarioCreate,
    ScenarioDetail,
    ScenarioLabels,
    ScenarioPage,
    ScenarioRunCreate,
    ScenarioRunMember,
    ScenarioRunOut,
    ScenarioRunPage,
    ScenarioSummary,
    ScenarioValidationOut,
    ScenarioVersionClose,
    ScenarioVersionConfirm,
    ScenarioVersionCreate,
    ScenarioVersionDecision,
    ScenarioVersionOut,
    ScenarioVersionSubmit,
    ScenarioVersionUpdate,
    ShockMatchCount,
    TemplateField,
    TemplateOut,
    TemplatePage,
)
from app.rules.service import RulesService, canonical_sha256

S = ScenarioVersionStatus
R = ScenarioRefusal
RR = ScenarioRunRefusal
SCENARIO = "scenario"
VERSION = "scenario_version"
NOT_FOUND = "Scenario not found."
RUN_NOT_FOUND = "Scenario run not found."
# Only these may leave for CLOSED (D-44 §5: any status other than DRAFT; RUNNING and TESTED
# are reserved).
CLOSABLE = frozenset({S.CONFIRMED_FOR_EXPLORATION, S.IN_REVIEW, S.APPROVED_FOR_TEST, S.REJECTED})
SHARED_LIBRARY = (S.APPROVED_FOR_TEST,)
LABELS = ScenarioLabels(statement=SCENARIO_STATEMENT)
_BOUNDED = frozenset({ParameterKind.DECIMAL, ParameterKind.INTEGER})


def approval_label(status: str) -> ApprovalLabel | None:
    """D-44 §8: what a runnable version (and its runs) is labelled with."""
    if status == S.CONFIRMED_FOR_EXPLORATION:
        return ApprovalLabel.EXPLORATION
    if status == S.APPROVED_FOR_TEST:
        return ApprovalLabel.APPROVED
    return None


def _template_field(spec: FieldSpec, directions: Mapping[str, str]) -> TemplateField:
    bounded = spec.kind in _BOUNDED
    values = list(spec.values)
    if spec.kind == ParameterKind.INSTRUMENT_TYPE and spec.direction is not None:
        values = instrument_types(directions, spec.direction)
    return TemplateField(
        name=spec.name,
        kind=spec.kind,
        required=spec.required,
        min=spec.min if bounded else None,
        min_exclusive=spec.min_exclusive,
        max=spec.max if bounded else None,
        scale=spec.scale,
        max_items=spec.max_items,
        values=values,
        unit=spec.unit,
        description=spec.description,
    )


class ScenarioService:
    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = ScenarioRepository(session, principal.scope)

    # --- templates and validation (BE-24.2) ----------------------------------------------

    async def _directions(self) -> dict[str, str]:
        """The ACTIVE cashflow_direction map (instrument type -> INFLOW/OUTFLOW); empty
        without an ACTIVE version, so every instrument type is then unknown."""
        rules = RulesService(self.session, self.principal)
        found = (await rules.active_parameters(DIRECTION_RULE) or {}).get("map")
        if not isinstance(found, Mapping):
            return {}
        return {str(k): str(v) for k, v in found.items()}

    async def templates(self) -> TemplatePage:
        directions = await self._directions()
        return TemplatePage(
            items=[
                TemplateOut(
                    template=spec.template,
                    name=spec.name,
                    description=spec.description,
                    direction=spec.direction,
                    changes_amounts=spec.changes_amounts,
                    parameters=[_template_field(f, directions) for f in spec.parameters],
                    selector=[_template_field(f, directions) for f in spec.selector],
                )
                for spec in TEMPLATES.values()
            ],
            max_shocks=MAX_SHOCKS,
            labels=LABELS,
        )

    async def _checked(
        self, action: str, resource: tuple[str, uuid.UUID | None], raw: Any
    ) -> list[dict[str, Any]]:
        """The normalised shocks, or 422 invalid_shock (audited) with value-free details."""
        shocks, problems = validate_shocks(raw, await self._directions())
        if not problems:
            problems = await self._unknown_instruments(shocks)
        if problems:
            await self._refuse(
                action,
                resource,
                ApiError(
                    422,
                    R.INVALID_SHOCK,
                    "The shocks do not fit the templates; see details for the problems.",
                    {"problems": [p.as_detail() for p in problems]},
                ),
            )
        return shocks

    async def _unknown_instruments(self, shocks: list[dict[str, Any]]) -> list[Problem]:
        """D-33: every picked instrument key must be an instrument of the tenant with a version
        of the selector's instrument_type that the author is cleared to see. Absent and hidden
        keys answer the same value-free code, so existence is not revealed."""
        clear = self.principal.clear_classifications
        domain = DomainService(self.session, self.principal)
        problems: list[Problem] = []
        for index, shock in enumerate(shocks):
            selector = shock.get("selector") or {}
            keys = selector.get("instrument_keys")
            if not keys:
                continue
            classes = await domain.instrument_key_classes(keys, selector.get("instrument_type"))
            if any(classes.get(k) not in clear for k in keys):
                problems.append(
                    Problem(index, "selector.instrument_keys", ShockProblem.UNKNOWN_INSTRUMENT)
                )
        return problems

    async def _masked(self, shocks: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
        """D-33: the shock lists with each picked instrument key the caller is not cleared for
        masked (DomainService.mask_instrument_keys, shared with the run-detail manifest)."""
        return await DomainService(self.session, self.principal).mask_instrument_keys(shocks)

    # --- refusals, audit and guards ------------------------------------------------------

    async def _refuse(
        self, action: str, resource: tuple[str, uuid.UUID | None], error: ApiError
    ) -> NoReturn:
        """Roll back, audit the refusal (DENIED, reason = the error code) and raise it."""
        kind, resource_id = resource
        await record_denial(
            self.session,
            self.principal,
            action,
            kind,
            str(resource_id) if resource_id else None,
            error.code,
        )
        raise error

    @staticmethod
    def _disabled() -> ApiError:
        return ApiError(
            409,
            R.SCENARIOS_DISABLED,
            "Scenario changes are switched off in this environment; scenarios stay readable.",
        )

    def _audit(
        self,
        action: str,
        version: ScenarioVersion,
        approval_ref: str | None = None,
        **extra: Any,
    ) -> None:
        """A scenario step, value-free: ids, version, status, the shocks' sha256, count and
        templates (never their parameters, never a comment)."""
        details: dict[str, Any] = {
            "scenario_id": str(version.scenario_id),
            "scenario_version_id": str(version.id),
            "version": version.version,
            "origin": version.origin,
            "shocks_sha256": version.shocks_sha256,
            "shock_count": len(version.shocks),
            "templates": ",".join(s["template"] for s in version.shocks),
            **extra,
        }
        audit_by(
            self.session,
            self.principal,
            action,
            VERSION,
            resource_id=str(version.id),
            approval_ref=approval_ref,
            details=details,
            details_classification=dict.fromkeys(details, ClassificationCode.PUBLIC.value),
        )

    async def _locked(self, action: str, scenario_id: uuid.UUID, number: int) -> ScenarioVersion:
        """The version, row-locked for this transaction; 404 alike for absent and foreign."""
        version = await self.repo.version(scenario_id, number, for_update=True)
        if version is None:
            await self._refuse(
                action, (SCENARIO, scenario_id), ApiError(404, R.NOT_FOUND, NOT_FOUND)
            )
        return version

    async def _require_author(self, action: str, version: ScenarioVersion) -> None:
        if version.author_user_id != self.principal.user_id:
            await self._refuse(
                action,
                (VERSION, version.id),
                ApiError(
                    409,
                    R.NOT_AUTHOR,
                    "Only the author of this version may change, confirm, submit or close it.",
                ),
            )

    async def _require_status(
        self, action: str, version: ScenarioVersion, allowed: frozenset[S], what: str
    ) -> None:
        if version.status not in allowed:
            await self._refuse(
                action,
                (VERSION, version.id),
                ApiError(
                    409,
                    R.INVALID_STATUS,
                    f"The version is {version.status}; {what}.",
                    {"status": version.status},
                ),
            )

    async def _acknowledged(
        self, action: str, version: ScenarioVersion, body: AiAcknowledgement
    ) -> tuple[bool, list[str]]:
        """D-44 §6: an AI_PROPOSED version needs the AI origin and every PROPOSED_DEFAULT
        acknowledged (422 otherwise). A MANUAL version records no acknowledgement."""
        if version.origin != ScenarioOrigin.AI_PROPOSED:
            return False, []
        if not body.acknowledge_ai_origin:
            await self._refuse(
                action,
                (VERSION, version.id),
                ApiError(
                    422,
                    R.AI_REVIEW_REQUIRED,
                    "This version was proposed by the AI: acknowledge its origin after "
                    "reviewing it.",
                ),
            )
        missing = sorted(
            set(proposed_defaults(version.parameter_sources)) - set(body.acknowledged_defaults)
        )
        if missing:
            await self._refuse(
                action,
                (VERSION, version.id),
                ApiError(
                    422,
                    R.UNACKNOWLEDGED_DEFAULTS,
                    "Every parameter the AI filled with a proposed default must be acknowledged.",
                    {"missing": missing},  # parameter paths: structural, value-free
                ),
            )
        return True, sorted(set(body.acknowledged_defaults))

    async def _materialise(self, version: ScenarioVersion) -> None:
        """One scenario_change row per shock, as the version leaves DRAFT (D-44 §5)."""
        await self.repo.insert_changes(
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": self.principal.tenant_id,
                    "scenario_version_id": version.id,
                    "shock_index": index,
                    "template": shock["template"],
                    "shock": shock,
                    "created_by": self.principal.actor,
                }
                for index, shock in enumerate(version.shocks)
            ]
        )

    def _new_version(
        self,
        scenario_id: uuid.UUID,
        number: int,
        shocks: list[dict[str, Any]],
        change_summary: str | None,
        parent_version_id: uuid.UUID | None = None,
    ) -> ScenarioVersion:
        return ScenarioVersion(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            scenario_id=scenario_id,
            version=number,
            status=S.DRAFT.value,
            origin=ScenarioOrigin.MANUAL.value,
            parent_version_id=parent_version_id,
            parameter_sources=None,
            shocks=shocks,
            shocks_sha256=shocks_sha256(shocks),
            change_summary=change_summary,
            authored_by=self.principal.actor,
            author_user_id=self.principal.user_id,
            ai_acknowledged=False,
            acknowledged_defaults=[],
            created_by=self.principal.actor,
        )

    # --- writes (BE-24.3) ----------------------------------------------------------------

    async def create(self, body: ScenarioCreate, enabled: bool) -> ScenarioDetail:
        action = ScenarioAction.CREATE.value
        resource: tuple[str, uuid.UUID | None] = (SCENARIO, None)
        if not enabled:
            await self._refuse(action, resource, self._disabled())
        shocks = await self._checked(action, resource, body.shocks)
        await self.repo.lock("scenario_code")  # one code assignment at a time per tenant
        code = body.code
        if code is None:
            code = f"SCN_{uuid.uuid4().hex[:12].upper()}"
            while await self.repo.code_taken(code):
                code = f"SCN_{uuid.uuid4().hex[:12].upper()}"
        elif await self.repo.code_taken(code):
            await self._refuse(
                action,
                resource,
                ApiError(409, R.DUPLICATE_CODE, "A scenario with this code already exists."),
            )
        scenario = Scenario(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            code=code,
            name=body.name,
            description=body.description,
            author_user_id=self.principal.user_id,
            created_by=self.principal.actor,
        )
        self.repo.add(scenario)
        await self.repo.flush()
        version = self._new_version(scenario.id, 1, shocks, body.change_summary)
        self.repo.add(version)
        await self.repo.flush()
        self._audit(action, version, code=code)
        await self.session.commit()
        return await self.detail(scenario.id, enabled)

    async def new_version(
        self, scenario_id: uuid.UUID, body: ScenarioVersionCreate, enabled: bool
    ) -> ScenarioVersionOut:
        action = ScenarioAction.VERSION.value
        resource: tuple[str, uuid.UUID | None] = (SCENARIO, scenario_id)
        if not enabled:
            await self._refuse(action, resource, self._disabled())
        if await self.repo.scenario(scenario_id) is None:
            await self._refuse(action, resource, ApiError(404, R.NOT_FOUND, NOT_FOUND))
        shocks = await self._checked(action, resource, body.shocks)
        await self.repo.lock(f"scenario:{scenario_id}")  # one version number at a time
        parent_id = None
        if body.parent_version is not None:
            parent = await self.repo.version(scenario_id, body.parent_version)
            if parent is None:
                await self._refuse(
                    action, resource, ApiError(404, R.NOT_FOUND, "Parent version not found.")
                )
            parent_id = parent.id
        number = await self.repo.last_version(scenario_id) + 1
        version = self._new_version(scenario_id, number, shocks, body.change_summary, parent_id)
        self.repo.add(version)
        await self.repo.flush()
        self._audit(action, version, parent_version=body.parent_version)
        await self.session.commit()
        return await self._version_view(scenario_id, number)

    async def edit(
        self, scenario_id: uuid.UUID, version: int, body: ScenarioVersionUpdate, enabled: bool
    ) -> ScenarioVersionOut:
        action = ScenarioAction.EDIT.value
        if not enabled:
            await self._refuse(action, (SCENARIO, scenario_id), self._disabled())
        row = await self._locked(action, scenario_id, version)
        await self._require_author(action, row)
        await self._require_status(action, row, frozenset({S.DRAFT}), "only a DRAFT is edited")
        shocks = await self._checked(action, (VERSION, row.id), body.shocks)
        summary = body.change_summary if body.change_summary is not None else row.change_summary
        sha = shocks_sha256(shocks)
        await self.repo.update_version(
            row.id, {"shocks": shocks, "shocks_sha256": sha, "change_summary": summary}
        )
        row.shocks, row.shocks_sha256 = shocks, sha
        self._audit(action, row)
        await self.session.commit()
        return await self._version_view(scenario_id, version)

    async def confirm(
        self, scenario_id: uuid.UUID, version: int, body: ScenarioVersionConfirm, enabled: bool
    ) -> ScenarioVersionOut:
        """DRAFT -> CONFIRMED_FOR_EXPLORATION by the author alone (D-47 §3)."""
        action = ScenarioAction.CONFIRM.value
        if not enabled:
            await self._refuse(action, (SCENARIO, scenario_id), self._disabled())
        row = await self._locked(action, scenario_id, version)
        await self._require_author(action, row)
        await self._require_status(
            action, row, frozenset({S.DRAFT}), "only a DRAFT is confirmed for exploration"
        )
        acknowledged, defaults = await self._acknowledged(action, row, body)
        await self.repo.update_version(
            row.id,
            {
                "status": S.CONFIRMED_FOR_EXPLORATION.value,
                "confirmed_at": func.clock_timestamp(),
                "ai_acknowledged": acknowledged,
                "acknowledged_defaults": defaults,
            },
        )
        await self._materialise(row)
        self._audit(action, row, status=S.CONFIRMED_FOR_EXPLORATION.value)
        await self.session.commit()
        return await self._version_view(scenario_id, version)

    async def submit(
        self, scenario_id: uuid.UUID, version: int, body: ScenarioVersionSubmit, enabled: bool
    ) -> ScenarioVersionOut:
        """DRAFT or CONFIRMED_FOR_EXPLORATION -> IN_REVIEW (promotion), by the author."""
        action = ScenarioAction.SUBMIT.value
        if not enabled:
            await self._refuse(action, (SCENARIO, scenario_id), self._disabled())
        row = await self._locked(action, scenario_id, version)
        await self._require_author(action, row)
        await self._require_status(
            action,
            row,
            frozenset({S.DRAFT, S.CONFIRMED_FOR_EXPLORATION}),
            "only a DRAFT or CONFIRMED_FOR_EXPLORATION version is submitted",
        )
        acknowledged, defaults = await self._acknowledged(action, row, body)
        was_draft = row.status == S.DRAFT
        await self.repo.update_version(
            row.id,
            {
                "status": S.IN_REVIEW.value,
                "submitted_by": self.principal.actor,
                "submitted_at": func.clock_timestamp(),
                "submit_comment": body.comment,
                "ai_acknowledged": acknowledged,
                "acknowledged_defaults": defaults,
            },
        )
        if was_draft:
            await self._materialise(row)
        self._audit(action, row, status=S.IN_REVIEW.value)
        await self.session.commit()
        return await self._version_view(scenario_id, version)

    async def decide(
        self, scenario_id: uuid.UUID, version: int, body: ScenarioVersionDecision, enabled: bool
    ) -> ScenarioVersionOut:
        """Maker-checker promotion (D-44 §6): IN_REVIEW -> APPROVED_FOR_TEST or REJECTED, by
        an approver who is not the author."""
        approve = body.decision == ProposalDecision.APPROVE
        action = (ScenarioAction.APPROVE if approve else ScenarioAction.REJECT).value
        if not enabled:
            await self._refuse(action, (SCENARIO, scenario_id), self._disabled())
        row = await self._locked(action, scenario_id, version)
        await self._require_status(
            action, row, frozenset({S.IN_REVIEW}), "only an IN_REVIEW version is decided"
        )
        if row.author_user_id == self.principal.user_id:
            await self._refuse(
                action,
                (VERSION, row.id),
                ApiError(
                    409,
                    R.SELF_APPROVAL,
                    "You cannot decide on a scenario version you authored (maker-checker).",
                ),
            )
        status = S.APPROVED_FOR_TEST if approve else S.REJECTED
        await self.repo.update_version(
            row.id,
            {
                "status": status.value,
                "decided_by": self.principal.actor,
                "decided_at": func.clock_timestamp(),
                "decision_comment": body.comment,
            },
        )
        self._audit(action, row, str(row.id), status=status.value)
        await self.session.commit()
        return await self._version_view(scenario_id, version)

    async def close(
        self, scenario_id: uuid.UUID, version: int, body: ScenarioVersionClose, enabled: bool
    ) -> ScenarioVersionOut:
        """Any status but DRAFT -> CLOSED (D-44 §5, D-47 §1): the author closes a
        non-approved version (scenario:propose); an approver closes an approved one
        (scenario:approve). Hidden from pickers, still readable by id."""
        action = ScenarioAction.CLOSE.value
        if not enabled:
            await self._refuse(action, (SCENARIO, scenario_id), self._disabled())
        row = await self._locked(action, scenario_id, version)
        await self._require_status(
            action, row, CLOSABLE, "only a version that has left DRAFT and is open is closed"
        )
        needed = (
            Permission.SCENARIO_APPROVE
            if row.status == S.APPROVED_FOR_TEST
            else Permission.SCENARIO_PROPOSE
        )
        if needed.value not in self.principal.permissions:
            await self._refuse(
                action,
                (VERSION, row.id),
                ApiError(
                    403,
                    R.FORBIDDEN,
                    "You do not have permission for this action.",
                    {"required_permission": needed.value},
                ),
            )
        if needed == Permission.SCENARIO_PROPOSE:
            await self._require_author(action, row)
        await self.repo.update_version(
            row.id,
            {
                "status": S.CLOSED.value,
                "closed_by": self.principal.actor,
                "closed_at": func.clock_timestamp(),
                "close_comment": body.comment,
            },
        )
        self._audit(action, row, status=S.CLOSED.value, closed_from=row.status)
        await self.session.commit()
        return await self._version_view(scenario_id, version)

    # --- reads (BE-24.4) -----------------------------------------------------------------

    async def _outs(self, versions: Sequence[ScenarioVersion]) -> list[ScenarioVersionOut]:
        """Versions as returned, with their parent's number and materialised changes."""
        numbers = {v.id: v.version for v in versions}
        changes: dict[uuid.UUID, list[ScenarioChange]] = {}
        for change in await self.repo.changes([v.id for v in versions]):
            changes.setdefault(change.scenario_version_id, []).append(change)
        # Instrument keys are masked by the caller's clearance in shocks and changes alike.
        shown = await self._masked(
            [list(v.shocks) for v in versions]
            + [[c.shock for c in changes.get(v.id, [])] for v in versions]
        )
        shown_shocks = dict(zip([v.id for v in versions], shown[: len(versions)], strict=True))
        shown_changes = dict(zip([v.id for v in versions], shown[len(versions) :], strict=True))
        return [
            ScenarioVersionOut.model_validate(
                {
                    "scenario_version_id": v.id,
                    "scenario_id": v.scenario_id,
                    "version": v.version,
                    "status": v.status,
                    "origin": v.origin,
                    "parent_version": numbers.get(v.parent_version_id)
                    if v.parent_version_id
                    else None,
                    "parent_version_id": v.parent_version_id,
                    "parameter_sources": v.parameter_sources,
                    "shocks": shown_shocks[v.id],
                    "shocks_sha256": v.shocks_sha256,
                    "change_summary": v.change_summary,
                    "changes": [
                        {"shock_index": c.shock_index, "template": c.template, "shock": shock}
                        for c, shock in zip(changes.get(v.id, []), shown_changes[v.id], strict=True)
                    ],
                    "authored_by": v.authored_by,
                    "author_user_id": v.author_user_id,
                    "created_at": v.created_at,
                    "updated_at": v.updated_at,
                    "confirmed_at": v.confirmed_at,
                    "submitted_by": v.submitted_by,
                    "submitted_at": v.submitted_at,
                    "submit_comment": v.submit_comment,
                    "decided_by": v.decided_by,
                    "decided_at": v.decided_at,
                    "decision_comment": v.decision_comment,
                    "closed_by": v.closed_by,
                    "closed_at": v.closed_at,
                    "close_comment": v.close_comment,
                    "ai_acknowledged": v.ai_acknowledged,
                    "acknowledged_defaults": list(v.acknowledged_defaults),
                    "runnable": approval_label(v.status) is not None,
                    "approval_label": approval_label(v.status),
                    "labels": LABELS,
                }
            )
            for v in versions
        ]

    async def _version_view(self, scenario_id: uuid.UUID, number: int) -> ScenarioVersionOut:
        versions = await self.repo.versions([scenario_id])
        [out] = [o for o in await self._outs(versions) if o.version == number]
        return out

    async def list(
        self,
        status: list[ScenarioVersionStatus] | None,
        origin: ScenarioOrigin | None,
        mine: bool,
        limit: int,
        offset: int,
    ) -> ScenarioPage:
        """Scenarios with a version matching the filter; by default the shared library
        (APPROVED_FOR_TEST only: never an exploration version, never a CLOSED one)."""
        statuses = tuple(status) if status else SHARED_LIBRARY
        author = self.principal.user_id if mine else None
        scenarios, total = await self.repo.scenario_page(statuses, origin, author, limit, offset)
        by_scenario: dict[uuid.UUID, list[ScenarioVersion]] = {}
        for v in await self.repo.versions([s.id for s in scenarios]):
            by_scenario.setdefault(v.scenario_id, []).append(v)

        def matches(v: ScenarioVersion) -> bool:
            return (
                v.status in statuses
                and (origin is None or v.origin == origin)
                and (author is None or v.author_user_id == author)
            )

        items = []
        for s in scenarios:
            versions = by_scenario.get(s.id, [])
            latest = versions[-1]
            approved = [v.version for v in versions if v.status == S.APPROVED_FOR_TEST]
            items.append(
                ScenarioSummary(
                    scenario_id=s.id,
                    code=s.code,
                    name=s.name,
                    description=s.description,
                    created_by=s.created_by,
                    created_at=s.created_at,
                    latest_version=latest.version,
                    latest_status=S(latest.status),
                    matching_versions=[v.version for v in versions if matches(v)],
                    approved_version=approved[-1] if approved else None,
                )
            )
        return ScenarioPage(items=items, total=total, limit=limit, offset=offset, labels=LABELS)

    async def detail(self, scenario_id: uuid.UUID, enabled: bool) -> ScenarioDetail:
        """One scenario with every version; 404 alike for an absent and a foreign id (D-04)."""
        scenario = await self.repo.scenario(scenario_id)
        if scenario is None:
            raise ApiError(404, R.NOT_FOUND, NOT_FOUND)
        versions = await self._outs(await self.repo.versions([scenario.id]))
        return ScenarioDetail(
            scenario_id=scenario.id,
            code=scenario.code,
            name=scenario.name,
            description=scenario.description,
            created_by=scenario.created_by,
            created_at=scenario.created_at,
            versions=versions,
            scenarios_enabled=enabled,
            labels=LABELS,
        )


class ScenarioRunService:
    """Phase 25 (BE-25.3, BE-25.4; D-45, D-46): run a version against a baseline
    maturity_ladder run through the calculations service, and compare baseline and stress.

    - `validation`: the dry run. Each shock alone on the baseline's pinned inputs, with
      value-free match counts, and the refusal codes a run would answer now. Nothing is
      written. 404 alike for an absent or foreign scenario, version or baseline (D-04).
    - `run` (`scenario.run`, and `calculation_run.create` per member run): refused 409,
      audited DENIED with nothing written: `scenarios_disabled`, `calculations_disabled`,
      `idempotency_conflict`, `scenario_not_runnable`, `invalid_baseline`,
      `baseline_not_reproducible`, `no_matching_cashflows` (404 `not_found` first for the
      scenario, version or baseline). Otherwise one append-only `scenario_run` and k + 1 member
      runs for k > 1 shocks (one for k = 1), each a synchronous stressed run. A replay of the
      key returns the same `scenario_run` (and completes any member run a crash left out).
    - `page`, `comparison`, `contributions`: reads (D-46), computed from the immutable ladder
      rows and parts; masked for the caller (D-33, D-46 §3).
    No AI is involved (D-45 §11).
    """

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal
        self.repo = ScenarioRepository(session, principal.scope)
        self.calc = CalculationService(session, principal)

    # --- refusals -------------------------------------------------------------------------

    async def _refuse(self, error: ApiError, resource: tuple[str, uuid.UUID | None]) -> NoReturn:
        kind, resource_id = resource
        await record_denial(
            self.session,
            self.principal,
            ScenarioRunAction.RUN.value,
            kind,
            str(resource_id) if resource_id else None,
            error.code,
        )
        raise error

    @staticmethod
    def _error(code: RR, message: str, details: dict[str, Any] | None = None) -> ApiError:
        return ApiError(404 if code == RR.NOT_FOUND else 409, code, message, details)

    @staticmethod
    def _approval(status: str) -> ApprovalState | None:
        if status == S.CONFIRMED_FOR_EXPLORATION:
            return ApprovalState.EXPLORATION
        if status == S.APPROVED_FOR_TEST:
            return ApprovalState.APPROVED
        return None

    @staticmethod
    def _counts(
        version: ScenarioVersion, found: Sequence[scenario_overlay.ShockCount] | None
    ) -> list[ShockMatchCount]:
        """One count per shock in declared order (zeros when the dry run could not run)."""
        by_index = {c.shock_sequence: c for c in found or []}
        counts = []
        for index, shock in enumerate(version.shocks, start=1):
            c = by_index.get(index)
            counts.append(
                ShockMatchCount(
                    shock_index=index,
                    template=ScenarioTemplate(shock["template"]),
                    matched_flows=c.matched_flows if c else 0,
                    parts=c.parts if c else 0,
                    direction_mismatches=c.direction_mismatches if c else 0,
                    skipped_past_due=c.skipped_past_due if c else 0,
                    skipped_incomplete=c.skipped_incomplete if c else 0,
                )
            )
        return counts

    # --- validation (BE-25.3) -------------------------------------------------------------

    async def validation(
        self,
        scenario_id: uuid.UUID,
        version: int,
        baseline_run_id: uuid.UUID,
        scenarios_enabled: bool,
        calculations_enabled: bool,
    ) -> ScenarioValidationOut:
        row = await self.repo.version(scenario_id, version)
        if row is None:
            raise ApiError(404, RR.NOT_FOUND, NOT_FOUND)
        check = await self.calc.baseline_check(baseline_run_id)
        if check.refusal == RR.NOT_FOUND or check.run is None:
            raise ApiError(404, RR.NOT_FOUND, "Baseline run not found.")
        blocking: list[str] = []
        if not scenarios_enabled:
            blocking.append(RR.SCENARIOS_DISABLED.value)
        if not calculations_enabled:
            blocking.append(RR.CALCULATIONS_DISABLED.value)
        approval = self._approval(row.status)
        if approval is None:
            blocking.append(RR.SCENARIO_NOT_RUNNABLE.value)
        found = None
        if check.refusal is not None:
            blocking.append(check.refusal)
        elif check.pinned is not None:
            found = await self.calc.scenario_counts(check.pinned, row.shocks)
        counts = self._counts(row, found)
        if check.refusal is None and any(c.matched_flows == 0 for c in counts):
            blocking.append(RR.NO_MATCHING_CASHFLOWS.value)
        return ScenarioValidationOut(
            scenario_id=row.scenario_id,
            scenario_version_id=row.id,
            version=row.version,
            status=S(row.status),
            baseline_run_id=baseline_run_id,
            shocks=counts,
            runnable=not blocking,
            blocking_reasons=blocking,
            run_count=len(scenario_overlay.subsets(len(row.shocks))),
            approval_state=approval,
            approval_label=APPROVAL_LABELS[approval] if approval else None,
            labels=LABELS,
        )

    # --- run (BE-25.3) --------------------------------------------------------------------

    @staticmethod
    def _fingerprint(version: ScenarioVersion, baseline_run_id: uuid.UUID) -> str:
        """What a replay of the Idempotency-Key must repeat: the version (and its shocks) and
        the baseline."""
        return canonical_sha256(
            {
                "scenario_version_id": str(version.id),
                "shocks_sha256": version.shocks_sha256,
                "baseline_run_id": str(baseline_run_id),
            }
        )

    async def run(
        self,
        scenario_id: uuid.UUID,
        body: ScenarioRunCreate,
        idempotency_key: str,
        scenarios_enabled: bool,
        calculations_enabled: bool,
    ) -> tuple[ScenarioRunOut, bool]:
        resource: tuple[str, uuid.UUID | None] = (SCENARIO, scenario_id)
        if not scenarios_enabled:
            await self._refuse(
                self._error(
                    RR.SCENARIOS_DISABLED,
                    "Stressed runs are switched off in this environment; existing runs stay "
                    "readable.",
                ),
                resource,
            )
        if not calculations_enabled:
            await self._refuse(
                self._error(
                    RR.CALCULATIONS_DISABLED,
                    "Calculations are switched off in this environment; existing runs stay "
                    "readable.",
                ),
                resource,
            )
        version = await self.repo.version(scenario_id, body.version)
        if version is None:
            await self._refuse(self._error(RR.NOT_FOUND, NOT_FOUND), resource)
        actor = self.principal.actor
        fingerprint = self._fingerprint(version, body.baseline_run_id)
        await self.repo.lock(f"scenario_run:{actor}:{idempotency_key}")
        existing = await self.repo.scenario_run_by_key(actor, idempotency_key)
        if existing is not None:
            if existing.request_sha256 != fingerprint:
                await self._refuse(
                    self._error(
                        RR.IDEMPOTENCY_CONFLICT,
                        "This Idempotency-Key was already used for a different run request.",
                        {"scenario_run_id": str(existing.id)},
                    ),
                    (AUDIT_RUN_RESOURCE, existing.id),
                )
            await self._members(existing, version, calculations_enabled, scenarios_enabled)
            return await self._view(existing.id), False
        version_resource: tuple[str, uuid.UUID | None] = (VERSION, version.id)
        approval = self._approval(version.status)
        if approval is None:
            await self._refuse(
                self._error(
                    RR.SCENARIO_NOT_RUNNABLE,
                    f"The version is {version.status}; only a CONFIRMED_FOR_EXPLORATION or "
                    "APPROVED_FOR_TEST version runs.",
                    {"status": version.status},
                ),
                version_resource,
            )
        check = await self.calc.baseline_check(body.baseline_run_id)
        if check.refusal == RR.NOT_FOUND or check.run is None:
            await self._refuse(
                self._error(RR.NOT_FOUND, "Baseline run not found."),
                ("calculation_run", body.baseline_run_id),
            )
        if check.refusal is not None or check.pinned is None:
            await self._refuse(
                self._error(
                    RR(check.refusal or RR.BASELINE_NOT_REPRODUCIBLE),
                    "The baseline must be a SUCCEEDED, non-stressed maturity_ladder run whose "
                    "pins still resolve (D-45 §10).",
                    {"baseline_run_id": str(body.baseline_run_id)},
                ),
                ("calculation_run", body.baseline_run_id),
            )
        found = await self.calc.scenario_counts(check.pinned, version.shocks)
        counts = self._counts(version, found)
        empty = [c.shock_index for c in counts if c.matched_flows == 0]
        if found is None or empty:
            await self._refuse(
                self._error(
                    RR.NO_MATCHING_CASHFLOWS,
                    "A shock matches no cash flow of the baseline; see the validation dry run.",
                    {"shock_indexes": empty},
                ),
                version_resource,
            )
        baseline = check.run
        scenario_run = ScenarioRun(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            scenario_id=version.scenario_id,
            scenario_version_id=version.id,
            version=version.version,
            shocks_sha256=version.shocks_sha256,
            approval_state=approval.value,
            baseline_run_id=baseline.id,
            reporting_date=baseline.reporting_date,
            shock_count=len(version.shocks),
            run_count=len(scenario_overlay.subsets(len(version.shocks))),
            shock_counts=[c.model_dump(mode="json") for c in counts],
            idempotency_key=idempotency_key,
            request_sha256=fingerprint,
            correlation_id=get_correlation_id(),
            created_by=actor,
        )
        self.repo.add(scenario_run)
        await self.repo.flush()
        details: dict[str, Any] = {
            "scenario_run_id": str(scenario_run.id),
            "scenario_id": str(version.scenario_id),
            "scenario_version_id": str(version.id),
            "version": version.version,
            "shocks_sha256": version.shocks_sha256,
            "shock_count": scenario_run.shock_count,
            "run_count": scenario_run.run_count,
            "approval_state": approval.value,
            "baseline_run_id": str(baseline.id),
        }
        audit_by(
            self.session,
            self.principal,
            ScenarioRunAction.RUN.value,
            AUDIT_RUN_RESOURCE,
            resource_id=str(scenario_run.id),
            details=details,
            details_classification=dict.fromkeys(details, ClassificationCode.PUBLIC.value),
        )
        await self.session.commit()
        await self._members(scenario_run, version, calculations_enabled, scenarios_enabled)
        return await self._view(scenario_run.id), True

    async def _members(
        self,
        scenario_run: ScenarioRun,
        version: ScenarioVersion,
        calculations_enabled: bool,
        scenarios_enabled: bool,
    ) -> None:
        """Every member run (D-45 §7), each a synchronous stressed run with its own derived
        key: a replay finds the runs that exist and creates only those that do not. The spec is
        built from the scenario_run's recorded pins (baseline, version, shocks_sha256,
        approval state); the version's shocks are the ones those pins name (the key's
        fingerprint covers version id and shocks_sha256), checked again here. Each member the
        call creates is audited `calculation_run.create` by CalculationService.create."""
        if (version.id, version.shocks_sha256) != (
            scenario_run.scenario_version_id,
            scenario_run.shocks_sha256,
        ):  # unreachable through run(): the fingerprint already matched; fail closed
            raise ApiError(
                409,
                RR.IDEMPOTENCY_CONFLICT,
                "This Idempotency-Key was already used for a different run request.",
                {"scenario_run_id": str(scenario_run.id)},
            )
        ids = (
            scenario_run.id, scenario_run.baseline_run_id, scenario_run.scenario_id,
            scenario_run.scenario_version_id, scenario_run.version,
        )  # fmt: skip
        for subset in scenario_overlay.subsets(scenario_run.shock_count):
            spec = ScenarioRunSpec(
                baseline_run_id=ids[1],
                scenario_run_id=ids[0],
                scenario_id=ids[2],
                scenario_version_id=ids[3],
                version=ids[4],
                shocks=tuple(version.shocks),
                shocks_sha256=scenario_run.shocks_sha256,
                shock_subset=subset,
                approval_state=scenario_run.approval_state,
            )
            key = f"scenario_run:{ids[0]}:{'-'.join(str(n) for n in subset)}"
            await self.calc.create(
                spec, key, calculations_enabled, scenarios_enabled=scenarios_enabled
            )

    # --- reads (BE-25.4) ------------------------------------------------------------------

    @staticmethod
    def _member(m: ScenarioMember) -> ScenarioRunMember:
        return ScenarioRunMember(
            calculation_run_id=m.run.id,
            shock_subset=list(m.pin.shock_subset),
            combined=m.pin.combined,
            status=CalculationRunStatus(m.run.status),
            manifest_sha256=m.run.manifest_sha256,
            result_sha256=m.run.result_sha256,
        )

    async def _outs(self, runs: Sequence[ScenarioRun]) -> list[ScenarioRunOut]:
        scenarios = {s.id: s for s in await self.repo.scenarios([r.scenario_id for r in runs])}
        members = await self.calc.scenario_members([r.id for r in runs])
        outs = []
        for r in runs:
            state = ApprovalState(r.approval_state)
            outs.append(
                ScenarioRunOut(
                    scenario_run_id=r.id,
                    scenario_id=r.scenario_id,
                    scenario_code=scenarios[r.scenario_id].code,
                    scenario_name=scenarios[r.scenario_id].name,
                    scenario_version_id=r.scenario_version_id,
                    version=r.version,
                    shocks_sha256=r.shocks_sha256,
                    approval_state=state,
                    approval_label=APPROVAL_LABELS[state],
                    baseline_run_id=r.baseline_run_id,
                    reporting_date=r.reporting_date,
                    members=[self._member(m) for m in members.get(r.id, [])],
                    shock_counts=[ShockMatchCount.model_validate(c) for c in r.shock_counts],
                    created_by=r.created_by,
                    created_at=r.created_at,
                    correlation_id=r.correlation_id,
                    labels=LABELS,
                )
            )
        return outs

    async def _view(self, scenario_run_id: uuid.UUID) -> ScenarioRunOut:
        found = await self.repo.scenario_run(scenario_run_id)
        if found is None:
            raise ApiError(404, RR.NOT_FOUND, RUN_NOT_FOUND)
        [out] = await self._outs([found])
        return out

    async def page(
        self,
        scenario_id: uuid.UUID | None,
        baseline_run_id: uuid.UUID | None,
        approval_state: ApprovalState | None,
        limit: int,
        offset: int,
    ) -> ScenarioRunPage:
        runs, total = await self.repo.scenario_run_page(
            scenario_id,
            baseline_run_id,
            approval_state.value if approval_state else None,
            limit,
            offset,
        )
        return ScenarioRunPage(
            items=await self._outs(runs), total=total, limit=limit, offset=offset, labels=LABELS
        )

    async def _scoped_run(self, scenario_id: uuid.UUID, scenario_run_id: uuid.UUID) -> ScenarioRun:
        """The scenario run, of this scenario, in the caller's tenant: 404 alike otherwise."""
        found = await self.repo.scenario_run(scenario_run_id)
        if found is None or found.scenario_id != scenario_id:
            raise ApiError(404, RR.NOT_FOUND, RUN_NOT_FOUND)
        return found

    async def comparison(
        self, scenario_id: uuid.UUID, scenario_run_id: uuid.UUID, min_aggregate_count: int = 3
    ) -> ScenarioComparisonOut:
        """Baseline versus stress (D-46), from the immutable ladder rows of the baseline and of
        every SUCCEEDED member run (a FAILED or BLOCKED member has no rows to compare; it is
        listed in `members` with its status)."""
        sr = await self._scoped_run(scenario_id, scenario_run_id)
        members = (await self.calc.scenario_members([sr.id])).get(sr.id, [])
        baseline = await self.calc.ladder(sr.baseline_run_id, min_aggregate_count)
        compared = []
        for m in members:
            if m.run.status not in RUN_SUCCEEDED:
                continue
            compared.append(
                _Compared(
                    m,
                    await self.calc.ladder(m.run.id, min_aggregate_count),
                    await self.calc.scenario_changed(m.run.id),
                )
            )
        table = _comparison(
            baseline,
            compared,
            self.principal.clear_classifications,
            min_aggregate_count,
            sr.shock_count,
        )
        state = ApprovalState(sr.approval_state)
        scenario = await self.repo.scenario(sr.scenario_id)
        summary = _summary(sr, scenario.code if scenario else "", state, compared, table)
        return ScenarioComparisonOut(
            scenario_run_id=sr.id,
            scenario_id=sr.scenario_id,
            scenario_version_id=sr.scenario_version_id,
            version=sr.version,
            approval_state=state,
            approval_label=APPROVAL_LABELS[state],
            baseline_run_id=sr.baseline_run_id,
            reporting_date=sr.reporting_date,
            members=[self._member(m) for m in members],
            buckets=[
                ComparisonBucket(
                    bucket_definition_id=b.id,
                    sequence=b.sequence,
                    code=b.code,
                    label=b.label,
                    lower_exclusive_days=b.lower_exclusive_days,
                    upper_inclusive_days=b.upper_inclusive_days,
                )
                for b in baseline.buckets
            ],
            currencies=table.currencies,
            largest_changes=table.largest,
            summary=summary,
            min_aggregate_count=min_aggregate_count,
            illustrative=True,
            labels=LABELS,
            # D-56 §4-5 (BE-30.2, BE-30.3): from the baseline run's pins; every member run pins
            # the same module, engine and rule versions (D-45 §1).
            validation_status=self.calc.validation_status(baseline.row.run),
            context=self.calc.result_context(baseline.row.run),
        )

    async def contributions(
        self,
        scenario_id: uuid.UUID,
        scenario_run_id: uuid.UUID,
        currency: str | None,
        bucket_code: str | None,
        shock_subset: list[int] | None,
        limit: int,
        offset: int,
    ) -> ScenarioContributionPage:
        """The drill-down (D-46 §4): one member run's changed parts (default the combined
        run), in a bucket before or after. Amounts are masked by their classification for a
        role not cleared for them (D-33); days, buckets and templates are internal."""
        sr = await self._scoped_run(scenario_id, scenario_run_id)
        members = (await self.calc.scenario_members([sr.id])).get(sr.id, [])
        wanted = sorted(set(shock_subset)) if shock_subset else None
        chosen = [
            m
            for m in members
            if (m.pin.combined if wanted is None else list(m.pin.shock_subset) == wanted)
        ]
        if not chosen:
            raise ApiError(404, RR.NOT_FOUND, "Member run not found.")
        member = chosen[0]
        rows, total = await self.calc.scenario_parts(
            member.run.id, currency, bucket_code, limit, offset
        )
        templates = [
            ScenarioTemplate(s["template"]) for s in member.run.manifest["scenario"]["shocks"]
        ]
        clear = self.principal.clear_classifications
        items = []
        for row in rows:
            v = row.value
            key: str | MaskedValue | None = None
            if row.instrument is not None:
                code = row.instrument_classes.get("instrument_id", UNCLASSIFIED)
                key = (
                    row.instrument.business_key
                    if code in clear
                    else MaskedValue(classification=code)
                )
            amount: str | MaskedValue = (
                decimal_text(v.value)
                if v.classification in clear
                else MaskedValue(classification=v.classification)
            )
            items.append(
                ScenarioContributionOut(
                    scenario_contribution_id=v.id,
                    calculation_run_id=v.calculation_run_id,
                    shock_subset=list(member.pin.shock_subset),
                    cashflow_event_id=v.cashflow_event_id,
                    part_path=v.part_path,
                    part_id=v.part_id,
                    instrument_business_key=key,
                    shock_sequences=list(v.shock_sequences),
                    templates=[templates[n - 1] for n in v.shock_sequences],
                    change=PartChange(v.change),
                    direction=FlowDirection(v.direction),
                    origin=FlowOrigin(v.origin),
                    currency=v.unit,
                    days_before=v.original_days,
                    days_after=v.days_after_reporting_date,
                    bucket_code_before=v.original_bucket_code,
                    bucket_code_after=v.bucket_code,
                    amount=amount,
                    classification=v.classification,
                    lineage=ContributionSourceLineage(
                        cashflow_event_id=v.cashflow_event_id,
                        canonical_record_id=v.canonical_record_id,
                        import_id=v.import_job_id,
                        source_record_id=v.source_record_id,
                        source_ordinal=v.source_ordinal,
                    ),
                )
            )
        return ScenarioContributionPage(
            scenario_run_id=sr.id,
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            labels=LABELS,
        )


# --- the comparison, computed on read (D-46 §1-3) ----------------------------------------------

FIGURES = ("inflows", "outflows", "net_gap", "cumulative_gap")
SUMMARY_TEMPLATE_VERSION = "1"


@dataclass(frozen=True)
class _Compared:
    member: ScenarioMember
    ladder: LadderView
    changed: list[tuple[str, str, str, uuid.UUID, str]]


@dataclass
class _Table:
    currencies: list[ComparisonCurrency]
    largest: list[LargestChange]
    # (currency, bucket code) -> member run id -> (turns negative net, breaks only combined)
    flags: dict[tuple[str, str], dict[uuid.UUID, bool | None]]
    breaks: dict[str, list[str]]
    unknown: dict[str, bool]
    first_negative: dict[str, tuple[str | MaskedValue | None, str | MaskedValue | None]]


def _shown(value: Decimal, code: str, clear: frozenset[str]) -> str | MaskedValue:
    return decimal_text(value) if code in clear else MaskedValue(classification=code)


def _first_negative(
    rows: Sequence[tuple[str, Decimal, str]], clear: frozenset[str]
) -> str | MaskedValue | None:
    """The first bucket whose cumulative gap is negative; masked when the deciding figure is
    masked; None when never negative."""
    for code, value, cls in rows:
        if cls not in clear:
            return MaskedValue(classification=cls)
        if value < 0:
            return code
    return None


def _comparison(
    baseline: LadderView,
    compared: Sequence[_Compared],
    clear: frozenset[str],
    minimum: int,
    shock_count: int,
) -> _Table:
    """Per currency and bucket, for each member: baseline, stressed and delta (exact Decimal
    subtraction), masked per D-46 §3; turns-negative flags; combined-only breaks; the
    largest clear changes, ranked per currency (no FX: rank restarts at 1 in each currency);
    the first negative cumulative bucket."""
    zero = Decimal(0)
    agg = AGGREGATE_CLASSIFICATION
    base_rows = {(r.currency, r.bucket_code): r for r in baseline.rows}
    member_rows = [{(r.currency, r.bucket_code): r for r in c.ladder.rows} for c in compared]
    currencies = sorted(
        {r.currency for r in baseline.rows} | {k[0] for rows in member_rows for k in rows}
    )
    buckets = baseline.buckets
    largest: list[tuple[Any, ...]] = []
    out_currencies: list[ComparisonCurrency] = []
    flags: dict[tuple[str, str], dict[uuid.UUID, bool | None]] = {}
    breaks: dict[str, list[str]] = {}
    unknown: dict[str, bool] = {}
    first_negative: dict[str, tuple[str | MaskedValue | None, str | MaskedValue | None]] = {}
    for currency in currencies:
        rows_out: list[ComparisonRow] = []
        # The classification each member's cumulative deltas carry so far (D-46 §3).
        carried: list[str | None] = [None] * len(compared)
        base_cum: list[tuple[str, Decimal, str]] = []
        member_cum: list[list[tuple[str, Decimal, str]]] = [[] for _ in compared]
        breaks[currency] = []
        unknown[currency] = False
        for bucket in buckets:
            key = (currency, bucket.code)
            b_row = base_rows.get(key)
            b_cls = baseline.classes.get(b_row.id, {}) if b_row else {}
            b_val = {f: getattr(b_row, f) if b_row else zero for f in FIGURES}
            b_code = {f: b_cls.get(f, agg) for f in FIGURES}
            base_cum.append((bucket.code, b_val["cumulative_gap"], b_code["cumulative_gap"]))
            cells: list[ComparisonCell] = []
            nets: list[tuple[bool, Decimal | None]] = []  # (combined, stressed net or masked)
            for i, c in enumerate(compared):
                s_row = member_rows[i].get(key)
                s_cls = c.ladder.classes.get(s_row.id, {}) if s_row else {}
                s_val = {f: getattr(s_row, f) if s_row else zero for f in FIGURES}
                s_code = {f: s_cls.get(f, agg) for f in FIGURES}
                changed = [ch for ch in c.changed if ch[0] == currency and ch[1] == bucket.code]
                by_direction = {
                    "inflows": {ch[3] for ch in changed if ch[2] == "INFLOW"},
                    "outflows": {ch[3] for ch in changed if ch[2] == "OUTFLOW"},
                }
                by_direction["net_gap"] = by_direction["inflows"] | by_direction["outflows"]
                changed_code = most_sensitive([ch[4] for ch in changed]) if changed else agg
                figures: dict[str, ComparisonFigure] = {}
                net_class = agg
                for f in FIGURES:
                    # D-46 §3: a delta's classification, whoever reads it: the most sensitive
                    # of its two sides and, when fewer than `minimum` distinct non-zero flows
                    # changed it, of those flows. A cumulative delta also carries every
                    # earlier net delta's, so once masked it stays masked in its currency.
                    # The viewer's clearance then masks it; a zero net, inflow or outflow
                    # delta stays clear. No differencing: the stressed side's ladder classes
                    # (`ladder_classes`, from the same changed-flow counts per part) already
                    # mask a stressed figure changed by too few flows, even at zero, and
                    # a delta is masked when either side is.
                    delta = s_val[f] - b_val[f]
                    if f == "cumulative_gap":
                        code = most_sensitive([b_code[f], s_code[f], net_class, carried[i] or agg])
                        carried[i] = code
                        masked = code not in clear
                    else:
                        few = 0 < len(by_direction[f]) < minimum
                        code = most_sensitive(
                            [b_code[f], s_code[f], *([changed_code] if few else [])]
                        )
                        if f == "net_gap":
                            net_class = code
                        masked = delta != zero and code not in clear
                    figures[f] = ComparisonFigure(
                        baseline=_shown(b_val[f], b_code[f], clear),
                        stressed=_shown(s_val[f], s_code[f], clear),
                        delta=MaskedValue(classification=code) if masked else decimal_text(delta),
                    )
                    if f == "net_gap" and not masked and delta != zero:
                        largest.append(
                            (-abs(delta), currency, bucket.sequence, not c.member.pin.combined,
                             list(c.member.pin.shock_subset), bucket.code, c, delta)
                        )  # fmt: skip
                net_known = b_code["net_gap"] in clear and s_code["net_gap"] in clear
                cum_known = b_code["cumulative_gap"] in clear and s_code["cumulative_gap"] in clear
                turns_net = (
                    b_val["net_gap"] >= zero and s_val["net_gap"] < zero if net_known else None
                )
                turns_cum = (
                    b_val["cumulative_gap"] >= zero and s_val["cumulative_gap"] < zero
                    if cum_known
                    else None
                )
                flags.setdefault(key, {})[c.member.run.id] = turns_net
                nets.append((c.member.pin.combined, s_val["net_gap"] if net_known else None))
                member_cum[i].append(
                    (bucket.code, s_val["cumulative_gap"], s_code["cumulative_gap"])
                )
                cells.append(
                    ComparisonCell(
                        calculation_run_id=c.member.run.id,
                        shock_subset=list(c.member.pin.shock_subset),
                        combined=c.member.pin.combined,
                        changed_flow_count=len(by_direction["net_gap"]),
                        turns_negative_net=turns_net,
                        turns_negative_cumulative=turns_cum,
                        **figures,
                    )
                )
            singles = [net for combined, net in nets if not combined]
            combined_nets = [net for combined, net in nets if combined]
            only: bool | None
            if not combined_nets or len(singles) < shock_count:
                # Not assessable: no combined run compared, or fewer single-shock runs compared
                # than the version has shocks (k = 1 has none; a FAILED member is not compared).
                only = None
            elif b_code["net_gap"] not in clear or any(
                n is None for n in [*singles, *combined_nets]
            ):
                only = None
            else:
                only = (
                    combined_nets[0] is not None
                    and combined_nets[0] < zero
                    and b_val["net_gap"] >= zero
                    and all(n is not None and n >= zero for n in singles)
                )
            if only:
                breaks[currency].append(bucket.code)
            unknown[currency] = unknown[currency] or only is None
            rows_out.append(
                ComparisonRow(
                    bucket_code=bucket.code,
                    bucket_sequence=bucket.sequence,
                    bucket_label=bucket.label,
                    cells=cells,
                    breaks_only_combined=only,
                )
            )
        firsts = []
        base_first = _first_negative(base_cum, clear)
        for i, c in enumerate(compared):
            stressed_first = _first_negative(member_cum[i], clear)
            firsts.append(
                FirstNegativeCumulative(
                    shock_subset=list(c.member.pin.shock_subset),
                    combined=c.member.pin.combined,
                    baseline_bucket_code=base_first,
                    stressed_bucket_code=stressed_first,
                )
            )
            if c.member.pin.combined:
                first_negative[currency] = (base_first, stressed_first)
        out_currencies.append(
            ComparisonCurrency(currency=currency, rows=rows_out, first_negative_cumulative=firsts)
        )
    ranked = [
        (rank, t)
        for currency in currencies
        for rank, t in enumerate(
            sorted((t for t in largest if t[1] == currency), key=lambda t: t[:5])[
                :MAX_LARGEST_CHANGES
            ],
            start=1,
        )
    ]
    return _Table(
        out_currencies,
        [
            LargestChange(
                rank=rank,
                currency=t[1],
                bucket_code=t[5],
                bucket_sequence=t[2],
                calculation_run_id=t[6].member.run.id,
                shock_subset=list(t[6].member.pin.shock_subset),
                combined=t[6].member.pin.combined,
                net_gap_delta=decimal_text(t[7]),
            )
            for rank, t in ranked
        ],
        flags,
        breaks,
        unknown,
        first_negative,
    )


def _which(subset: Sequence[int], combined: bool) -> str:
    if combined:
        return "the combined run"
    return (
        f"shock {subset[0]} alone" if len(subset) == 1 else "shocks " + ", ".join(map(str, subset))
    )


def _summary(
    sr: ScenarioRun,
    code: str,
    state: ApprovalState,
    compared: Sequence[_Compared],
    table: _Table,
) -> ComparisonSummary:
    """D-46 §2: a deterministic, templated (non-AI) summary of the comparison's facts. It
    cites only clear figures and flags, never a masked one (the D-51 fallback)."""
    runs = len(compared)
    sentences = [
        f"{APPROVAL_LABELS[state].value}: scenario {code} version {sr.version} against "
        f"baseline run {sr.baseline_run_id} (reporting date {sr.reporting_date.isoformat()}), "
        f"{sr.shock_count} shock(s) in {sr.run_count} run(s), {runs} compared."
    ]
    combined = [c for c in compared if c.member.pin.combined]
    for currency in sorted(table.breaks):
        if combined:
            run_id = combined[0].member.run.id
            turned = [
                bucket
                for (cur, bucket), by_run in table.flags.items()
                if cur == currency and by_run.get(run_id) is True
            ]
            unknown = any(
                by_run.get(run_id) is None
                for (cur, _), by_run in table.flags.items()
                if cur == currency
            )
            if turned:
                sentences.append(
                    f"{currency}: the combined run turns the net gap negative in bucket(s) "
                    f"{', '.join(turned)}."
                )
            elif not unknown:
                sentences.append(
                    f"{currency}: no bucket's net gap turns negative in the combined run."
                )
            if unknown:
                sentences.append(
                    f"{currency}: some figures are masked for your role and are not cited."
                )
        if table.breaks[currency]:
            sentences.append(
                f"{currency}: bucket(s) {', '.join(table.breaks[currency])} break only when the "
                "shocks are combined."
            )
        first = table.first_negative.get(currency)
        if first is not None and not any(isinstance(x, MaskedValue) for x in first):
            base, stressed = first
            if stressed is not None:
                sentences.append(
                    f"{currency}: under the combined run the cumulative gap first turns negative "
                    f"in bucket {stressed} (baseline: {base or 'never'})."
                )
    for top in (x for x in table.largest if x.rank == 1):  # one per currency, no FX
        sentences.append(
            f"{top.currency}: the largest clear change is a net gap delta of "
            f"{top.net_gap_delta} in bucket {top.bucket_code} "
            f"({_which(top.shock_subset, top.combined)})."
        )
    sentences.append(SCENARIO_STATEMENT)
    return ComparisonSummary(template_version=SUMMARY_TEMPLATE_VERSION, sentences=sentences)
