"""XLSX adapter (D-16 §3, D-17).

* Only `.xlsx`, detected from content: legacy `.xls` (OLE2) and macro-enabled workbooks
  (`.xlsm` content type or a VBA project) are refused, whatever the file is called.
* Zip-bomb guard before anything is decompressed: total uncompressed size and the total
  compression ratio are bounded.
* openpyxl in `read_only=True, data_only=True` mode with defusedxml installed. Formulas are
  never evaluated: a formula cell yields its cached value and a flag (a second, formula-mode
  pass over the same rows tells which cells hold formulas).
* XLSX stores numbers as IEEE doubles: a numeric cell becomes `Decimal(repr(x))` (the
  shortest round trip), flagged beyond 15 significant digits. Dates become ISO strings.
* One sheet: the profile's named sheet, else the first. The marker is the sheet name
  `ILLUSTRATIVE SYNTHETIC` or a workbook title starting with the full marker (D-24 §2).
* Empty cells inside the header width read as ""; cells beyond it are kept as extras.
"""

import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.cell.read_only import EMPTY_CELL, ReadOnlyCell
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet._read_only import ReadOnlyWorksheet

from app.ingestion.adapters.base import (
    ZIP_MAGIC,
    AdapterError,
    AdapterOptions,
    Draft,
    Limits,
    Locator,
    RowFlag,
    RowStream,
    SourceAdapter,
    check_header,
    from_cells,
    too_many_columns,
)
from app.ingestion.constants import (
    SYNTHETIC_MARKER,
    XLSX_MARKER_SHEET,
    RowFlagCode,
    SourceFormat,
    UploadRefusal,
)

_PARTS = ("[Content_Types].xml", "xl/workbook.xml")
_MACRO_TYPE = b"macroEnabled"
_EXACT_DIGITS = 15  # a double holds 15 significant decimal digits exactly


def _invalid() -> AdapterError:
    return AdapterError(UploadRefusal.XLSX_INVALID, "The workbook could not be read.")


def _significant_digits(value: Decimal) -> int:
    return len(value.normalize().as_tuple().digits)


def _text(cell: Any) -> tuple[str | None, list[RowFlagCode]]:
    """A cell's value as text, plus any flag the conversion raises."""
    value = cell.value
    flags: list[RowFlagCode] = []
    if getattr(cell, "data_type", None) == "e":
        flags.append(RowFlagCode.CELL_ERROR)
    if value is None:
        return None, flags
    if isinstance(value, bool):
        return ("true" if value else "false"), flags
    if isinstance(value, int | float):
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
        if number.is_finite() and _significant_digits(number) > _EXACT_DIGITS:
            flags.append(RowFlagCode.PRECISION_BEYOND_15_DIGITS)
        return str(number), flags
    if isinstance(value, datetime):
        if value.time() == time(0) and value.tzinfo is None:
            return value.date().isoformat(), flags
        return value.isoformat(), flags
    if isinstance(value, date | time):
        return value.isoformat(), flags
    return str(value), flags


def _bounded_rows(ws: ReadOnlyWorksheet, limits: Limits) -> None:
    """openpyxl pads every row up to its last present cell (styled or not). Bound that
    width so a far-right cell cannot make each row 16,384 cells wide: the width counts
    only cells holding a value, and more than the column limit is refused. Relies on
    openpyxl 3.1's ReadOnlyWorksheet._get_row (pinned <3.2; a test covers it)."""

    def get_row(
        row: list[dict[str, Any]],
        min_col: int = 1,
        max_col: int | None = None,
        values_only: bool = False,
    ) -> tuple[Any, ...]:
        width = max((c["column"] for c in row if c["value"] is not None), default=0)
        if width > limits.max_columns:
            raise too_many_columns(limits, {"sheet": ws.title, "row": row[0]["row"]})
        cells: list[Any] = [EMPTY_CELL] * width
        for c in row:
            if c["column"] <= width:
                cells[c["column"] - 1] = ReadOnlyCell(ws, **c)
        return tuple(cells)

    ws.reset_dimensions()  # never pad to a declared (possibly huge) dimension
    ws._get_row = get_row  # type: ignore[attr-defined]


class XlsxAdapter(SourceAdapter):
    format = SourceFormat.XLSX
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def detect(self, data: bytes) -> bool:
        if not data.startswith(ZIP_MAGIC):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except (zipfile.BadZipFile, ValueError, OSError):
            return False
        return all(p in names for p in _PARTS)

    def iter_rows(self, data: bytes, options: AdapterOptions, limits: Limits) -> RowStream:
        _check_archive(data, limits)
        try:
            values = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_links=False
            )
            formulas = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=False, keep_links=False
            )
        except Exception:  # any parser failure: XML, zip, missing part, entity attack
            raise _invalid() from None
        try:
            ws_v, ws_f = _sheet(values, options), _sheet(formulas, options)
            for ws in (ws_v, ws_f):
                _bounded_rows(ws, limits)
            rows = _paired(_rows(ws_v), _rows(ws_f))
            header = next(rows, None)
            title = values.properties.title or ""
            marker = ws_v.title.startswith(XLSX_MARKER_SHEET) or title.startswith(SYNTHETIC_MARKER)
            columns = _header(header, ws_v.title, limits)
        except BaseException:
            values.close()
            formulas.close()
            raise
        sheet = ws_v.title
        return RowStream(
            columns, marker, _drafts(rows, columns, sheet, limits, values, formulas), sheet
        )


def _check_archive(data: bytes, limits: Limits) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            total = sum(i.file_size for i in infos)
            packed = sum(i.compress_size for i in infos)
            if total > limits.max_xlsx_uncompressed_bytes:
                raise AdapterError(
                    UploadRefusal.XLSX_UNCOMPRESSED_TOO_LARGE,
                    "The workbook is too large once uncompressed.",
                    {"limit": limits.max_xlsx_uncompressed_bytes},
                )
            if total > limits.max_xlsx_ratio * max(packed, 1):
                raise AdapterError(
                    UploadRefusal.XLSX_COMPRESSION_RATIO_TOO_HIGH,
                    "The workbook's compression ratio is too high.",
                    {"limit": limits.max_xlsx_ratio},
                )
            names = {i.filename for i in infos}
            if "xl/vbaProject.bin" in names or _MACRO_TYPE in zf.read("[Content_Types].xml"):
                raise AdapterError(
                    UploadRefusal.MACRO_ENABLED_WORKBOOK,
                    "Macro-enabled workbooks (.xlsm) are not accepted; save it as .xlsx.",
                )
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise _invalid() from None


def _sheet(wb: Workbook, options: AdapterOptions) -> ReadOnlyWorksheet:
    ws: Any
    if options.sheet_name is not None:
        if options.sheet_name not in wb.sheetnames:
            raise AdapterError(
                UploadRefusal.SHEET_NOT_FOUND, "The workbook has no sheet with the profile's name."
            )
        ws = wb[options.sheet_name]
    else:
        if not wb.worksheets:
            raise AdapterError(UploadRefusal.MISSING_HEADER, "The workbook has no sheet.")
        ws = wb.worksheets[0]
    if not isinstance(ws, ReadOnlyWorksheet):
        raise _invalid()
    return ws


def _rows(ws: Any) -> Iterator[tuple[Any, ...]]:
    """The sheet's rows of read-only cells (typed loosely: the stubs model Worksheet)."""
    rows: Iterator[tuple[Any, ...]] = ws.iter_rows()
    return rows


def _paired(
    values: Iterator[tuple[Any, ...]], formulas: Iterator[tuple[Any, ...]]
) -> Iterator[tuple[tuple[Any, ...], tuple[Any, ...]]]:
    """Each row from both passes. A parser failure mid-sheet is an invalid workbook."""
    try:
        yield from zip(values, formulas, strict=True)
    except AdapterError:
        raise
    except Exception:
        raise _invalid() from None


def _header(
    header: tuple[tuple[Any, ...], tuple[Any, ...]] | None, sheet: str, limits: Limits
) -> list[str]:
    cells = [_text(c)[0] for c in header[0]] if header else []
    while cells and cells[-1] is None:
        cells.pop()
    if not cells:
        raise AdapterError(UploadRefusal.MISSING_HEADER, "The sheet has no header row.")
    return check_header([c or "" for c in cells], {"sheet": sheet, "row": 1}, limits)


def _drafts(
    rows: Iterator[tuple[tuple[Any, ...], tuple[Any, ...]]],
    columns: list[str],
    sheet: str,
    limits: Limits,
    *workbooks: Workbook,
) -> Iterator[Draft]:
    try:
        for number, (row_v, row_f) in enumerate(rows, start=2):
            locator: Locator = {"sheet": sheet, "row": number}
            texts: list[str | None] = []
            flags: list[RowFlag] = []
            # A formula without a cached value has no value in the values pass, so walk
            # the wider of the two passes.
            for i in range(1, max(len(row_v), len(row_f)) + 1):
                text, codes = _text(row_v[i - 1] if i <= len(row_v) else EMPTY_CELL)
                formula = i <= len(row_f) and getattr(row_f[i - 1], "data_type", None) == "f"
                if formula:
                    codes.append(
                        RowFlagCode.FORMULA_CACHED_VALUE
                        if text is not None
                        else RowFlagCode.FORMULA_WITHOUT_CACHED_VALUE
                    )
                flags.extend(RowFlag(code, i) for code in codes)
                texts.append(text)
            while texts and texts[-1] is None:
                texts.pop()
            if not texts and not flags:
                yield Draft(locator, {}, blank=True)
                continue
            cells = [t or "" for t in texts]
            if len(cells) < len(columns):
                cells += [""] * (len(columns) - len(cells))  # an empty cell is ""
            yield from_cells(columns, cells, locator, limits, flags, flag_missing=False)
    finally:
        for wb in workbooks:
            wb.close()
