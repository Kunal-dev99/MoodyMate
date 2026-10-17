"""mapping.constants

Module constants and lifecycle enums (D-06, D-18, D-19, D-23). All values are UPPERCASE
and every enum column is CHECK-constrained in the database (migration 0014).
"""

from enum import StrEnum


class MappingVersionStatus(StrEnum):
    """D-06/D-23: `mapping_version.status` is the `schema_version` set."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


_V = MappingVersionStatus
# D-06 transition table: status -> statuses it may move to. Enforced by the service and by
# the database trigger (0014 holds a frozen copy). An approval activates in the same
# transaction (D-18 §3), so APPROVED is reserved, as it is for schema change proposals.
MAPPING_VERSION_TRANSITIONS: dict[MappingVersionStatus, frozenset[MappingVersionStatus]] = {
    _V.DRAFT: frozenset({_V.IN_REVIEW, _V.REJECTED}),
    _V.IN_REVIEW: frozenset({_V.ACTIVE, _V.REJECTED}),
    _V.APPROVED: frozenset(),
    _V.ACTIVE: frozenset({_V.DEPRECATED}),
    _V.DEPRECATED: frozenset({_V.RETIRED}),
    _V.RETIRED: frozenset(),
    _V.REJECTED: frozenset(),
}


class ColumnPolicy(StrEnum):
    """D-23 `mapping_rule.column_policy`: a source column is mapped to a target field, or
    preserved as it is (never dropped)."""

    MAP = "MAP"
    PRESERVE = "PRESERVE"


class NullPolicy(StrEnum):
    """What a rule does with an empty source value (missing cell, JSON null, only spaces)."""

    ALLOW_NULL = "ALLOW_NULL"  # the target stays empty (a required target then fails)
    REJECT_NULL = "REJECT_NULL"  # the row fails this rule
    USE_DEFAULT = "USE_DEFAULT"  # `default_value` goes through the transform instead


class TransformCode(StrEnum):
    """D-19: the closed allowlist. Adding one needs code, tests and an appended decision."""

    COPY = "COPY"
    TRIM = "TRIM"
    UPPER = "UPPER"
    LOWER = "LOWER"
    PARSE_DECIMAL = "PARSE_DECIMAL"
    SCALE = "SCALE"
    PARSE_DATE = "PARSE_DATE"
    PARSE_BOOLEAN = "PARSE_BOOLEAN"
    MAP_VALUES = "MAP_VALUES"
    DEFAULT = "DEFAULT"
    CONSTANT = "CONSTANT"


class ValidationOutcome(StrEnum):
    """A dry-run validation's result. OK and WARNINGS are `ok` (the version may be
    submitted); FAILED has at least one BLOCKER or ERROR."""

    OK = "OK"
    WARNINGS = "WARNINGS"
    FAILED = "FAILED"


class IssueSeverity(StrEnum):
    BLOCKER = "BLOCKER"  # the mapping cannot be applied to this import at all
    ERROR = "ERROR"  # rows would fail (Phase 11 would quarantine or reject them)
    WARNING = "WARNING"  # worth a look; does not stop activation


class IssueCode(StrEnum):
    """Dry-run issue codes. An issue names rules, fields and columns, never a value."""

    REQUIRED_FIELD_UNMAPPED = "required_field_unmapped"
    UNKNOWN_TARGET_FIELD = "unknown_target_field"
    SOURCE_COLUMN_MISSING = "source_column_missing"
    TRANSFORM_TARGET_INCOMPATIBLE = "transform_target_incompatible"
    PROFILE_TYPE_MISMATCH = "profile_type_mismatch"
    TRANSFORM_FAILED = "transform_failed"
    COLUMN_WITHOUT_POLICY = "column_without_policy"
    DUPLICATE_BUSINESS_KEY = "duplicate_business_key"
    MISSING_BUSINESS_KEY = "missing_business_key"


class Refusal(StrEnum):
    """Error codes of refused mapping writes (each audited DENIED, D-24 §4)."""

    MAPPING_CHANGE_DISABLED = "mapping_change_disabled"
    DUPLICATE_CODE = "duplicate_code"
    CONTRACT_ALREADY_MAPPED = "contract_already_mapped"
    CONTRACT_SUPERSEDED = "contract_superseded"
    SCHEMA_MISMATCH = "schema_mismatch"
    NO_ACTIVE_VERSION = "no_active_version"
    UNKNOWN_TARGET_FIELD = "unknown_target_field"
    DUPLICATE_TARGET_FIELD = "duplicate_target_field"
    CONFLICTING_COLUMN_POLICY = "conflicting_column_policy"
    NO_CHANGE = "no_change"
    NOT_AUTHOR = "not_author"
    SELF_APPROVAL = "self_approval"
    INVALID_STATUS = "invalid_status"
    MAPPING_VALIDATION_REQUIRED = "mapping_validation_required"
    SCHEMA_VERSION_NOT_ACTIVE = "schema_version_not_active"
    COMMENT_REQUIRED = "comment_required"
    PROFILE_REQUIRED = "profile_required"
    CONTRACT_MISMATCH = "contract_mismatch"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"


# Bounds of a mapping version's content.
MAX_RULES = 500
MAX_MAP_VALUES = 500  # MAP_VALUES table entries
MAX_PARAM_TEXT = 200  # any text parameter (a value, a separator, a table key)
SCALE_MAX_POWER = 18  # SCALE(power_of_ten) in -18..18


# --- Phase 12: drift detection and review (BE-12.1, BE-12.3, BE-12.4, D-23) -------------------


class DriftType(StrEnum):
    """D-23 `data_drift_event.drift_type`: how an import's profile differs from its
    baseline's (the latest COMPLETED or COMPLETED_WITH_WARNINGS import of the same source)."""

    COLUMN_ADDED = "COLUMN_ADDED"
    COLUMN_REMOVED = "COLUMN_REMOVED"
    TYPE_CHANGED = "TYPE_CHANGED"
    NULL_RATE_SHIFT = "NULL_RATE_SHIFT"
    RANGE_SHIFT = "RANGE_SHIFT"


class DriftStatus(StrEnum):
    """D-23 `data_drift_event.status`. A person reviews an OPEN event once."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ACTION_REQUIRED = "ACTION_REQUIRED"


# D-06 transition table (migration 0016 holds a frozen copy): one review, then final.
DRIFT_TRANSITIONS: dict[DriftStatus, frozenset[DriftStatus]] = {
    DriftStatus.OPEN: frozenset({DriftStatus.ACKNOWLEDGED, DriftStatus.ACTION_REQUIRED}),
    DriftStatus.ACKNOWLEDGED: frozenset(),
    DriftStatus.ACTION_REQUIRED: frozenset(),
}
# Part of every event: a change to the detection rules is a new version.
DRIFT_DETECTOR_VERSION = 1
