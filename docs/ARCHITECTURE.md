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
| domain | WS3 FC-01, FC-02, FC-06, FC-08 | legal_entity, counterparty, account, instrument_type, instrument, instrument_version, instrument_attribute_definition, instrument_attribute_value, position, position_version, contractual_term, cashflow_schedule, cashflow_event, cashflow_component, collateral_asset, collateral_link, market_observation, reference_data_item, assumption_set | `GET instruments`, `GET instruments/{id}`, `GET assumption-sets`, `GET assumption-sets/{id}` |
| rules | WS3 FC-07 | rule_definition, rule_version, rule_set, rule_set_member, calculation_module, module_version | `GET rules`, `GET rules/{id}`, `GET calculation-modules` |
| calculations | WS3 FC-03, FC-04 (FC-05/06 extension points) | calculation_run, calculation_input_ref, cashflow_result, maturity_bucket_definition, liquidity_gap_result, hqla_asset_result, lcr_result, capital_result, collateral_result, intermediate_value, calculation_warning | `POST calculation-runs`, `GET calculation-runs/{id}`, `GET …/{id}/results`, `GET …/{id}/liquidity`, `GET …/{id}/maturity-ladder` |
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
