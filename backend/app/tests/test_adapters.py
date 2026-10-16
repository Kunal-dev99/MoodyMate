"""BE-8.3 (D-16, D-17, D-24 §1-2): the SourceAdapter interface and the CSV, XLSX and JSON
adapters. Every value comes out as a string (never a float); every refusal has its own code;
no refusal carries a cell value."""

import io
import json
import random
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from app.ingestion.adapters import (
    ADAPTERS,
    HARD_LIMITS,
    AdapterError,
    AdapterOptions,
    ParsedUpload,
    SourceAdapter,
    parse_upload,
)
from app.ingestion.constants import RowFlagCode, SourceFormat, UploadRefusal
from app.tests import xlsx_kit
from app.tests.xlsx_kit import xlsx
from scripts.make_synthetic_samples import (
    DATASETS,
    OUT,
    ROWS,
    SEED,
    VARIANTS,
    Dataset,
)

EXACT = "12345678901234.123456"
CSV, XLSX, JSON = SourceFormat.CSV, SourceFormat.XLSX, SourceFormat.JSON


def parse(data: bytes, fmt: SourceFormat, **options: Any) -> ParsedUpload:
    limits = options.pop("limits", HARD_LIMITS)
    return parse_upload(data, fmt, AdapterOptions(**options), limits)


def refused(data: bytes, fmt: SourceFormat, **options: Any) -> AdapterError:
    with pytest.raises(AdapterError) as caught:
        parse(data, fmt, **options)
    return caught.value


def flags(parsed: ParsedUpload) -> list[tuple[int, str, int | None]]:
    return [(r.ordinal, f.code, f.column) for r in parsed.rows for f in r.flags]


# --- the interface -------------------------------------------------------------------------


def test_one_adapter_per_format_behind_one_interface() -> None:
    assert set(ADAPTERS) == {CSV, XLSX, JSON}
    for fmt, adapter in ADAPTERS.items():
        assert isinstance(adapter, SourceAdapter)
        assert adapter.format == fmt


@pytest.mark.parametrize(
    ("data", "fmt"),
    [
        (b"a,b\n1,2\n", CSV),
        (b'[{"a": "1"}]', JSON),
        (b'\xef\xbb\xbf  {"marker": "x", "records": []}', JSON),
        (xlsx_kit.table(["a"], ["1"]), XLSX),
    ],
)
def test_detect_recognises_only_its_own_format(data: bytes, fmt: SourceFormat) -> None:
    assert {f for f, a in ADAPTERS.items() if a.detect(data)} == {fmt}


def test_values_are_strings_never_floats() -> None:
    parsed = [
        parse(b"a,b\n1.5,x\n", CSV),
        parse(b'[{"a": 1.5, "b": 2, "c": true, "d": "x"}]', JSON),
        parse(xlsx([[("s", "a"), ("s", "b")], [("n", "1.5"), ("n", "2")]]), XLSX),
    ]
    for p in parsed:
        for row in p.rows:
            assert all(type(v) is str for v in row.values.values()), row.values


# --- CSV -------------------------------------------------------------------------------------


def test_csv_utf8_bom_is_accepted_and_not_part_of_the_header() -> None:
    p = parse("﻿id,name\n1,Zoë\n".encode(), CSV)
    assert p.columns == ["id", "name"]
    assert p.rows[0].values == {"id": "1", "name": "Zoë"}


def test_csv_quoted_newlines_stay_in_the_value_and_the_locator_is_the_start_line() -> None:
    data = b'id,note\n1,"first line\nsecond line"\n2,plain\n'
    p = parse(data, CSV)
    assert [r.values["note"] for r in p.rows] == ["first line\nsecond line", "plain"]
    assert [r.locator for r in p.rows] == [{"line": 2}, {"line": 4}]
    assert [r.ordinal for r in p.rows] == [1, 2]


@pytest.mark.parametrize("delimiter", [";", "\t", "|", ","])
def test_csv_uses_the_configured_delimiter(delimiter: str) -> None:
    data = delimiter.join(["id", "amount", "note"]) + "\n"
    data += delimiter.join(["1", EXACT, '"a,b;c|d"']) + "\n"
    p = parse(data.encode(), CSV, delimiter=delimiter)
    assert p.columns == ["id", "amount", "note"]
    assert p.rows[0].values == {"id": "1", "amount": EXACT, "note": "a,b;c|d"}


def test_csv_with_another_delimiter_is_one_column() -> None:
    p = parse(b"id;amount\n1;2\n", CSV, delimiter=",")
    assert p.columns == ["id;amount"]


def test_csv_marker_line_is_skipped_and_recorded() -> None:
    data = b"# ILLUSTRATIVE / SYNTHETIC sample: test.\nid,amount\n1,2\n"
    p = parse(data, CSV)
    assert p.marker_present is True
    assert p.columns == ["id", "amount"]
    assert p.rows[0].locator == {"line": 3}


@pytest.mark.parametrize(
    "first",
    [
        "# a comment",
        "#ILLUSTRATIVE / SYNTHETIC",
        " # ILLUSTRATIVE / SYNTHETIC",
        "# illustrative / synthetic",
        "# ILLUSTRATIVE SYNTHETIC",
    ],
)
def test_no_other_first_line_is_skipped(first: str) -> None:
    """D-24 §2: no generic '#' skip. The line is the header, nothing is dropped."""
    p = parse(f"{first}\n1\n".encode(), CSV)
    assert p.marker_present is False
    assert p.columns == [first]
    assert [r.values for r in p.rows] == [{first: "1"}]


def test_a_comment_line_after_the_header_is_a_row() -> None:
    p = parse(b"id\n# ILLUSTRATIVE / SYNTHETIC\n1\n", CSV)
    assert [r.values for r in p.rows] == [{"id": "# ILLUSTRATIVE / SYNTHETIC"}, {"id": "1"}]


def test_csv_ragged_rows_are_kept_and_flagged() -> None:
    p = parse(b"a,b,c\n1,2\n1,2,3,4,5\n", CSV)
    short, long = p.rows
    assert short.values == {"a": "1", "b": "2"}  # nothing invented for c
    assert long.values == {"a": "1", "b": "2", "c": "3"} and long.extras == ["4", "5"]
    assert flags(p) == [(1, RowFlagCode.MISSING_CELLS, 3), (2, RowFlagCode.EXTRA_CELLS, 4)]


def test_interior_blank_rows_are_kept_trailing_ones_are_not_rows() -> None:
    p = parse(b"a\n1\n\n2\n\n\n", CSV)
    assert [r.values for r in p.rows] == [{"a": "1"}, {}, {"a": "2"}]
    assert flags(p) == [(2, RowFlagCode.BLANK_ROW, None)]


def test_csv_header_only_has_no_rows() -> None:
    p = parse(b"a,b\n", CSV)
    assert p.columns == ["a", "b"] and p.rows == []


@pytest.mark.parametrize(
    ("data", "reason"),
    [(b"a,a\n1,2\n", "duplicate"), (b"a,,c\n1,2,3\n", "blank")],
)
def test_csv_header_must_name_each_column_once(data: bytes, reason: str) -> None:
    error = refused(data, CSV)
    assert error.code == UploadRefusal.INVALID_HEADER
    assert error.details == {"reason": reason, "column": 2}


@pytest.mark.parametrize("data", [b"", b"\xef\xbb\xbf", b"# ILLUSTRATIVE / SYNTHETIC x\n"])
def test_csv_without_a_header_is_refused(data: bytes) -> None:
    assert refused(data, CSV).code == UploadRefusal.MISSING_HEADER


def test_csv_malformed_quoting_is_refused() -> None:
    assert refused(b'a,b\n1,"2"x\n', CSV).code == UploadRefusal.CSV_MALFORMED


# --- encoding ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt"),
    [("instrument__non_utf8.csv", CSV), ("cashflow_event__non_utf8.json", JSON)],
)
def test_non_utf8_samples_are_refused(name: str, fmt: SourceFormat) -> None:
    error = refused((OUT / name).read_bytes(), fmt)
    assert error.code == UploadRefusal.ENCODING_NOT_UTF8
    assert error.status_code == 422


@pytest.mark.parametrize("fmt", [CSV, JSON])
def test_nul_characters_are_refused(fmt: SourceFormat) -> None:
    data = b"a\nx\x00y\n" if fmt == CSV else b'[{"a": "x\\u0000y"}]'
    assert refused(data, fmt).code == UploadRefusal.INVALID_CHARACTER


# --- exact decimal text through every format ---------------------------------------------------


def test_exact_decimal_string_survives_csv() -> None:
    assert parse(f"v\n{EXACT}\n".encode(), CSV).rows[0].values == {"v": EXACT}


@pytest.mark.parametrize("literal", [EXACT, json.dumps(EXACT)])
def test_exact_decimal_string_survives_json(literal: str) -> None:
    """As a JSON number (parse_float=Decimal) and as a JSON string."""
    p = parse(f'[{{"v": {literal}}}]'.encode(), JSON)
    assert p.rows[0].values == {"v": EXACT}
    assert p.rows[0].flags == []


def test_exact_decimal_string_survives_xlsx_as_text() -> None:
    p = parse(xlsx([[("s", "v")], [("s", EXACT)]]), XLSX)
    assert p.rows[0].values == {"v": EXACT}
    assert p.rows[0].flags == []


def test_xlsx_numeric_cell_is_decimal_of_repr_with_a_precision_flag() -> None:
    """XLSX stores IEEE doubles: the cell's value is Decimal(repr(x)), and beyond 15
    significant digits the row carries a precision flag (D-17)."""
    p = parse(xlsx([[("s", "v"), ("s", "w")], [("n", EXACT), ("n", "1234.5")]]), XLSX)
    value = float(EXACT)
    assert p.rows[0].values == {"v": str(Decimal(repr(value))), "w": "1234.5"}
    assert p.rows[0].values["v"] != EXACT  # the double cannot hold it
    assert flags(p) == [(1, RowFlagCode.PRECISION_BEYOND_15_DIGITS, 1)]


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("123456789012345", False),  # 15 digits: exact in a double
        ("1234567890123456", True),
        ("0.000000000000000123", False),  # leading zeros are not significant
        ("1E+20", False),
        ("12345678.12345678", True),
    ],
)
def test_xlsx_precision_flag_counts_significant_digits(text: str, flagged: bool) -> None:
    p = parse(xlsx([[("s", "v")], [("n", text)]]), XLSX)
    assert (RowFlagCode.PRECISION_BEYOND_15_DIGITS in {f.code for f in p.rows[0].flags}) is flagged
    assert Decimal(p.rows[0].values["v"]) == Decimal(repr(float(text))) or Decimal(
        p.rows[0].values["v"]
    ) == Decimal(text)


def test_xlsx_formula_yields_its_cached_value_and_a_flag() -> None:
    data = xlsx(
        [
            [("s", "a"), ("s", "b"), ("s", "c")],
            [("n", "2"), ("f", "A2*21", "42"), ("f", "A2/0", None)],
        ]
    )
    p = parse(data, XLSX)
    assert p.rows[0].values == {"a": "2", "b": "42", "c": ""}
    assert flags(p) == [
        (1, RowFlagCode.FORMULA_CACHED_VALUE, 2),
        (1, RowFlagCode.FORMULA_WITHOUT_CACHED_VALUE, 3),
    ]


def test_xlsx_dates_booleans_errors_and_empty_cells() -> None:
    data = xlsx(
        [
            [("s", "d"), ("s", "b"), ("s", "e"), ("s", "x"), ("s", "y")],
            [("d", "46295"), ("b", "1"), ("e", "#DIV/0!"), None, ("s", "last")],
        ]
    )
    p = parse(data, XLSX)
    assert p.rows[0].values == {
        "d": "2026-09-30",
        "b": "true",
        "e": "#DIV/0!",
        "x": "",
        "y": "last",
    }
    assert flags(p) == [(1, RowFlagCode.CELL_ERROR, 3)]


def test_xlsx_locator_is_sheet_and_row() -> None:
    p = parse(xlsx([[("s", "a")], [("s", "1")], [("s", "2")]]), XLSX)
    assert [r.locator for r in p.rows] == [
        {"sheet": "ILLUSTRATIVE SYNTHETIC", "row": 2},
        {"sheet": "ILLUSTRATIVE SYNTHETIC", "row": 3},
    ]


def test_xlsx_reads_the_profile_sheet_else_the_first() -> None:
    data = xlsx([[("s", "a")], [("s", "1")]], sheet="Data", sheets=("Other",))
    assert parse(data, XLSX).sheet_name == "Data"
    assert refused(data, XLSX, sheet_name="Other").code == UploadRefusal.MISSING_HEADER
    error = refused(data, XLSX, sheet_name="Missing")
    assert error.code == UploadRefusal.SHEET_NOT_FOUND


@pytest.mark.parametrize(
    ("sheet", "title", "present"),
    [
        ("ILLUSTRATIVE SYNTHETIC", "ILLUSTRATIVE / SYNTHETIC x", True),
        ("Data", "ILLUSTRATIVE / SYNTHETIC x", True),
        ("ILLUSTRATIVE SYNTHETIC", None, True),
        ("Data", "Quarterly positions", False),
        ("Data", None, False),
    ],
)
def test_xlsx_marker_is_the_sheet_name_or_the_title(
    sheet: str, title: str | None, present: bool
) -> None:
    data = xlsx([[("s", "a")], [("s", "1")]], sheet=sheet, title=title)
    assert parse(data, XLSX).marker_present is present


def test_xlsm_is_refused_by_content_not_extension() -> None:
    macro = xlsx([[("s", "a")]], main_type=xlsx_kit.XLSM_MAIN)
    assert refused(macro, XLSX).code == UploadRefusal.MACRO_ENABLED_WORKBOOK
    vba = xlsx([[("s", "a")]], vba=True)
    assert refused(vba, XLSX).code == UploadRefusal.MACRO_ENABLED_WORKBOOK


def test_legacy_xls_is_refused() -> None:
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
    assert refused(ole, XLSX).code == UploadRefusal.LEGACY_XLS_NOT_SUPPORTED


def test_a_zip_that_is_not_a_workbook_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("hello.txt", "hi")
    assert refused(buffer.getvalue(), XLSX).code == UploadRefusal.UNSUPPORTED_FILE_TYPE


def _rewrite(data: bytes, member: str, content: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, content if info.filename == member else src.read(info))
    return out.getvalue()


@pytest.mark.parametrize(
    ("member", "content"),
    [
        ("xl/worksheets/sheet1.xml", b"<worksheet><sheetData><row><c><v>"),
        ("xl/workbook.xml", b"not xml at all"),
    ],
)
def test_a_broken_workbook_is_refused(member: str, content: bytes) -> None:
    broken = _rewrite(xlsx([[("s", "a")], [("s", "1")]]), member, content)
    assert refused(broken, XLSX).code == UploadRefusal.XLSX_INVALID


def test_xml_entity_attacks_are_refused() -> None:
    """defusedxml (D-17): a DTD with entities in a workbook part is refused, not expanded."""
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&lol2;</t></is></c></row>'
        b"</sheetData></worksheet>"
    )
    broken = _rewrite(xlsx([[("s", "a")]]), "xl/worksheets/sheet1.xml", bomb)
    assert refused(broken, XLSX).code == UploadRefusal.XLSX_INVALID


def test_openpyxl_uses_defusedxml() -> None:
    assert openpyxl.DEFUSEDXML is True


def test_format_must_match_the_profile() -> None:
    assert refused(b"a,b\n1,2\n", XLSX).code == UploadRefusal.FORMAT_MISMATCH
    assert refused(xlsx_kit.table(["a"], ["1"]), CSV).code == UploadRefusal.FORMAT_MISMATCH
    assert refused(b'[{"a": "1"}]', CSV).code == UploadRefusal.FORMAT_MISMATCH
    assert refused(b"a,b\n1,2\n", JSON).code == UploadRefusal.FORMAT_MISMATCH


def test_binary_content_is_refused() -> None:
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    for fmt in (CSV, JSON, XLSX):
        assert refused(png, fmt).code == UploadRefusal.UNSUPPORTED_FILE_TYPE


# --- JSON ------------------------------------------------------------------------------------


def test_json_top_level_array_of_flat_objects() -> None:
    p = parse(b'[{"a": "1", "b": 2}, {"b": 3, "c": null}]', JSON)
    assert p.marker_present is False
    assert p.columns == ["a", "b", "c"]  # first appearance order
    assert [r.values for r in p.rows] == [{"a": "1", "b": "2"}, {"b": "3"}]
    assert [r.locator for r in p.rows] == [{"index": 0}, {"index": 1}]
    assert flags(p) == [
        (1, RowFlagCode.MISSING_CELLS, 3),
        (2, RowFlagCode.MISSING_CELLS, 1),
        (2, RowFlagCode.NULL_VALUE, 3),
    ]


def test_json_marker_object_is_accepted_and_recorded() -> None:
    body = {"marker": "ILLUSTRATIVE / SYNTHETIC sample", "records": [{"a": "1"}]}
    p = parse(json.dumps(body).encode(), JSON)
    assert p.marker_present is True
    assert [r.values for r in p.rows] == [{"a": "1"}]


@pytest.mark.parametrize(
    "body",
    [
        {"marker": "ILLUSTRATIVE / SYNTHETIC", "records": [], "extra": 1},
        {"marker": "SYNTHETIC", "records": []},
        {"marker": " ILLUSTRATIVE / SYNTHETIC", "records": []},
        {"marker": 1, "records": []},
        {"records": []},
        {"marker": "ILLUSTRATIVE / SYNTHETIC"},
        {"marker": "ILLUSTRATIVE / SYNTHETIC", "records": {"a": "1"}},
        {"data": [{"a": "1"}]},
        [1, 2],
        [{"a": "1"}, "not an object"],
    ],
)
def test_json_other_shapes_are_refused(body: Any) -> None:
    assert refused(json.dumps(body).encode(), JSON).code == UploadRefusal.JSON_INVALID_SHAPE


@pytest.mark.parametrize("body", [b'"just a string"', b"42", b"true"])
def test_json_scalar_documents_are_refused(body: bytes) -> None:
    """Content sniffing sees an array or object as JSON; a bare scalar is not (a CSV may
    start with a quote or a digit), so it is refused as not being the profile's format."""
    assert refused(body, JSON).code == UploadRefusal.FORMAT_MISMATCH


def test_quoted_csv_header_is_still_csv() -> None:
    assert parse(b'"id","name"\n1,x\n', CSV).columns == ["id", "name"]


@pytest.mark.parametrize(
    "record",
    [{"a": {"nested": "1"}}, {"a": ["1", "2"]}, {"a": []}, {"a": {}}],
)
def test_nested_json_values_are_refused(record: dict[str, Any]) -> None:
    error = refused(json.dumps([{"ok": "1"}, record]).encode(), JSON)
    assert error.code == UploadRefusal.JSON_NESTED_VALUE
    assert error.details == {"index": 1, "column": 2}  # columns: ok, a


def test_deeply_nested_json_is_refused_cleanly() -> None:
    data = b"[" * 100_000 + b"]" * 100_000
    assert refused(data, JSON).code in {
        UploadRefusal.JSON_MALFORMED,
        UploadRefusal.JSON_INVALID_SHAPE,
    }


@pytest.mark.parametrize(
    "data", [b'[{"a": 1', b'[{"a": NaN}]', b'[{"a": Infinity}]', b"[{'a': 1}]"]
)
def test_malformed_json_is_refused(data: bytes) -> None:
    assert refused(data, JSON).code == UploadRefusal.JSON_MALFORMED


def test_duplicate_json_keys_are_refused_not_overwritten() -> None:
    error = refused(b'[{"a": "1", "a": "2"}]', JSON)
    assert error.code == UploadRefusal.INVALID_HEADER
    assert error.details == {"reason": "duplicate", "index": 0}


def test_json_scalars_become_strings() -> None:
    p = parse(b'[{"i": 10000000000000000000001, "f": 1.10, "e": 1e3, "t": true, "n": false}]', JSON)
    assert p.rows[0].values == {
        "i": "10000000000000000000001",
        "f": "1.10",
        "e": "1E+3",
        "t": "true",
        "n": "false",
    }


# --- D-16 limits: one code each ----------------------------------------------------------------

LIMIT_CASES = [
    ("max_bytes", 50, CSV, b"a\n" + b"1\n" * 40, UploadRefusal.PAYLOAD_TOO_LARGE, 413),
    ("max_rows", 3, CSV, b"a\n1\n2\n3\n4\n", UploadRefusal.TOO_MANY_ROWS, 422),
    ("max_rows", 3, JSON, b'[{"a":1},{"a":2},{"a":3},{"a":4}]', UploadRefusal.TOO_MANY_ROWS, 422),
    ("max_columns", 2, CSV, b"a,b,c\n1,2,3\n", UploadRefusal.TOO_MANY_COLUMNS, 422),
    ("max_columns", 2, JSON, b'[{"a":1,"b":2,"c":3}]', UploadRefusal.TOO_MANY_COLUMNS, 422),
    ("max_cell_chars", 3, CSV, b"a\n1234\n", UploadRefusal.CELL_TOO_LONG, 422),
    ("max_cell_chars", 3, JSON, b'[{"a":"1234"}]', UploadRefusal.CELL_TOO_LONG, 422),
]


@pytest.mark.parametrize(("limit", "value", "fmt", "data", "code", "status"), LIMIT_CASES)
def test_each_limit_has_its_own_code(
    limit: str, value: int, fmt: SourceFormat, data: bytes, code: str, status: int
) -> None:
    error = refused(data, fmt, limits=replace(HARD_LIMITS, **{limit: value}))
    assert (error.code, error.status_code) == (code, status)
    # One under the limit is accepted.
    parse(data, fmt, limits=replace(HARD_LIMITS, **{limit: value + 1000}))


@pytest.mark.parametrize(
    ("limit", "value", "rows", "code"),
    [
        ("max_rows", 2, [["a"], ["1"], ["2"], ["3"]], UploadRefusal.TOO_MANY_ROWS),
        ("max_columns", 2, [["a", "b", "c"], ["1", "2", "3"]], UploadRefusal.TOO_MANY_COLUMNS),
        ("max_columns", 2, [["a", "b"], ["1", "2", "3"]], UploadRefusal.TOO_MANY_COLUMNS),
        ("max_cell_chars", 3, [["a"], ["1234"]], UploadRefusal.CELL_TOO_LONG),
    ],
)
def test_each_limit_applies_to_xlsx(
    limit: str, value: int, rows: list[list[str]], code: str
) -> None:
    data = xlsx_kit.table(*rows)
    assert refused(data, XLSX, limits=replace(HARD_LIMITS, **{limit: value})).code == code


def test_far_right_styled_cells_do_not_widen_rows() -> None:
    """openpyxl pads a row up to its last present cell; a styled empty cell in the last
    column (XFD) on every row must neither slow parsing nor count as a column."""
    import time

    far: xlsx_kit.Cell = ("at", "XFD", ("styled",))
    rows: list[list[xlsx_kit.Cell]] = [
        [("s", "a"), far],
        *[[("s", str(i)), far] for i in range(5000)],
    ]
    started = time.monotonic()
    parsed = parse(xlsx(rows, compression=zipfile.ZIP_DEFLATED), XLSX)
    assert time.monotonic() - started < 10
    assert parsed.columns == ["a"] and len(parsed.rows) == 5000
    assert parsed.rows[0].values == {"a": "0"} and parsed.rows[0].extras == []


def test_a_far_right_value_is_too_many_columns() -> None:
    data = xlsx([[("s", "a")], [("s", "1"), ("at", "XFD", ("s", "far"))]])
    error = refused(data, XLSX)
    assert error.code == UploadRefusal.TOO_MANY_COLUMNS
    assert error.details == {"sheet": "ILLUSTRATIVE SYNTHETIC", "row": 2, "limit": 200}


def test_hard_ceilings_hold_at_their_values() -> None:
    """The ceilings themselves (no lowered settings): rows, columns and cell length."""
    rows = b"a\n" + b"1\n" * 50_000
    assert len(parse(rows, CSV).rows) == 50_000
    assert refused(rows + b"1\n", CSV).code == UploadRefusal.TOO_MANY_ROWS
    header = ",".join(f"c{i}" for i in range(200)).encode()
    assert len(parse(header + b"\n", CSV).columns) == 200
    assert refused(header + b",c200\n", CSV).code == UploadRefusal.TOO_MANY_COLUMNS
    assert parse(b"a\n" + b"x" * 4000 + b"\n", CSV).rows[0].values["a"] == "x" * 4000
    assert refused(b"a\n" + b"x" * 4001 + b"\n", CSV).code == UploadRefusal.CELL_TOO_LONG
    big = b"a\n" + b"x" * (10 * 1024 * 1024)
    assert refused(big, CSV).code == UploadRefusal.PAYLOAD_TOO_LARGE


def test_a_very_long_csv_field_is_cell_too_long_not_a_parser_error() -> None:
    """Beyond the csv module's own field limit (131072) the code is still cell_too_long."""
    assert refused(b"a\n" + b"x" * 200_000 + b"\n", CSV).code == UploadRefusal.CELL_TOO_LONG


def test_xlsx_uncompressed_size_is_bounded() -> None:
    data = xlsx_kit.table(["a"], ["1"])  # stored, so ratio 1
    limits = replace(HARD_LIMITS, max_xlsx_uncompressed_bytes=1000)
    error = refused(data, XLSX, limits=limits)
    assert error.code == UploadRefusal.XLSX_UNCOMPRESSED_TOO_LARGE


def test_xlsx_compression_ratio_is_bounded() -> None:
    """A zip bomb: 2 MiB of whitespace deflates to a few KiB (ratio far above 100:1)."""
    bomb = xlsx(
        [[("s", "a")], [("s", "1")]],
        compression=zipfile.ZIP_DEFLATED,
        padding=2 * 1024 * 1024,
    )
    assert len(bomb) < 50_000
    assert refused(bomb, XLSX).code == UploadRefusal.XLSX_COMPRESSION_RATIO_TOO_HIGH


def test_xlsx_uncompressed_ceiling_holds_at_50_mib() -> None:
    bomb = xlsx([[("s", "a")]], compression=zipfile.ZIP_DEFLATED, padding=50 * 1024 * 1024 + 1)
    assert refused(bomb, XLSX).code == UploadRefusal.XLSX_UNCOMPRESSED_TOO_LARGE


def test_ordinary_compressed_workbooks_pass_the_ratio_check() -> None:
    rows = [["id", "amount"], *[[f"ID-{i}", f"{i}.25"] for i in range(200)]]
    data = xlsx([[("s", v) for v in r] for r in rows], compression=zipfile.ZIP_DEFLATED)
    assert len(parse(data, XLSX).rows) == 200


def test_refusals_carry_no_cell_values() -> None:
    secret = "SENTINEL-VALUE-7731"
    cases = [
        (f"a\n{secret * 300}\n".encode(), CSV),
        (f'[{{"a": {{"x": "{secret}"}}}}]'.encode(), JSON),
        (f'[{{"a": "{secret}"}}'.encode(), JSON),
        (f'a,b\n1,"{secret}"x\n'.encode(), CSV),
        (f"{secret},{secret}\n1,2\n".encode(), CSV),
        (xlsx_kit.table(["a"], [secret * 300]), XLSX),
    ]
    for data, fmt in cases:
        error = refused(data, fmt)
        assert secret not in f"{error.code} {error.message} {error.details}", error.code


# --- the committed synthetic samples -----------------------------------------------------------


def _table(ds: Dataset, variant: str | None) -> tuple[list[str], list[dict[str, str]]]:
    """The generator's rows for a dataset (and variant), before rendering."""
    rng = random.Random(f"{SEED}:{ds.name}")  # noqa: S311 - the generator's own seeded rows
    t = (list(ds.columns), [ds.make_row(rng, i) for i in range(1, ROWS + 1)])
    return VARIANTS[variant](ds, t) if variant else t


def _samples(ext: str) -> list[tuple[Dataset, str | None, Path]]:
    found = []
    for ds in DATASETS:
        if ds.fmt != ext:
            continue
        for path in sorted(OUT.glob(f"{ds.name}*.{ext}")):
            variant = path.stem.split("__", 1)[1] if "__" in path.stem else None
            if variant != "non_utf8":
                found.append((ds, variant, path))
    return found


def _expected(ds: Dataset, column: str, value: str) -> Any:
    if column in ds.decimals:
        try:
            return Decimal(value)  # numeric cells come back as doubles
        except ArithmeticError:
            return value
    return value


@pytest.mark.parametrize(
    ("ds", "variant", "path"), _samples("xlsx"), ids=lambda v: getattr(v, "name", str(v))
)
def test_openpyxl_opens_every_committed_position_workbook(
    ds: Dataset, variant: str | None, path: Path
) -> None:
    """Phase 7 review note: the hand-written samples open in openpyxl (read_only,
    data_only) and yield the expected rows, directly and through the adapter."""
    columns, rows = _table(ds, variant)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        raw = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()
    assert raw[0] == columns and len(raw) == len(rows) + 1

    parsed = parse(path.read_bytes(), XLSX)
    assert parsed.columns == columns
    assert parsed.marker_present is True
    assert len(parsed.rows) == len(rows) == ROWS
    for want, got in zip(rows, parsed.rows, strict=True):
        assert set(got.values) == set(columns)
        for c in columns:
            expected = _expected(ds, c, want[c])
            actual = Decimal(got.values[c]) if isinstance(expected, Decimal) else got.values[c]
            assert actual == expected, (c, want[c], got.values[c])


@pytest.mark.parametrize(("ds", "variant", "path"), _samples("csv") + _samples("json"))
def test_every_committed_text_sample_parses_exactly(
    ds: Dataset, variant: str | None, path: Path
) -> None:
    columns, rows = _table(ds, variant)
    parsed = parse(path.read_bytes(), CSV if ds.fmt == "csv" else JSON)
    assert parsed.marker_present is True
    assert parsed.columns == columns
    assert [r.values for r in parsed.rows] == rows  # exact text, decimals included
