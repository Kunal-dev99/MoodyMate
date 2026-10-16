"""CSV adapter (D-16 §2, D-24 §2).

UTF-8 only (a BOM is allowed), the delimiter from the source profile, a header row
required, every value a string. Line 1 is skipped only when it starts with exactly
`# ILLUSTRATIVE / SYNTHETIC`, and the import records that the marker was present; no other
line is ever skipped. Rows shorter or longer than the header are kept and flagged, with no
value invented and no cell dropped. The locator is the physical line a record starts on.
"""

import csv
import io
from _csv import Reader
from collections.abc import Iterator

from app.ingestion.adapters.base import (
    AdapterError,
    AdapterOptions,
    Draft,
    Limits,
    Locator,
    RowStream,
    SourceAdapter,
    check_header,
    decode_utf8,
    from_cells,
    looks_like_json,
    looks_textual,
)
from app.ingestion.constants import CSV_MARKER_LINE, SourceFormat, UploadRefusal

# csv.Error text for a field beyond the module's own limit (131072 characters): larger than
# any D-16 cell, so it is reported as cell_too_long.
_FIELD_LIMIT = "field larger than field limit"


class CsvAdapter(SourceAdapter):
    format = SourceFormat.CSV
    media_type = "text/csv"

    def detect(self, data: bytes) -> bool:
        return looks_textual(data) and not looks_like_json(data)

    def iter_rows(self, data: bytes, options: AdapterOptions, limits: Limits) -> RowStream:
        text = decode_utf8(data)
        if "\x00" in text:
            line = text.count("\n", 0, text.index("\x00")) + 1
            raise AdapterError(
                UploadRefusal.INVALID_CHARACTER,
                "The file contains a NUL character.",
                {"line": line},
            )
        first_end = text.find("\n")
        first = text if first_end < 0 else text[:first_end]
        marker = first.startswith(CSV_MARKER_LINE)
        offset = 0
        if marker:
            text, offset = ("" if first_end < 0 else text[first_end + 1 :]), 1
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=options.delimiter, strict=True)
        header = _next(reader, offset)
        if header is None or not header[1]:
            raise AdapterError(UploadRefusal.MISSING_HEADER, "The file has no header row.")
        columns = check_header(header[1], {"line": header[0]}, limits)
        return RowStream(columns, marker, _drafts(reader, columns, offset, limits))


def _next(reader: Reader, offset: int) -> tuple[int, list[str]] | None:
    """The next record and the physical line it starts on."""
    start = reader.line_num + 1 + offset
    try:
        return start, next(reader)
    except StopIteration:
        return None
    except csv.Error as exc:
        if _FIELD_LIMIT in str(exc):
            raise AdapterError(
                UploadRefusal.CELL_TOO_LONG, "A cell is too long.", {"line": start}
            ) from None
        raise AdapterError(
            UploadRefusal.CSV_MALFORMED,
            "The CSV is malformed (for example, a stray quote).",
            {"line": reader.line_num + offset},
        ) from None


def _drafts(reader: Reader, columns: list[str], offset: int, limits: Limits) -> Iterator[Draft]:
    while (record := _next(reader, offset)) is not None:
        line, cells = record
        locator: Locator = {"line": line}
        if not cells:
            yield Draft(locator, {}, blank=True)
            continue
        yield from_cells(columns, cells, locator, limits)
