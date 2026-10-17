"""core.decimals

The Decimal policy of calculation modules (D-31 §2). WS2 keeps its own exact contexts; this
is the shared one for WS3 calculations.

- `exact_context()`: a context wide enough for any D-16 value that traps Inexact, Rounded
  and InvalidOperation (and division by zero), so an operation that would round raises
  instead (fail closed).
- `DeclaredRounding` and `quantize`: the only way a module rounds. The module declares the
  exponent and the mode (default ROUND_HALF_EVEN, PROVISIONAL), and `quantize` applies it.
- `decimal_text`: exact decimal text, never exponent form (`format(d, "f")`).
"""

from dataclasses import dataclass
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
