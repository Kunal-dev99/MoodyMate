# Kickoff 01 — WS1 Data Governance, Security & Platform Foundation

Instance of `docs/MASTER_PROMPT_FEATURE_KICKOFF.md`.

## 1. Requirement

- **Feature:** WS1 platform foundation
- **Workstream / feature IDs:** WS1 DG-01, DG-02, DG-03, DG-04, DG-05, DG-06 (global
  phase G-P1 in Architecture §16)
- **Problem and user:** every later workstream (ingestion, calculations, risk operations,
  AI) needs a secure, audited, versioned base. Platform admins, data stewards, risk
  analysts, approvers and auditors need to know who did what, under which schema version,
  with which permission.
- **In scope:**
  - backend and frontend skeletons (the repo has no application code yet), including the
    error envelope, correlation IDs, redacted logging, the migration framework, CI, and
    the design system applied to the app shell
  - identity and server-side authorization: fail-closed permissions and
    principal-derived row scope
  - append-only audit trail and audit timeline
  - data classification, masking and log redaction
  - schema registry with immutable versions and version diff
  - schema-change proposals with impact analysis and maker-checker approval
  - environment and retention controls, recorded as settings and documented assumptions
  - WS1 verification suite
- **Out of scope:** see the Deferred list in `docs/DELIVERY_PLAN.md`.
- **Acceptance (business terms):**
  - A signed-in user sees only what their role allows; anything else gets a clear 403.
  - Every privileged action appears in an audit timeline that cannot be edited.
  - A data steward can browse schemas, compare versions and propose a change, see its
    downstream impact before submitting, and have a different person approve it.
  - Approved changes create a new version and never rewrite the old one.

## 2–6

As in the master prompt. Plan delivered to `docs/DELIVERY_PLAN.md` (Phases 1–6).
Decisions D-05…D-12 proposed in `docs/DECISIONS.md`.
