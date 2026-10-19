# Kickoff 05 — Advanced: Agentic Stress-Test Explorer

Instance of `docs/MASTER_PROMPT_FEATURE_KICKOFF.md`. **The user's own design is binding:**
`docs/design/agentic-stress-explorer.md`. Revised on 2026-10-19 to follow it.

## 1. Requirement

- **Feature:** a new sidebar section, **Advanced**, directly below "AI Assistant"
  (`frontend/src/config/navigation.ts:121-128`). Its first item is the **Agentic Stress-Test
  Explorer**, which turns the maturity ladder into a scenario simulation system:
  - an AI agent proposes financial stress situations;
  - it configures controlled changes to cash flows;
  - it runs the existing deterministic engine;
  - it explains how the liquidity position changes.

  **The AI does not calculate the ladder** (design, "Core idea").
- **Workstream / feature IDs:**
  - WS4: RO-02, RO-06 and RO-07;
  - WS5: AI-01, AI-03, AI-04, AI-05 and AI-08;
  - WS3: FC-08.

  Spec names are reused: Data Model §6 `scenario`, `scenario_change`, `scenario_run`; §7
  `ai_request`, `ai_tool_call`, `ai_proposal`; §9 statuses. The WS4 routes `POST scenarios`,
  `POST scenarios/{id}/run` and `GET scenarios/{id}/comparison`, and the WS5 routes
  `POST ai/assist`, `GET ai/requests/{id}`, `POST ai/explanations` and
  `GET ai/explanations/{id}`, are canonical (D-05).
- **In scope (design §8 MVP):**
  - **three templates:** `DELAYED_INFLOWS`, `DEPOSIT_WITHDRAWAL` and `FUNDING_UNAVAILABLE`,
    with exact-decimal `affected_fraction`, `delay_days` and `delay_calendar = CALENDAR_DAYS`
    (D-44);
  - **a deterministic scenario engine:**
    - partial delays split a flow; the parts are stored in `scenario_contribution`, so no
      D-01 exception is needed;
    - a conservation post-check;
    - each shock is run alone and combined;
    - pinned, reproducible runs (D-45);
  - **a comparison:** deltas, buckets that turn negative, buckets that break only when
    combined, the largest changes, a templated summary and masked drill-down (D-46);
  - **governance:** the analyst confirms their own scenario for exploration runs ("not
    approved"), and promotion needs a second person (D-47, user-approved);
  - **the agent:**
    - natural-language intent → template spec, or clarifying questions, and never a silent
      guess;
    - a bounded conversation stored as hashes plus structured state;
    - closed tools over the service layer (`create_scenario_draft`, `validate`,
      `run_exploration` only after the human confirms, `get_result`, `compare`);
    - "what if" adaptation (D-48);
  - **a data boundary:** the free text goes to Groq after a length cap, a warning and a
    deterministic row-data pre-filter, in local and staging only (D-49, user-approved);
  - **evidence-backed explanations** checked by a deterministic verifier, with a templated
    fallback (D-51);
  - **offline evaluation and the §8 success metrics.**
- **Out of scope:** see "Deferred (out of scope for Feature 05)" in `docs/DELIVERY_PLAN.md`.
  In short:
  - bounded threshold search (v2);
  - multi-scenario optimisation;
  - a funding-source attribute and counterparty selection;
  - business-day calendars;
  - LCR and credit stress;
  - production use of an external LLM.
- **Acceptance (business terms):**
  - "Advanced" appears below "AI Assistant", with "Agentic Stress-Test Explorer" first.
  - The analyst types, for example, "Stress-test our 30-day liquidity if 20% of inflows are
    delayed by 14 days". The agent produces a validated draft, or asks for what is missing.
  - The analyst confirms the draft. The agent runs it on the baseline's exact pins and shows
    the comparison per currency and bucket, for each shock and combined.
  - The user's §5 worked example reproduces exactly (bucket 1 net +20 → +10, bucket 2 −10 → 0,
    bucket 3 unchanged, total inflows conserved).
  - Explanations cite run figures that the verifier has checked, or a templated summary is
    shown instead.
  - "What if 30 days?" creates a new version and compares again.
  - Re-runs are identical.
  - Approved scenarios need a second person, and the AI never confirms or approves.
  - Everything is labelled "Illustrative stress exploration, not a regulatory stress test".
    Nothing claims regulatory status or parity with Moody's or any vendor.

## 2. Pre-plan reading (done 2026-10-19, master `0e443eb`)

1. **Design and specs.**
   - `docs/design/agentic-stress-explorer.md`, all of it.
   - `docs/architecture_spec.txt`:
     - §6.2 run flow and §6.3 governed AI (:190-220);
     - §13 guardrails (:428-444);
     - Data Model §6, §7 and §9.
   - `docs/workstream_plans_spec.txt`: WS4 (:971-1005) and WS5 (:1247-1308).
   - `docs/ARCHITECTURE.md:59,63`.
2. **Decisions.** D-01…D-06, D-10, D-11, D-13, D-14, D-21 §2, D-26, D-30 §1, D-31…D-36 and
   D-43. New: **D-44…D-51.**
   - D-47 is **active, approved by the user**.
   - D-49 §4–5 are **approved by the user**.
   - **No D-01 exception.**
3. **Codebase gaps.** See "Pre-plan findings" in the Feature 05 section of
   `docs/DELIVERY_PLAN.md`:
   - `app/ai` and `app/orchestration` are empty;
   - `intermediate_value` allows one contribution per flow, so `scenario_contribution` is
     needed;
   - the CHECKs are closed, so the pins get their own tables;
   - re-runs are version-gated;
   - there is no funding-source or projected counterparty attribute;
   - the "AI Assistant" label collides with this feature's name;
   - there are 49 tests;
   - the head is 0023, with 0024–0025 reserved.

## 3. Standing rules

As in the master prompt (D-01…D-04), plus:
- **The AI only interprets, proposes, orchestrates closed tools and explains.** It never
  calculates, confirms, submits, approves, closes or touches the DB directly. `app/calculations`
  and `app/orchestration` never import `app.ai`.
- Scenarios are versioned data from three templates. Model output is validated and refused
  when invalid. The agent asks when unsure.
- A pure, Decimal-only engine: exact splits and conservation; runs pinned to the snapshot,
  rules, assumptions, module, scenario version and shock subset; snapshots never mutated.
- Only aggregated, masked, synthetic context and pre-filtered free text go to an LLM (D-49).
  Live Groq runs only in local and staging, never in CI.
- Explanations are verified against run output (D-51).
- Keys live only in a git-ignored `backend/.env` or the cloud secret store (D-50).
- No regulatory or vendor-parity claims.
- D-26: **+0** backend tests (49).

## 4. Plan

Delivered to `docs/DELIVERY_PLAN.md` as "Feature 05", Phases 24–27. The **build order follows
the design: the engine first, then the agent.**

- **24:** the Scenario API: templates, versions, exploration confirmation, promotion, and the
  Advanced section (migration 0026 with the scenario permissions seed).
- **25:** the deterministic scenario engine and comparison (0027). It starts after Phase 21
  merges, because both edit the engine.
- **26:** the agent: intent, clarification, the bounded conversation, closed tools, and the
  provider interface (mock and Groq) (0028 with the `ai:use` seed).
- **27:** evidence-backed explanations with the verifier, offline evaluation and success
  metrics, docs and verification (0029).

The migrations are pre-numbered 0026–0029 and re-chained in merge order by the coordinator.

**Status:** awaiting user approval of the revised plan. The hard stops (D-47 and D-49 §4–5)
are already answered. Still open: the `GROQ_MODEL` id, v1 funding-source selection, and the
PROVISIONAL bounds. No code before approval.

## 5. Groq: what the user provides, and where

Nothing is needed until Phase 26 merges. CI never needs a key.

- **Local:** in `backend/.env` (git-ignored, `.gitignore:5-7`):
  `AI_ENABLED=true`, `AI_SCENARIO_PROPOSALS_ENABLED=true`, `LLM_PROVIDER=groq`,
  `GROQ_API_KEY=<your key>`, `GROQ_MODEL=<model id>`, and optionally `LLM_TIMEOUT_SECONDS=20`
  and `AI_MAX_REQUEST_CHARS=1000`. Use `ENV=local` with `DATA_CLASS=synthetic`.
- **Cloud demo (staging):** the same variables, set as environment secrets. The network
  policy must allow HTTPS to `api.groq.com`. A TLS-intercepting proxy needs its CA bundle set
  through the environment; verification is never disabled.
- **Never** paste the key into chat, an issue, a PR, a doc or a test. If it was ever exposed,
  rotate it in the Groq console.
- The analyst's free text **is** sent to Groq. The UI warns against pasting client data, and
  the pre-filter refuses row-level-looking text before any call.

## 5–6 Build

Run through the CLAUDE.md phase build protocol (D-36) and `.claude/workflows/phase-build.js`:
- one worktree per phase, e.g. `../MoodyMate-wt/stress-p24`, with DB copy
  `createdb -T moodymate_dev moodymate_stress_p24`, and per-agent test DBs and e2e ports;
- the backend commits the OpenAPI contract first, and the frontend runs `npm run gen:api`;
- exactly one frontend agent owns `navigation.ts`, `features.ts` and the sidebar group.
