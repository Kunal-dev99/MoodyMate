# MoodyMate — Architecture (live)

**Goal:** a configurable financial-risk platform that replaces the Moody's calculation and
balance-sheet risk capabilities a bank client actually uses. It ingests uncertain source
data safely and runs deterministic, reproducible, auditable liquidity calculations, with
governed AI assistance on top.

This file is the **live** architecture and changes through `docs/DECISIONS.md`. The
**signed-off** baseline is the PDF set below. Its text is extracted for grepping.

| Signed-off source | Grep-able text |
|---|---|
| `docs/System Design And Architecture/Financial_Risk_Platform_Architecture_and_System_Design.pdf` (v0.1) | `docs/architecture_spec.txt` |
| `docs/System Design And Architecture/Financial_Risk_Platform_Detailed_Data_Model.pdf` (v0.1) | `docs/architecture_spec.txt` |
| `docs/MVP_Developement_plan/*.pdf` (WS1–WS5 FE/BE plans + index) | `docs/workstream_plans_spec.txt` |

## Stack

| Concern | Choice | Source |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2.0 (async) + Alembic, Python 3.12 | Arch §5, §18 (FastAPI recommended); ORM and migrations: bootstrap assumption |
| Database | PostgreSQL 16 (relational core) | Data Model §1 |
| Object storage | S3-compatible bucket for raw artifacts and reports (local: filesystem adapter) | Arch §5 |
| Async jobs | DB-backed job queue (`workflow_job` + `event_outbox`), broker adapter boundary kept | Arch §5 |
| Identity | Enterprise OIDC/OAuth2 provider; no local password store | Arch §10 |
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind 3 | Arch §5 (React/TS) + design bundle |
| Design system | `fp_design_system_template/` ("Redwood Professional", shadcn/ui) | repo |
| API contract | REST `/api/v1`, OpenAPI is the source of truth; FE types generated from it | Arch §9 |
| Deployment | **Not decided.** Cloud-neutral and containerised (Arch §5, §18). Pending client hosting/security input | — |

## Shape

A **modular monolith**: one deployable backend with strict internal modules. Each module
follows `Router → Service → Repository → Database` and has the same file set:
`router.py, schemas.py, models.py, service.py, repository.py, events.py, constants.py`.
Shared plumbing goes in `backend/app/core/`. Four logical layers (Foundation → Configurable
Domain → Calculation → Intelligence) map onto the modules below. They are not separate
apps.

```
backend/app/{core, governance, ingestion, mapping, domain, rules, calculations,
             orchestration, reconciliation, lineage, reporting, ai, db, api/v1, tests}
frontend/src/{app, features, components, api (generated), types}
```

## Module → tables → endpoints

Tables are from the Detailed Data Model. Endpoints are the **proposed** contracts from the
WS plans and must be finalised in OpenAPI before coding.

| Module | WS / features | Tables | Endpoints (`/api/v1`) |
|---|---|---|---|
| governance | WS1 DG-01..06 | tenant, user_account, role, permission, user_role, role_permission, data_classification, access_policy, audit_event, schema_definition, schema_version, schema_field, schema_change_proposal, schema_dependency | `GET me`, `GET me/permissions`, `GET users`, `GET users/{id}` (read-only, `admin:read`), public `GET auth/config`, dev-only `GET dev/personas` + `POST dev/sessions` (AUTH_MODE=dev on local/test only), `GET schemas`, `GET schemas/{id}/versions`, `GET schemas/{id}/diff`, `GET audit-events`, `POST schema-change-proposals`, `GET …/{id}`, `POST …/{id}/impact-analysis`, `POST …/{id}/approval` |
| ingestion | WS2 DI-01, DI-02 | source_system, source_profile, source_contract, import_job, import_artifact, import_batch, source_record | `POST sources`, `POST imports`, `GET imports/{id}` |
| mapping (+ quality) | WS2 DI-03, DI-04, DI-06 | field_mapping, mapping_version, mapping_rule, canonical_record, record_lineage, validation_profile, validation_issue, quarantine_item, data_profile_snapshot, data_drift_event | `GET mappings`, `POST mappings`, `POST imports/{id}/mappings/validate`, `GET imports/{id}/profile`, `POST imports/{id}/validate`, `GET imports/{id}/issues`, `GET quarantine/{batch_id}`, `GET imports/{id}/drift` |
| domain | WS3 FC-01, FC-02, FC-06, FC-08 | domain_projection (D-29: one per projected import, append-only), legal_entity, counterparty, account, instrument_type, instrument, instrument_version, instrument_attribute_definition, instrument_attribute_value, position, position_version, contractual_term, cashflow_schedule, cashflow_event, cashflow_component, collateral_asset, collateral_link, market_observation, reference_data_item, assumption_set. Built in Phase 14 (migration 0017): domain_projection, instrument_type (seeded PROVISIONAL), instrument, instrument_version, instrument_attribute_definition, instrument_attribute_value, position, position_version, cashflow_event | `POST imports/{id}/projection` (added, D-29; `domain:project`, audited `import.project`, behind `DOMAIN_PROJECTION_ENABLED`), `GET imports/{id}/projection` (added, read only, `domain:read`), `GET instruments`, `GET instruments/{id}` (`domain:read`), `GET assumption-sets`, `GET assumption-sets/{id}` |
| rules | WS3 FC-07 | rule_definition, rule_version, rule_set, rule_set_member, calculation_module, module_version. Built in Phase 15 (migration 0018, D-32): calculation_module, module_version (implementation_status AVAILABLE/PLANNED, declared input and output contracts, engine_version; append-only), rule_definition (closed parameter kinds; append-only), rule_version (immutable outside DRAFT, DB lifecycle trigger, one ACTIVE per definition), rule_set and rule_set_member (content-addressed by the sorted version ids; append-only), maturity_bucket_definition (materialised DAY_BUCKETS rows; append-only), assumption_set (versioned, read-only in Feature 03). Tagged seed `0018_calculations` (D-11, D-35): `maturity_ladder` v1 AVAILABLE, ACTIVE ILLUSTRATIVE `maturity_buckets`, `cashflow_direction`, `past_due_treatment` v1, the eight buckets and the ACTIVE empty `baseline (illustrative)` assumption set. Phase 17 (migration 0020, tag `0020_lcr_hqla`): module `lcr_hqla` v1 AVAILABLE and ACTIVE ILLUSTRATIVE rule versions `hqla_level` (ENUM_MAP BOND→L1), `hqla_haircut` (DECIMAL_MAP BOND "0.0000"), `lcr_outflow_run_off` (DEPOSIT "0.1000"), `lcr_inflow_rate` (LOAN "0.5000"), `lcr_inflow_horizon_days` (INTEGER 30) and `lcr_inflow_cap` (DECIMAL "0.7500"); decimals are JSON strings padded to scale (D-32); a type may not be both HQLA and run-off, or either and an inflow (`PARAMETERS_INVALID`). | `GET calculation-modules` (`rule:read`; what a run would pin, for the New-run panel), `GET rules`, `GET rules/{id}` (`rule:read`), `POST rules/{id}/versions`, `POST …/versions/{version}/submit` (`rule:propose`), `POST …/versions/{version}/approval` (`rule:approve`; maker-checker, 409 `self_approval`): Phase 16, audited `rule.*`, behind `RULE_CHANGE_ENABLED`; `POST calculation-runs` with `rerun_of` re-runs on the original pins |
| calculations | WS3 FC-03, FC-04 (FC-05/06 extension points) | calculation_run, calculation_input_ref, cashflow_result, maturity_bucket_definition (in `rules`), liquidity_gap_result, hqla_asset_result, lcr_result, capital_result, collateral_result, intermediate_value, calculation_warning. Built in Phase 15 (migration 0018, D-30, D-31): dataset_snapshot and dataset_snapshot_member (content-addressed, at most one projected import per schema code; append-only), calculation_run (written RUNNING, one move to a terminal status by trigger, then immutable; manifest + manifest_sha256, result_sha256, idempotency key), calculation_input_ref, liquidity_gap_result, intermediate_value, calculation_warning (inserted only while the run is RUNNING, append-only). Modules live in `app/calculations/modules/` (pure, Decimal only; `maturity_ladder` v1). Masking (D-33): ladder aggregates are `internal`, but a figure built from fewer than `CALC_MIN_AGGREGATE_COUNT` (default 3, PROVISIONAL) contributions, and every figure derived from it (net, cumulative), takes the contributions' classification; contributions are masked by their own. Phase 17 (migration 0020, D-35 §2): `lcr_hqla` v1 (pure, Decimal only; the ratio is its one declared rounding, 4 dp ROUND_HALF_EVEN) writes lcr_result (one row per run and currency: HQLA by level, outflows, inflows before and after the cap, denominator, ratio NULL with `LCR_DENOMINATOR_ZERO` when not computable; the figure identities are CHECK-constrained; the LCR warnings live in `lcr_result.warning_codes` because 0018's warning CHECK is not widened, D-01) and hqla_asset_result (one row per asset position, included at its level or excluded `NOT_HQLA_ELIGIBLE`, with position-version lineage), both under 0018's run-output guards (inserted only while the run is RUNNING, run row FOR SHARE; append-only). LCR masking (D-33): the row is `internal`; an HQLA level, outflows or inflows from fewer than `CALC_MIN_AGGREGATE_COUNT` contributions, and every figure derived from them, take the contributions' classification; only non-zero contributions count toward the minimum (also for the ladder); zeros and a NULL ratio stay clear; asset rows are masked by their own | `POST dataset-snapshots` (added, D-30; `calc:run`, audited `dataset_snapshot.create`), `GET dataset-snapshots`, `GET dataset-snapshots/{id}` (added, `calc:read`), `POST calculation-runs` (`calc:run`, `Idempotency-Key`, audited `calculation_run.create`/`.finish`, behind `CALCULATIONS_ENABLED`), `GET calculation-runs` (added, list), `GET calculation-runs/{id}`, `GET …/{id}/results`, `GET …/{id}/maturity-ladder` (`calc:read`), `GET …/{id}/liquidity` (Phase 17) |
| orchestration | WS4 RO-01, RO-02, RO-06, RO-07 | workflow_job, review_task, event_outbox, scenario, scenario_change, scenario_run | `POST jobs`, `GET jobs/{id}`, `GET runs`, `POST scenarios`, `POST scenarios/{id}/run`, `GET scenarios/{id}/comparison`, `POST reviews/{id}/decision` |
| reconciliation | WS2 DI-05, WS4 RO-04 | reconciliation_run, reconciliation_item, benchmark_case, benchmark_result | `GET imports/{id}/reconciliation`, `POST reconciliations`, `GET reconciliations/{id}` |
| lineage | WS4 RO-03 | lineage_node, lineage_edge | `GET calculation-runs/{id}/lineage` |
| reporting | WS4 RO-05 | report_definition, report_instance | `POST reports`, `GET reports/{id}` |
| ai | WS5 AI-01..08 | ai_request, ai_retrieval_reference, ai_tool_call, ai_proposal, ai_evaluation_run, ai_feedback | `POST ai/assist`, `GET ai/requests/{id}`, `POST rule-proposals`, `GET rule-proposals/{id}`, `POST ai/explanations`, `GET ai/explanations/{id}`, `POST workflows`, `GET workflows/{id}`, `POST workflows/{id}/approval`, `POST ai/evaluations`, `GET ai/evaluations/{id}` |

## Frontend workspaces (Arch §14)

Platform admin · Source & schema registry · Ingestion workbench · Financial domain ·
Calculation operations · Reconciliation & scenarios · Reports · AI assistant (optional,
switchable off).

## Non-negotiables (from the signed-off docs)

- Every calculation run pins its dataset snapshot plus schema, mapping, rule-set,
  assumption, module and engine versions. Runs are immutable, and a re-run creates a new run.
- Fixed-precision decimals and explicit currency/unit fields. Dates are typed distinctly
  (business, effective, timestamp).
- Unknown source fields are preserved or quarantined, never dropped. Drift creates a
  review, never an automatic schema change.
- AI is outside the authoritative calculation path, proposal-only, uses allowlisted
  tools, and can be disabled.
- No claim of regulatory certification or Moody's parity. Domain experts approve formulas,
  golden cases and tolerances.

## Environments, retention and backup

- **Environment guard (BE-6.1, D-14):** `DATA_CLASS=client` is allowed only when `ENV=production`. local, test and staging are synthetic only. `DATA_CLASS` must be set explicitly in staging and production, and every response carries `X-Data-Class`.
- **Retention, deletion and backup:** provisional assumptions per artifact class are in [`docs/RETENTION.md`](RETENTION.md). They need client confirmation, and no numeric RPO/RTO is set (Arch §11).

## Open contract conflicts (resolve via a decision before coding)

1. Endpoint naming between the Architecture doc §9 and the WS plans: `datasets/imports` vs
   `imports`, `mappings/{id}/versions` vs `mappings`, `ai/requests` and
   `ai/proposals/{id}/approve` vs `ai/assist` and `rule-proposals`.
2. Lifecycle enums: Architecture Appendix B (lowercase, e.g. `post_check`,
   `completed_with_warnings`) vs Data Model §9 (uppercase, e.g. `SUCCEEDED`, `BLOCKED`)
   for calculation_run, import_job and schema_version.
3. Error envelope: the Architecture doc adds `retryable`; the WS plans don't have it.
4. Phase IDs: the Architecture doc uses global P0–P6, while each WS plan uses its own
   P0–P6 meanings.
5. Design bundle still carries fp_reporting route labels and status pills.

## Security controls added in Feature 06 Phase 28 (D-53, D-54, D-55)

**Rate limits (D-53, `app/core/ratelimit.py`).** An in-process token bucket
per (tenant, principal, route template) guards the five expensive routes. Defaults (settings
`RATE_LIMIT_<ROUTE>_BURST` / `_PER_MINUTE`, PROVISIONAL; production values are a client item):

| Route | Setting prefix | Burst | Per minute |
|---|---|---|---|
| `POST /imports` | `RATE_LIMIT_CREATE_IMPORT` | 5 | 10 |
| `POST /imports/{id}/validate` | `RATE_LIMIT_VALIDATE_IMPORT` | 5 | 10 |
| `GET /scenarios/{id}/versions/{v}/validation` | `RATE_LIMIT_SCENARIO_VALIDATION` | 5 | 10 |
| `POST /calculation-runs` | `RATE_LIMIT_CALCULATION_RUN` | 5 | 10 |
| `POST /scenarios/{id}/run` | `RATE_LIMIT_SCENARIO_RUN` | 3 | 5 |

Authentication and the route's permission run first; a refusal is 429 `rate_limited`
(`retryable: true`) with `Retry-After` in whole seconds, audited DENIED with reason
`rate_limited`, and nothing is written. `RATE_LIMIT_ENABLED=false` is allowed only in
local/test. **Per process:** N API processes allow N times the rate; a shared store is required
before more than one API instance runs (deferred, D-53 §5).

**Security headers (D-54).** Backend middleware (`app/core/security_headers.py`) sets
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and
`Cross-Origin-Opener-Policy: same-origin` on every response, and
`Strict-Transport-Security: max-age=31536000; includeSubDomains` only when ENV is
staging/production (TLS terminates in front of the app). The frontend (`next.config.mjs`,
`src/config/buildEnv.mjs`) serves the same frame and nosniff headers plus a CSP in
**Report-Only** mode; enforcing it in production is a client decision (D-54 §5).

**Route resolution (`app/core/routes.py`).** FastAPI 0.162 includes routers lazily, so
`app.routes` no longer lists included routes and `scope["route"]` holds the unprefixed route.
`served_routes(app)` and `route_template(request)` resolve the served path and dependencies; the
D-04 declared-permission guard, the route registry check, denial audit strings and rate-limit
keys all use them, and the guard fails closed (403) when a route cannot be resolved.

**Dependency scanning (D-55).** CI fails on any high/critical npm advisory not listed (with an
expiry) in `frontend/audit-allowlist.json` (`backend/scripts/check_audit_allowlist.py`) and runs
`pip-audit` on the backend environment. Remaining advisories: `docs/security/ADVISORIES.md`.
