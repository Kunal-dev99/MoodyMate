"""JSON adapter (D-16 §4 as amended by D-24 §1).

UTF-8 (a BOM is allowed). The document is either a top-level array of flat objects, or
exactly `{"marker": "<starts with ILLUSTRATIVE / SYNTHETIC>", "records": [flat objects]}`
with no other keys (the marker is recorded). Anything else is refused.

Numbers are parsed with `parse_float=Decimal`, so their text survives exactly; NaN and
Infinity are refused. A nested value (object or array) is refused, and so is a key repeated
within one object (it would silently overwrite a value). `null` keeps the key out of the
values and is flagged. Columns are the keys in order of first appearance; the locator is
the array index.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any, NoReturn

from app.ingestion.adapters.base import (
    AdapterError,
    AdapterOptions,
    Draft,
    Limits,
    RowFlag,
    RowStream,
    SourceAdapter,
    check_cell,
    decode_utf8,
    looks_like_json,
    looks_textual,
    too_many_columns,
    too_many_rows,
)
from app.ingestion.constants import (
    SYNTHETIC_MARKER,
    RowFlagCode,
    SourceFormat,
    UploadRefusal,
)


class _Object(dict[str, Any]):
    """A parsed JSON object that remembers whether a key was repeated."""

    duplicate = False


def _object(pairs: list[tuple[str, Any]]) -> _Object:
    obj = _Object(pairs)
    obj.duplicate = len(obj) != len(pairs)
    return obj


def _constant(_: str) -> NoReturn:
    raise ValueError("NaN and Infinity are not JSON numbers")


def _shape(message: str, details: dict[str, str | int] | None = None) -> AdapterError:
    return AdapterError(UploadRefusal.JSON_INVALID_SHAPE, message, details)


class JsonAdapter(SourceAdapter):
    format = SourceFormat.JSON
    media_type = "application/json"

    def detect(self, data: bytes) -> bool:
        return looks_textual(data) and looks_like_json(data)

    def iter_rows(self, data: bytes, options: AdapterOptions, limits: Limits) -> RowStream:
        text = decode_utf8(data)
        try:
            doc = json.loads(
                text, parse_float=Decimal, parse_constant=_constant, object_pairs_hook=_object
            )
        except json.JSONDecodeError as exc:
            raise AdapterError(
                UploadRefusal.JSON_MALFORMED,
                "The file is not valid JSON.",
                {"line": exc.lineno, "column": exc.colno},
            ) from None
        except (ValueError, RecursionError):
            raise AdapterError(
                UploadRefusal.JSON_MALFORMED, "The file is not valid JSON."
            ) from None
        records, marker = _records(doc)
        if len(records) > limits.max_rows:
            raise too_many_rows(limits)
        columns = _columns(records, limits)
        return RowStream(columns, marker, _drafts(records, columns, limits))


def _records(doc: Any) -> tuple[list[Any], bool]:
    if isinstance(doc, list):
        return doc, False
    if (
        isinstance(doc, _Object)
        and not doc.duplicate
        and set(doc) == {"marker", "records"}
        and isinstance(doc["marker"], str)
        and doc["marker"].startswith(SYNTHETIC_MARKER)
        and isinstance(doc["records"], list)
    ):
        return doc["records"], True
    raise _shape(
        "Expected a top-level array of objects, or exactly "
        f'{{"marker": "{SYNTHETIC_MARKER} ...", "records": [...]}}.'
    )


def _columns(records: list[Any], limits: Limits) -> list[str]:
    columns: dict[str, None] = {}
    for i, record in enumerate(records):
        if not isinstance(record, _Object):
            raise _shape("Every record must be an object.", {"index": i})
        if record.duplicate:
            raise AdapterError(
                UploadRefusal.INVALID_HEADER,
                "A record repeats a key.",
                {"reason": "duplicate", "index": i},
            )
        for key in record:
            if key in columns:
                continue
            if len(columns) >= limits.max_columns:
                raise too_many_columns(limits, {"index": i})
            check_cell(key, {"index": i, "column": len(columns) + 1}, limits)
            if not key.strip():
                raise AdapterError(
                    UploadRefusal.INVALID_HEADER,
                    "Each key needs a non-blank name.",
                    {"reason": "blank", "index": i},
                )
            columns[key] = None
    return list(columns)


def _text(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal | int):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def _drafts(records: list[Any], columns: list[str], limits: Limits) -> Iterator[Draft]:
    for i, record in enumerate(records):
        values: dict[str, str] = {}
        flags: list[RowFlag] = []
        missing = False
        for n, column in enumerate(columns, start=1):
            where: dict[str, str | int] = {"index": i, "column": n}
            if column not in record:
                if not missing:
                    flags.append(RowFlag(RowFlagCode.MISSING_CELLS, n))
                    missing = True
                continue
            value = record[column]
            if value is None:
                flags.append(RowFlag(RowFlagCode.NULL_VALUE, n))
                continue
            text = _text(value)
            if text is None:
                raise AdapterError(
                    UploadRefusal.JSON_NESTED_VALUE,
                    "Values must be scalars; nested objects and arrays are refused.",
                    where,
                )
            check_cell(text, where, limits)
            values[column] = text
        yield Draft({"index": i}, values, [], flags)
