"""The source adapter contract (BE-8.3, DI-02, D-16, D-17).

A `SourceAdapter` knows one file format. It never depends on another format and never
touches the database or the artifact store:

* `detect(data)`: does the content (never the file name) look like this format?
* `iter_rows(data, options, limits)`: a `RowStream`: the header columns, whether the file
  carries the ILLUSTRATIVE / SYNTHETIC marker (D-24), and a lazy iterator of `Draft` rows,
  each a locator plus `dict[str, str]` of values. Values are always strings, never floats.

`collect` turns the drafts into numbered `SourceRow`s and applies the row limit. Every
refusal is an `AdapterError` with its own code (`UploadRefusal`) and details that name
positions (line, row, index, column number), never a cell value or a column name.
"""

import codecs
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import ClassVar

from app.core.config import IMPORT_HARD_LIMITS
from app.ingestion.constants import RowFlagCode, SourceFormat, UploadRefusal

Locator = dict[str, str | int]
Details = dict[str, str | int]

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls (and other OLE2 files)
ZIP_MAGIC = b"PK\x03\x04"
_SNIFF = 8192


@dataclass(frozen=True)
class Limits:
    """D-16 §1 bounds. The defaults are the hard ceilings; settings may only lower them."""

    max_bytes: int = IMPORT_HARD_LIMITS["IMPORT_MAX_BYTES"]
    max_rows: int = IMPORT_HARD_LIMITS["IMPORT_MAX_ROWS"]
    max_columns: int = IMPORT_HARD_LIMITS["IMPORT_MAX_COLUMNS"]
    max_cell_chars: int = IMPORT_HARD_LIMITS["IMPORT_MAX_CELL_CHARS"]
    max_xlsx_uncompressed_bytes: int = IMPORT_HARD_LIMITS["IMPORT_MAX_XLSX_UNCOMPRESSED_BYTES"]
    max_xlsx_ratio: int = IMPORT_HARD_LIMITS["IMPORT_MAX_XLSX_RATIO"]


HARD_LIMITS = Limits()


@dataclass(frozen=True)
class AdapterOptions:
    """From the source profile: the CSV delimiter and the XLSX sheet to read."""

    delimiter: str = ","
    sheet_name: str | None = None


@dataclass(frozen=True)
class RowFlag:
    code: RowFlagCode
    column: int | None = None  # 1-based position in the row; never a name or a value


@dataclass
class Draft:
    """A row as an adapter read it, before numbering."""

    locator: Locator
    values: dict[str, str]
    extras: list[str] = field(default_factory=list)
    flags: list[RowFlag] = field(default_factory=list)
    blank: bool = False


@dataclass(frozen=True)
class SourceRow:
    ordinal: int  # 1-based position among the data rows
    locator: Locator  # {"line": n} | {"sheet": name, "row": n} | {"index": i}
    values: dict[str, str]  # column -> cell text, for the cells the row has
    extras: list[str]  # cells beyond the header (never dropped)
    flags: list[RowFlag]


@dataclass
class RowStream:
    columns: list[str]
    marker_present: bool
    drafts: Iterator[Draft]
    sheet_name: str | None = None


class AdapterError(Exception):
    """A refused file. `details` hold positions and limits only."""

    def __init__(
        self,
        code: UploadRefusal,
        message: str,
        details: Details | None = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code


class SourceAdapter(ABC):
    format: ClassVar[SourceFormat]
    media_type: ClassVar[str]

    @abstractmethod
    def detect(self, data: bytes) -> bool:
        """True when the content is this format (the file name is never consulted)."""

    @abstractmethod
    def iter_rows(self, data: bytes, options: AdapterOptions, limits: Limits) -> RowStream:
        """The header and a lazy iterator of rows; raises AdapterError on refusal."""


# --- shared helpers -------------------------------------------------------------------------


def looks_textual(data: bytes) -> bool:
    """Neither an archive nor binary: binary content has NUL bytes and is not UTF-8."""
    if data.startswith((ZIP_MAGIC, OLE_MAGIC)):
        return False
    head = data[:_SNIFF]
    if b"\x00" not in head:
        return True
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


def looks_like_json(data: bytes) -> bool:
    """After an optional BOM and whitespace, the document opens an array or an object."""
    head = data[:_SNIFF].removeprefix(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
    return head[:1] in (b"[", b"{")


def decode_utf8(data: bytes) -> str:
    """D-16: UTF-8 only; a leading BOM is allowed and removed."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdapterError(
            UploadRefusal.ENCODING_NOT_UTF8,
            "The file is not valid UTF-8.",
            {"byte_offset": exc.start},
        ) from None
    return text[1:] if text.startswith("﻿") else text


def check_cell(text: str, where: Details, limits: Limits) -> None:
    if len(text) > limits.max_cell_chars:
        raise AdapterError(
            UploadRefusal.CELL_TOO_LONG,
            f"A cell is longer than {limits.max_cell_chars} characters.",
            {**where, "limit": limits.max_cell_chars},
        )
    if "\x00" in text:
        raise AdapterError(
            UploadRefusal.INVALID_CHARACTER, "A cell contains a NUL character.", where
        )


def too_many_columns(limits: Limits, where: Details | None = None) -> AdapterError:
    return AdapterError(
        UploadRefusal.TOO_MANY_COLUMNS,
        f"The file has more than {limits.max_columns} columns.",
        {**(where or {}), "limit": limits.max_columns},
    )


def check_header(names: list[str], where: Details, limits: Limits) -> list[str]:
    """Every column named once; within the column and cell limits."""
    if len(names) > limits.max_columns:
        raise too_many_columns(limits, where)
    seen: set[str] = set()
    for i, name in enumerate(names, start=1):
        check_cell(name, {**where, "column": i}, limits)
        reason = "blank" if not name.strip() else "duplicate" if name in seen else None
        if reason:
            raise AdapterError(
                UploadRefusal.INVALID_HEADER,
                "Each header column needs a unique, non-blank name.",
                {"reason": reason, "column": i},
            )
        seen.add(name)
    return names


def from_cells(
    columns: list[str],
    cells: list[str],
    locator: Locator,
    limits: Limits,
    flags: list[RowFlag] | None = None,
    flag_missing: bool = True,
) -> Draft:
    """A row of positional cells: values by column, cells beyond the header as extras."""
    flags = list(flags or [])
    if len(cells) > limits.max_columns:
        raise too_many_columns(limits, locator)
    for i, text in enumerate(cells, start=1):
        check_cell(text, {**locator, "column": i}, limits)
    values = dict(zip(columns, cells, strict=False))
    extras = cells[len(columns) :]
    if flag_missing and len(cells) < len(columns):
        flags.append(RowFlag(RowFlagCode.MISSING_CELLS, len(cells) + 1))
    if extras:
        flags.append(RowFlag(RowFlagCode.EXTRA_CELLS, len(columns) + 1))
    return Draft(locator, values, extras, sorted(flags, key=_flag_order))


def _flag_order(flag: RowFlag) -> tuple[int, str]:
    return (flag.column or 0, flag.code)


def too_many_rows(limits: Limits) -> AdapterError:
    return AdapterError(
        UploadRefusal.TOO_MANY_ROWS,
        f"The file has more than {limits.max_rows} data rows.",
        {"limit": limits.max_rows},
    )


def collect(drafts: Iterator[Draft], limits: Limits) -> list[SourceRow]:
    """Number the rows. A blank row between data rows is kept (flagged BLANK_ROW); blank
    rows after the last data row carry nothing and are not rows (D-20: nothing with data
    is ever dropped)."""
    rows: list[SourceRow] = []
    pending: list[Locator] = []
    pending_beyond = 0  # blank rows past the limit, counted, not kept

    def add(locator: Locator, draft: Draft | None) -> None:
        if len(rows) >= limits.max_rows:
            raise too_many_rows(limits)
        if draft is None:
            rows.append(SourceRow(len(rows) + 1, locator, {}, [], [RowFlag(RowFlagCode.BLANK_ROW)]))
        else:
            rows.append(SourceRow(len(rows) + 1, locator, draft.values, draft.extras, draft.flags))

    for draft in drafts:
        if draft.blank:
            if len(rows) + len(pending) < limits.max_rows:
                pending.append(draft.locator)
            else:
                pending_beyond += 1
            continue
        if pending_beyond:
            raise too_many_rows(limits)
        for locator in pending:
            add(locator, None)
        pending.clear()
        add(draft.locator, draft)
    return rows
