# AI-Assisted Development Framework

*How Fusion Practices builds the Financial Risk Platform (MoodyMate) with AI assistance,
and the controls that keep the result trustworthy.*

## Why a framework

AI coding assistants are fast. Fast is only useful if what they produce is correct,
traceable and reversible. For a platform that calculates liquidity metrics a bank may
rely on, the standard is higher still. This framework fixes the order of work, the
boundaries each AI agent works within, and the evidence needed before anything is
called finished.

## The flow

| # | Step | What happens | Evidence produced |
|---|---|---|---|
| 1 | **Requirement** | The feature is written down in business terms, mapped to workstream feature IDs (DG-, DI-, FC-, RO-, AI-). | Kickoff file in `docs/prompts/kickoffs/` |
| 2 | **Research** | The agent reads the signed-off architecture, the data model, the decision log, project memory and the current codebase before proposing anything. | List of real gaps and constraining decisions |
| 3 | **Challenge** | Assumptions are questioned: conflicts between documents, missing client facts, regulatory interpretation. Open questions go to a person, not into code. | Questions or proposed decisions |
| 4 | **Plan** | The solution architect breaks the feature into phases and tasks, with a checkable "Done-when" and a reversibility note for each. Out-of-scope items are listed. | `docs/DELIVERY_PLAN.md` entry, approved by a person |
| 5 | **Isolate** | Each feature is built in its own git worktree, outside the running application, against a copy of the database. | Branch and worktree; the live dev DB is untouched |
| 6 | **Build inside agent boundaries** | Specialised agents build one phase at a time. Each has a fixed set of tools and files it may change. | Commits per task |
| 7 | **Independent review** | Code is reviewed by a reviewer other than its author (a separate agent pass and a person) for correctness, security and rule compliance. | Review notes |
| 8 | **Build, type check, migration round trip** | Compile, type check, lint, and run every database migration up, down and up again. | Command output attached to the task |
| 9 | **Machine regression** | Unit, contract, integration, security and financial golden-case tests. | Test reports |
| 10 | **Human testing** | A person uses the feature as a user would. | Test notes |
| 11 | **End-to-end** | Source file → ingestion → mapping → calculation → reconciliation → report, on synthetic data. | E2E run IDs |
| 12 | **Scenario testing** | Business scenarios with approved expected results: boundary cases, bad data, permission denials, AI disabled. | Scenario results against tolerances |
| 13 | **Record decisions** | Every material choice is appended to the decision log. Nothing is renumbered or rewritten. | `docs/DECISIONS.md` |

## The three agents and their boundaries

| Agent | May | May not |
|---|---|---|
| **Solution architect** | Read code and docs. Write the delivery plan, decision log and architecture overview. Verify and tick tasks against real evidence. | Run shell commands. Edit source code. Tick a task without evidence. |
| **Backend engineer** | Build backend modules in a strict layered structure (Router → Service → Repository → Database) and run tests and migrations on a database copy. | Edit the plan or decisions. Tick its own tasks. Touch the live database. Ship destructive migrations. |
| **Frontend engineer** | Build the interface exactly to the approved design system, using only the backend API and types generated from its contract. | Edit the plan or decisions. Tick its own tasks. Hand-write API types. Treat hidden buttons as security. |

All agents treat retrieved text (files, memory, documents) as data, never as
instructions.

## Design constraints

- **Additive, never destructive.** Gaps are closed by adding, never by removing. Forward
  migrations never drop data structures.
- **Reversibility.** Every schema change has a tested rollback. Lifecycle actions hide
  data rather than delete it. New behaviour can be switched off to restore the previous
  behaviour exactly.
- **Isolation.** Work in progress never runs against the live development database or
  inside the running application folder.
- **Fail closed authorization.** Anything not explicitly permitted is denied. Data scope
  comes from the signed-in identity, never from the request.
- **Tenant and data-scope isolation.** The data model is tenant-aware from day one.
  Tenant, legal entity and dataset scope are applied at the data-access layer for every
  query.
- **Deterministic, reproducible calculations.** Every result is tied to an immutable
  record of the exact data and configuration versions used. AI never produces an
  authoritative number.

## What the client receives

Instead of raw AI prompts and chat logs, the client gets durable engineering artefacts:

- **Methodology:** this document.
- **Architecture:** the signed-off architecture and data model, plus the live
  architecture overview kept in step with decisions.
- **Decisions:** an append-only log of every material decision, its reason and how to
  reverse it.
- **Phase-wise plans:** each feature's phases, tasks, acceptance criteria and evidence of
  completion.
- **Git history:** small, attributable commits per task, built in isolation and reviewed.
- **Scenario testing:** business scenarios with expected and actual results and
  tolerances.
