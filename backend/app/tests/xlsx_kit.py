"""Hand-written XLSX files for adapter and import tests.

openpyxl writes formulas without cached values and cannot write a macro-enabled content
type, so the parts are written directly (as the synthetic sample generator does). A cell
is a tuple:

* ("s", text): an inline string
* ("n", "12345.6"): a numeric cell holding exactly that text
* ("d", serial): a date-formatted serial number
* ("f", formula, cached): a formula with its cached numeric value (cached may be None)
* ("b", "1"): a boolean
* ("e", "#DIV/0!"): an error value
* ("at", "XFD", cell): `cell` placed at another column of the same row
* ("styled",): a cell with a style and no value
* None: no cell
"""

import io
import zipfile
from typing import Any
from xml.sax.saxutils import escape

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

Cell = tuple[Any, ...] | None


def col(i: int) -> str:
    name, i = "", i + 1
    while i:
        i, rem = divmod(i - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, cell: Cell) -> str:
    if cell is None:
        return ""
    kind = cell[0]
    if kind == "at":
        return _cell(cell[1] + ref.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), cell[2])
    if kind == "styled":
        return f'<c r="{ref}" s="1"/>'
    if kind == "s":
        return (
            f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(cell[1])}</t></is></c>'
        )
    if kind == "n":
        return f'<c r="{ref}"><v>{cell[1]}</v></c>'
    if kind == "d":
        return f'<c r="{ref}" s="1"><v>{cell[1]}</v></c>'
    if kind == "b":
        return f'<c r="{ref}" t="b"><v>{cell[1]}</v></c>'
    if kind == "e":
        return f'<c r="{ref}" t="e"><v>{escape(cell[1])}</v></c>'
    if kind == "f":
        cached = "" if cell[2] is None else f"<v>{cell[2]}</v>"
        return f'<c r="{ref}"><f>{escape(cell[1])}</f>{cached}</c>'
    raise ValueError(kind)


def xlsx(
    rows: list[list[Cell]],
    *,
    sheet: str = "ILLUSTRATIVE SYNTHETIC",
    title: str | None = "ILLUSTRATIVE / SYNTHETIC test workbook",
    sheets: tuple[str, ...] = (),
    main_type: str = XLSX_MAIN,
    vba: bool = False,
    compression: int = zipfile.ZIP_STORED,
    padding: int = 0,
) -> bytes:
    """One worksheet with `rows` (plus empty `sheets` after it). `padding` adds that many
    spaces of whitespace inside the sheet XML (for compression-ratio tests)."""
    body = "".join(
        f'<row r="{n}">' + "".join(_cell(f"{col(i)}{n}", c) for i, c in enumerate(r)) + "</row>"
        for n, r in enumerate(rows, start=1)
    )
    names = (sheet, *sheets)
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(names) + 1)
    )
    parts = {
        "[Content_Types].xml": HEAD
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{main_type}"/>{overrides}'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>',
        "_rels/.rels": HEAD + f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
        'relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
        "docProps/core.xml": HEAD + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.'
        'org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        + (f"<dc:title>{escape(title)}</dc:title>" if title else "")
        + "</cp:coreProperties>",
        "xl/workbook.xml": HEAD
        + f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>'
        + "".join(
            f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, n in enumerate(names, start=1)
        )
        + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": HEAD
        + f'<Relationships xmlns="{PKG}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(names) + 1)
        )
        + f'<Relationship Id="rId{len(names) + 1}" Type="{REL}/styles" Target="styles.xml"/>'
        "</Relationships>",
        "xl/styles.xml": HEAD + f'<styleSheet xmlns="{MAIN}">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '</cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"'
        ' xfId="0"/><xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0"'
        ' applyNumberFormat="1"/></cellXfs></styleSheet>',
        "xl/worksheets/sheet1.xml": HEAD
        + f'<worksheet xmlns="{MAIN}"><sheetData>{body}</sheetData>'
        + " " * padding
        + "</worksheet>",
    }
    for i in range(2, len(names) + 1):
        parts[f"xl/worksheets/sheet{i}.xml"] = (
            HEAD + f'<worksheet xmlns="{MAIN}"><sheetData/></worksheet>'
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for name, content in parts.items():
            zf.writestr(name, content.encode("utf-8"))
        if vba:
            zf.writestr("xl/vbaProject.bin", b"\x00fake vba project")
    return buffer.getvalue()


def table(header: list[str], *rows: list[str]) -> bytes:
    """A workbook of inline strings: a header row and data rows."""
    cells: list[list[Cell]] = [[("s", v) for v in r] for r in (header, *rows)]
    return xlsx(cells)


def rewrite(data: bytes, member: str, content: bytes) -> bytes:
    """The workbook `data` with one part replaced by `content` (to break it on purpose)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, content if info.filename == member else src.read(info))
    return out.getvalue()
