"""A test-only, in-memory feed adapter (BE-13.1): proof that a new kind of source plugs in
behind `SourceAdapter` without changing the core. It is wired in only from `app/tests`
(never registered in `app.ingestion.adapters.ADAPTERS`) and runs through the same contract
suite as CSV, XLSX and JSON (`test_adapters.py`). See docs/ADAPTERS.md.

A feed delivers messages, not a file. The fake's "frame" is the bytes of one in-memory
delivery:

    FAKEFEED/1[ <marker>]          the frame header (the marker is optional, D-24)
    ["col_a", "col_b", ...]        the column names, a JSON array of strings
    ["v1", 2.50, true, ...]        one JSON array of scalars per message

A message's locator is its offset in the delivery (`{"offset": n}`, 0-based). Numbers are
parsed with `parse_float=Decimal`, so their text survives exactly; no value is ever a float.
Messages shorter or longer than the header are kept and flagged by the shared helpers.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from app.ingestion.adapters.base import (
    AdapterError,
    AdapterOptions,
    Draft,
    Limits,
    RowStream,
    SourceAdapter,
    check_header,
    decode_utf8,
    from_cells,
)
from app.ingestion.constants import SYNTHETIC_MARKER, SourceFormat, UploadRefusal

MAGIC = "FAKEFEED/1"


def _scalar(value: Any) -> str:
    """JSON text for one value; a Decimal is written as a JSON number with its exact text."""
    return str(value) if isinstance(value, Decimal) else json.dumps(value)


def frame(columns: list[str], messages: list[list[Any]], marker: bool = False) -> bytes:
    """One delivery of the fake feed, as bytes."""
    head = f"{MAGIC} {SYNTHETIC_MARKER}" if marker else MAGIC
    lines = [head, json.dumps(columns)]
    lines += ["[" + ", ".join(_scalar(v) for v in m) + "]" for m in messages]
    return ("\n".join(lines) + "\n").encode()


def _parse(line: str, where: dict[str, str | int]) -> list[Any]:
    try:
        cells = json.loads(line, parse_float=Decimal)
    except ValueError:
        raise AdapterError(
            UploadRefusal.JSON_MALFORMED, "A feed message is not valid JSON.", where
        ) from None
    if not isinstance(cells, list):
        raise AdapterError(
            UploadRefusal.JSON_INVALID_SHAPE, "A feed message must be an array.", where
        )
    return cells


def _text(value: Any, where: dict[str, str | int]) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal | int):
        return str(value)
    if isinstance(value, str):
        return value
    raise AdapterError(
        UploadRefusal.JSON_NESTED_VALUE, "Feed values must be scalars and not null.", where
    )


class FakeFeedAdapter(SourceAdapter):
    # SourceFormat has no FEED member: a real feed adapter adds one (with the CHECK
    # constraint on source_profile.format) when it is registered. The fake's records are
    # JSON, so it reports JSON; it is never put in ADAPTERS.
    format = SourceFormat.JSON
    media_type = "application/x-moodymate-fake-feed"

    def detect(self, data: bytes) -> bool:
        return data.startswith(MAGIC.encode())

    def iter_rows(self, data: bytes, options: AdapterOptions, limits: Limits) -> RowStream:
        lines = decode_utf8(data).split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        head = lines[0] if lines else ""
        if head not in (MAGIC, f"{MAGIC} {SYNTHETIC_MARKER}"):
            raise AdapterError(UploadRefusal.UNSUPPORTED_FILE_TYPE, "Not a fake feed frame.")
        if len(lines) < 2:
            raise AdapterError(UploadRefusal.MISSING_HEADER, "The feed sent no columns.")
        names = _parse(lines[1], {"offset": -1})
        if not all(isinstance(n, str) for n in names):
            raise AdapterError(
                UploadRefusal.INVALID_HEADER, "Column names must be strings.", {"reason": "type"}
            )
        columns = check_header(names, {"offset": -1}, limits)
        return RowStream(columns, head != MAGIC, self._drafts(lines[2:], columns, limits))

    @staticmethod
    def _drafts(lines: list[str], columns: list[str], limits: Limits) -> Iterator[Draft]:
        for offset, line in enumerate(lines):
            where: dict[str, str | int] = {"offset": offset}
            if not line.strip():
                yield Draft(where, {}, blank=True)
                continue
            cells = [
                _text(v, {**where, "column": i}) for i, v in enumerate(_parse(line, where), start=1)
            ]
            yield from_cells(columns, cells, where, limits)
