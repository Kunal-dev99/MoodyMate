"""core.text

Shared request-text types (Phase 7 review D8). Free text is stripped, must keep at least
one character, and may not hold control characters: Postgres refuses NUL in text and JSONB
(a 500, not a 422), and other controls hide in logs, files and UIs. Bound the length per
field with `Field(max_length=...)`.

- `SafeText`: one line; no control character at all (C0 incl. NUL, DEL, C1).
- `SafeMultilineText`: as above, but line breaks and tabs are kept (textarea fields).
"""

import unicodedata
from typing import Annotated

from pydantic import AfterValidator, StringConstraints

_MULTILINE_ALLOWED = frozenset("\n\r\t")


def _refuse_controls(value: str, allowed: frozenset[str]) -> str:
    bad = sorted({c for c in value if unicodedata.category(c) == "Cc" and c not in allowed})
    if bad:
        shown = ", ".join(f"U+{ord(c):04X}" for c in bad)
        raise ValueError(f"control characters are not allowed ({shown})")
    return value


def single_line(value: str) -> str:
    return _refuse_controls(value, frozenset())


def multiline(value: str) -> str:
    return _refuse_controls(value, _MULTILINE_ALLOWED)


SafeText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(single_line)
]
SafeMultilineText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(multiline)
]
