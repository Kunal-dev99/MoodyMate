"""ingestion.constants

Module constants and lifecycle enums (D-06, D-23). All values are UPPERCASE and every enum
column is CHECK-constrained in the database (migration 0009).
"""

from enum import StrEnum


class SourceKind(StrEnum):
    """D-23. Only FILE_DROP is usable in the MVP; the others are reserved."""

    FILE_DROP = "FILE_DROP"
    WAREHOUSE = "WAREHOUSE"
    APPLICATION = "APPLICATION"
    FEED = "FEED"


USABLE_SOURCE_KINDS = frozenset({SourceKind.FILE_DROP})


class SourceFormat(StrEnum):
    """D-23: `source_profile.format`."""

    CSV = "CSV"
    XLSX = "XLSX"
    JSON = "JSON"


class SourceEncoding(StrEnum):
    """D-16: UTF-8 only (a BOM is allowed). Other encodings are deferred."""

    UTF_8 = "UTF-8"


class CsvDelimiter(StrEnum):
    """D-16: the delimiters a CSV source profile may name."""

    COMMA = ","
    SEMICOLON = ";"
    TAB = "\t"
    PIPE = "|"


class ContractStatus(StrEnum):
    """Data Model §9 `source_contract.status`."""

    PROVISIONAL = "PROVISIONAL"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


# D-06 transition table: status -> statuses it may move to. Enforced by the service and by
# the database trigger (0009, a frozen copy kept equal by a test), which also freezes a
# contract's content from creation in every status (D-24 §3).
CONTRACT_TRANSITIONS: dict[ContractStatus, frozenset[ContractStatus]] = {
    ContractStatus.PROVISIONAL: frozenset(
        {ContractStatus.IN_REVIEW, ContractStatus.APPROVED, ContractStatus.SUPERSEDED}
    ),
    ContractStatus.IN_REVIEW: frozenset({ContractStatus.APPROVED, ContractStatus.SUPERSEDED}),
    ContractStatus.APPROVED: frozenset({ContractStatus.SUPERSEDED}),
    ContractStatus.SUPERSEDED: frozenset(),
}

# D-11 seed tags (PROVISIONAL). Each seed migration's downgrade removes only its own rows.
WS2_PERMISSION_SEED_TAG = "0010_ws2_permissions"
SOURCE_SEED_TAG = "0011_ws2_source_seed"

# BE-7.5: every synthetic sample carries this marker (header comment, sheet name or
# top-level key, per format). XLSX sheet names cannot contain "/", see the generator.
SYNTHETIC_MARKER = "ILLUSTRATIVE / SYNTHETIC"
CSV_MARKER_LINE = f"# {SYNTHETIC_MARKER}"  # D-24 §2: the only first line a CSV may skip
XLSX_MARKER_SHEET = "ILLUSTRATIVE SYNTHETIC"  # sheet names cannot hold "/"


# --- Phase 8: imports (BE-8.1, BE-8.5) --------------------------------------------------------


class ImportStatus(StrEnum):
    """Data Model §9 `import_job.status` plus QUARANTINED from Appendix B (D-06, D-21).
    Appendix B's `uploaded` is RECEIVED and `ready` is COMPLETED."""

    CREATED = "CREATED"
    RECEIVED = "RECEIVED"
    PROFILING = "PROFILING"
    MAPPING_REQUIRED = "MAPPING_REQUIRED"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_S = ImportStatus
# D-06/D-21 transition table: status -> statuses it may move to. Enforced by the service
# (ImportService.move) and by the database trigger (0012, a frozen copy kept equal by a test).
# - upload: CREATED -> RECEIVED in the same transaction (BE-8.4);
# - profiling (Phase 9): RECEIVED -> PROFILING -> RECEIVED | MAPPING_REQUIRED;
# - validation (Phase 11) runs once: RECEIVED | MAPPING_REQUIRED -> VALIDATING -> an outcome;
# - the outcomes COMPLETED, COMPLETED_WITH_WARNINGS, QUARANTINED, FAILED and CANCELLED are
#   final. A re-run or a correction is a new import (D-20, D-21).
IMPORT_TRANSITIONS: dict[ImportStatus, frozenset[ImportStatus]] = {
    _S.CREATED: frozenset({_S.RECEIVED, _S.FAILED, _S.CANCELLED}),
    _S.RECEIVED: frozenset({_S.PROFILING, _S.VALIDATING, _S.FAILED, _S.CANCELLED}),
    _S.PROFILING: frozenset({_S.RECEIVED, _S.MAPPING_REQUIRED, _S.FAILED}),
    _S.MAPPING_REQUIRED: frozenset({_S.VALIDATING, _S.FAILED, _S.CANCELLED}),
    _S.VALIDATING: frozenset({_S.COMPLETED, _S.COMPLETED_WITH_WARNINGS, _S.QUARANTINED, _S.FAILED}),
    _S.COMPLETED: frozenset(),
    _S.COMPLETED_WITH_WARNINGS: frozenset(),
    _S.QUARANTINED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
}


class DataClass(StrEnum):
    """D-14/D-16 §5: the class of data an import was uploaded as (the environment's)."""

    SYNTHETIC = "synthetic"
    CLIENT = "client"


class RetentionState(StrEnum):
    """`import_artifact.retention_state`. D-20 §4 (PROVISIONAL): artifacts are kept
    indefinitely until the client sets retention; no other state exists yet."""

    RETAINED = "RETAINED"


class RowFlagCode(StrEnum):
    """Facts an adapter records about a row (BE-8.3). A flag names a column by its 1-based
    position, never by a value. Validation (Phase 11) decides what each one means."""

    MISSING_CELLS = "MISSING_CELLS"  # fewer cells than columns; nothing is invented
    EXTRA_CELLS = "EXTRA_CELLS"  # more cells than columns; kept in `extra_values`
    BLANK_ROW = "BLANK_ROW"  # an empty row between data rows
    NULL_VALUE = "NULL_VALUE"  # JSON null (the key is kept out of the values)
    FORMULA_CACHED_VALUE = "FORMULA_CACHED_VALUE"  # XLSX: the cached value of a formula
    FORMULA_WITHOUT_CACHED_VALUE = "FORMULA_WITHOUT_CACHED_VALUE"  # XLSX: read as ""
    PRECISION_BEYOND_15_DIGITS = "PRECISION_BEYOND_15_DIGITS"  # XLSX number (D-17)
    CELL_ERROR = "CELL_ERROR"  # XLSX error value such as #DIV/0!


class UploadRefusal(StrEnum):
    """Error codes of a refused upload (D-16, D-17, D-21, D-24). Each D-16 limit has its
    own code. None of them carries a cell value."""

    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413
    TOO_MANY_ROWS = "too_many_rows"
    TOO_MANY_COLUMNS = "too_many_columns"
    CELL_TOO_LONG = "cell_too_long"
    XLSX_UNCOMPRESSED_TOO_LARGE = "xlsx_uncompressed_too_large"
    XLSX_COMPRESSION_RATIO_TOO_HIGH = "xlsx_compression_ratio_too_high"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    FORMAT_MISMATCH = "format_mismatch"
    LEGACY_XLS_NOT_SUPPORTED = "legacy_xls_not_supported"
    MACRO_ENABLED_WORKBOOK = "macro_enabled_workbook"
    ENCODING_NOT_UTF8 = "encoding_not_utf8"
    INVALID_CHARACTER = "invalid_character"
    MISSING_HEADER = "missing_header"
    INVALID_HEADER = "invalid_header"
    CSV_MALFORMED = "csv_malformed"
    JSON_MALFORMED = "json_malformed"
    JSON_INVALID_SHAPE = "json_invalid_shape"
    JSON_NESTED_VALUE = "json_nested_value"
    SHEET_NOT_FOUND = "sheet_not_found"
    XLSX_INVALID = "xlsx_invalid"
    # Request-level refusals of POST /imports.
    SYNTHETIC_ATTESTATION_REQUIRED = "synthetic_attestation_required"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNKNOWN_CONTROL_TOTAL_COLUMN = "unknown_control_total_column"
    NO_CURRENT_CONTRACT = "no_current_contract"
    NO_ACTIVE_VERSION = "no_active_version"
