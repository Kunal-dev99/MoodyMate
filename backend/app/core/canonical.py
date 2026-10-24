"""core.canonical

The one hashing implementation (D-52). Every production content hash is built here; the
migrations keep their own frozen copies as they were written (D-52 §4).

The policy (D-52 §1): the bytes are the UTF-8 of
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`.

- Keys must be `str`.
- A finite `Decimal` becomes a JSON string of `format(d, "f")` (`Decimal("1.10")` -> `"1.10"`).
- A float, a non-finite Decimal (NaN, Infinity) or any other type is refused
  (`CanonicalValueError`), never coerced.
- The digest is the lowercase hex SHA-256.

Legacy kinds (D-52 §3) keep their own settings, one policy per kind, for old and new rows alike:

- `legacy_ascii_sha256`: the former default (`ensure_ascii=True`) for a kind whose payload
  class a test cannot prove ASCII-only. For ASCII text without U+007F (DEL) it equals
  `canonical_sha256` (§2).
- `legacy_key_sha256`: a business key, a list of strings with the default separators, compared
  across imports.

`HASH_SITES` names each record kind, its policy and the reason; production sites hash through
`sha256_for(kind, value)`, so a kind cannot drift from its registered policy. Raw-byte hashes
(artifacts, uploaded files, the dataset identity over row hashes) use `sha256_hex` and are not
JSON kinds; `calculation_input_ref.sha256` copies the pinned kinds' hashes. No code path
rewrites a stored `*_sha256` (D-52 §4). New kinds use CANONICAL only (D-52 §7).
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class CanonicalValueError(ValueError):
    """A value outside the canonical JSON class (D-52 §1). The message names the type and
    the structural path only, never the value."""


def _normalise(value: Any, path: str) -> Any:
    """The value with each finite Decimal replaced by its exact text; anything outside the
    class (float, NaN, a non-str key, any other type) raises."""
    if isinstance(value, str | bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalValueError(f"a non-finite Decimal at {path or '$'}")
        return format(value, "f")
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalValueError(f"a {type(key).__name__} key at {path or '$'}")
            out[key] = _normalise(item, f"{path}.key")
        return out
    if isinstance(value, list):
        return [_normalise(item, f"{path}[{i}]") for i, item in enumerate(value)]
    raise CanonicalValueError(f"a {type(value).__name__} at {path or '$'}")


def canonical_json(value: Any) -> str:
    """The canonical JSON text of a value (D-52 §1)."""
    return json.dumps(
        _normalise(value, ""),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of raw bytes (files, artifacts, joined digests)."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    """SHA-256 of the canonical JSON of a value (D-52 §1)."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def legacy_ascii_sha256(value: Any) -> str:
    """The former default (`ensure_ascii=True`, sorted, compact) for LEGACY_ASCII kinds
    (D-52 §3). The value class is the canonical one; only the escaping of non-ASCII text
    differs, so for ASCII text without U+007F (DEL) this equals `canonical_sha256`
    (D-52 §2)."""
    text = json.dumps(
        _normalise(value, ""),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def legacy_key_sha256(parts: Sequence[str]) -> str:
    """A business key's hash (LEGACY_KEY, D-52 §3): the list of its parts, in key-column
    order, with the default separators and `ensure_ascii=False`. Compared across imports, so
    it never changes."""
    key = list(parts)
    for i, part in enumerate(key):
        if not isinstance(part, str):
            raise CanonicalValueError(f"a {type(part).__name__} at [{i}]")
    text = json.dumps(key, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- the registry (D-52 §3, §5; BE-29.2) --------------------------------------------------------


class HashPolicy(StrEnum):
    CANONICAL = "CANONICAL"
    LEGACY_ASCII = "LEGACY_ASCII"
    LEGACY_KEY = "LEGACY_KEY"


class HashKind(StrEnum):
    """Every record kind with a content hash built in production code."""

    # CANONICAL: always built with ensure_ascii=False (unchanged bytes).
    RULE_PARAMETERS = "rule_parameters"
    RULE_SET = "rule_set"
    DATASET_SNAPSHOT = "dataset_snapshot"
    CALCULATION_MANIFEST = "calculation_manifest"
    CALCULATION_RESULT = "calculation_result"
    CALCULATION_RUN_REQUEST = "calculation_run_request"
    SCENARIO_SHOCKS = "scenario_shocks"
    SCENARIO_RUN_REQUEST = "scenario_run_request"
    MAPPING_VERSION = "mapping_version"
    SOURCE_ROW = "source_row"
    DATA_PROFILE = "data_profile"
    CANONICAL_PAYLOAD = "canonical_payload"
    # LEGACY_ASCII: the former default; a non-ASCII component is accepted by the API.
    SOURCE_CONTRACT = "source_contract"
    IMPORT_REQUEST = "import_request"
    IMPORT_MANIFEST = "import_manifest"
    VALIDATION_PROFILE_RULES = "validation_profile_rules"
    SCHEMA_CONTRACT = "schema_contract"
    # LEGACY_KEY: business keys, compared across imports.
    BUSINESS_KEY = "business_key"
    DRY_RUN_BUSINESS_KEY = "dry_run_business_key"


@dataclass(frozen=True)
class HashSite:
    """One record kind: its policy, where it is built, where it is stored and why."""

    policy: HashPolicy
    built_by: str  # module:function
    stored_in: str  # table.column(s), or "not stored"
    reason: str


C, A, K = HashPolicy.CANONICAL, HashPolicy.LEGACY_ASCII, HashPolicy.LEGACY_KEY
_ALWAYS_CANONICAL = "Always built with ensure_ascii=False, sorted and compact: D-52 §1 as is."
_NON_ASCII = (
    "Former default (ensure_ascii=True). The API accepts a non-ASCII component ({}), so the "
    "payload class is not ASCII-only; stored rows keep verifying (D-52 §3)."
)
_SEEDED = (
    "Former default (ensure_ascii=True). Components ({}) come from seeds or the database, "
    "not from a request a test can refuse, so ASCII-only cannot be proved by a 422 (D-52 §3)."
)

HASH_SITES: Mapping[HashKind, HashSite] = MappingProxyType(
    {
        HashKind.RULE_PARAMETERS: HashSite(
            C, "app.rules.service:RulesService", "rule_version.parameters_sha256", _ALWAYS_CANONICAL
        ),
        HashKind.RULE_SET: HashSite(
            C, "app.rules.service:rule_set_sha256", "rule_set.content_sha256", _ALWAYS_CANONICAL
        ),
        HashKind.DATASET_SNAPSHOT: HashSite(
            C,
            "app.calculations.snapshots:snapshot_sha256",
            "dataset_snapshot.content_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.CALCULATION_MANIFEST: HashSite(
            C,
            "app.calculations.runs:CalculationService",
            "calculation_run.manifest_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.CALCULATION_RESULT: HashSite(
            C,
            "app.calculations.results:reproduce",
            "calculation_run.result_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.CALCULATION_RUN_REQUEST: HashSite(
            C,
            "app.calculations.queries:RunSpec/RerunSpec/ScenarioRunSpec.sha256",
            "calculation_run.request_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.SCENARIO_SHOCKS: HashSite(
            C,
            "app.orchestration.scenario_templates:shocks_sha256",
            "scenario_version.shocks_sha256, scenario_run.shocks_sha256, "
            "calculation_scenario_pin.shocks_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.SCENARIO_RUN_REQUEST: HashSite(
            C,
            "app.orchestration.service:ScenarioRunService._fingerprint",
            "scenario_run.request_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.MAPPING_VERSION: HashSite(
            C,
            "app.mapping.service:mapping_sha256",
            "mapping_version.content_sha256, mapping_validation.mapping_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.SOURCE_ROW: HashSite(
            C, "app.ingestion.imports:row_sha256", "source_record.row_sha256", _ALWAYS_CANONICAL
        ),
        HashKind.DATA_PROFILE: HashSite(
            C,
            "app.ingestion.profiling:profile_sha256",
            "data_profile_snapshot.content_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.CANONICAL_PAYLOAD: HashSite(
            C,
            "app.ingestion.validation:payload_sha256",
            "canonical_record.payload_sha256",
            _ALWAYS_CANONICAL,
        ),
        HashKind.SOURCE_CONTRACT: HashSite(
            A,
            "app.ingestion.contracts:contract_sha256",
            "source_contract.content_sha256",
            _NON_ASCII.format("expected column names are free text"),
        ),
        HashKind.IMPORT_REQUEST: HashSite(
            A,
            "app.ingestion.imports:request_sha256",
            "import_job.request_sha256",
            _NON_ASCII.format("control_totals keys are column names, free text"),
        ),
        HashKind.IMPORT_MANIFEST: HashSite(
            A,
            "app.ingestion.manifests:ImportValidation",
            "import_manifest.manifest_sha256, dataset_snapshot_member.manifest_sha256",
            _SEEDED.format("schema code, validation profile code, artifact classification"),
        ),
        HashKind.VALIDATION_PROFILE_RULES: HashSite(
            A,
            "app.ingestion.validation:profile_rules_sha256",
            "validation_profile.content_sha256",
            _SEEDED.format("the profile's rules are seeded by migration 0015"),
        ),
        HashKind.SCHEMA_CONTRACT: HashSite(
            A,
            "app.governance.service:contract_sha256",
            "schema_version.content_sha256",
            _NON_ASCII.format("a field's unit is free text"),
        ),
        HashKind.BUSINESS_KEY: HashSite(
            K,
            "app.ingestion.validation:business_key_sha256",
            "quarantine_item.business_key_sha256",
            "Compared across imports (duplicates, retries): its bytes never change.",
        ),
        HashKind.DRY_RUN_BUSINESS_KEY: HashSite(
            K,
            "app.mapping.service:DryRun.add",
            "not stored",
            "The mapping dry run's duplicate check counts keys as validation does.",
        ),
    }
)


def sha256_for(kind: HashKind, value: Any) -> str:
    """The hash of a record of this kind, under its registered policy."""
    policy = HASH_SITES[kind].policy
    if policy is HashPolicy.CANONICAL:
        return canonical_sha256(value)
    if policy is HashPolicy.LEGACY_ASCII:
        return legacy_ascii_sha256(value)
    return legacy_key_sha256(value)
