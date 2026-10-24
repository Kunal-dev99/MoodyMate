"""ingestion.service

Source registration (BE-7.4, DI-01): a source system, its profile and versioned source
contracts. Bounded file import (BE-8.4, DI-02): one upload becomes an import job with a
write-once raw artifact and one immutable `source_record` per row, synchronously (D-21).

Every write is audited in the same transaction and committed here (D-10). Every refused
write is audited too, as DENIED with its reason code (D-24 §4), through `app.core.refusals.refuse`
after a rollback. Target schemas are checked through the governance
service, never its repository.

BE-35.2: the implementation is split by concern, and this module re-exports every public name
for one release (cross-module callers keep importing from here; new code imports the module
that defines the name):

- `contracts`: source systems, profiles and contracts (`SourceService`);
- `imports`: the upload request, policy and limits, and `ImportReads` (the reads);
- `profiling`: the pure profiler and `ImportProfiling`;
- `validation`: the pure validation engine;
- `manifests`: `ImportValidation` (validate, reconcile, the import manifest);
- `uploads`: `ImportService` (`POST imports`);
- `quarantine`: quarantine review and retry (`QuarantineService`).
"""

from app.ingestion.constants import (
    CREATE_IMPORT,
)
from app.ingestion.contracts import (
    CREATE_CONTRACT,
    CREATE_SOURCE,
    ContractSpec,
    ProfileSpec,
    SourceService,
    contract_sha256,
    current_contract,
    profile_of,
    spec_sha256,
)
from app.ingestion.imports import (
    PROFILE_IMPORT,
    DriftThresholds,
    ImportLabels,
    ImportPolicy,
    UploadSpec,
    drift_thresholds,
    limits_from,
    move,
    request_sha256,
    row_sha256,
)
from app.ingestion.manifests import (
    VALIDATE_IMPORT,
    VALIDATE_PAGE_ROWS,
    VALIDATED_STATUSES,
)
from app.ingestion.profiling import (
    DATE_FORMATS,
    PROFILE_DISTINCT_CAP,
    PROFILE_FETCH_ROWS,
    PROFILE_TOP_VALUES,
    PROFILE_VALUE_MAX_CHARS,
    PROFILER_VERSION,
    TYPE_ORDER,
    ProfileInput,
    Profiler,
    ProfileResult,
    ProfileView,
    normalise_name,
    profile_sha256,
    suggested_target,
)
from app.ingestion.quarantine import (
    AUTOMATIC_MOVES,
    DISPOSITION,
    QuarantineService,
    QuarantineView,
    manual_moves,
)
from app.ingestion.uploads import (
    ImportService,
)
from app.ingestion.validation import (
    MESSAGE_MAX,
    VALIDATOR_PARTS,
    Finding,
    MappedRule,
    RowResult,
    Validator,
    ValidatorInput,
    business_key_sha256,
    payload_sha256,
    profile_entries,
    profile_rules_sha256,
    target_fields_of,
    validate_rows,
)

__all__ = [
    "AUTOMATIC_MOVES",
    "CREATE_CONTRACT",
    "CREATE_IMPORT",
    "CREATE_SOURCE",
    "ContractSpec",
    "DATE_FORMATS",
    "DISPOSITION",
    "DriftThresholds",
    "Finding",
    "ImportLabels",
    "ImportPolicy",
    "ImportService",
    "MESSAGE_MAX",
    "MappedRule",
    "PROFILER_VERSION",
    "PROFILE_DISTINCT_CAP",
    "PROFILE_FETCH_ROWS",
    "PROFILE_IMPORT",
    "PROFILE_TOP_VALUES",
    "PROFILE_VALUE_MAX_CHARS",
    "ProfileInput",
    "ProfileResult",
    "ProfileSpec",
    "ProfileView",
    "Profiler",
    "QuarantineService",
    "QuarantineView",
    "RowResult",
    "SourceService",
    "TYPE_ORDER",
    "UploadSpec",
    "VALIDATED_STATUSES",
    "VALIDATE_IMPORT",
    "VALIDATE_PAGE_ROWS",
    "VALIDATOR_PARTS",
    "Validator",
    "ValidatorInput",
    "business_key_sha256",
    "contract_sha256",
    "current_contract",
    "drift_thresholds",
    "limits_from",
    "manual_moves",
    "move",
    "normalise_name",
    "payload_sha256",
    "profile_entries",
    "profile_of",
    "profile_rules_sha256",
    "profile_sha256",
    "request_sha256",
    "row_sha256",
    "spec_sha256",
    "suggested_target",
    "target_fields_of",
    "validate_rows",
]
