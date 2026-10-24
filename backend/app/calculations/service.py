"""calculations.service

Dataset snapshots (BE-15.3, D-30) and the calculation engine (BE-15.4, D-31), plus the reads
of BE-15.5.

Snapshot (`POST /dataset-snapshots`, `calc:run`, audited `dataset_snapshot.create`): the
members are checked in a fixed order, and the first failing check refuses (409, audited
DENIED with its code): 404 `not_found` (absent and foreign alike), `not_projected`,
`duplicate_schema`, `reconciliation_broken` (a D-28 BROKEN member), `ambiguous_business_key`
(a business key with two versions in the snapshot). `content_sha256` hashes the members'
(schema_code, import_id, manifest_sha256), sorted by schema code, so the same members return
the existing snapshot and nothing is written.

Run (`POST /calculation-runs`, `calc:run`, `Idempotency-Key`, audited `calculation_run.create`
and `calculation_run.finish`). Refused before any run is written (409, audited DENIED):
`calculations_disabled`, `idempotency_conflict`, 404 `not_found` (snapshot) and
`module_not_available`. Then:

1. Pins: the ACTIVE version of every rule the module reads, written once as a
   content-addressed rule set (D-32 §4); the ACTIVE baseline assumption set; the module
   version; the engine version; the snapshot's members (each import manifest, projection,
   schema and mapping version, counts and reconciliation outcome).
2. The run is written RUNNING with its manifest (`manifest_sha256` over the pinned content
   only) and one `calculation_input_ref` per pin, and committed: a crash leaves evidence.
3. The module runs as a pure function over frozen inputs, sorted by source row, under its
   declared Decimal context (no AI, no I/O inside it).
4. Post-checks, recomputed independently of the module: every in-scope cash flow is bucketed
   once or excluded with a warning code; every contribution lands in a pinned bucket; the
   bucket totals equal the sum of their contributions; the cumulative gap is the running
   sum of the net gaps.
5. The run ends SUCCEEDED, SUCCEEDED_WITH_WARNINGS, FAILED (a post-check or the engine
   failed: warnings only, no results) or BLOCKED (the input contract or the pinned
   configuration was not met), with `result_sha256` over the canonical outputs. It never
   moves again (DB triggers).

Decimal only; every hash is over canonical JSON (sorted keys, exact decimal text).

Stressed runs (Phase 25, BE-25.3; D-45): `ScenarioRunSpec` runs `maturity_ladder` on a
baseline run's exact pins (reusing the re-run pin resolution) with a `scenario` manifest
section. The pure overlay (`scenario_overlay`) splits the matching cash flows into parts, the
unchanged module runs on them, and the SCENARIO_OVERLAY and SCENARIO_CONSERVATION
post-checks run besides the existing ones. Parts are stored in `scenario_contribution` and
the per-flow applications in `scenario_shock_application` (never `intermediate_value`), and
`result_sha256` covers both for stressed runs only. A re-run of a stressed run re-pins the
same version and subset, even once the version is CLOSED. Non-stressed manifests and result
documents keep exactly their Feature 03 key sets.

BE-35.2: the implementation is split by concern, and this module re-exports every public name
for one release (cross-module callers keep importing from here; new code imports the module
that defines the name):

- `snapshots`: dataset snapshots and `snapshot_sha256`;
- `results`: the pure post-checks, result document, `reproduce` and read classifications;
- `queries`: the run views and requests, and `RunQueries` (the service's base and its reads);
- `execution`: `RunExecution` (inputs, the module run, the overlay, finishing a run);
- `runs`: `CalculationService` (pins, manifest, create) and the stale-run sweeper.
"""

from app.calculations.execution import (
    RunExecution,
)
from app.calculations.executor import (
    INLINE,
    Admission,
    RunExecutor,
    WorkersBusy,
)
from app.calculations.queries import (
    RUN_NOT_FOUND,
    AssetRow,
    BaselineCheck,
    ContributionRow,
    InstrumentRef,
    LadderView,
    LiquidityView,
    RerunSpec,
    ResultsView,
    RunModule,
    RunQueries,
    RunRow,
    RunSpec,
    ScenarioMember,
    ScenarioPartRow,
    ScenarioRunSpec,
    is_stressed,
)
from app.calculations.results import (
    LADDER_PARTS,
    PUBLIC,
    SUCCEEDED,
    ScenarioOutputs,
    W,
    definitions_match,
    kept_lcr,
    ladder_classes,
    lcr_post_checks,
    liquidity_classes,
    post_checks,
    reproduce,
    result_document,
)
from app.calculations.runs import (
    STALE_RUN,
    SWEEP_BATCH,
    SWEEPER_ACTOR,
    CalculationService,
    run_sweeper,
    sweep_stale_runs,
    workers_busy,
)
from app.calculations.snapshots import (
    SnapshotService,
    SnapshotView,
    snapshot_sha256,
)

__all__ = [
    "Admission",
    "AssetRow",
    "BaselineCheck",
    "CalculationService",
    "ContributionRow",
    "INLINE",
    "InstrumentRef",
    "LADDER_PARTS",
    "LadderView",
    "LiquidityView",
    "PUBLIC",
    "RUN_NOT_FOUND",
    "RerunSpec",
    "ResultsView",
    "RunExecution",
    "RunExecutor",
    "RunModule",
    "RunQueries",
    "RunRow",
    "RunSpec",
    "STALE_RUN",
    "SUCCEEDED",
    "SWEEPER_ACTOR",
    "SWEEP_BATCH",
    "ScenarioMember",
    "ScenarioOutputs",
    "ScenarioPartRow",
    "ScenarioRunSpec",
    "SnapshotService",
    "SnapshotView",
    "W",
    "WorkersBusy",
    "definitions_match",
    "is_stressed",
    "kept_lcr",
    "ladder_classes",
    "lcr_post_checks",
    "liquidity_classes",
    "post_checks",
    "reproduce",
    "result_document",
    "run_sweeper",
    "snapshot_sha256",
    "sweep_stale_runs",
    "workers_busy",
]
