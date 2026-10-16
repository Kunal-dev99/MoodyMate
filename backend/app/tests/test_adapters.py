"""BE-8.3 (D-16, D-17, D-24 §1-2): adapter refusals. Every bad file and every limit is
refused with its own code, and no refusal carries a cell value. Values come out as exact
strings, never floats. No database needed."""

import io
import zipfile
from dataclasses import replace
from typing import Any

import openpyxl
import pytest

from app.ingestion.adapters import HARD_LIMITS, AdapterError, AdapterOptions, parse_upload
from app.ingestion.constants import SourceFormat, UploadRefusal
from app.tests import xlsx_kit
from app.tests.xlsx_kit import rewrite, xlsx
from scripts.make_synthetic_samples import OUT

CSV, XLSX, JSON = SourceFormat.CSV, SourceFormat.XLSX, SourceFormat.JSON
R = UploadRefusal


def refused(data: bytes, fmt: SourceFormat, **limits: Any) -> AdapterError:
    with pytest.raises(AdapterError) as caught:
        parse_upload(data, fmt, AdapterOptions(), replace(HARD_LIMITS, **limits))
    return caught.value


def test_bad_files_and_limits_are_refused_with_their_own_code() -> None:
    exact = "12345678901234.123456"
    parsed = parse_upload(f"a\n{exact}\n".encode(), CSV, AdapterOptions(), HARD_LIMITS)
    assert parsed.rows[0].values == {"a": exact}  # exact text, never a float
    parsed = parse_upload(f'[{{"a": {exact}}}]'.encode(), JSON, AdapterOptions(), HARD_LIMITS)
    assert parsed.rows[0].values == {"a": exact}

    zip_not_workbook = io.BytesIO()
    with zipfile.ZipFile(zip_not_workbook, "w") as zf:
        zf.writestr("hello.txt", "hi")
    entity_bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&lol2;</t></is></c></row>'
        b"</sheetData></worksheet>"
    )
    cases: list[tuple[str, bytes, SourceFormat, dict[str, int], str]] = [
        # The committed bad samples.
        (
            "non-UTF-8 CSV",
            (OUT / "instrument__non_utf8.csv").read_bytes(),
            CSV,
            {},
            R.ENCODING_NOT_UTF8,
        ),
        (
            "non-UTF-8 JSON",
            (OUT / "cashflow_event__non_utf8.json").read_bytes(),
            JSON,
            {},
            R.ENCODING_NOT_UTF8,
        ),
        ("XLSX sent as CSV", (OUT / "position.xlsx").read_bytes(), CSV, {}, R.FORMAT_MISMATCH),
        (
            "JSON sent as CSV",
            (OUT / "cashflow_event.json").read_bytes(),
            CSV,
            {},
            R.FORMAT_MISMATCH,
        ),
        ("CSV sent as XLSX", b"a,b\n1,2\n", XLSX, {}, R.FORMAT_MISMATCH),
        # Malformed content.
        ("NUL character", b"a\nx\x00y\n", CSV, {}, R.INVALID_CHARACTER),
        ("duplicate header", b"a,a\n1,2\n", CSV, {}, R.INVALID_HEADER),
        ("no header", b"", CSV, {}, R.MISSING_HEADER),
        ("bad quoting", b'a,b\n1,"x"y\n', CSV, {}, R.CSV_MALFORMED),
        ("nested JSON", b'[{"a": {"b": "1"}}]', JSON, {}, R.JSON_NESTED_VALUE),
        ("broken JSON", b'[{"a": "1"', JSON, {}, R.JSON_MALFORMED),
        ("duplicate JSON key", b'[{"a": "1", "a": "2"}]', JSON, {}, R.INVALID_HEADER),
        ("binary", b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4, CSV, {}, R.UNSUPPORTED_FILE_TYPE),
        # Workbooks (D-17).
        (
            "macro content named xlsx",
            xlsx([[("s", "a")]], main_type=xlsx_kit.XLSM_MAIN),
            XLSX,
            {},
            R.MACRO_ENABLED_WORKBOOK,
        ),
        ("VBA project", xlsx([[("s", "a")]], vba=True), XLSX, {}, R.MACRO_ENABLED_WORKBOOK),
        (
            "legacy xls",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504,
            XLSX,
            {},
            R.LEGACY_XLS_NOT_SUPPORTED,
        ),
        ("zip, not a workbook", zip_not_workbook.getvalue(), XLSX, {}, R.UNSUPPORTED_FILE_TYPE),
        (
            "XML entity expansion",
            rewrite(xlsx([[("s", "a")]]), "xl/worksheets/sheet1.xml", entity_bomb),
            XLSX,
            {},
            R.XLSX_INVALID,
        ),
        (
            "zip bomb (ratio)",
            xlsx([[("s", "a")]], compression=zipfile.ZIP_DEFLATED, padding=2 * 1024 * 1024),
            XLSX,
            {},
            R.XLSX_COMPRESSION_RATIO_TOO_HIGH,
        ),
        (
            "uncompressed size",
            xlsx_kit.table(["a"], ["1"]),
            XLSX,
            {"max_xlsx_uncompressed_bytes": 1000},
            R.XLSX_UNCOMPRESSED_TOO_LARGE,
        ),
        # D-16 limits, one code each.
        ("bytes", b"a\n" + b"1\n" * 40, CSV, {"max_bytes": 50}, R.PAYLOAD_TOO_LARGE),
        ("rows", b"a\n1\n2\n3\n4\n", CSV, {"max_rows": 3}, R.TOO_MANY_ROWS),
        ("rows JSON", b'[{"a":1},{"a":2},{"a":3},{"a":4}]', JSON, {"max_rows": 3}, R.TOO_MANY_ROWS),
        ("columns", b"a,b,c\n1,2,3\n", CSV, {"max_columns": 2}, R.TOO_MANY_COLUMNS),
        ("cell", b"a\n1234\n", CSV, {"max_cell_chars": 3}, R.CELL_TOO_LONG),
        (
            "rows XLSX",
            xlsx_kit.table(["a"], ["1"], ["2"], ["3"]),
            XLSX,
            {"max_rows": 2},
            R.TOO_MANY_ROWS,
        ),
        (
            "cell XLSX",
            xlsx_kit.table(["a"], ["1234"]),
            XLSX,
            {"max_cell_chars": 3},
            R.CELL_TOO_LONG,
        ),
        ("huge CSV field", b"a\n" + b"x" * 200_000 + b"\n", CSV, {}, R.CELL_TOO_LONG),
    ]
    for name, data, fmt, limits, code in cases:
        error = refused(data, fmt, **limits)
        assert error.code == code, f"{name}: {error.code}, expected {code}"
        assert error.status_code == (413 if code == R.PAYLOAD_TOO_LARGE else 422), name
    assert openpyxl.DEFUSEDXML is True

    secret = "SENTINEL-VALUE-7731"
    for data, fmt in (
        (f"a\n{secret * 300}\n".encode(), CSV),
        (f'[{{"a": {{"x": "{secret}"}}}}]'.encode(), JSON),
        (f'a,b\n1,"{secret}"x\n'.encode(), CSV),
        (f"{secret},{secret}\n1,2\n".encode(), CSV),
        (xlsx_kit.table(["a"], [secret * 300]), XLSX),
    ):
        error = refused(data, fmt)
        assert secret not in f"{error.code} {error.message} {error.details}", error.code
