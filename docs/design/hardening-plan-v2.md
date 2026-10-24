> The user's own document (uploaded 2026-10-24 as `MoodyMate_MVP_Hardening_Plan_1.md`), copied verbatim; binding input for Feature 06 in `docs/DELIVERY_PLAN.md`.

# MoodyMate MVP Hardening Plan

**Document type:** Engineering hardening plan  
**Repository baseline:** `376c561` (as reported in the supplied audit)  
**Purpose:** Stabilize the existing MoodyMate MVP without a wholesale rewrite. Preserve verified strengths, fix security and credibility defects, improve execution safety, and prepare the financial domain for validated expansion.

> **Decision:** KEEP + REFACTOR. Do not rewrite the application without new evidence that targeted refactoring is inadequate.

## 1. Objectives

1. Preserve the exact-decimal calculation engines, ingestion adapters, governance, audit trail, lineage, schema versioning, and reproducible calculation runs.
2. Remove misleading demo behaviour and claims.
3. Address critical dependency and production-security risks.
4. Correct known financial-domain projection defects and define a governed path for schema evolution.
5. Prevent CPU-heavy calculation work from blocking API request handling.
6. Establish evidence-based methodology validation before describing outputs as regulatory or production-grade.
7. Improve maintainability, observability, build reproducibility, and scalability incrementally.

## 2. Scope and non-goals

### In scope

- Dependency remediation and automated dependency scanning.
- Demo accuracy and API parity.
- Canonical hashing and shared financial-data helpers.
- Calculation and ingestion execution architecture.
- Instrument schema and projection correctness.
- Financial methodology validation.
- Security controls, test quality, deployment readiness, and observability.
- A staged path toward broader financial intelligence capabilities.

### Out of scope for this hardening effort

- A wholesale rewrite of the frontend or backend.
- Building a complete Moody's-equivalent product in one release.
- Presenting illustrative seeded rules as regulatory methodology.
- Building an AI assistant before the underlying data and analytical contracts are reliable.
- Premature distributed infrastructure or database partitioning without workload evidence.

## 3. Evidence baseline

The following findings come from the supplied repository audit and have not been independently re-run as part of this document.

### Strengths to preserve

- Backend calculation paths use Python `Decimal` and database `NUMERIC`, with explicit rounding and failure handling.
- Calculation runs pin inputs, rules, and module versions and use hashes to check reproducibility.
- Ingestion includes schema contracts, mapping, validation, quarantine, reconciliation, drift checks, and lineage.
- Append-only records, audit events, maker-checker controls, and tenant scoping are implemented.
- The frontend uses a typed API client rather than direct database access.
- Reported tests: 49 backend tests passed, 618 frontend tests passed, backend line coverage 83%, and 52 Playwright tests reportedly green in CI.
- The pure calculation engines have materially stronger coverage than several orchestration and authentication services.

### Known gaps and defects

- `next` dependency tree has reported critical/high advisories; the current installed tree must be rechecked and remediated.
- Two browser-only demos use floating-point arithmetic, hard-coded values/fingerprints, or inaccurate claims.
- Instrument maturity is stored as a string in schema v1 and is not projected to the typed database date field; current instrument maturity is therefore reported as `NULL`.
- Floating-rate terms, effective dating, currency/FX tables, legal-entity relationships, and financial statements are missing or incomplete.
- Liquidity and stress calculation rules are labelled provisional/illustrative; methodology has not received domain-expert approval.
- LCR Level 2A/2B caps are not applied; HQLA uses a provisional balance proxy; there is no FX consolidation.
- Calculation work executes synchronously in the request path and loads whole snapshots into memory.
- No rate limiting, production security headers, metrics/tracing, readiness probe, Docker deployment artifacts, or Python dependency lockfile were reported.
- Canonical JSON hashing has inconsistent `ensure_ascii` settings.
- Large service files, frontend duplication, circular dependencies, and fixed list limits increase maintenance and scaling risk.

## 4. Severity and priority definitions

- **P0:** Resolve before exposing the affected capability to real users or making high-stakes financial claims.
- **P1:** Resolve before production deployment or significant scale-up.
- **P2:** Address as part of maintainability and product expansion.
- **P3:** Defer until evidence or product needs justify the work.

A P0 label does not mean every local synthetic-data demo must stop. It means the affected behaviour must not be represented as secure, validated, or production-ready until its exit criteria are met.

## 5. Workstream A: security and demo credibility

**Priority:** P0  
**Target:** First hardening release

### Tasks

- [ ] Inspect the resolved dependency tree and identify the exact vulnerable package paths and fixed versions.
- [ ] Upgrade Next.js and affected transitive dependencies to supported patched versions compatible with the application.
- [ ] Run `npm audit --omit=dev` and review any remaining advisories individually; document mitigations and acceptance owners for any unavoidable findings.
- [ ] Add dependency scanning for both JavaScript and Python dependencies to CI (for example, `npm audit` and `pip-audit` or an equivalent approved scanner).
- [ ] Add a frontend production-build guard so a missing API base URL fails clearly instead of silently defaulting to `localhost:8000`.
- [ ] Correct or remove the Agent demo's claims of “exact decimal arithmetic” and verified SHA-256 fingerprints.
- [ ] Correct or remove the Liquidity Intelligence demo's hard-coded “verified” fingerprints, fixed LCR movement text, and displayed run-off rates that are not applied.
- [ ] Add a zero-denominator guard to any remaining illustrative LCR UI calculation; show “not computable” rather than `Infinity` or `NaN`.
- [ ] Ensure demo labels explicitly identify synthetic or illustrative data.
- [ ] Add security headers appropriate to the deployment, including CSP, HSTS in HTTPS production, and clickjacking protection. Validate compatibility before enforcing a strict CSP.
- [ ] Add bounded rate limits/resource controls to expensive validation, upload, calculation, and scenario-run endpoints.

### Acceptance criteria

- No known unmitigated critical dependency advisory in the deployed dependency tree.
- High-severity advisories are remediated or have documented, time-bounded risk acceptance.
- CI scans JavaScript and Python dependencies.
- Demo pages make no claims that contradict their implementation.
- Division by zero cannot produce a displayed infinite or undefined financial ratio.
- Expensive endpoints have documented limits and tests for enforcement.
- Production API configuration cannot silently target localhost.

## 6. Workstream B: numeric integrity, canonical hashing, and shared helpers

**Priority:** P0/P1

### Tasks

- [ ] Create one canonical JSON serialization and SHA-256 helper in the core layer.
- [ ] Fix and document the canonical serialization policy, including a single `ensure_ascii` setting, key ordering, Unicode handling, and separators.
- [ ] Add golden hash tests for ASCII and non-ASCII payloads and verify consistency across every call site.
- [ ] Keep financial amounts as decimal strings at the API/UI boundary where exact representation matters.
- [ ] Centralize decimal parsing, scale/precision validation, comparison, and display formatting where shared semantics are intended.
- [ ] Centralize masking and `most_sensitive` policy; use one backend enforcement path and one reusable frontend masked-value component.
- [ ] Consolidate status-pill rendering, rule-status mappings, maker-checker checks, comment validation, date validation, refusal handling, and idempotency-key creation where appropriate.
- [ ] Ensure no refactor changes financial semantics without golden tests.
- [ ] Add property-based or fuzz tests for decimal parsing, numeric bounds, scenario overlays, and canonical serialization.

### Acceptance criteria

- One canonical hashing implementation is used by all production paths.
- Existing pinned run/result hashes are handled through an explicit compatibility/migration policy, not silently changed.
- Decimal parsing rejects unsupported precision rather than silently rounding.
- Shared-helper refactors retain existing behavioural tests.
- Sensitive-value masking is enforced server-side regardless of frontend display behaviour.

## 7. Workstream C: financial methodology and calculation validation

**Priority:** P0 before financial/regulatory claims

### Tasks

- [ ] Define the intended use, jurisdiction, reporting basis, and target methodology for every calculation module.
- [ ] Record the status of each calculation as `ILLUSTRATIVE`, `INTERNAL_VALIDATED`, or `REGULATORY_VALIDATED` (or another explicitly approved taxonomy).
- [ ] Document the business meaning, formula, units, input requirements, exclusions, assumptions, rounding, and limitations of each metric.
- [ ] Obtain review by a qualified banking/liquidity subject-matter expert for the target methodology.
- [ ] Build a golden test pack from approved reference calculations, not only self-authored engineering examples.
- [ ] Implement required LCR Level 2A/2B caps if the chosen methodology requires them.
- [ ] Replace the provisional HQLA balance proxy with the correct valuation basis for the chosen methodology.
- [ ] Define instrument-specific deposit classes and applicable runoff assumptions rather than applying a single rate to all deposits.
- [ ] Define the treatment of day-zero, past-due, missing, negative, and out-of-horizon cash flows.
- [ ] Keep currency-level results separate unless an approved FX conversion method and rate source are available.
- [ ] Validate denominator-zero handling, inflow-cap boundaries, rounding boundaries, very large values, and reconciliation tolerances end to end through the API.
- [ ] Validate that stressed and baseline results use the same pinned inputs, rules, and module versions where required.
- [ ] Clearly label unsupported calculations and prevent them from being presented as approved regulatory results.

### Acceptance criteria

- Each supported calculation has a versioned methodology document and an accountable reviewer.
- Golden cases have expected results approved independently of the implementation.
- All methodology-specific caps, classifications, and valuation rules required for the selected scope are implemented and tested.
- Warnings and exclusions are visible and traceable.
- Outputs state units, currency, as-of date, methodology/version, and validation status.
- No regulatory compliance claim is made without explicit methodology and domain sign-off.

## 8. Workstream D: financial domain model and projection correctness

**Priority:** P0 for instrument analytics; sequence schema changes through the project's migration governance

### Tasks

- [ ] Fix instrument maturity projection using a new governed schema/projector version. Do not silently mutate an already-published schema contract.
- [ ] Add tests for date parsing, invalid dates, missing maturity, round-trip projection, and backwards compatibility.
- [ ] Define a typed legal-entity/counterparty/issuer model and real relationships where referential integrity is required.
- [ ] Define explicit links from instruments, positions, cash flows, and credit exposures to their intended identities. Plan how existing text-key records will be reconciled.
- [ ] Introduce typed fixed/floating interest-rate terms, including reference index, spread, reset frequency, next reset date, caps/floors where applicable, day-count convention, and payment frequency.
- [ ] Add effective-date semantics for contract changes, rating history, and state transitions. Distinguish import/version time from business-effective time.
- [ ] Define lifecycle states and events for relevant instruments (for example active, matured, prepaid, restructured, or defaulted) based on product scope.
- [ ] Introduce currency reference data and governed FX-rate observations with source, timestamp, rate type, and reporting-currency policy.
- [ ] Define bond-specific attributes only when needed for supported bond analytics: identifier, issuer, coupon, issue date, price, accrued interest, and call/put terms as applicable.
- [ ] Add deposit classification fields needed by the chosen liquidity methodology.
- [ ] Keep canonical source payloads and lineage intact when extending projections.

### Acceptance criteria

- Instrument maturity is correctly typed and projected.
- A supported fixed-rate loan can be represented without hiding key contract terms in arbitrary text attributes.
- A floating-rate instrument can represent its reference rate and reset behaviour.
- Cash flows and positions resolve to the intended instrument identity, with documented treatment of historical records.
- Effective-dated changes can be represented without confusing import chronology with contractual validity.
- FX consolidation is unavailable or explicitly blocked until the rate source and conversion policy are configured.
- All schema changes have migrations, rollback/forward strategy, projector tests, and lineage verification.

## 9. Workstream E: calculation execution and data scale

**Priority:** P0 before significant production scale

### Tasks

- [ ] Refactor `calculations/service.py` into smaller responsibilities such as snapshot resolution, run creation, execution, result storage, post-checks, and result queries.
- [ ] Move CPU-bound calculation execution off the async HTTP event loop into a worker process or bounded worker pool.
- [ ] Use the existing run lifecycle consistently and return a job/run identifier for long-running work.
- [ ] Define retry, timeout, cancellation, idempotency, and failure semantics for background runs.
- [ ] Ensure a retry cannot create duplicate financial effects or mutate a pinned baseline.
- [ ] Move heavy projection and file-processing tasks to bounded background execution when needed.
- [ ] Measure peak memory and runtime using representative synthetic datasets larger than the current demo.
- [ ] Reduce whole-snapshot loading through partitioned queries, streaming, or chunked processing where measurements justify it.
- [ ] Add database connection-pool limits and timeouts.
- [ ] Add pagination that makes truncation explicit; use cursor pagination where appropriate rather than silently stopping at 100/200 rows.
- [ ] Replace local filesystem artifact storage with an object-storage adapter before running multiple backend instances.
- [ ] Add performance regression tests for run duration, memory, concurrent requests, and worker saturation.

### Acceptance criteria

- A long calculation does not block unrelated API requests on the same web worker.
- Run status transitions are observable and tested for success, failure, retry, and timeout.
- Re-running a pinned run remains deterministic.
- Performance tests establish an explicit supported dataset size and concurrency target.
- The service returns a clear limit/error rather than exhausting memory or silently truncating results.
- Multi-instance deployment does not depend on instance-local upload files.

## 10. Workstream F: maintainability and frontend correctness

**Priority:** P1/P2

### Tasks

- [ ] Break the circular dependencies between calculations/orchestration and core/governance on the backend.
- [ ] Break the runtime cycle between frontend calculations and advanced components.
- [ ] Split the 2,599-line calculation service and 2,405-line ingestion service by responsibility.
- [ ] Split the 751-line mapping workbench and 650-line comparison view into smaller components and hooks.
- [ ] Move shared constants, helpers, refusal handling, and idempotency-key generation into neutral core modules.
- [ ] Serve rule limits and upload limits from a canonical API/configuration source rather than duplicating backend constants in the frontend.
- [ ] Fix silent pagination truncation and expose total counts or continuation tokens.
- [ ] Add frontend token-refresh handling or a clear, tested reauthentication path.
- [ ] Enforce permission checks consistently in all UI routes, while keeping the backend as the authorization authority.
- [ ] Remove dead helpers and unreachable components after confirming no dynamic usage.
- [ ] Retire mock pages once real API-backed equivalents are available.

### Acceptance criteria

- No unintentional runtime dependency cycles remain in the targeted modules.
- Frontend and backend limits cannot drift silently.
- Large lists are navigable without silent data loss.
- Refactoring does not change API contracts or financial results without an explicit versioned change.
- The UI consistently labels synthetic, provisional, and validated outputs.

## 11. Workstream G: testing, security operations, and deployment

**Priority:** P1 before production deployment

### Tasks

- [ ] Remove the arbitrary test-function cap or use parametrized cases so failures are isolated and parallelizable.
- [ ] Split oversized test functions into focused tests for calculation boundaries, permissions, lifecycle transitions, and errors.
- [ ] Add OIDC/JWKS tests, including key rotation, invalid algorithms, expiry, issuer/audience validation, and failure paths.
- [ ] Add security tests for upload bombs, malformed archives, injection, request headers, rate limits, tenant isolation, and authorization boundaries.
- [ ] Add Python dependency locking and install locked dependencies in CI.
- [ ] Add Dockerfiles and a repeatable production build process.
- [ ] Add a deployment pipeline with environment/configuration validation and secret management.
- [ ] Add structured request/access logs with correlation IDs and safe redaction.
- [ ] Add metrics for request latency/errors, run duration/status, worker backlog, import failures, memory, and database pool saturation.
- [ ] Add readiness and liveness endpoints with distinct semantics.
- [ ] Add alerting and an error-tracker integration appropriate to the deployment.
- [ ] Document backup/restore, retention enforcement, migration deployment, incident response, and recovery objectives.
- [ ] Document the current trigger behaviour implemented in migrations.
- [ ] Define an API compatibility and deprecation policy before introducing breaking changes.

### Acceptance criteria

- CI runs locked builds, backend/frontend tests, static checks, dependency scans, migration round trips, and end-to-end tests.
- Authorization and tenant-isolation tests pass.
- Production deployment is repeatable from a clean checkout.
- Operators can detect elevated error rates, slow runs, stuck jobs, and worker saturation.
- Restore and migration procedures are documented and exercised in a non-production environment.

## 12. Suggested delivery sequence

This is a dependency-oriented sequence, not a commitment to a fixed calendar.

| Stage | Focus | Exit gate |
|---|---|---|
| 1 | Dependency security and demo credibility | Critical dependency risk addressed; false claims removed |
| 2 | Canonical hashing and regression safety | Consistent hashes and golden tests |
| 3 | Methodology scope and expert review | Approved definitions and reference cases |
| 4 | Instrument schema/projector correction | Typed maturity and governed domain links |
| 5 | Background execution and performance | Non-blocking jobs, deterministic retries, measured capacity |
| 6 | Deployment and observability | Repeatable build, logs/metrics, security controls |
| 7 | Financial intelligence expansion | First end-to-end company/credit workflow with traceable calculations |

Some work can run in parallel, but schema and methodology decisions should be documented before dependent analytical features are implemented.

## 13. Release gates

### Gate A: Hardened internal MVP

- [ ] Security and demo-credibility tasks completed.
- [ ] Core test suites green.
- [ ] Canonical hashing policy consistent.
- [ ] No known silent precision loss in supported production calculation paths.
- [ ] Provisional methodology is clearly labelled.
- [ ] Instrument maturity defect fixed or the affected feature explicitly blocked.

### Gate B: Limited pilot with controlled users and synthetic/non-production data

- [ ] Deployment process is repeatable.
- [ ] Authentication, authorization, tenant isolation, rate limits, and upload controls are tested.
- [ ] Calculation jobs do not block request handling.
- [ ] Operational logs and metrics are available.
- [ ] Dataset and concurrency limits are documented.
- [ ] Outputs and limitations are clearly explained to pilot users.

### Gate C: Real financial data or regulatory-grade use

- [ ] Data licensing, privacy, retention, and access controls are approved.
- [ ] Target methodology is documented and reviewed by qualified domain experts.
- [ ] Outputs are validated against independent reference calculations.
- [ ] FX, valuation, classification, and reporting policies are approved where applicable.
- [ ] Security review, recovery testing, and production monitoring are complete.
- [ ] No unapproved provisional rule is represented as a validated regulatory result.

Passing Gate A or B does not imply Gate C readiness.

## 14. KEEP / REFACTOR / BUILD decisions

| Component | Decision | Guardrail |
|---|---|---|
| Decimal calculation functions | **KEEP** | Change only with regression tests and methodology approval |
| Scenario overlay | **KEEP** | Preserve conservation checks and pinned-run determinism |
| Ingestion adapters | **KEEP** | Preserve file-hardening and strict parsing |
| Governance, audit, lineage | **KEEP** | Preserve append-only semantics and approval controls |
| Database migration foundation | **KEEP** | Extend through governed, tested migrations |
| Calculation service | **REFACTOR** | Separate execution, orchestration, storage, and query responsibilities |
| Ingestion/mapping services | **REFACTOR** | Extract cohesive services; preserve adapter behaviour |
| Domain schema/projector | **REFACTOR / EXTEND** | Version schemas and retain historical lineage |
| Frontend feature areas | **REFACTOR** | Remove duplication and silent truncation incrementally |
| Agent and liquidity demo logic | **REMOVE OR REBUILD** | Must use real APIs or be clearly labelled as non-computational mockups |
| AI/reporting/lineage stubs | **BUILD WHEN READY** | Depend on verified data contracts and deterministic analytics |
| Deployment/observability | **BUILD** | Required before production operations |

## 15. Definition of done for every hardening change

Each change must:

1. Link to a finding or requirement in this plan.
2. State its scope and any API/schema/behavioural impact.
3. Include focused tests and relevant regression tests.
4. Preserve decimal precision, lineage, permissions, audit history, and deterministic behaviour where applicable.
5. Include migration and rollback considerations when the database or persisted data changes.
6. Avoid introducing mock data or hard-coded financial outputs into production paths.
7. Update documentation and operational guidance.
8. Report test commands and actual results; do not claim tests passed unless they were run.
9. Identify remaining limitations and any risk accepted by an accountable owner.
10. Avoid unrelated refactors in the same change unless needed to safely deliver the task.

## 16. Instructions for AI coding agents

When implementing this plan:

- Inspect the current repository and the applicable architecture/decision records before editing.
- Treat this document as a plan, not permission to make every change in one pass.
- Work in small, reviewable phases and ask for clarification when a financial rule or business meaning is unspecified.
- Do not invent regulatory assumptions, reference rates, FX sources, risk grades, or golden expected values.
- Do not change a financial formula solely to make a test pass.
- Do not alter historical hashes or append-only records without an explicit migration/compatibility design.
- Do not claim “exact”, “verified”, “compliant”, “production-ready”, or “validated” unless the implementation and evidence support that claim.
- Never weaken authentication, authorization, tenant scoping, masking, audit, or input validation to make a workflow easier.
- Run targeted tests after each change and the relevant full suites before declaring completion.
- Summarize changed files, design decisions, test evidence, migration implications, and unresolved risks.
- Stop and report blockers instead of fabricating missing information.

## 17. Final engineering decision

**MoodyMate should be hardened through targeted refactoring and domain expansion, not rewritten wholesale.**

Preserve the strongest assets: exact-decimal arithmetic, governed ingestion, lineage, audit, immutable history, and reproducible runs. Address security and credibility defects first; then correct instrument semantics, validate the liquidity methodology, and move heavy execution to background workers. Build broader credit and market intelligence only on top of those verified foundations.

The goal is not merely to make the current MVP larger. It is to make every supported financial result explainable, reproducible, appropriately validated, and safe to extend.
