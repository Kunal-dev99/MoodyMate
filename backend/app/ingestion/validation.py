"""ingestion.validation (BE-35.2: split from ingestion.service, which re-exports it)

The validation engine (BE-11.2): a pure, deterministic function of an import's records in
file order, the pinned mapping version's rules, the pinned schema version's fields, the
contract's business key and a validation profile. Every row ends in exactly one outcome
(D-20 §1); a finding names rows, fields, columns and rules, never a value.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.core.canonical import HashKind, sha256_for
from app.governance.constants import FieldType
from app.ingestion.constants import (
    FILE_LEVEL_CODES,
    ISO_4217_CODES,
    SEVERITY_DISPOSITIONS,
    ImportStatus,
    IssueDisposition,
    RowFlagCode,
    RowOutcome,
    ValidationCode,
    ValidationSeverity,
    is_iso4217_unit,
)
from app.mapping.transforms import TargetField, Transform, TransformError, apply, compile_transform

# --- Phase 11: the validation engine (BE-11.2) ---------------------------------------------------
#
# A pure, deterministic function of the import's records in file order, the pinned mapping
# version's rules, the pinned schema version's fields, the contract's business key and a
# validation profile. Every row ends in exactly one outcome (D-20 §1); nothing is dropped,
# nothing is rounded (Decimal only, through the D-19 transform engine), and a finding names
# rows, fields, columns and rules, never a value.


@dataclass(frozen=True)
class MappedRule:
    """One rule of the pinned mapping version, as validation applies it."""

    position: int
    source_column: str | None
    target_field: str | None
    column_policy: str  # MAP | PRESERVE (D-23)
    transform_code: str
    transform_params: dict[str, Any]
    null_policy: str
    default_value: str | None

    @classmethod
    def of(cls, rule: Any) -> "MappedRule":
        return cls(
            rule.position,
            rule.source_column,
            rule.target_field,
            rule.column_policy,
            rule.transform_code,
            dict(rule.transform_params),
            rule.null_policy,
            rule.default_value,
        )


@dataclass(frozen=True)
class Finding:
    """One validation issue (a `validation_issue` row before it has ids)."""

    sequence: int
    ordinal: int | None
    code: ValidationCode
    severity: ValidationSeverity
    disposition: IssueDisposition
    field: str | None
    source_column: str | None
    rule_position: int | None
    message: str


@dataclass(frozen=True)
class RowResult:
    ordinal: int
    outcome: RowOutcome
    findings: list[Finding]
    payload: dict[str, str | None] | None  # ACCEPTED rows only
    trace: list[dict[str, Any]]
    key_sha256: str | None  # the business key's hash, when the row has a full key
    reason_codes: list[str]  # why a held row is held


def profile_rules_sha256(rules: Mapping[str, Mapping[str, str]]) -> str:
    """Content hash of a profile's rules. Migration 0015 holds a frozen copy."""
    return sha256_for(HashKind.VALIDATION_PROFILE_RULES, rules)


def profile_entries(
    rules: Mapping[str, Mapping[str, str]],
) -> dict[ValidationCode, tuple[ValidationSeverity, IssueDisposition]]:
    """A stored profile as the engine applies it. A code the profile lacks, or an entry
    whose disposition does not fit its severity, fails closed: a file-level code blocks the
    file, a row-level code quarantines the row."""
    entries: dict[ValidationCode, tuple[ValidationSeverity, IssueDisposition]] = {}
    for code in ValidationCode:
        file_level = code in FILE_LEVEL_CODES
        entries[code] = _entry(rules.get(code.value), file_level) or (
            (ValidationSeverity.BLOCKER, IssueDisposition.FILE_BLOCKED)
            if file_level
            else (ValidationSeverity.ERROR, IssueDisposition.QUARANTINED)
        )
    return entries


def _entry(
    entry: Mapping[str, str] | None, file_level: bool
) -> tuple[ValidationSeverity, IssueDisposition] | None:
    """A profile entry, if it is well formed and fits its code's level."""
    try:
        severity = ValidationSeverity(entry["severity"]) if entry else None
        disposition = IssueDisposition(entry["disposition"]) if entry else None
    except (KeyError, TypeError, ValueError):
        return None
    if severity is None or disposition is None:
        return None
    if disposition not in SEVERITY_DISPOSITIONS[severity]:
        return None
    if (disposition == IssueDisposition.FILE_BLOCKED) != (
        file_level and severity == ValidationSeverity.BLOCKER
    ):
        return None
    if file_level and disposition in (IssueDisposition.QUARANTINED, IssueDisposition.REJECTED):
        return None  # a file-level issue blocks the file or warns; it holds no row
    return severity, disposition


# Transform engine reason codes (D-19) -> validation codes; anything else is INVALID_TYPE.
_REASONS: dict[str, ValidationCode] = {
    "required_value_missing": ValidationCode.REQUIRED_VALUE_MISSING,
    "null_value": ValidationCode.REQUIRED_VALUE_MISSING,
    "invalid_date": ValidationCode.INVALID_DATE,
    "not_a_date": ValidationCode.INVALID_DATE,
    "scale_exceeded": ValidationCode.SCALE_EXCEEDED,
    "precision_exceeded": ValidationCode.PRECISION_EXCEEDED,
    "unmapped_value": ValidationCode.UNMAPPED_VALUE,
}
# Row facts (D-25) that are issues. NULL_VALUE is not: an empty value meets the required
# check like any other.
_FLAG_CODES: dict[str, ValidationCode] = {
    RowFlagCode.BLANK_ROW: ValidationCode.BLANK_ROW,
    RowFlagCode.EXTRA_CELLS: ValidationCode.EXTRA_CELLS,
    RowFlagCode.MISSING_CELLS: ValidationCode.MISSING_CELLS,
    RowFlagCode.CELL_ERROR: ValidationCode.CELL_ERROR,
    RowFlagCode.FORMULA_WITHOUT_CACHED_VALUE: ValidationCode.FORMULA_WITHOUT_CACHED_VALUE,
    RowFlagCode.FORMULA_CACHED_VALUE: ValidationCode.FORMULA_CACHED_VALUE,
    RowFlagCode.PRECISION_BEYOND_15_DIGITS: ValidationCode.PRECISION_BEYOND_15_DIGITS,
}
_FLAG_MESSAGES: dict[ValidationCode, str] = {
    ValidationCode.BLANK_ROW: "The row is blank.",
    ValidationCode.EXTRA_CELLS: "The row has more cells than the header.",
    ValidationCode.MISSING_CELLS: "The row has fewer cells than the header.",
    ValidationCode.CELL_ERROR: "The cell holds a spreadsheet error value.",
    ValidationCode.FORMULA_WITHOUT_CACHED_VALUE: "The cell is a formula without a cached value.",
    ValidationCode.FORMULA_CACHED_VALUE: "The cell's value is a formula's cached result.",
    ValidationCode.PRECISION_BEYOND_15_DIGITS: "The spreadsheet number has more than 15 "
    "significant digits, so digits may have been lost.",
}
_OUTCOME_RANK = {
    IssueDisposition.ACCEPTED_WITH_WARNING: 0,
    IssueDisposition.QUARANTINED: 1,
    IssueDisposition.REJECTED: 2,
    IssueDisposition.FILE_BLOCKED: 3,
}
MESSAGE_MAX = 500


def _field_problem(code: ValidationCode, target: TargetField) -> str:
    match code:
        case ValidationCode.REQUIRED_VALUE_MISSING:
            return "a required value is missing"
        case ValidationCode.INVALID_DATE:
            return "the value is not a valid date in the expected format"
        case ValidationCode.SCALE_EXCEEDED:
            return f"the value has more than {target.scale} decimal places (never rounded)"
        case ValidationCode.PRECISION_EXCEEDED:
            digits = (target.precision or 0) - (target.scale or 0)
            return f"the value has more than {digits} integer digits"
        case ValidationCode.INVALID_CURRENCY_CODE:
            return "the value is not an ISO 4217 currency code"
        case ValidationCode.UNMAPPED_VALUE:
            return "the value is not in the rule's value table"
        case _:
            return f"the value is not a valid {target.data_type}"


def business_key_sha256(values: Mapping[str, str], columns: Sequence[str]) -> str | None:
    """The hash of a row's business key (trimmed), or None when any part is empty."""
    key = [(values.get(c) or "").strip() for c in columns]
    if not columns or not all(key):
        return None
    return sha256_for(HashKind.BUSINESS_KEY, key)


def payload_sha256(payload: Mapping[str, str | None]) -> str:
    return sha256_for(HashKind.CANONICAL_PAYLOAD, payload)


ValidatorInput = tuple[int, Mapping[str, str], Sequence[str], Sequence[Mapping[str, Any]]]


class Validator:
    """Feed records (ordinal, values, extras, flags) in file order with `add`; read the
    file-level `findings` first and the `tally` at the end.

    Checks: per `schema_field` type and required (an empty or blank cell is missing, so the
    "" an XLSX pads with is missing too, D-25), DECIMAL precision and scale (over-scale is an
    error, never rounded), date validity and format, ISO 4217 codes for fields whose unit is
    ISO 4217, duplicate and empty business keys, the adapter's row facts, and file-level
    blockers. Columns PRESERVEd or not mapped stay in `source_record` and give one
    UNMAPPED_COLUMN warning each (never dropped)."""

    def __init__(
        self,
        rules: Sequence[MappedRule],
        fields: Mapping[str, TargetField],
        units: Mapping[str, str | None],
        columns: Sequence[str],
        business_keys: Sequence[str],
        profile: Mapping[ValidationCode, tuple[ValidationSeverity, IssueDisposition]],
    ) -> None:
        self.fields = dict(fields)
        self.columns = list(columns)
        self.business_keys = list(business_keys)
        self.profile = profile
        self.currency = frozenset(n for n, u in units.items() if is_iso4217_unit(u))
        self.findings: list[Finding] = []  # file-level, available after __init__
        self._sequence = 0
        self._first: dict[str, int] = {}  # business key hash -> first row
        self.accepted = self.quarantined = self.rejected = 0
        self.warnings = 0
        present = set(self.columns)
        self.runs: list[tuple[MappedRule, Transform, TargetField]] = []
        mapped_columns: set[str] = set()
        for rule in rules:
            if rule.column_policy != "MAP":
                continue
            target = self.fields.get(rule.target_field or "")
            try:
                transform = compile_transform(rule.transform_code, rule.transform_params)
            except TransformError:
                transform = None
            if target is None or transform is None:
                self._file(
                    ValidationCode.MAPPING_RULE_INVALID,
                    f"Rule {rule.position} cannot be applied to the pinned schema version.",
                    field=rule.target_field,
                    column=rule.source_column,
                    rule=rule.position,
                )
                continue
            if rule.source_column is not None:
                mapped_columns.add(rule.source_column)
                if rule.source_column not in present:
                    code = (
                        ValidationCode.REQUIRED_COLUMN_MISSING
                        if target.required
                        else ValidationCode.SOURCE_COLUMN_MISSING
                    )
                    self._file(
                        code,
                        f"Column '{rule.source_column}' (rule {rule.position}) feeds "
                        f"{'required ' if target.required else ''}field '{target.name}' but "
                        "is not in the file.",
                        field=target.name,
                        column=rule.source_column,
                        rule=rule.position,
                    )
                    continue
            self.runs.append((rule, transform, target))
        targeted = {r.target_field for r in rules if r.column_policy == "MAP"}
        for name, target in self.fields.items():
            if target.required and name not in targeted:
                self._file(
                    ValidationCode.REQUIRED_FIELD_UNMAPPED,
                    f"Required field '{name}' is not mapped by the mapping version.",
                    field=name,
                )
        for column in self.business_keys:
            if column not in present:
                self._file(
                    ValidationCode.BUSINESS_KEY_COLUMN_MISSING,
                    f"Business key column '{column}' is not in the file.",
                    column=column,
                )
        for column in self.columns:
            if column not in mapped_columns:
                self._file(
                    ValidationCode.UNMAPPED_COLUMN,
                    f"Column '{column}' is not mapped to a schema field; its values are kept "
                    "in the source record.",
                    column=column,
                )
        self.blocked = [f.code for f in self.findings if f.disposition == "FILE_BLOCKED"]
        self.key_field = next(
            (r.target_field for r, _, _ in self.runs if r.source_column in self.business_keys[:1]),
            None,
        )

    # --- findings ---------------------------------------------------------------------------

    def _finding(
        self,
        ordinal: int | None,
        code: ValidationCode,
        message: str,
        field: str | None = None,
        column: str | None = None,
        rule: int | None = None,
    ) -> Finding:
        severity, disposition = self.profile[code]
        self._sequence += 1
        if severity == ValidationSeverity.WARNING:
            self.warnings += 1
        return Finding(
            self._sequence,
            ordinal,
            code,
            severity,
            disposition,
            field,
            column,
            rule,
            message[:MESSAGE_MAX],
        )

    def _file(self, code: ValidationCode, message: str, **where: Any) -> None:
        self.findings.append(self._finding(None, code, message, **where))

    # --- rows --------------------------------------------------------------------------------

    def add(
        self,
        ordinal: int,
        values: Mapping[str, str],
        extras: Sequence[str],
        flags: Sequence[Mapping[str, Any]],
    ) -> RowResult:
        key = business_key_sha256(values, self.business_keys)
        if self.blocked:
            # D-20 §1: a file-level BLOCKER fails the import; no row is accepted or dropped.
            self.rejected += 1
            return RowResult(
                ordinal, RowOutcome.REJECTED, [], None, [], key, sorted(set(self.blocked))
            )
        findings: list[Finding] = []
        blank = False
        for flag in flags:
            fact = _FLAG_CODES.get(str(flag.get("code")))
            if fact is None:
                continue
            blank = blank or fact == ValidationCode.BLANK_ROW
            position = flag.get("column")
            column = (
                self.columns[position - 1]
                if isinstance(position, int) and 1 <= position <= len(self.columns)
                else None
            )
            findings.append(self._finding(ordinal, fact, _FLAG_MESSAGES[fact], column=column))
        payload: dict[str, str | None] = dict.fromkeys(sorted(self.fields))
        trace: list[dict[str, Any]] = []
        if not blank:
            for rule, transform, target in self.runs:
                raw = values.get(rule.source_column) if rule.source_column is not None else None
                outcome = apply(transform, raw, rule.null_policy, rule.default_value, target)
                code: ValidationCode | None = None
                if outcome.reason is not None:
                    code = _REASONS.get(outcome.reason, ValidationCode.INVALID_TYPE)
                elif outcome.text is not None:
                    if target.name in self.currency and outcome.text not in ISO_4217_CODES:
                        code = ValidationCode.INVALID_CURRENCY_CODE
                    else:
                        payload[target.name] = outcome.text
                if code is not None:
                    findings.append(
                        self._finding(
                            ordinal,
                            code,
                            f"'{target.name}' (rule {rule.position}): "
                            f"{_field_problem(code, target)}.",
                            field=target.name,
                            column=rule.source_column,
                            rule=rule.position,
                        )
                    )
                trace.append(
                    {
                        "field": target.name,
                        "source_column": rule.source_column,
                        "rule_position": rule.position,
                        "transform": transform.code.value,
                    }
                )
            findings.extend(self._key_findings(ordinal, key))
        worst = max((_OUTCOME_RANK[f.disposition] for f in findings), default=0)
        held = sorted({f.code.value for f in findings if _OUTCOME_RANK[f.disposition] > 0})
        if worst >= _OUTCOME_RANK[IssueDisposition.REJECTED]:
            self.rejected += 1
            return RowResult(ordinal, RowOutcome.REJECTED, findings, None, [], key, held)
        if worst == _OUTCOME_RANK[IssueDisposition.QUARANTINED]:
            self.quarantined += 1
            return RowResult(ordinal, RowOutcome.QUARANTINED, findings, None, [], key, held)
        self.accepted += 1
        return RowResult(ordinal, RowOutcome.ACCEPTED, findings, payload, trace, key, [])

    def _key_findings(self, ordinal: int, key: str | None) -> list[Finding]:
        if not self.business_keys:
            return []
        label = ", ".join(self.business_keys)
        field, column = self.key_field, self.business_keys[0]
        if key is None:
            return [
                self._finding(
                    ordinal,
                    ValidationCode.MISSING_BUSINESS_KEY,
                    f"The business key ({label}) is empty.",
                    field=field,
                    column=column,
                )
            ]
        first = self._first.setdefault(key, ordinal)
        if first == ordinal:
            return []
        return [
            self._finding(
                ordinal,
                ValidationCode.DUPLICATE_BUSINESS_KEY,
                f"The business key ({label}) repeats the one of row {first}.",
                field=field,
                column=column,
            )
        ]

    def add_all(self, rows: Iterable[ValidatorInput]) -> list[RowResult]:
        return [self.add(*row) for row in rows]

    # --- the result ----------------------------------------------------------------------------

    def status(self) -> ImportStatus:
        """D-21: FAILED on a file-level BLOCKER, QUARANTINED with any held row, else
        COMPLETED (with warnings, when any)."""
        if self.blocked:
            return ImportStatus.FAILED
        if self.quarantined or self.rejected:
            return ImportStatus.QUARANTINED
        return ImportStatus.COMPLETED_WITH_WARNINGS if self.warnings else ImportStatus.COMPLETED


def validate_rows(
    rules: Sequence[MappedRule],
    fields: Mapping[str, TargetField],
    units: Mapping[str, str | None],
    columns: Sequence[str],
    business_keys: Sequence[str],
    profile: Mapping[ValidationCode, tuple[ValidationSeverity, IssueDisposition]],
    rows: Iterable[ValidatorInput],
) -> tuple[Validator, list[RowResult]]:
    """The validation of these records (a pure function; BE-11.2)."""
    validator = Validator(rules, fields, units, columns, business_keys, profile)
    return validator, validator.add_all(rows)


def target_fields_of(version: Any) -> tuple[dict[str, TargetField], dict[str, str | None]]:
    """A schema version's fields as the transform engine fits values to them, and units."""
    fields = {
        f.name: TargetField(
            name=f.name,
            data_type=FieldType(f.data_type),
            required=f.required,
            precision=f.precision,
            scale=f.scale,
            classification=f.classification,
        )
        for f in version.fields
    }
    return fields, {f.name: f.unit for f in version.fields}


# The engine's parts, for the test that proves it holds no float (BE-11.2).
VALIDATOR_PARTS = (
    "profile_entries",
    "business_key_sha256",
    "payload_sha256",
    "Validator",
    "validate_rows",
)
