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
