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
    CONTENT_TOO_LARGE = "content_too_large"  # D-25 §1: decoded characters, every format
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
    # Phase 11: a retry (POST /imports with retry_of_import_id, D-20 §3).
    RETRY_SOURCE_MISMATCH = "retry_source_mismatch"
    RETRY_NOT_APPLICABLE = "retry_not_applicable"


# --- Phase 11: validation, quarantine and canonical records (BE-11.1 – BE-11.5) -------------


class ValidationSeverity(StrEnum):
    """Data Model §9 `validation_issue.severity`."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"


class IssueDisposition(StrEnum):
    """D-23 `validation_issue.disposition`: what the issue does to its row (or file)."""

    ACCEPTED_WITH_WARNING = "ACCEPTED_WITH_WARNING"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    FILE_BLOCKED = "FILE_BLOCKED"


# The dispositions a severity may carry (a profile entry outside this is refused).
SEVERITY_DISPOSITIONS: dict[ValidationSeverity, frozenset[IssueDisposition]] = {
    ValidationSeverity.INFO: frozenset({IssueDisposition.ACCEPTED_WITH_WARNING}),
    ValidationSeverity.WARNING: frozenset({IssueDisposition.ACCEPTED_WITH_WARNING}),
    ValidationSeverity.ERROR: frozenset({IssueDisposition.QUARANTINED}),
    ValidationSeverity.BLOCKER: frozenset(
        {IssueDisposition.REJECTED, IssueDisposition.FILE_BLOCKED}
    ),
}


class RowOutcome(StrEnum):
    """D-20 §1 / D-23: every source row ends in exactly one outcome."""

    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class ValidationCode(StrEnum):
    """Validation issue codes (BE-11.2). An issue names a row, a field or a column and a
    rule, never a value. File-level codes have no row."""

    # File level: the mapping cannot be applied to this file (FILE_BLOCKED) or is partial.
    REQUIRED_FIELD_UNMAPPED = "REQUIRED_FIELD_UNMAPPED"
    REQUIRED_COLUMN_MISSING = "REQUIRED_COLUMN_MISSING"
    BUSINESS_KEY_COLUMN_MISSING = "BUSINESS_KEY_COLUMN_MISSING"
    MAPPING_RULE_INVALID = "MAPPING_RULE_INVALID"
    SOURCE_COLUMN_MISSING = "SOURCE_COLUMN_MISSING"
    UNMAPPED_COLUMN = "UNMAPPED_COLUMN"  # PRESERVEd or not in the contract: kept, never dropped
    # Row level, from the adapter's row facts (D-25).
    BLANK_ROW = "BLANK_ROW"
    EXTRA_CELLS = "EXTRA_CELLS"
    MISSING_CELLS = "MISSING_CELLS"
    CELL_ERROR = "CELL_ERROR"
    FORMULA_WITHOUT_CACHED_VALUE = "FORMULA_WITHOUT_CACHED_VALUE"
    FORMULA_CACHED_VALUE = "FORMULA_CACHED_VALUE"
    PRECISION_BEYOND_15_DIGITS = "PRECISION_BEYOND_15_DIGITS"
    # Row level, per field.
    REQUIRED_VALUE_MISSING = "REQUIRED_VALUE_MISSING"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_DATE = "INVALID_DATE"
    SCALE_EXCEEDED = "SCALE_EXCEEDED"
    PRECISION_EXCEEDED = "PRECISION_EXCEEDED"
    INVALID_CURRENCY_CODE = "INVALID_CURRENCY_CODE"
    UNMAPPED_VALUE = "UNMAPPED_VALUE"
    MISSING_BUSINESS_KEY = "MISSING_BUSINESS_KEY"
    DUPLICATE_BUSINESS_KEY = "DUPLICATE_BUSINESS_KEY"


# Codes that concern the whole file (no row); the rest concern one row.
FILE_LEVEL_CODES = frozenset(
    {
        ValidationCode.REQUIRED_FIELD_UNMAPPED,
        ValidationCode.REQUIRED_COLUMN_MISSING,
        ValidationCode.BUSINESS_KEY_COLUMN_MISSING,
        ValidationCode.MAPPING_RULE_INVALID,
        ValidationCode.SOURCE_COLUMN_MISSING,
        ValidationCode.UNMAPPED_COLUMN,
    }
)

_W = (ValidationSeverity.WARNING, IssueDisposition.ACCEPTED_WITH_WARNING)
_E = (ValidationSeverity.ERROR, IssueDisposition.QUARANTINED)
_R = (ValidationSeverity.BLOCKER, IssueDisposition.REJECTED)
_F = (ValidationSeverity.BLOCKER, IssueDisposition.FILE_BLOCKED)
_C = ValidationCode
# The default validation profile (PROVISIONAL, D-11): issue code -> severity -> disposition.
# Migration 0015 seeds a frozen copy (kept equal by test_validation); the engine applies the
# stored profile, never this table directly.
DEFAULT_VALIDATION_RULES: dict[ValidationCode, tuple[ValidationSeverity, IssueDisposition]] = {
    _C.REQUIRED_FIELD_UNMAPPED: _F,
    _C.REQUIRED_COLUMN_MISSING: _F,
    _C.BUSINESS_KEY_COLUMN_MISSING: _F,
    _C.MAPPING_RULE_INVALID: _F,
    _C.SOURCE_COLUMN_MISSING: _W,
    _C.UNMAPPED_COLUMN: _W,
    _C.BLANK_ROW: _R,
    _C.EXTRA_CELLS: _R,
    _C.MISSING_CELLS: _W,
    _C.CELL_ERROR: _E,
    _C.FORMULA_WITHOUT_CACHED_VALUE: _E,
    _C.FORMULA_CACHED_VALUE: (ValidationSeverity.INFO, IssueDisposition.ACCEPTED_WITH_WARNING),
    _C.PRECISION_BEYOND_15_DIGITS: _E,
    _C.REQUIRED_VALUE_MISSING: _E,
    _C.INVALID_TYPE: _E,
    _C.INVALID_DATE: _E,
    _C.SCALE_EXCEEDED: _E,
    _C.PRECISION_EXCEEDED: _E,
    _C.INVALID_CURRENCY_CODE: _E,
    _C.UNMAPPED_VALUE: _E,
    _C.MISSING_BUSINESS_KEY: _E,
    _C.DUPLICATE_BUSINESS_KEY: _E,
}
DEFAULT_VALIDATION_PROFILE_CODE = "default"
VALIDATION_PROFILE_SEED_TAG = "0015_validation_profile_seed"
VALIDATOR_VERSION = 1  # part of the manifest: a change to the engine's rules is a new version


class QuarantineStatus(StrEnum):
    """Data Model §9 `quarantine_item.status`."""

    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    REJECTED = "REJECTED"
    CORRECTED = "CORRECTED"
    RETRY_READY = "RETRY_READY"
    RESOLVED = "RESOLVED"


_Q = QuarantineStatus
# D-20 §2 transition table: status -> statuses it may move to. Enforced by the service and by
# the database trigger (0015, a frozen copy kept equal by a test). An item starts OPEN
# (QUARANTINED row) or REJECTED (REJECTED row). REJECTED and RESOLVED are final.
QUARANTINE_TRANSITIONS: dict[QuarantineStatus, frozenset[QuarantineStatus]] = {
    _Q.OPEN: frozenset({_Q.UNDER_REVIEW}),
    _Q.UNDER_REVIEW: frozenset({_Q.REJECTED, _Q.RETRY_READY, _Q.OPEN}),
    _Q.RETRY_READY: frozenset({_Q.CORRECTED}),
    _Q.CORRECTED: frozenset({_Q.RESOLVED, _Q.RETRY_READY}),
    _Q.REJECTED: frozenset(),
    _Q.RESOLVED: frozenset(),
}
QUARANTINE_INITIAL = frozenset({_Q.OPEN, _Q.REJECTED})
# Moves a reviewer makes by hand (POST .../disposition). The others happen only when a retry
# import is validated (D-20 §2: RETRY_READY -> CORRECTED -> RESOLVED | RETRY_READY).
MANUAL_QUARANTINE_MOVES: frozenset[tuple[QuarantineStatus, QuarantineStatus]] = frozenset(
    {
        (_Q.OPEN, _Q.UNDER_REVIEW),
        (_Q.UNDER_REVIEW, _Q.REJECTED),
        (_Q.UNDER_REVIEW, _Q.RETRY_READY),
        (_Q.UNDER_REVIEW, _Q.OPEN),
    }
)
# Statuses an original import's items may be retried from.
RETRYABLE_IMPORT_STATUSES = frozenset({ImportStatus.QUARANTINED, ImportStatus.FAILED})

# ISO 4217 active alphabetic codes (list one, 2024), for fields whose unit is ISO 4217.
ISO_4217_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD
    BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD
    EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD
    IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL
    MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN
    PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP
    STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED
    VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX
    YER ZAR ZMW ZWG ZWL
    """.split()
)


def is_iso4217_unit(unit: str | None) -> bool:
    """A schema field's unit names ISO 4217 ('ISO4217', 'ISO 4217', any case)."""
    return unit is not None and unit.replace(" ", "").replace("-", "").upper() == "ISO4217"


# Audit action of an upload; a refusal is recorded under the same action, DENIED (D-24 §4).
# Defined here since BE-35.2: source registration (contracts) and imports both refuse
# under it, and neither imports the other's module for it.
CREATE_IMPORT = "import.create"
