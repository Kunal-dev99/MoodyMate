"""BE-7.5 (WS2-P0): synthetic samples are reproducible and marked ILLUSTRATIVE / SYNTHETIC."""

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.ingestion.constants import SYNTHETIC_MARKER
from scripts.make_synthetic_samples import OUT, SHEET_NAME, VARIANTS, build

BACKEND = Path(__file__).resolve().parent.parent.parent
DATASETS = {"instrument": "csv", "position": "xlsx", "cashflow_event": "json"}
MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def run_generator(out: Path) -> dict[str, str]:
    """Run the script as a user would; return file name -> SHA-256."""
    subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [sys.executable, "-m", "scripts.make_synthetic_samples", "--out", str(out)],
        cwd=BACKEND,
        check=True,
        capture_output=True,
    )
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}


def test_two_runs_give_identical_files(tmp_path: Path) -> None:
    first = run_generator(tmp_path / "a")
    second = run_generator(tmp_path / "b")
    assert first == second
    expected = {f"{name}.{ext}" for name, ext in DATASETS.items()}
    expected |= {
        f"{name}__{variant}.{ext}"
        for name, ext in DATASETS.items()
        for variant in VARIANTS
        if not (variant == "non_utf8" and ext == "xlsx")
    }
    assert set(first) == expected | {"SHA256SUMS"}
    sums = (tmp_path / "a" / "SHA256SUMS").read_text().splitlines()
    assert {line.split("  ")[1]: line.split("  ")[0] for line in sums} == {
        n: h for n, h in first.items() if n != "SHA256SUMS"
    }


def _xlsx_marker(content: bytes) -> tuple[str, str]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        # Our own generator's output, not untrusted input.
        workbook = ElementTree.fromstring(zf.read("xl/workbook.xml"))  # noqa: S314
        core = zf.read("docProps/core.xml").decode()
    [sheet] = workbook.iter(f"{MAIN_NS}sheet")
    return sheet.attrib["name"], core


@pytest.mark.parametrize("name", sorted(n for n in build() if n != "SHA256SUMS"))
def test_every_file_carries_the_marker(name: str) -> None:
    content = build()[name]
    encoding = "cp1252" if "__non_utf8" in name else "utf-8"
    if name.endswith(".csv"):
        assert content.decode(encoding).splitlines()[0].startswith(f"# {SYNTHETIC_MARKER}")
    elif name.endswith(".json"):
        top = json.loads(content.decode(encoding))
        assert top["marker"].startswith(SYNTHETIC_MARKER)
    else:
        sheet, core = _xlsx_marker(content)
        assert sheet == SHEET_NAME == "ILLUSTRATIVE SYNTHETIC"  # "/" is not allowed there
        assert SYNTHETIC_MARKER in core


def _csv_rows(name: str, encoding: str = "utf-8") -> list[list[str]]:
    lines = build()[name].decode(encoding).splitlines()[1:]  # skip the marker comment
    return [line.split(",") for line in lines]


def test_bad_variants_carry_their_defect() -> None:
    good = _csv_rows("instrument.csv")
    header = good[0]
    assert "notional" not in _csv_rows("instrument__missing_required_column.csv")[0]
    assert _csv_rows("instrument__unknown_extra_column.csv")[0] == [*header, "legacy_flag"]

    def column(rows: list[list[str]], col: str) -> list[str]:
        return [r[header.index(col)] for r in rows[1:]]

    assert "2026-02-30" in column(_csv_rows("instrument__bad_date.csv"), "maturity_date")
    assert "EURO" in column(_csv_rows("instrument__bad_currency_code.csv"), "currency")
    keys = column(_csv_rows("instrument__duplicate_business_key.csv"), "instrument_id")
    assert len(keys) != len(set(keys))
    over = column(_csv_rows("instrument__over_scale_decimal.csv"), "notional")
    assert any(len(v.split(".")[1]) > 2 for v in over)
    with pytest.raises(UnicodeDecodeError):
        build()["instrument__non_utf8.csv"].decode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        build()["cashflow_event__non_utf8.json"].decode("utf-8")
    # The good files are clean: unique keys, valid UTF-8, every decimal at scale 2.
    assert len(set(column(good, "instrument_id"))) == len(good) - 1
    assert all(len(v.split(".")[1]) == 2 for v in column(good, "notional"))


def test_json_decimals_are_exact_numbers() -> None:
    from decimal import Decimal

    records = json.loads(build()["cashflow_event.json"], parse_float=Decimal)["records"]
    assert all(isinstance(r["amount"], Decimal) for r in records)
    assert all(r["amount"].as_tuple().exponent == -2 for r in records)


def test_committed_samples_are_current() -> None:
    for name, content in build().items():
        assert (
            OUT / name
        ).read_bytes() == content, f"{name}: run `python -m scripts.make_synthetic_samples`"
