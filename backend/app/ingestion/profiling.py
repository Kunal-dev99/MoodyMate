"""ingestion.profiling (BE-35.2: split from ingestion.service, which re-exports it)

Field profiling (BE-9.2, BE-9.3): the pure, deterministic profiler of an import's records
(`Profiler`, `profile_sha256`), the profile view (BE-9.3), and `ImportProfiling`, which
stores an import's profile snapshot (DI-06: it observes and suggests, it never changes a
schema, a contract or a record).
"""

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from starlette.concurrency import run_in_threadpool

from app.core.canonical import HashKind, sha256_for
from app.core.decimals import SOURCE, DecimalTextError, parse_decimal_text
from app.core.errors import ApiError
from app.governance.constants import FieldType
from app.governance.service import audit_by
from app.ingestion.constants import ImportStatus, RowFlagCode
from app.ingestion.contracts import _public, profile_of
from app.ingestion.imports import PROFILE_IMPORT, DriftThresholds, ImportReads, move
from app.ingestion.models import DataProfileSnapshot, ImportJob

# --- Phase 9: field profiling (BE-9.2, BE-9.3) --------------------------------------------------
#
# The profiler is a pure, deterministic function of an import's records in file order: the
# same records always give the same observations and the same `content_sha256`. Arithmetic
# is Decimal only (never float). It observes and suggests; it never changes a schema, a
# contract or a record (DI-06).

PROFILER_VERSION = 1  # part of the hash: a change to the rules below is a new version
PROFILE_TOP_VALUES = 10  # top values kept per column
PROFILE_DISTINCT_CAP = 10_000  # distinct values tracked per column; beyond, counts are capped
PROFILE_VALUE_MAX_CHARS = 100  # a top value longer than this is stored truncated
PROFILE_FETCH_ROWS = 1_000  # records per streamed partition
_RATE = Decimal("0.0001")  # null rates: four decimal places, half-even
_RATE_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)

_INTEGER = re.compile(r"[+-]?[0-9]+")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)
_BOOLEANS = frozenset({"true", "false"})
_ISO_4217 = re.compile(r"[A-Z]{3}")
_AMOUNT = r"[+-]?[0-9][0-9,]*(?:\.[0-9]+)?"
# "EUR 12.50", "€12.50", "12.50 EUR", "12.50€". A code before the amount needs a space, so
# identifiers such as "INS-000001" are not amounts.
_AMOUNT_WITH_CURRENCY = re.compile(
    rf"(?:[A-Z]{{3}} |[$€£¥] ?){_AMOUNT}|{_AMOUNT} ?(?:[A-Z]{{3}}|[$€£¥])"
)
_YMD = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_YMD_SLASH = re.compile(r"([0-9]{4})/([0-9]{2})/([0-9]{2})")
_XX_SLASH = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")
_DMY_DOT = re.compile(r"([0-9]{2})\.([0-9]{2})\.([0-9]{4})")
_DMY_DASH = re.compile(r"([0-9]{2})-([0-9]{2})-([0-9]{4})")
# (label, pattern, positions of year, month, day among the groups). The order is the
# preference when several formats hold every value (day-first before month-first).
DATE_FORMATS: tuple[tuple[str, re.Pattern[str], tuple[int, int, int]], ...] = (
    ("YYYY-MM-DD", _YMD, (0, 1, 2)),
    ("YYYY/MM/DD", _YMD_SLASH, (0, 1, 2)),
    ("DD/MM/YYYY", _XX_SLASH, (2, 1, 0)),
    ("MM/DD/YYYY", _XX_SLASH, (2, 0, 1)),
    ("DD.MM.YYYY", _DMY_DOT, (2, 1, 0)),
    ("DD-MM-YYYY", _DMY_DASH, (2, 1, 0)),
)
# Candidate types, most specific first (the governance FieldType values).
TYPE_ORDER = (
    FieldType.INTEGER,
    FieldType.DECIMAL,
    FieldType.BOOLEAN,
    FieldType.DATE,
    FieldType.TIMESTAMP,
    FieldType.STRING,
)


def _parse_date(value: str) -> dict[str, date]:
    """The value as a calendar date under each format it is valid in."""
    found: dict[str, date] = {}
    for label, pattern, (y, m, d) in DATE_FORMATS:
        match = pattern.fullmatch(value)
        if match is None:
            continue
        parts = match.groups()
        try:
            found[label] = date(int(parts[y]), int(parts[m]), int(parts[d]))
        except ValueError:
            continue  # not a calendar date (e.g. 30 February)
    return found


def _timestamp(value: str) -> datetime | None:
    if _TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _numeric(value: str) -> tuple[Decimal, int, int] | None:
    """Plain decimal text (no exponent, grouping or spaces) as (value, integer digits,
    scale), as NUMERIC counts them."""
    try:
        number = parse_decimal_text(value, SOURCE)
    except DecimalTextError:
        return None
    whole, _, fraction = value.lstrip("+-").partition(".")
    return number, len(whole.lstrip("0")), len(fraction)


def _rate(part: int, whole: int) -> str | None:
    if whole == 0:
        return None
    with localcontext(_RATE_CONTEXT):
        return str((Decimal(part) / Decimal(whole)).quantize(_RATE))


class _ColumnProfile:
    """Running observations of one column. Memory is bounded by PROFILE_DISTINCT_CAP."""

    def __init__(self, name: str, position: int) -> None:
        self.name = name
        self.position = position
        self.values = 0
        self.nulls = 0
        self.max_length: int | None = None
        self.counts: dict[str, int] = {}
        self.capped = False
        self.integer = True
        self.boolean = True
        self.numeric = True
        self.num_min: tuple[Decimal, str] | None = None
        self.num_max: tuple[Decimal, str] | None = None
        self.int_digits = 0
        self.scale = 0
        self.dates: dict[str, int] = {}
        self.date_min: dict[str, date] = {}
        self.date_max: dict[str, date] = {}
        self.timestamp = True
        self.ts_kinds: set[bool] = set()  # aware?
        self.ts_min: datetime | None = None
        self.ts_max: datetime | None = None
        self.iso_codes = 0
        self.amounts = 0

    def add(self, value: str | None) -> None:
        if value is None or not value.strip():
            self.nulls += 1  # a missing cell, a JSON null, "" (XLSX pads) or only spaces
            return
        self.values += 1
        self.max_length = max(self.max_length or 0, len(value))
        if value in self.counts:
            self.counts[value] += 1
        elif len(self.counts) < PROFILE_DISTINCT_CAP:
            self.counts[value] = 1
        else:
            self.capped = True
        self.integer = self.integer and _INTEGER.fullmatch(value) is not None
        self.boolean = self.boolean and value.casefold() in _BOOLEANS
        if self.numeric:
            self._add_number(value)
        for label, day in _parse_date(value).items():
            self.dates[label] = self.dates.get(label, 0) + 1
            self.date_min[label] = min(self.date_min.get(label, day), day)
            self.date_max[label] = max(self.date_max.get(label, day), day)
        if self.timestamp:
            self._add_timestamp(value)
        if _ISO_4217.fullmatch(value):
            self.iso_codes += 1
        elif _AMOUNT_WITH_CURRENCY.fullmatch(value):
            self.amounts += 1

    def _add_number(self, value: str) -> None:
        parsed = _numeric(value)
        if parsed is None:
            self.numeric = False
            return
        number, digits, scale = parsed
        if self.num_min is None or number < self.num_min[0]:
            self.num_min = (number, value)  # the first text of the smallest value
        if self.num_max is None or number > self.num_max[0]:
            self.num_max = (number, value)
        self.int_digits = max(self.int_digits, digits)
        self.scale = max(self.scale, scale)

    def _add_timestamp(self, value: str) -> None:
        moment = _timestamp(value)
        if moment is None:
            self.timestamp = False
            return
        aware = moment.tzinfo is not None
        self.ts_kinds.add(aware)
        if len(self.ts_kinds) > 1:
            return  # naive and aware moments do not compare: no range
        if aware:
            moment = moment.astimezone(UTC)
        self.ts_min = moment if self.ts_min is None else min(self.ts_min, moment)
        self.ts_max = moment if self.ts_max is None else max(self.ts_max, moment)

    def _date_format(self) -> str | None:
        """The first format (in preference order) that holds every value, if any."""
        return next(
            (label for label, _, _ in DATE_FORMATS if self.dates.get(label) == self.values),
            None,
        )

    def candidates(self) -> list[FieldType]:
        if self.values == 0:
            return []
        holds = {
            FieldType.INTEGER: self.integer,
            FieldType.DECIMAL: self.numeric,
            FieldType.BOOLEAN: self.boolean,
            FieldType.DATE: self._date_format() is not None,
            FieldType.TIMESTAMP: self.timestamp,
            FieldType.STRING: True,
        }
        return [t for t in TYPE_ORDER if holds[t]]

    def _range(self, types: list[FieldType]) -> tuple[str | None, str | None]:
        label = self._date_format()
        if label is not None:
            return self.date_min[label].isoformat(), self.date_max[label].isoformat()
        if FieldType.TIMESTAMP in types and len(self.ts_kinds) == 1:
            assert self.ts_min is not None and self.ts_max is not None
            return self.ts_min.isoformat(), self.ts_max.isoformat()
        if FieldType.DECIMAL in types:
            assert self.num_min is not None and self.num_max is not None
            return self.num_min[1], self.num_max[1]
        return None, None

    def result(self, rows: int) -> dict[str, Any]:
        types = self.candidates()
        low, high = self._range(types)
        numeric = FieldType.DECIMAL in types
        top = sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))[:PROFILE_TOP_VALUES]
        return {
            "name": self.name,
            "position": self.position,
            "value_count": self.values,
            "null_count": self.nulls,
            "null_rate": _rate(self.nulls, rows),
            "distinct_count": len(self.counts),
            "distinct_capped": self.capped,
            "candidate_types": [t.value for t in types],
            "min": low,
            "max": high,
            "max_length": self.max_length,
            "max_precision": max(self.int_digits + self.scale, 1) if numeric else None,
            "max_scale": self.scale if numeric else None,
            "date_formats": dict(sorted(self.dates.items())),
            "currency_pattern_hits": {
                "ISO_4217_CODE": self.iso_codes,
                "AMOUNT_WITH_CURRENCY": self.amounts,
            },
            "top_values": [
                {
                    "value": value[:PROFILE_VALUE_MAX_CHARS],
                    "count": count,
                    "truncated": len(value) > PROFILE_VALUE_MAX_CHARS,
                }
                for value, count in top
            ],
            "top_values_exact": not self.capped,
        }


@dataclass(frozen=True)
class ProfileResult:
    row_count: int
    summary: dict[str, Any]
    columns: list[dict[str, Any]]
    content_sha256: str


def profile_sha256(row_count: int, summary: dict[str, Any], columns: list[dict[str, Any]]) -> str:
    """The hash of what was observed (not of ids or times)."""
    payload = {
        "profiler_version": PROFILER_VERSION,
        "row_count": row_count,
        "summary": summary,
        "columns": columns,
    }
    return sha256_for(HashKind.DATA_PROFILE, payload)


ProfileInput = tuple[Mapping[str, str], Sequence[str], Sequence[Mapping[str, Any]]]


class Profiler:
    """Feed records (values, extras, flags) in file order, then take the `result()`.

    D-25 row semantics: a missing cell or JSON null is an empty value of that column; extra
    cells are counted per row (they belong to no column); a blank row counts as a row whose
    every column is empty. Nothing is dropped and nothing is invented."""

    def __init__(self, columns: Sequence[str]) -> None:
        self._columns = [_ColumnProfile(name, i) for i, name in enumerate(columns, start=1)]
        self._rows = 0
        self._blank = 0
        self._missing = 0
        self._extra_rows = 0
        self._max_extra = 0
        self._flags: dict[str, int] = {}

    def add(
        self, values: Mapping[str, str], extras: Sequence[str], flags: Sequence[Mapping[str, Any]]
    ) -> None:
        self._rows += 1
        codes = [str(flag["code"]) for flag in flags]
        for code in codes:
            self._flags[code] = self._flags.get(code, 0) + 1
        self._blank += RowFlagCode.BLANK_ROW in codes
        self._missing += RowFlagCode.MISSING_CELLS in codes
        if extras:
            self._extra_rows += 1
            self._max_extra = max(self._max_extra, len(extras))
        for column in self._columns:
            column.add(values.get(column.name))

    def add_all(self, rows: Iterable[ProfileInput]) -> None:
        for values, extras, flags in rows:
            self.add(values, extras, flags)

    def result(self) -> ProfileResult:
        summary: dict[str, Any] = {
            "blank_rows": self._blank,
            "rows_with_missing_cells": self._missing,
            "rows_with_extra_cells": self._extra_rows,
            "max_extra_cells": self._max_extra,
            "flag_counts": dict(sorted(self._flags.items())),
        }
        columns = [c.result(self._rows) for c in self._columns]
        return ProfileResult(
            self._rows, summary, columns, profile_sha256(self._rows, summary, columns)
        )


def normalise_name(name: str) -> str:
    """A column or field name compared loosely: case-folded, every run of non-word
    characters (spaces, dashes, dots, underscores) one underscore, trimmed."""
    return re.sub(r"[\W_]+", "_", name.casefold()).strip("_")


def suggested_target(column: str, field_names: Sequence[str]) -> str | None:
    """The one field of the pinned schema version whose normalised name equals the
    column's (BE-9.3). Ambiguous or no match: no suggestion."""
    key = normalise_name(column)
    matches = [f for f in field_names if normalise_name(f) == key]
    return matches[0] if key and len(matches) == 1 else None


@dataclass(frozen=True)
class ProfileView:
    """An import's stored profile with what its suggestions are computed against: the
    pinned contract's expected columns and the pinned schema version's fields."""

    job: ImportJob
    snapshot: DataProfileSnapshot
    contract_version: int
    schema_id: uuid.UUID
    schema_code: str
    schema_version: int
    expected_columns: frozenset[str]
    field_names: list[str]
    mapped: dict[str, str]  # column -> classification of its mapped field (Phase 10)
    default_classification: str

    def classification(self, column: str) -> str:
        """BE-9.3: the mapped field's classification, else the source profile's default."""
        return self.mapped.get(column, self.default_classification)

    def in_contract(self, column: str) -> bool:
        return column in self.expected_columns

    def suggested_target(self, column: str) -> str | None:
        return suggested_target(column, self.field_names)


class ImportProfiling(ImportReads):
    """Profiling an import (BE-9.2) and its profile view (BE-9.3)."""

    async def profile(
        self, import_id: uuid.UUID, drift: DriftThresholds | None = None
    ) -> DataProfileSnapshot:
        """Profile a RECEIVED import: RECEIVED -> PROFILING -> RECEIVED when a usable ACTIVE
        mapping exists, else MAPPING_REQUIRED (D-21). One transaction, audited
        `import.profile` with metadata only (no column names, no values). Idempotent: an
        import that has a profile gets it back unchanged.

        With `drift` (DRIFT_DETECTION_ENABLED), the new profile is compared with the
        source's baseline in the same transaction (BE-12.3), by the mapping module."""
        job = await self.repo.lock(import_id)
        if job is None:
            await self._refuse(
                ApiError(404, "not_found", "Import not found."), import_id, PROFILE_IMPORT
            )
        existing = await self.repo.snapshot(job.id)
        if existing is not None:
            await self.session.rollback()  # release the lock; nothing changed
            return existing
        if job.status != ImportStatus.RECEIVED:
            await self._refuse(
                ApiError(
                    409,
                    "invalid_status",
                    f"Only a RECEIVED import can be profiled; this one is {job.status}.",
                    {"status": job.status},
                ),
                job.id,
                PROFILE_IMPORT,
            )
        move(job, ImportStatus.PROFILING)
        await self.repo.flush()  # each move is its own UPDATE: the trigger checks both
        columns = list(dict.fromkeys(c for batch in job.batches for c in batch.columns))
        profiler = Profiler(columns)
        async for partition in self.repo.record_partitions(job.id, PROFILE_FETCH_ROWS):
            # CPU-bound work off the event loop, one bounded partition at a time.
            await run_in_threadpool(profiler.add_all, [tuple(row) for row in partition])
        result = profiler.result()
        snapshot = DataProfileSnapshot(
            import_job_id=job.id,
            profiler_version=PROFILER_VERSION,
            row_count=result.row_count,
            column_count=len(result.columns),
            summary=result.summary,
            columns=result.columns,
            content_sha256=result.content_sha256,
            created_by=self.principal.actor,
        )
        self.repo.add(snapshot)
        usable = await self.has_usable_mapping(job)
        move(job, ImportStatus.RECEIVED if usable else ImportStatus.MAPPING_REQUIRED)
        await self.repo.flush()
        if drift is not None:
            from app.mapping.service import DriftService  # mapping depends on ingestion

            await DriftService(self.session, self.principal).detect(job, snapshot, drift)
        details: dict[str, Any] = {
            "status": job.status,
            "snapshot_id": str(snapshot.id),
            "content_sha256": result.content_sha256,
            "profiler_version": PROFILER_VERSION,
            "row_count": result.row_count,
            "column_count": len(result.columns),
            "usable_mapping": usable,
        }
        audit_by(
            self.session,
            self.principal,
            PROFILE_IMPORT,
            "import_job",
            resource_id=str(job.id),
            details=details,
            details_classification=_public(*details),
        )
        await self.session.commit()
        return snapshot

    async def profile_view(self, import_id: uuid.UUID) -> "ProfileView":
        """What GET /imports/{id}/profile shows (BE-9.3): the stored profile plus, per
        column, its classification and the read-only suggestions. Reads only."""
        job = await self.get(import_id)  # 404, the same for absent and foreign (D-04)
        snapshot = await self.repo.snapshot(job.id)
        if snapshot is None:
            raise ApiError(404, "profile_not_found", "This import has no profile.")
        source = await self.sources.get(job.source_system_id)
        profile = profile_of(source)
        contract = next(c for c in profile.contracts if c.id == job.source_contract_id)
        schema = await self.schemas.get_schema(contract.schema_id)
        version = next(v for v in schema.versions if v.id == job.schema_version_id)
        return ProfileView(
            job=job,
            snapshot=snapshot,
            contract_version=contract.version,
            schema_id=schema.id,
            schema_code=schema.code,
            schema_version=version.version,
            expected_columns=frozenset(c["name"] for c in contract.expected_columns),
            field_names=[f.name for f in version.fields],
            mapped=await self.mapped_classifications(job),
            default_classification=profile.default_classification,
        )
