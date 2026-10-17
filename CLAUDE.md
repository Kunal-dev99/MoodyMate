# MoodyMate — working rules for Claude Code

The delivery plan is `docs/DELIVERY_PLAN.md`, the decision log is `docs/DECISIONS.md`, and the
architecture overview is `docs/ARCHITECTURE.md`. Active decisions bind every change.

## Phase build protocol (parallel agent swarm)

This is the default for every phase. The saved workflow `.claude/workflows/phase-build.js` runs
the build part of it end to end. The user's standing approval is: "build Phase N" means run
that workflow, merge on green CI, and report only at the end, with the phase summary and any
hard-stop items.

1. **Contract first.** The backend agent first exports the phase's API (Pydantic schemas,
   declared routes and permissions) to `backend/openapi.json`, and only then implements.
   Frontend agents start at once and run `npm run gen:api` as soon as the routes appear. They
   use generated types only.
2. **Fan out in one message.** Each agent owns separate files:
   - **backend-engineer:** services, routes, migration.
   - **Test writer** (a backend-engineer instance): the phase's one test function, or
     assertions folded into existing test functions.
   - **frontend-engineer(s),** split by page area. Exactly one of them owns
     `src/config/navigation.ts` and `src/config/features.ts`.

   Shared files such as `test_ws1_verification.py` are edited in small, separate hunks.
3. **Isolation (D-03).**
   - One worktree per phase.
   - Its own DB copy: `createdb -T moodymate_dev`. Never touch `moodymate_dev` itself.
   - Each agent gets its own test DB and its own e2e ports.
   - Frontend agents that run Playwright at the same time build in separate output folders.
4. **Tests.**
   - Agents run targeted tests only.
   - The coordinator runs the full backend suite once.
   - CI is the full Playwright run.
   - The backend suite stays under 50 tests (D-26): new checks are folded into existing test
     functions.
5. **Review.** One quick, read-only review agent runs alongside e2e.
   - MAJOR and MEDIUM findings are fixed before merge.
   - LOW findings go in the phase report (`docs/evidence/phase-N/REPORT.md`).
6. **Parallel phases.** A later phase may start in its own worktree with a pre-numbered
   migration (`down_revision` points at the last merged head). The coordinator re-chains it
   at merge time, and later phases merge earlier ones in before their PR.
7. **Coordinator (the main session).** It owns:
   - merges and migration order;
   - conflict hand-offs to the owning agent;
   - plan ticks and the phase report;
   - the PR, and the merge once CI is green.

### Open questions: the decision-resolver

When an engineer or reviewer raises an open question, the coordinator sends it to the
`decision-resolver` agent (`.claude/agents/decision-resolver.md`) instead of asking the user.
Typical questions:
- a status code;
- plan wording that doesn't match what was built;
- a masking edge case;
- where a test goes, or what to name something;
- a contract gap;
- a methodology default.

The resolver follows existing decisions. Where none applies, it picks the safer or stricter
option. The coordinator records each ruling, marked **auto-resolved**, either as a dated
amendment in `docs/DECISIONS.md` (if it changes or extends a decision) or as a note in the
phase report.

### Hard stops (the only things that go to the user)

These go to the user in ONE message at the end of a phase, never mid-phase:
- deleting data, or anything irreversible on shared systems;
- a D-01 exception: a destructive migration, or widening an existing CHECK;
- any change to the roles and permissions model, or to who can approve what;
- real client data, regulatory claims, or vendor-parity claims;
- anything the client must decide before production. List these as open items; they don't
  block the build.

Everything else is decided inside the phase and recorded.
