"""Source adapters (BE-8.3, DI-02): one per file format behind the `SourceAdapter`
interface, so later formats (and Phase 13's adapter readiness work) add an adapter, not a
branch in the service. `parse_upload` is the single entry point the import service uses.
"""

from dataclasses import dataclass

from app.ingestion.adapters.base import (
    HARD_LIMITS,
    OLE_MAGIC,
    AdapterError,
    AdapterOptions,
    Limits,
    RowFlag,
    SourceAdapter,
    SourceRow,
    collect,
)
from app.ingestion.adapters.csv_adapter import CsvAdapter
from app.ingestion.adapters.json_adapter import JsonAdapter
from app.ingestion.adapters.xlsx_adapter import XlsxAdapter
from app.ingestion.constants import SourceFormat, UploadRefusal

ADAPTERS: dict[SourceFormat, SourceAdapter] = {
    SourceFormat.CSV: CsvAdapter(),
    SourceFormat.XLSX: XlsxAdapter(),
    SourceFormat.JSON: JsonAdapter(),
}

__all__ = [
    "ADAPTERS",
    "HARD_LIMITS",
    "AdapterError",
    "AdapterOptions",
    "Limits",
    "ParsedUpload",
    "RowFlag",
    "SourceAdapter",
    "SourceRow",
    "parse_upload",
]


@dataclass(frozen=True)
class ParsedUpload:
    format: SourceFormat
    media_type: str  # detected from content
    columns: list[str]
    rows: list[SourceRow]
    marker_present: bool
    sheet_name: str | None


def payload_too_large(limits: Limits) -> AdapterError:
    return AdapterError(
        UploadRefusal.PAYLOAD_TOO_LARGE,
        f"The file is larger than {limits.max_bytes} bytes.",
        {"limit": limits.max_bytes},
        status_code=413,
    )


def parse_upload(
    data: bytes, fmt: SourceFormat, options: AdapterOptions, limits: Limits
) -> ParsedUpload:
    """Parse a whole upload with the profile's adapter, or raise AdapterError."""
    if len(data) > limits.max_bytes:
        raise payload_too_large(limits)
    if data.startswith(OLE_MAGIC):
        raise AdapterError(
            UploadRefusal.LEGACY_XLS_NOT_SUPPORTED,
            "Legacy .xls workbooks are not accepted; save it as .xlsx.",
        )
    adapter = ADAPTERS[fmt]
    if not adapter.detect(data):
        detected = [f for f, a in ADAPTERS.items() if f != fmt and a.detect(data)]
        if detected:
            raise AdapterError(
                UploadRefusal.FORMAT_MISMATCH,
                f"The source expects {fmt} but the file is {detected[0]}.",
                {"expected": fmt.value, "detected": detected[0].value},
            )
        raise AdapterError(UploadRefusal.UNSUPPORTED_FILE_TYPE, f"The file is not a {fmt} file.")
    stream = adapter.iter_rows(data, options, limits)
    rows = collect(stream.drafts, limits)
    return ParsedUpload(
        fmt, adapter.media_type, stream.columns, rows, stream.marker_present, stream.sheet_name
    )
