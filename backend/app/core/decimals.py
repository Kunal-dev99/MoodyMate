"""core.decimals

The Decimal policy of calculation modules (D-31 §2). WS2 keeps its own exact contexts; this
is the shared one for WS3 calculations.

- `exact_context()`: a context wide enough for any D-16 value that traps Inexact, Rounded
  and InvalidOperation (and division by zero), so an operation that would round raises
  instead (fail closed).
- `DeclaredRounding` and `quantize`: the only way a module rounds. The module declares the
  exponent and the mode (default ROUND_HALF_EVEN, PROVISIONAL), and `quantize` applies it.
- `decimal_text`: exact decimal text, never exponent form (`format(d, "f")`).
- `parse_decimal_text` (BE-29.3): the one parser of decimal text, over one regex family with
  named profiles. It returns the exact `Decimal` of the text or raises `DecimalTextError`
  with a value-free code; it refuses over-scale input and never rounds.
  - `STRICT` (API and rules): `-?(0|[1-9][0-9]*)(.[0-9]+)?`, at most 30 integer digits and
    18 places (D-32 §1);
  - `UNSIGNED` (scenario parameters and module parameters): `[0-9]+(.[0-9]+)?`;
  - `SOURCE` (ingestion leniency as today): `[+-]?[0-9]+(.[0-9]+)?`, leading zeros kept.
  ASCII digits only, no exponent, grouping, spaces, NaN or Infinity, and `fullmatch` only
  (so a trailing newline is never accepted).
"""

import re
from dataclasses import dataclass, field
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
)
from enum import StrEnum

# Far beyond the widest NUMERIC a D-16 cell can hold (4,000 characters), so exact sums of up
# to IMPORT_MAX_ROWS of them never round.
EXACT_PRECISION = 100_000


def exact_context() -> Context:
    """A fresh exact context: any rounding or invalid operation raises."""
    return Context(
        prec=EXACT_PRECISION,
        rounding=ROUND_HALF_EVEN,
        traps=[Inexact, Rounded, InvalidOperation, DivisionByZero, Overflow],
    )


@dataclass(frozen=True)
class DeclaredRounding:
    """An explicit rounding a module declares: the result exponent (e.g. Decimal("0.0001")
    for 4 dp) and the mode."""

    exponent: Decimal
    rounding: str = ROUND_HALF_EVEN


def quantize(value: Decimal, declared: DeclaredRounding) -> Decimal:
    """Round `value` as declared. The only rounding allowed under the exact policy."""
    context = Context(prec=EXACT_PRECISION, rounding=declared.rounding, traps=[InvalidOperation])
    return value.quantize(declared.exponent, context=context)


def decimal_text(value: Decimal) -> str:
    """Exact decimal text, never in exponent form."""
    return format(value, "f")


# --- decimal text (BE-29.3) --------------------------------------------------------------------


class DecimalTextProblem(StrEnum):
    NOT_DECIMAL_TEXT = "not_decimal_text"  # outside the profile's grammar
    TOO_MANY_DIGITS = "too_many_digits"  # more digits than the profile or caller allows
    SCALE_EXCEEDED = "scale_exceeded"  # more places than allowed: refused, never rounded


class DecimalTextError(ValueError):
    """Decimal text refused. Carries a code only: never the value (it may be classified)."""

    def __init__(self, code: DecimalTextProblem) -> None:
        super().__init__(code.value)
        self.code = code


_SIGNS = {"": "", "-": "-?", "+-": "[+-]?"}


@dataclass(frozen=True)
class DecimalProfile:
    """One member of the decimal-text regex family."""

    name: str
    signs: str  # "", "-" or "+-": the signs the text may start with
    leading_zeros: bool  # False: the integer part is 0 or starts with 1-9
    max_integer_digits: int | None = None
    max_scale: int | None = None
    pattern: str = field(init=False)  # anchored; also usable as a PostgreSQL regex
    _regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        integer = "[0-9]+" if self.leading_zeros else "(0|[1-9][0-9]*)"
        pattern = f"^{_SIGNS[self.signs]}{integer}(\\.[0-9]+)?$"
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(self, "_regex", re.compile(pattern))

    def fullmatch(self, text: str) -> bool:
        return self._regex.fullmatch(text) is not None


STRICT = DecimalProfile("STRICT", "-", leading_zeros=False, max_integer_digits=30, max_scale=18)
UNSIGNED = DecimalProfile("UNSIGNED", "", leading_zeros=True)
SOURCE = DecimalProfile("SOURCE", "+-", leading_zeros=True)


def _tighter(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else min(a, b)


def parse_decimal_text(
    text: object,
    profile: DecimalProfile = STRICT,
    *,
    max_scale: int | None = None,
    max_digits: int | None = None,
) -> Decimal:
    """The exact Decimal of `text` under `profile`. `max_scale` (places) and `max_digits`
    (all digits, leading zeros included) tighten the profile's own limits. Checked in this
    order: grammar, digits, scale. Never rounds."""
    if not isinstance(text, str) or not profile.fullmatch(text):
        raise DecimalTextError(DecimalTextProblem.NOT_DECIMAL_TEXT)
    whole, _, fraction = text.lstrip("+-").partition(".")
    if (profile.max_integer_digits is not None and len(whole) > profile.max_integer_digits) or (
        max_digits is not None and len(whole) + len(fraction) > max_digits
    ):
        raise DecimalTextError(DecimalTextProblem.TOO_MANY_DIGITS)
    scale = _tighter(profile.max_scale, max_scale)
    if scale is not None and len(fraction) > scale:
        raise DecimalTextError(DecimalTextProblem.SCALE_EXCEEDED)
    return Decimal(text)  # exact: constructing from text never rounds
