# Source adapter contract (BE-13.1, DI-02)

**Status:** describes the code in `backend/app/ingestion/adapters/` as of Feature 02 Phase 13.
The contract is enforced by `test_every_adapter_meets_the_contract` in
`backend/app/tests/test_adapters.py`. That test runs one loop over CSV, XLSX, JSON and a
test-only in-memory `FakeFeedAdapter` (`backend/app/tests/fake_feed.py`). If the code and
this document disagree, the code and its test win, and this document is corrected.

A source adapter knows one input format. Adding a format, or later a feed or warehouse
source, means adding an adapter. It never means adding a branch to the import service.
Real feed, warehouse, API and event connectors are deferred (DELIVERY_PLAN "Deferred"). Only
the interface and the test fake exist.

## 1. Interface

`app.ingestion.adapters.base.SourceAdapter` (an ABC):

| Member | Type | Contract |
|---|---|---|
| `format` | `ClassVar[SourceFormat]` | The `source_profile.format` value the adapter serves. |
| `media_type` | `ClassVar[str]` | The media type recorded as `import_artifact.detected_mime`. It comes from the content, never from the client's `Content-Type` or the file name. |
| `detect(data: bytes) -> bool` | method | True when the **content** is this format. The file name is never consulted. It must be false for content that another registered adapter claims, so that `parse_upload` can tell `format_mismatch` from `unsupported_file_type`. |
| `iter_rows(data: bytes, options: AdapterOptions, limits: Limits) -> RowStream` | method | Reads the header and returns a `RowStream`. Raises `AdapterError` on refusal. |

`RowStream`:

| Field | Type | Meaning |
|---|---|---|
| `columns` | `list[str]` | The header in source order. Each name is unique and non-blank (`check_header`). |
| `marker_present` | `bool` | Whether the file carries the `ILLUSTRATIVE / SYNTHETIC` marker (D-24 §2). |
| `drafts` | `Iterator[Draft]` | A **lazy** iterator of rows. Rows are read on demand, never pre-built as a list. |
| `sheet_name` | `str \| None` | XLSX only: the sheet that was read. Recorded on `import_batch`. |

`Draft` is a row as the adapter read it, before numbering. It has `locator`, `values`,
`extras`, `flags`, and `blank` (an empty row).

`AdapterOptions` comes from the source profile. It has `delimiter` (CSV) and `sheet_name`
(XLSX). Adapters ignore options they do not use.

### Rules every adapter follows

- **Pure.** No database, no artifact store, no network, no other adapter. An adapter turns
  bytes into rows and nothing else. The contract test checks that the adapter's module
  imports none of `sqlalchemy`, `app.db`, `artifacts`, a service or another `*_adapter`.
- **Shared helpers.** Use the helpers in `base.py`:
  - `decode_utf8` (UTF-8 only, BOM removed);
  - `check_header`;
  - `check_cell`;
  - `from_cells`, which maps positional cells to values, keeps extras and flags
    MISSING_CELLS/EXTRA_CELLS;
  - `too_many_columns` and `too_many_rows`.

  These keep codes and details identical across formats.
- **Nothing is dropped (D-20).** Every cell with data ends up in `values` or `extras`. A
  blank row *between* data rows becomes a row flagged `BLANK_ROW`. Blank rows after the last
  data row are not rows (`collect`).
- **Nothing is invented.** A short row is flagged `MISSING_CELLS`. No default value is
  filled in. The exception is XLSX, where an empty cell inside the header width reads as
  `""`.

## 2. Entry point and numbering

`parse_upload(data, fmt, options, limits) -> ParsedUpload` is the only entry point the import
service uses (`ImportService`, run in a thread pool). It works in this order:

1. More than `limits.max_bytes` → `payload_too_large` (413).
2. OLE2 magic (legacy `.xls`) → `legacy_xls_not_supported`.
3. `ADAPTERS[fmt].detect(data)` is false:
   - another registered adapter detects the data → `format_mismatch`, with details
     `{expected, detected}`;
   - no adapter detects it → `unsupported_file_type`.
4. `iter_rows`, then `collect(drafts, limits)`. This numbers the rows and applies the row
   limit.

`ParsedUpload` has `format`, `media_type`, `columns`, `rows: list[SourceRow]`,
`marker_present` and `sheet_name`.

`SourceRow` (frozen):

| Field | Type | Meaning |
|---|---|---|
| `ordinal` | `int` | 1-based position among the data rows, contiguous. Blank interior rows count. |
| `locator` | `dict[str, str \| int]` | Where the row is in the source, by position only (below). Unique per row. |
| `values` | `dict[str, str]` | Column name → cell text, for the cells the row has. **Always `str`.** |
| `extras` | `list[str]` | Cells beyond the header, in order. Never dropped. |
| `flags` | `list[RowFlag]` | Facts about the row: `RowFlag(code: RowFlagCode, column: int \| None)`. `column` is the 1-based position, never a name or a value. Sorted by column, then code. |

## 3. Locator shapes

| Adapter | Locator | Meaning |
|---|---|---|
| CSV | `{"line": n}` | The physical line the record starts on (1-based, the marker line included). |
| XLSX | `{"sheet": name, "row": n}` | The worksheet and its 1-based row number (the header is row 1). |
| JSON | `{"index": i}` | 0-based index in the records array. |
| FakeFeed (test only) | `{"offset": n}` | 0-based message offset in the delivery. |

A new adapter picks keys that name a **position** in its source, such as an offset, a
partition, or a page and index. Keys are strings and values are `str` or `int`. A locator
never holds a cell value or a business key.

## 4. Limits (D-16 §1)

`Limits` (frozen). The defaults are the hard ceilings in `app.core.config.IMPORT_HARD_LIMITS`.
The `IMPORT_MAX_*` settings may lower them and may never raise them.

| Field | Hard ceiling | Refusal code | Applies to |
|---|---|---|---|
| `max_bytes` | 10 MiB | `payload_too_large` (413) | every upload (checked before any adapter runs) |
| `max_rows` | 50,000 data rows | `too_many_rows` | every adapter (`collect`; JSON also checks it early) |
| `max_columns` | 200 | `too_many_columns` | header and every row |
| `max_cell_chars` | 4,000 characters | `cell_too_long` | every header name and cell |
| `max_xlsx_uncompressed_bytes` | 50 MiB | `xlsx_uncompressed_too_large` | XLSX archive, before decompression |
| `max_xlsx_ratio` | 100 | `xlsx_compression_ratio_too_high` | XLSX archive, before decompression |

Every adapter must enforce `max_rows`, `max_columns` and `max_cell_chars`, with these codes.
The contract test checks all three. A streaming adapter should refuse as soon as a limit is
crossed, without reading the rest of the input. The whole input is already bounded by
`max_bytes`.

## 5. Error codes

A refusal is an `AdapterError(code: UploadRefusal, message, details, status_code=422)`. The
import service turns it into the error envelope `{code, message, details, correlation_id}`
and records a DENIED audit event. **`details` hold positions and limits only**, such as
`line`, `row`, `sheet`, `index`, `offset`, `column`, `byte_offset`, `limit` and `reason`.
They never hold a cell value or a column name (D-24). The message is fixed text. The
contract test plants a sentinel value and checks that no refusal carries it.

| Code | When |
|---|---|
| `payload_too_large` (413) | Over `max_bytes`. |
| `too_many_rows`, `too_many_columns`, `cell_too_long` | Over a D-16 limit. |
| `xlsx_uncompressed_too_large`, `xlsx_compression_ratio_too_high` | XLSX zip-bomb guard. |
| `unsupported_file_type` | No registered adapter detects the content. |
| `format_mismatch` | Another registered adapter detects the content. |
| `legacy_xls_not_supported` | OLE2 content (`.xls`). |
| `macro_enabled_workbook` | `.xlsm` content type or a VBA project. |
| `encoding_not_utf8` | Not valid UTF-8 (`byte_offset`). |
| `invalid_character` | A NUL character. |
| `missing_header` | No header row, or no sheet. |
| `invalid_header` | A blank or duplicate column name, or a repeated JSON key (`reason`). |
| `csv_malformed` | A CSV quoting error (`line`). |
| `json_malformed`, `json_invalid_shape`, `json_nested_value` | Bad JSON, the wrong top-level shape, or a nested value. |
| `sheet_not_found` | The profile names a sheet the workbook does not have. |
| `xlsx_invalid` | Any workbook parser failure, including XML entity attacks. |

A new adapter reuses these codes where they fit. A new code is added to `UploadRefusal`
(and the OpenAPI contract), never invented ad hoc.

## 6. The no-float rule

Values leave an adapter as **exact text**. The type is `str`, never `float`, and it is
never rounded:

| Adapter | How |
|---|---|
| CSV | Cells are text as read. |
| JSON | `json.loads(..., parse_float=Decimal)`, so a number keeps its exact text. NaN and Infinity are refused (`json_malformed`). `true`/`false` become `"true"`/`"false"`. `null` keeps the key out of `values` and is flagged `NULL_VALUE`. |
| XLSX | XLSX stores numbers as IEEE doubles. A numeric cell becomes `str(Decimal(repr(x)))`, the shortest round trip, and is flagged `PRECISION_BEYOND_15_DIGITS` beyond 15 significant digits. Dates become ISO-8601 text. Formulas are never evaluated: the cached value is used and flagged `FORMULA_CACHED_VALUE`, or flagged `FORMULA_WITHOUT_CACHED_VALUE` when there is none. |
| FakeFeed | As JSON. |

Typing happens later, in mapping and validation, with `Decimal` at the schema field's
precision and scale (D-22). The contract test checks that every value has `type(v) is str`
and that `100.25` and `-0.5` come out as exactly `"100.25"` and `"-0.5"` from every
adapter.

## 7. Provenance fields

The adapter supplies the facts and the import service records them, in the same
transaction as the upload's audit event (BE-8.4):

| Recorded on | Field | From |
|---|---|---|
| `import_artifact` | `sha256`, `size_bytes`, `storage_ref` | The artifact store (content-addressed, write-once, D-15) |
| | `detected_mime` | `adapter.media_type` |
| | `original_filename`, `classification`, `retention_state` | The request, the source profile's default classification, and `RETAINED` (D-20 §4) |
| `import_job` | `format`, `synthetic_marker`, `row_count`, `data_class` | The profile, `RowStream.marker_present`, `len(rows)`, and the environment's data class (D-14) |
| `import_batch` | `columns`, `sheet_name`, `row_count` | `RowStream.columns` and `RowStream.sheet_name` |
| `source_record` | `ordinal`, `locator`, `raw_values`, `extra_values`, `flags`, `row_sha256` | `SourceRow`, plus SHA-256 of the canonical JSON of `{values, extras}` (`row_sha256`) |

From there, `record_lineage` ties each `canonical_record` back to its `source_record`, and
the import manifest pins the artifact hash (Phase 11). An adapter therefore never needs its
own provenance store. It must make `locator` and `ordinal` stable, so that the same bytes
always give the same rows.

## 8. Adding an adapter

1. Implement `SourceAdapter` in `backend/app/ingestion/adapters/<name>_adapter.py` using the
   shared helpers in `base.py`.
2. Add a `SourceFormat` member, and a forward migration that widens the
   `source_profile.format` and `import_job.format` CHECK constraints
   (`ck_source_profile_format`, `ck_import_job_format`). Widening means replacing a
   constraint, so get a named decision under D-01 first. Under D-02 the downgrade must
   refuse while rows use the new value.
3. Register it in `ADAPTERS` in `backend/app/ingestion/adapters/__init__.py`. Make sure its
   `detect` and the others' `detect` do not overlap. In particular, CSV claims any text that
   is not JSON.
4. Add a `Kit` to `contract_kits()` in `backend/app/tests/test_adapters.py`, with a `make`
   function that writes a table in the new format. The contract test must pass unchanged.
5. A non-file source (feed, warehouse) also needs an `import_job` producer other than
   `POST /imports`. That belongs to the deferred job model (WS4 RO-01, D-21) and is out of
   scope for this contract.

`FakeFeedAdapter` is the worked example of steps 1 and 4. It is never registered, and no
module under `backend/app` outside `backend/app/tests` refers to it. The contract test
checks that too.
