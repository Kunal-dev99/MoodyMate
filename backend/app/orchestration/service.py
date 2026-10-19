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
from typing import Any, NoReturn

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.classification import UNCLASSIFIED, MaskedValue
from app.core.errors import ApiError
from app.core.principal import Principal
from app.domain.service import DomainService
from app.governance.constants import ClassificationCode, Permission, ProposalDecision
from app.governance.service import audit_by, record_denial
from app.orchestration.constants import (
    DIRECTION_RULE,
    MAX_SHOCKS,
    SCENARIO_STATEMENT,
    ApprovalLabel,
    ParameterKind,
    ScenarioAction,
    ScenarioOrigin,
    ScenarioRefusal,
    ScenarioVersionStatus,
    ShockProblem,
)
from app.orchestration.models import Scenario, ScenarioChange, ScenarioVersion
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
    ScenarioCreate,
    ScenarioDetail,
    ScenarioLabels,
    ScenarioPage,
    ScenarioSummary,
    ScenarioVersionClose,
    ScenarioVersionConfirm,
    ScenarioVersionCreate,
    ScenarioVersionDecision,
    ScenarioVersionOut,
    ScenarioVersionSubmit,
    ScenarioVersionUpdate,
    TemplateField,
    TemplateOut,
    TemplatePage,
)
from app.rules.service import RulesService

S = ScenarioVersionStatus
R = ScenarioRefusal
SCENARIO = "scenario"
VERSION = "scenario_version"
NOT_FOUND = "Scenario not found."
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
        """Copies of the shock lists with each instrument key the caller is not cleared for
        (the instrument_id field's classification, as GET /instruments and run results mask
        it, D-33) replaced by a MaskedValue; a key that no longer matches an instrument is
        masked as unclassified (fail closed)."""
        keys = {
            k
            for group in shocks
            for shock in group
            for k in ((shock.get("selector") or {}).get("instrument_keys") or [])
        }
        if not keys:
            return shocks
        classes = await DomainService(self.session, self.principal).instrument_key_classes(
            sorted(keys)
        )
        clear = self.principal.clear_classifications

        def shown(key: str) -> str | dict[str, Any]:
            code = classes.get(key, UNCLASSIFIED)
            if code in clear:
                return key
            return MaskedValue(classification=code).model_dump()

        def one(shock: dict[str, Any]) -> dict[str, Any]:
            selector = shock.get("selector") or {}
            if not selector.get("instrument_keys"):
                return shock
            return {
                **shock,
                "selector": {
                    **selector,
                    "instrument_keys": [shown(k) for k in selector["instrument_keys"]],
                },
            }

        return [[one(shock) for shock in group] for group in shocks]

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
