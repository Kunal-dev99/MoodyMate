"""orchestration.scenario_templates

The closed template list and its static validators (Phase 24, BE-24.2; D-44 §1-4, design §8).
Pure: no I/O, no float. The service passes in the ACTIVE `cashflow_direction` map (instrument
type -> INFLOW/OUTFLOW) that the instrument-type fields are checked against.

A scenario is data, never code (D-44 §1): 1 to 5 shocks, each `{template, parameters,
selector}` with typed fields. Fractions are exact decimal text padded to scale 4; more places
are refused (never rounded), and JSON fractions and exponents are refused (`inexact_number`).
Every refusal is a value-free `Problem` (shock index, structural field path, code): never a
submitted value and never the name of an unknown key.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.orchestration.constants import (
    FRACTION_SCALE,
    MAX_DELAY_DAYS,
    MAX_INSTRUMENT_KEYS,
    MAX_SHOCKS,
    MAX_SOURCE_SYSTEM_CODE,
    MAX_TEXT_FIELD,
    MAX_UNAVAILABLE_DAYS,
    MIN_SHOCKS,
    DelayCalendar,
    FlowDirection,
    FlowOrigin,
    ParameterKind,
    ScenarioTemplate,
    ShockProblem,
)

K = ParameterKind
P = ShockProblem
# Always `fullmatch`: with `match`, `$` also accepts a trailing newline ("USD\n"), which the
# typed response models (pydantic-core regex) then refuse, failing every later read.
_PLAIN_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
_EXPONENT = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)[eE][+-]?[0-9]+")
_CURRENCY = re.compile(r"[A-Z]{3}")
_QUANTUM = Decimal(1).scaleb(-FRACTION_SCALE)  # 0.0001
MAX_INSTRUMENT_TYPE = 50


@dataclass(frozen=True)
class FieldSpec:
    """One typed parameter or selector field of a template (D-44 §2-3)."""

    name: str
    kind: ParameterKind
    required: bool
    description: str
    min: str | None = None
    min_exclusive: bool = False
    max: str | None = None
    scale: int | None = None
    max_items: int | None = None
    values: tuple[str, ...] = ()
    unit: str | None = None
    direction: FlowDirection | None = None  # INSTRUMENT_TYPE: the direction it must have
    default: str | None = None  # ENUM: the value an absent field takes


@dataclass(frozen=True)
class TemplateSpec:
    template: ScenarioTemplate
    name: str
    description: str
    direction: FlowDirection
    changes_amounts: bool
    parameters: tuple[FieldSpec, ...]
    selector: tuple[FieldSpec, ...]


def _fraction(name: str, description: str) -> FieldSpec:
    return FieldSpec(
        name, K.DECIMAL, True, description, min="0", min_exclusive=True, max="1",
        scale=FRACTION_SCALE, unit="fraction",
    )  # fmt: skip


def _days(name: str, low: int, high: int, required: bool, description: str) -> FieldSpec:
    return FieldSpec(name, K.INTEGER, required, description, min=str(low), max=str(high),
                     unit="calendar days")  # fmt: skip


_CALENDAR = FieldSpec(
    "delay_calendar", K.ENUM, False, "CALENDAR_DAYS only in v1.",
    values=tuple(DelayCalendar), default=DelayCalendar.CALENDAR_DAYS.value,
)  # fmt: skip
COMMON_SELECTOR: tuple[FieldSpec, ...] = (
    FieldSpec("currency", K.CURRENCY, False, "Only flows in this ISO 4217 currency."),
    FieldSpec(
        "origin", K.ENUM, False, "CONTRACTUAL or BEHAVIOURAL flows only.", values=tuple(FlowOrigin)
    ),  # fmt: skip
    _days(
        "due_from_day",
        0,
        MAX_DELAY_DAYS,
        False,
        "Flows due on or after this day after the reporting date.",
    ),  # fmt: skip
    _days(
        "due_to_day",
        0,
        MAX_DELAY_DAYS,
        False,
        "Flows due on or before this day after the reporting date.",
    ),  # fmt: skip
)
FUNDING_SELECTOR: tuple[FieldSpec, ...] = (
    FieldSpec(
        "instrument_type",
        K.INSTRUMENT_TYPE,
        True,
        "The funding source's INFLOW instrument type.",
        direction=FlowDirection.INFLOW,
    ),  # fmt: skip
    FieldSpec(
        "instrument_keys",
        K.INSTRUMENT_KEYS,
        False,
        "At most 50 instrument business keys, picked by the analyst.",
        max_items=MAX_INSTRUMENT_KEYS,
    ),  # fmt: skip
    FieldSpec(
        "source_system_code",
        K.TEXT,
        False,
        "Matches all of a snapshot's cash flows or none in v1 (D-30 §1).",
        max=str(MAX_SOURCE_SYSTEM_CODE),
    ),  # fmt: skip
    *COMMON_SELECTOR,
)

TEMPLATES: dict[ScenarioTemplate, TemplateSpec] = {
    ScenarioTemplate.DELAYED_INFLOWS: TemplateSpec(
        ScenarioTemplate.DELAYED_INFLOWS,
        "Delayed inflows",
        "Delayed loan repayments: affected_fraction of each matching INFLOW moves delay_days "
        "later and the rest stays (the whole flow when the fraction is 1).",
        FlowDirection.INFLOW,
        False,
        (
            FieldSpec(
                "cashflow_type",
                K.INSTRUMENT_TYPE,
                True,
                "An INFLOW instrument type, e.g. LOAN.",
                direction=FlowDirection.INFLOW,
            ),  # fmt: skip
            _fraction("affected_fraction", "0 < f <= 1, scale 4."),
            _days("delay_days", 1, MAX_DELAY_DAYS, True, "How much later the share is due."),
            _CALENDAR,
        ),
        COMMON_SELECTOR,
    ),
    ScenarioTemplate.DEPOSIT_WITHDRAWAL: TemplateSpec(
        ScenarioTemplate.DEPOSIT_WITHDRAWAL,
        "Deposit withdrawal",
        "Increased deposit withdrawals: each matching OUTFLOW amount is multiplied by "
        "(1 + increase_fraction). The only template that changes amounts.",
        FlowDirection.OUTFLOW,
        True,
        (
            FieldSpec(
                "cashflow_type",
                K.INSTRUMENT_TYPE,
                True,
                "An OUTFLOW instrument type, e.g. DEPOSIT.",
                direction=FlowDirection.OUTFLOW,
            ),  # fmt: skip
            _fraction(
                "increase_fraction", "0 < x <= 1, scale 4 (PROVISIONAL bound); '+15%' is '0.1500'."
            ),  # fmt: skip
        ),
        COMMON_SELECTOR,
    ),
    ScenarioTemplate.FUNDING_UNAVAILABLE: TemplateSpec(
        ScenarioTemplate.FUNDING_UNAVAILABLE,
        "Funding unavailable",
        "A funding source unavailable: affected_fraction of each matching INFLOW due in "
        "(0, unavailable_days] moves to day unavailable_days + 1 (timing only).",
        FlowDirection.INFLOW,
        False,
        (
            _days(
                "unavailable_days",
                1,
                MAX_UNAVAILABLE_DAYS,
                True,
                "How long the source is unavailable.",
            ),  # fmt: skip
            _fraction("affected_fraction", "0 < f <= 1, scale 4; 1 is fully unavailable."),
            _CALENDAR,
        ),
        FUNDING_SELECTOR,
    ),
}

_SHOCK_KEYS = ("template", "parameters", "selector")


@dataclass(frozen=True, order=True)
class Problem:
    """One value-free refusal: which shock (None: the list), which field (a structural path),
    and what is wrong."""

    shock_index: int | None
    field: str
    code: str

    def as_detail(self) -> dict[str, Any]:
        return {"shock_index": self.shock_index, "field": self.field, "code": self.code}


@dataclass
class _Checker:
    directions: Mapping[str, str]
    problems: list[Problem] = field(default_factory=list)

    def refuse(self, index: int | None, path: str, code: ShockProblem) -> Any:
        """Record a problem; returns None, so a checker can `return self.refuse(...)`."""
        self.problems.append(Problem(index, path, code.value))
        return None

    def value(self, index: int, path: str, spec: FieldSpec, raw: Any) -> Any:
        """The normalised value of one present field, or None after recording a problem."""
        problems = len(self.problems)
        result = self._value(index, path, spec, raw)
        return None if len(self.problems) > problems else result

    def _value(self, index: int, path: str, spec: FieldSpec, raw: Any) -> Any:
        if spec.kind == K.DECIMAL:
            return self._decimal(index, path, spec, raw)
        if spec.kind == K.INTEGER:
            if isinstance(raw, float):
                return self.refuse(index, path, P.INEXACT_NUMBER)
            if not isinstance(raw, int) or isinstance(raw, bool):
                return self.refuse(index, path, P.NOT_INTEGER)
            if not int(spec.min or 0) <= raw <= int(spec.max or 0):
                return self.refuse(index, path, P.OUT_OF_RANGE)
            return raw
        if not isinstance(raw, str) and spec.kind != K.INSTRUMENT_KEYS:
            return self.refuse(index, path, P.NOT_TEXT)
        if spec.kind == K.ENUM:
            return raw if raw in spec.values else self.refuse(index, path, P.UNSUPPORTED_VALUE)
        if spec.kind == K.CURRENCY:
            ok = _CURRENCY.fullmatch(raw) is not None
            return raw if ok else self.refuse(index, path, P.UNSUPPORTED_VALUE)
        if spec.kind == K.TEXT:
            ok = 1 <= len(raw) <= int(spec.max or 0)
            return raw if ok else self.refuse(index, path, P.OUT_OF_RANGE)
        if spec.kind == K.INSTRUMENT_TYPE:
            if not 1 <= len(raw) <= MAX_INSTRUMENT_TYPE or raw not in self.directions:
                return self.refuse(index, path, P.UNKNOWN_INSTRUMENT_TYPE)
            if self.directions[raw] != spec.direction:
                return self.refuse(index, path, P.DIRECTION_MISMATCH)
            return raw
        return self._keys(index, path, spec, raw)  # INSTRUMENT_KEYS

    def _decimal(self, index: int, path: str, spec: FieldSpec, raw: Any) -> Any:
        if isinstance(raw, float) or (isinstance(raw, str) and _EXPONENT.fullmatch(raw.strip())):
            return self.refuse(index, path, P.INEXACT_NUMBER)
        if not isinstance(raw, str) or not _PLAIN_DECIMAL.fullmatch(raw):
            return self.refuse(index, path, P.NOT_DECIMAL)
        places = len(raw.partition(".")[2])
        if places > (spec.scale or 0):
            return self.refuse(index, path, P.TOO_MANY_PLACES)
        value = Decimal(raw)
        low, high = Decimal(spec.min or "0"), Decimal(spec.max or "0")
        if (value <= low if spec.min_exclusive else value < low) or value > high:
            return self.refuse(index, path, P.OUT_OF_RANGE)
        return str(value.quantize(_QUANTUM))

    def _keys(self, index: int, path: str, spec: FieldSpec, raw: Any) -> Any:
        if not isinstance(raw, list):
            return self.refuse(index, path, P.NOT_A_LIST)
        if len(raw) > (spec.max_items or 0):
            return self.refuse(index, path, P.TOO_MANY_ITEMS)
        if not all(isinstance(k, str) and 1 <= len(k) <= MAX_TEXT_FIELD for k in raw):
            return self.refuse(index, path, P.NOT_TEXT)
        if len(set(raw)) != len(raw):
            return self.refuse(index, path, P.DUPLICATE_ITEM)
        return list(raw)

    def fields(
        self, index: int, part: str, specs: tuple[FieldSpec, ...], raw: Any, *, keep_all: bool
    ) -> dict[str, Any]:
        """A parameters or selector object: known fields only, required ones present."""
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            self.refuse(index, part, P.NOT_AN_OBJECT)
            return {}
        known = {s.name for s in specs}
        if any(k not in known for k in raw):
            self.refuse(index, part, P.UNKNOWN_FIELD)  # never the unknown key's name
        out: dict[str, Any] = {}
        for spec in specs:
            given = raw.get(spec.name)
            path = f"{part}.{spec.name}"
            if given is None:
                if spec.required:
                    self.refuse(index, path, P.REQUIRED)
                elif keep_all or spec.default is not None:
                    out[spec.name] = spec.default
                continue
            out[spec.name] = self.value(index, path, spec, given)
        return out

    def shock(self, index: int, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, Mapping):
            self.refuse(index, "shock", P.NOT_AN_OBJECT)
            return None
        if any(k not in _SHOCK_KEYS for k in raw):
            self.refuse(index, "shock", P.UNKNOWN_FIELD)
        template = raw.get("template")
        if template is None:
            self.refuse(index, "template", P.REQUIRED)
            return None
        if not isinstance(template, str) or template not in TEMPLATES:
            self.refuse(index, "template", P.UNKNOWN_TEMPLATE)
            return None
        spec = TEMPLATES[ScenarioTemplate(template)]
        if raw.get("parameters") is None:
            self.refuse(index, "parameters", P.REQUIRED)
        parameters = self.fields(
            index, "parameters", spec.parameters, raw.get("parameters"), keep_all=True
        )
        selector = self.fields(
            index, "selector", spec.selector, raw.get("selector"), keep_all=False
        )
        low, high = selector.get("due_from_day"), selector.get("due_to_day")
        if isinstance(low, int) and isinstance(high, int) and low > high:
            self.refuse(index, "selector.due_from_day", P.INVALID_WINDOW)
        return {"template": spec.template.value, "parameters": parameters, "selector": selector}


def validate_shocks(
    raw: Any, directions: Mapping[str, str]
) -> tuple[list[dict[str, Any]], list[Problem]]:
    """The normalised shocks (decimals padded to scale 4, the calendar defaulted, absent
    selector fields left out) and every value-free problem, sorted. The shocks are only
    meaningful when there is no problem. `directions` is the ACTIVE cashflow_direction map."""
    checker = _Checker(directions)
    if not isinstance(raw, list):
        checker.refuse(None, "shocks", P.NOT_A_LIST)
        return [], checker.problems
    if len(raw) < MIN_SHOCKS:
        checker.refuse(None, "shocks", P.TOO_FEW_SHOCKS)
    if len(raw) > MAX_SHOCKS:
        checker.refuse(None, "shocks", P.TOO_MANY_SHOCKS)
    if checker.problems:
        return [], checker.problems
    shocks = [checker.shock(i, s) for i, s in enumerate(raw)]
    problems = sorted(set(checker.problems), key=lambda p: (p.shock_index or 0, p.field, p.code))
    return [s for s in shocks if s is not None], problems


def shocks_sha256(shocks: list[dict[str, Any]]) -> str:
    """SHA-256 of the canonical JSON of normalised shocks (sorted keys, no spaces): the same
    canonical form as `app.rules.service.canonical_sha256`."""
    text = json.dumps(shocks, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def instrument_types(directions: Mapping[str, str], direction: FlowDirection) -> list[str]:
    """The keys of the ACTIVE cashflow_direction map with this direction, sorted."""
    return sorted(k for k, v in directions.items() if v == direction.value)


def proposed_defaults(parameter_sources: Mapping[str, str] | None) -> list[str]:
    """The parameter paths an AI_PROPOSED version filled with a PROPOSED_DEFAULT (D-48 §4)."""
    return sorted(p for p, s in (parameter_sources or {}).items() if s == "PROPOSED_DEFAULT")
