"""The mapping transform engine (BE-10.2, D-19).

A closed allowlist of named, pure, deterministic functions from one source cell (text, or
None when empty) to one value. Parameters are validated against each transform's JSON
Schema (a strict pydantic model: unknown keys, wrong types and out-of-range values are
refused). There is no eval, no expression, no template, no regex and no user code here:
every parser below walks characters by hand. Arithmetic is Decimal only, in a context that
traps inexact results, so nothing is ever rounded silently or passed through a float.

A failing row raises `ValueFailure` with a reason code. Reason codes never carry the value.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, localcontext
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.core.text import single_line
from app.governance.constants import FieldType
from app.mapping.constants import (
    MAX_MAP_VALUES,
    MAX_PARAM_TEXT,
    SCALE_MAX_POWER,
    NullPolicy,
    TransformCode,
)

# --- failures -------------------------------------------------------------------------------


class ValueFailure(Exception):
    """One cell could not be transformed or does not fit its target. `reason` is a code;
    the value itself is never part of the exception."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TransformError(Exception):
    """A transform definition is not allowed: an unknown code or invalid params (422)."""

    def __init__(self, code: str, message: str, details: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


# --- parameters (their JSON Schemas are the D-19 parameter contracts) -----------------------

ParamText = Annotated[
    StrictStr, Field(min_length=1, max_length=MAX_PARAM_TEXT), AfterValidator(single_line)
]
MapKey = Annotated[StrictStr, Field(max_length=MAX_PARAM_TEXT), AfterValidator(single_line)]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoParams(_Params):
    """COPY, TRIM, UPPER and LOWER take no parameters."""


class DateFormat(StrEnum):
    """PARSE_DATE formats: fixed layouts, parsed by position (no regex, no strptime)."""

    ISO = "YYYY-MM-DD"
    YMD_SLASH = "YYYY/MM/DD"
    DMY_SLASH = "DD/MM/YYYY"
    MDY_SLASH = "MM/DD/YYYY"
    DMY_DOT = "DD.MM.YYYY"
    DMY_DASH = "DD-MM-YYYY"
    COMPACT = "YYYYMMDD"


class ParseDecimalParams(_Params):
    decimal_sep: Literal[".", ","] = Field(default=".", description="Decimal separator.")
    thousands_sep: Literal["", ",", ".", " ", "'"] = Field(
        default="", description="Digit-group separator; groups of three digits only."
    )

    @model_validator(mode="after")
    def _distinct(self) -> "ParseDecimalParams":
        if self.decimal_sep == self.thousands_sep:
            raise ValueError("decimal_sep and thousands_sep must differ")
        return self


class ScaleParams(_Params):
    power_of_ten: StrictInt = Field(
        ge=-SCALE_MAX_POWER,
        le=SCALE_MAX_POWER,
        description="The plain decimal is multiplied by 10^power_of_ten, exactly.",
    )


class ParseDateParams(_Params):
    format: DateFormat


class ParseBooleanParams(_Params):
    true_values: list[ParamText] = Field(min_length=1, max_length=20)
    false_values: list[ParamText] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _disjoint(self) -> "ParseBooleanParams":
        true = {v.strip().casefold() for v in self.true_values}
        false = {v.strip().casefold() for v in self.false_values}
        if true & false:
            raise ValueError("true_values and false_values overlap (compared case-insensitively)")
        return self


class OnMissing(StrEnum):
    ERROR = "ERROR"  # the row fails this rule
    NULL = "NULL"  # the target stays empty
    KEEP = "KEEP"  # the source text passes through unchanged


class MapValuesParams(_Params):
    table: dict[MapKey, ParamText] = Field(
        min_length=1,
        max_length=MAX_MAP_VALUES,
        description="Exact source text -> target text (case- and space-sensitive).",
    )
    on_missing: OnMissing = OnMissing.ERROR


class ValueParams(_Params):
    """DEFAULT(value) and CONSTANT(value)."""

    value: ParamText


class Output(StrEnum):
    """What a transform yields, before it is fitted to the target field's type."""

    TEXT = "TEXT"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"


Value = str | Decimal | date | bool | None


# --- strict hand-written parsers --------------------------------------------------------------

_DIGITS = frozenset("0123456789")
MAX_DIGITS = 100  # digits in one decimal value; more is refused, never rounded
_EXACT = Context(prec=MAX_DIGITS * 3, traps=[Inexact, InvalidOperation, Overflow])


def _all_digits(text: str) -> bool:
    return bool(text) and all(c in _DIGITS for c in text)


def plain_decimal(text: str) -> Decimal:
    """`[+-]digits[.digits]`: ASCII digits only, no exponent, no spaces, no NaN/Infinity,
    no underscores (all of which `Decimal()` itself would accept)."""
    body = text[1:] if text[:1] in ("+", "-") else text
    whole, dot, fraction = body.partition(".")
    if not _all_digits(whole) or (dot and not _all_digits(fraction)):
        raise ValueFailure("not_a_decimal")
    if len(whole) + len(fraction) > MAX_DIGITS:
        raise ValueFailure("too_many_digits")
    return Decimal(text)  # exact: constructing from text never rounds


def _ungroup(whole: str, sep: str) -> str:
    """Integer digits with `sep` between groups of three (the first group 1-3 digits)."""
    if not sep or sep not in whole:
        return whole
    groups = whole.split(sep)
    if not 1 <= len(groups[0]) <= 3 or any(len(g) != 3 for g in groups[1:]):
        raise ValueFailure("not_a_decimal")
    return "".join(groups)


def _parse_decimal(value: str, p: ParseDecimalParams) -> Decimal:
    text = value.strip()
    sign = text[:1] if text[:1] in ("+", "-") else ""
    body = text[len(sign) :]
    whole, sep, fraction = body.partition(p.decimal_sep)
    if p.thousands_sep and p.thousands_sep in fraction:
        raise ValueFailure("not_a_decimal")
    whole = _ungroup(whole, p.thousands_sep)
    return plain_decimal(sign + whole + ("." + fraction if sep else ""))


def _scale(value: str, p: ScaleParams) -> Decimal:
    number = plain_decimal(value.strip())
    with localcontext(_EXACT):
        return number.scaleb(p.power_of_ten)


# (year, month, day) slices and the separator positions each format requires.
_LAYOUTS: dict[DateFormat, tuple[int, slice, slice, slice, dict[int, str]]] = {
    DateFormat.ISO: (10, slice(0, 4), slice(5, 7), slice(8, 10), {4: "-", 7: "-"}),
    DateFormat.YMD_SLASH: (10, slice(0, 4), slice(5, 7), slice(8, 10), {4: "/", 7: "/"}),
    DateFormat.DMY_SLASH: (10, slice(6, 10), slice(3, 5), slice(0, 2), {2: "/", 5: "/"}),
    DateFormat.MDY_SLASH: (10, slice(6, 10), slice(0, 2), slice(3, 5), {2: "/", 5: "/"}),
    DateFormat.DMY_DOT: (10, slice(6, 10), slice(3, 5), slice(0, 2), {2: ".", 5: "."}),
    DateFormat.DMY_DASH: (10, slice(6, 10), slice(3, 5), slice(0, 2), {2: "-", 5: "-"}),
    DateFormat.COMPACT: (8, slice(0, 4), slice(4, 6), slice(6, 8), {}),
}


def _parse_date(value: str, p: ParseDateParams) -> date:
    text = value.strip()
    length, ys, ms, ds, seps = _LAYOUTS[p.format]
    if len(text) != length or any(text[i] != c for i, c in seps.items()):
        raise ValueFailure("invalid_date")
    parts = (text[ys], text[ms], text[ds])
    if not all(_all_digits(part) for part in parts):
        raise ValueFailure("invalid_date")
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        raise ValueFailure("invalid_date") from None  # e.g. 30 February


def _parse_boolean(value: str, p: ParseBooleanParams) -> bool:
    key = value.strip().casefold()
    if key in {v.strip().casefold() for v in p.true_values}:
        return True
    if key in {v.strip().casefold() for v in p.false_values}:
        return False
    raise ValueFailure("invalid_boolean")


def _map_values(value: str, p: MapValuesParams) -> str | None:
    if value in p.table:
        return p.table[value]
    if p.on_missing == OnMissing.KEEP:
        return value
    if p.on_missing == OnMissing.NULL:
        return None
    raise ValueFailure("unmapped_value")


def _is_empty(value: str | None) -> bool:
    return value is None or not value.strip()


# --- the allowlist ------------------------------------------------------------------------------


@dataclass(frozen=True)
class TransformSpec:
    params: type[_Params]
    output: Output
    fn: Callable[[Any, Any], Value]
    # DEFAULT and CONSTANT decide about empty input themselves; the others never see it
    # (the rule's null policy does).
    takes_empty: bool = False


TRANSFORMS: dict[TransformCode, TransformSpec] = {
    TransformCode.COPY: TransformSpec(NoParams, Output.TEXT, lambda v, p: v),
    TransformCode.TRIM: TransformSpec(NoParams, Output.TEXT, lambda v, p: v.strip()),
    TransformCode.UPPER: TransformSpec(NoParams, Output.TEXT, lambda v, p: v.upper()),
    TransformCode.LOWER: TransformSpec(NoParams, Output.TEXT, lambda v, p: v.lower()),
    TransformCode.PARSE_DECIMAL: TransformSpec(ParseDecimalParams, Output.DECIMAL, _parse_decimal),
    TransformCode.SCALE: TransformSpec(ScaleParams, Output.DECIMAL, _scale),
    TransformCode.PARSE_DATE: TransformSpec(ParseDateParams, Output.DATE, _parse_date),
    TransformCode.PARSE_BOOLEAN: TransformSpec(ParseBooleanParams, Output.BOOLEAN, _parse_boolean),
    TransformCode.MAP_VALUES: TransformSpec(MapValuesParams, Output.TEXT, _map_values),
    TransformCode.DEFAULT: TransformSpec(
        ValueParams, Output.TEXT, lambda v, p: p.value if _is_empty(v) else v, takes_empty=True
    ),
    TransformCode.CONSTANT: TransformSpec(
        ValueParams, Output.TEXT, lambda v, p: p.value, takes_empty=True
    ),
}


@dataclass(frozen=True)
class Transform:
    """A validated transform: its code, params as stored and the callable."""

    code: TransformCode
    params: dict[str, Any]
    spec: TransformSpec
    parsed: _Params

    @property
    def output(self) -> Output:
        return self.spec.output

    def __call__(self, value: str | None) -> Value:
        if value is None and not self.spec.takes_empty:
            return None
        return self.spec.fn(value, self.parsed)


def compile_transform(code: str, params: dict[str, Any] | None) -> Transform:
    """The transform `code` with `params`, validated, or TransformError (422)."""
    try:
        known = TransformCode(code)
    except ValueError:
        raise TransformError(
            "unknown_transform",
            "This transform is not on the allowlist (D-19).",
            [],  # the unknown code is not echoed (no raw request input in errors)
        ) from None
    spec = TRANSFORMS[known]
    try:
        # JSON mode, strict: exactly what the parameter's JSON Schema allows (no coercion
        # of "2" to 2, enums by value).
        parsed = spec.params.model_validate_json(json.dumps(params or {}), strict=True)
    except ValidationError as exc:
        raise TransformError(
            "invalid_transform_params",
            f"Invalid parameters for {known}.",
            [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors(include_input=False)
            ],
        ) from None
    return Transform(known, parsed.model_dump(mode="json"), spec, parsed)


# --- fitting a value to its target field ------------------------------------------------------


@dataclass(frozen=True)
class TargetField:
    name: str
    data_type: FieldType
    required: bool
    precision: int | None = None
    scale: int | None = None
    classification: str = "restricted"


# Which transform outputs a target type accepts (TEXT is parsed strictly per row).
COMPATIBLE: dict[Output, frozenset[FieldType]] = {
    Output.TEXT: frozenset(FieldType),
    Output.DECIMAL: frozenset({FieldType.DECIMAL, FieldType.INTEGER, FieldType.STRING}),
    Output.DATE: frozenset({FieldType.DATE, FieldType.STRING}),
    Output.BOOLEAN: frozenset({FieldType.BOOLEAN, FieldType.STRING}),
}


def _decimal_text(number: Decimal) -> str:
    return format(number, "f")


def _whole_digits(number: Decimal) -> int:
    """Digits before the decimal point (0 for zero and for |x| < 1)."""
    return max(number.adjusted() + 1, 0) if number else 0


def _fit_decimal(value: Value, target: TargetField) -> str:
    if isinstance(value, bool) or isinstance(value, date):
        raise ValueFailure("not_a_decimal")
    number = value if isinstance(value, Decimal) else plain_decimal(str(value).strip())
    if target.scale is not None:
        try:
            with localcontext(_EXACT):
                number = number.quantize(Decimal(1).scaleb(-target.scale))
        except Inexact:
            # Not representable at the field's scale: refused, never rounded.
            raise ValueFailure("scale_exceeded") from None
    if target.precision is not None and _whole_digits(number) > target.precision - (
        target.scale or 0
    ):
        raise ValueFailure("precision_exceeded")
    return _decimal_text(number)


def _fit_integer(value: Value, target: TargetField) -> str:
    if isinstance(value, bool) or isinstance(value, date):
        raise ValueFailure("not_an_integer")
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueFailure("not_an_integer")
        number = value.quantize(Decimal(1), context=_EXACT)
    else:
        text = str(value).strip()
        body = text[1:] if text[:1] in ("+", "-") else text
        if not _all_digits(body):
            raise ValueFailure("not_an_integer")
        number = plain_decimal(text)
    if target.precision is not None and len(number.as_tuple().digits) > target.precision:
        raise ValueFailure("precision_exceeded")
    return _decimal_text(number)


def _fit_date(value: Value) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _parse_date(value, ParseDateParams(format=DateFormat.ISO)).isoformat()
    raise ValueFailure("not_a_date")


def _fit_timestamp(value: Value) -> str:
    if not isinstance(value, str):
        raise ValueFailure("not_a_timestamp")
    text = value.strip()
    # ISO 8601 date and time: the date part is checked by hand, the rest by fromisoformat.
    if len(text) < 16 or text[10] not in ("T", " "):
        raise ValueFailure("not_a_timestamp")
    _parse_date(text[:10], ParseDateParams(format=DateFormat.ISO))
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueFailure("not_a_timestamp") from None


def _fit_boolean(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.strip() in ("true", "false"):
        return value.strip()
    raise ValueFailure("not_a_boolean")


def _fit_string(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, date):
        return value.isoformat()
    assert isinstance(value, str)
    return value


def fit(value: Value, target: TargetField) -> str:
    """The transformed value as the canonical text of its target type (D-22: decimals as
    text at the field's scale, dates ISO-8601), or ValueFailure."""
    match target.data_type:
        case FieldType.DECIMAL:
            return _fit_decimal(value, target)
        case FieldType.INTEGER:
            return _fit_integer(value, target)
        case FieldType.DATE:
            return _fit_date(value)
        case FieldType.TIMESTAMP:
            return _fit_timestamp(value)
        case FieldType.BOOLEAN:
            return _fit_boolean(value)
        case _:
            return _fit_string(value)


# --- one rule over one cell --------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """`text` is the canonical target text (None: empty); `reason` is set on failure."""

    text: str | None = None
    reason: str | None = None

    @property
    def failed(self) -> bool:
        return self.reason is not None


def apply(
    transform: Transform,
    raw: str | None,
    null_policy: str,
    default_value: str | None,
    target: TargetField | None,
) -> Outcome:
    """A rule applied to one cell: null policy, transform, then the fit to the target."""
    value: str | None = raw
    if _is_empty(raw) and not transform.spec.takes_empty:
        if null_policy == NullPolicy.REJECT_NULL:
            return Outcome(reason="null_value")
        if null_policy == NullPolicy.USE_DEFAULT and default_value is not None:
            value = default_value
        else:
            value = None
    try:
        result = transform(value)
        if result is None or (isinstance(result, str) and not result.strip()):
            if target is not None and target.required:
                return Outcome(reason="required_value_missing")
            return Outcome()
        return Outcome(text=fit(result, target) if target is not None else _fit_string(result))
    except ValueFailure as failure:
        return Outcome(reason=failure.reason)


# The engine's parts, for the test that proves it holds no float and no regex (BE-10.2).
ENGINE_PARTS = (
    "plain_decimal",
    "_ungroup",
    "_parse_decimal",
    "_scale",
    "_parse_date",
    "_parse_boolean",
    "_map_values",
    "compile_transform",
    "_whole_digits",
    "_fit_decimal",
    "_fit_integer",
    "_fit_date",
    "_fit_timestamp",
    "_fit_boolean",
    "_fit_string",
    "fit",
    "apply",
)
