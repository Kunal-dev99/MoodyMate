"""Adapters, no database needed.

1. BE-8.3 (D-16, D-17, D-24 §1-2): adapter refusals. Every bad file and every limit is
   refused with its own code, and no refusal carries a cell value. Values come out as exact
   strings, never floats.
2. BE-13.1: the adapter contract (docs/ADAPTERS.md), one loop over every adapter: CSV,
   XLSX, JSON and the test-only in-memory `FakeFeedAdapter` (app/tests/fake_feed.py). A
   future adapter is added to `contract_kits()` and must pass the same checks.
"""

import inspect
import io
import json
import sys
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
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
    Limits,
    RowFlag,
    SourceAdapter,
    SourceRow,
    parse_upload,
)
from app.ingestion.adapters.base import collect
from app.ingestion.constants import RowFlagCode, SourceFormat, UploadRefusal
from app.tests import fake_feed, xlsx_kit
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


# --- 2. The adapter contract (BE-13.1) ------------------------------------------------------------

COLUMNS = ["trade_id", "amount", "ccy"]
TABLE: list[list[Any]] = [["T-1", Decimal("100.25"), "EUR"], ["T-2", Decimal("-0.5"), "USD"]]
EXPECTED = [{"trade_id": "T-1", "amount": "100.25", "ccy": "EUR"}, {"trade_id": "T-2",
            "amount": "-0.5", "ccy": "USD"}]  # fmt: skip
SECRET = "SENTINEL-CONTRACT-4410"
Make = Callable[[list[str], list[list[Any]], bool], bytes]


def _csv(columns: list[str], rows: list[list[Any]], marker: bool) -> bytes:
    lines = (["# ILLUSTRATIVE / SYNTHETIC"] if marker else []) + [",".join(columns)]
    lines += [",".join(str(v) for v in r) for r in rows]
    return ("\n".join(lines) + "\n").encode()


def _json(columns: list[str], rows: list[list[Any]], marker: bool) -> bytes:
    def scalar(v: Any) -> str:  # a Decimal is a JSON number with its exact text
        return str(v) if isinstance(v, Decimal) else json.dumps(v)

    records = ", ".join(
        "{"
        + ", ".join(f"{json.dumps(c)}: {scalar(v)}" for c, v in zip(columns, r, strict=True))
        + "}"
        for r in rows
    )
    text = f"[{records}]"
    if marker:
        text = f'{{"marker": "ILLUSTRATIVE / SYNTHETIC test", "records": {text}}}'
    return text.encode()


def _xlsx(columns: list[str], rows: list[list[Any]], marker: bool) -> bytes:
    def cell(v: Any) -> xlsx_kit.Cell:
        return ("n", str(v)) if isinstance(v, Decimal) else ("s", str(v))

    cells = [[cell(c) for c in columns], *([cell(v) for v in r] for r in rows)]
    if marker:
        return xlsx(cells)
    return xlsx(cells, sheet="Sheet1", title=None)


@dataclass(frozen=True)
class Kit:
    name: str
    adapter: SourceAdapter
    make: Make
    positional: bool  # cells by position (extras possible); JSON names every value
    registered: bool  # in ADAPTERS; the fake never is


def contract_kits() -> list[Kit]:
    return [
        Kit("CSV", ADAPTERS[SourceFormat.CSV], _csv, True, True),
        Kit("XLSX", ADAPTERS[SourceFormat.XLSX], _xlsx, True, True),
        Kit("JSON", ADAPTERS[SourceFormat.JSON], _json, False, True),
        Kit("FakeFeed", fake_feed.FakeFeedAdapter(), fake_feed.frame, True, False),
    ]


def _rows(kit: Kit, data: bytes, limits: Limits = HARD_LIMITS) -> tuple[Any, list[SourceRow]]:
    stream = kit.adapter.iter_rows(data, AdapterOptions(), limits)
    return stream, collect(stream.drafts, limits)


def _refusal(kit: Kit, data: bytes, **limits: int) -> AdapterError:
    with pytest.raises(AdapterError) as caught:
        _rows(kit, data, replace(HARD_LIMITS, **limits))
    return caught.value


def test_every_adapter_meets_the_contract() -> None:
    kits = contract_kits()
    assert {k.adapter.format for k in kits if k.registered} == set(ADAPTERS), "a format untested"
    samples = {k.name: k.make(COLUMNS, TABLE, True) for k in kits}
    for kit in kits:
        a, data = kit.adapter, samples[kit.name]
        # Interface: a SourceAdapter with its format and the media type it reports.
        assert isinstance(a, SourceAdapter), kit.name
        assert isinstance(a.format, SourceFormat) and a.media_type, kit.name
        # Detection by content only: its own content, never another registered format's.
        assert a.detect(data), f"{kit.name}: does not detect its own content"
        for other in kits:
            if other is not kit and (other.registered or not kit.registered):
                claimed = a.detect(samples[other.name])
                assert not claimed, f"{kit.name}: claims {other.name} content"

        # Rows: the header in order, the marker, numbered rows with positional locators.
        stream, rows = _rows(kit, data)
        assert isinstance(stream.drafts, Iterator), f"{kit.name}: rows are not lazy"
        assert stream.columns == COLUMNS, f"{kit.name}: columns {stream.columns}"
        assert stream.marker_present is True, f"{kit.name}: marker not recorded"
        assert _rows(kit, kit.make(COLUMNS, TABLE, False))[0].marker_present is False, kit.name
        assert [r.ordinal for r in rows] == [1, 2], f"{kit.name}: ordinals"
        locators = [r.locator for r in rows]
        for loc in locators:
            assert loc and all(
                isinstance(k, str) and type(v) in (str, int) for k, v in loc.items()
            ), f"{kit.name}: locator {loc}"
        assert len({json.dumps(loc, sort_keys=True) for loc in locators}) == 2, kit.name
        # No-float rule: every value is the exact text, a str, never a float.
        assert [r.values for r in rows] == EXPECTED, f"{kit.name}: values {rows}"
        for r in rows:
            assert all(type(v) is str for v in r.values.values()), f"{kit.name}: non-str value"
            assert r.extras == [] and r.flags == [], f"{kit.name}: unexpected facts on {r}"
        if kit.registered:  # the import service's entry point gives the same rows
            parsed = parse_upload(data, a.format, AdapterOptions(), HARD_LIMITS)
            assert (parsed.columns, parsed.rows, parsed.media_type) == (
                COLUMNS,
                rows,
                a.media_type,
            ), kit.name

        # Nothing is dropped: a cell beyond the header is kept as an extra and flagged.
        if kit.positional:
            _, [ragged] = _rows(kit, kit.make(COLUMNS, [["T-3", Decimal("1"), "EUR", "x"]], True))
            assert ragged.extras == ["x"], f"{kit.name}: extra cell {ragged}"
            assert ragged.flags == [RowFlag(RowFlagCode.EXTRA_CELLS, 4)], kit.name

        # Limits and refusals: each with its own code, positions only, never a value.
        wide = kit.make([*COLUMNS, "extra"], [[*TABLE[0], "x"]], True)
        long_cell = kit.make(COLUMNS, [["T-1", SECRET * 10, "EUR"]], True)
        for case, error, code in (
            ("columns", _refusal(kit, wide, max_columns=3), UploadRefusal.TOO_MANY_COLUMNS),
            ("rows", _refusal(kit, data, max_rows=1), UploadRefusal.TOO_MANY_ROWS),
            (
                "cell",
                _refusal(kit, long_cell, max_cell_chars=len(SECRET)),
                UploadRefusal.CELL_TOO_LONG,
            ),
            (
                "header",
                _refusal(kit, kit.make(["a", "a"], [["1", "2"]], True)),
                UploadRefusal.INVALID_HEADER,
            ),
        ):
            assert error.code == code, f"{kit.name} {case}: {error.code}, expected {code}"
            assert error.status_code == 422, f"{kit.name} {case}"
            details = error.details or {}
            assert all(type(v) in (str, int) for v in details.values()), f"{kit.name} {case}"
            text = f"{error.message} {details}"
            assert SECRET not in text and "T-1" not in text, f"{kit.name} {case}: leaks a value"

        # Isolation: an adapter touches neither the database nor the artifact store, and
        # depends on no other adapter.
        source = inspect.getsource(sys.modules[type(a).__module__])
        for forbidden in ("sqlalchemy", "app.db", "artifacts", "_adapter import", "service"):
            assert forbidden not in source, f"{kit.name}: imports {forbidden}"

    # The fake is wired in only from app/tests (the BE-13.1 grep).
    app_dir = Path(__file__).resolve().parents[1]
    tests_dir = Path(__file__).resolve().parent
    for path in app_dir.rglob("*.py"):
        if tests_dir not in path.parents:
            text = path.read_text()
            assert "FakeFeedAdapter" not in text and "fake_feed" not in text, path
