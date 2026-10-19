# Kickoff 05 — Advanced: Agentic Stress-Test Explorer

Instance of `docs/MASTER_PROMPT_FEATURE_KICKOFF.md`.

## 1. Requirement

- **Feature:** a new sidebar section, **Advanced**, placed directly below "AI Assistant"
  (`frontend/src/config/navigation.ts:121-128`). Its first item is the **Agentic Stress-Test
  Explorer** for the maturity ladder:
  - an agent proposes stress scenarios, for example delayed loan repayments, increased
    deposit withdrawals or reduced access to funding;
  - approved scenarios run through the existing deterministic calculation framework;
  - the impact is compared bucket by bucket against the baseline run.
- **Innovation:** the analyst explores a structured set of plausible stress events and
  their consequences, instead of designing every scenario by hand.
- **Workstream / feature IDs:**
  - WS4: RO-02 (scenario management), RO-06 (approval, for scenarios only) and RO-07
    (reproducibility);
  - WS5: AI-01 (structured proposals), AI-04 (thin: drafts behind approval gates, no tool
    calls), AI-05 (offline evaluation) and AI-08 (governance);
  - WS3: FC-08 (pinning).

  The entities and endpoints reuse the spec names: Data Model §6 `scenario`,
  `scenario_change` and `scenario_run` (spec :991-995); §7 `ai_request` and `ai_proposal`
  (:1022, :1028); and §9 `scenario.status` and `ai_proposal.status` (:1180, :1184). WS4
  `POST /scenarios`, `POST /scenarios/{id}/run` and `GET /scenarios/{id}/comparison`, and
  WS5 `POST /ai/assist` and `GET /ai/requests/{id}`, are the canonical names (D-05).
- **Problem and user:**
  - Risk analysts want to see how the illustrative ladder reacts to plausible stresses
    without hand-designing every case.
  - Approvers control which scenarios may run.
  - Auditors trace every stressed figure to the scenario version, the baseline pins and
    the AI request that proposed it, if any.
- **Provider:** Groq (an OpenAI-compatible API), as one provider behind a provider-neutral
  interface. A deterministic offline mock is the default, and CI uses it. The provider is
  configured only from environment variables (D-50).
- **In scope:**
  - a closed shock vocabulary: INFLOW_DELAY, INFLOW_HAIRCUT, OUTFLOW_RUN_OFF and
    OUTFLOW_ACCELERATION (D-44);
  - versioned scenarios with maker-checker;
  - a pure overlay applied in the engine on the baseline run's exact pins (D-45);
  - comparison of deltas and turns-negative buckets, with D-33/D-43-style masking (D-46);
  - the AI proposer, with provenance and a closed data boundary (D-48, D-49);
  - offline evaluation, documentation and verification.
- **Out of scope:** see "Deferred (out of scope for Feature 05)" in `docs/DELIVERY_PLAN.md`.
  In short:
  - splitting a flow between buckets;
  - segment selectors;
  - stressing LCR or credit;
  - free-text prompts;
  - tool-using agents;
  - AI explanations;
  - reports;
  - production use of an external LLM.
- **Acceptance (business terms):**
  - The sidebar shows "Advanced" below "AI Assistant", with "Agentic Stress-Test Explorer"
    as its first item.
  - An analyst picks a baseline ladder run and asks the agent for a set of scenarios (a
    focus and a count, with no free text). The proposals arrive as DRAFT scenarios marked
    AI-proposed, with model id, prompt version and hashes. Invalid AI output is refused and
    never stored as a scenario.
  - The analyst edits a draft, acknowledges its AI origin, and submits it. A different
    person, an approver, approves it.
  - The analyst runs it against the baseline. The stressed run reuses the baseline's pins
    exactly, and re-running it gives identical hashes.
  - The comparison shows inflow, outflow, net and cumulative deltas per currency and bucket,
    marks the buckets that turn negative, and drills down to the changed contributions and
    their source rows. Amounts are masked as on the ladder.
  - With AI switched off, everything except "Propose scenarios" still works.
  - Every page says "Illustrative stress exploration, not a regulatory stress test." Nothing
    claims regulatory status or parity with Moody's or any vendor.

## 2. Pre-plan reading (done 2026-10-19, master `0e443eb`)

1. **Architecture and plan text.**
   - `docs/architecture_spec.txt`:
     - §6.2 run flow (:190-206) and §6.3 governed AI workflow (:208-220);
     - §13 AI guardrails (:428-444), including "Do not let an agent … approve its own
       proposal" and "External model APIs … must be approved";
     - Data Model §6, §7 and §9 (above).
   - `docs/workstream_plans_spec.txt`:
     - WS4 RO-02/06/07 (:971-983) and the P2 scenario comparison, "identical input
       snapshots" (:995-996);
     - WS5 AI-01…08 (:1247-1264);
     - WS5 backend responsibilities: "provider-neutral AI service interface; local/template
       mode must work without external APIs" (:1297).
   - `docs/ARCHITECTURE.md:59,63`: the scenario tables belong to `orchestration`, and `ai_*`
     to `ai`.
2. **Constraining decisions.** D-01…D-06, D-10, D-11, D-13, D-14, D-21 §2, D-26, D-31…D-36 and
   D-43. New proposals: **D-44…D-50.**
   - **D-47 is a hard stop:** new permissions, and who approves scenarios.
   - **D-49 §5 is a hard stop:** live calls to an external LLM provider.
   - **No D-01 exception:** only tables are added.
3. **Codebase gaps.** They are listed under "Pre-plan findings" in the Feature 05 section of
   `docs/DELIVERY_PLAN.md`. The main points:
   - `app/ai` and `app/orchestration` are empty skeletons;
   - `AI_ENABLED` exists and nothing reads it;
   - `intermediate_value` allows one contribution per cash flow, so there is no split shock;
   - the `ref_type` and warning CHECKs are closed, so the new pins and applications get new
     tables;
   - re-runs are version-gated, so `ENGINE_VERSION` and `MANIFEST_VERSION` stay;
   - "AI Assistant" carries a stale "Feature 05" label;
   - the sidebar has no group beyond "Administration";
   - there are 49 backend tests;
   - the migration head is 0023, with 0024–0025 reserved for Feature 04.

## 3. Standing rules

As in the master prompt (D-01…D-04), plus:
- **The AI only proposes, and never calculates, submits, approves or runs** (D-31 §8, D-48
  §7). `app/calculations` and `app/orchestration` never import `app.ai`.
- Scenarios are typed data from a closed list. There are no formulas or code, and model output
  is validated and refused when invalid.
- Pure, Decimal-only, deterministic overlay; immutable runs; stressed runs pinned to the
  snapshot, rule versions, assumption set, module version and scenario version.
- Only aggregated, masked, synthetic context is sent to an LLM (D-49), and a sentinel test
  enforces it.
- Synthetic data only outside production (D-14). Live Groq calls are allowed only in `local`
  and `staging`, and never in CI.
- Keys only in a git-ignored `backend/.env` or the cloud secret store. Never in chat, logs,
  errors, audit, fixtures or `openapi.json` (D-50).
- No claim of a regulatory stress test, or of Moody's or vendor parity.
- The backend suite stays under 50 tests (D-26): **+0** for the whole feature.

## 4. Plan

Delivered to `docs/DELIVERY_PLAN.md` as "Feature 05", Phases 24–27 (numbering per D-08):

- 24: scenario registry, maker-checker and the Advanced sidebar section (migration 0026,
  with the scenario permissions seed);
- 25: stressed runs and baseline comparison (0027). Recommended to start after Phase 21
  merges, because both edit the engine;
- 26: the agent, with AI-proposed scenarios, the provider interface (mock and Groq), the data
  boundary and provenance (0028, with the `ai:use` seed);
- 27: offline evaluation, documentation and verification.

Phases 24 and 26 can run in parallel with Feature 04 Phases 21–23. The migrations are
pre-numbered 0026–0028 and re-chained in merge order by the coordinator.

Decisions D-44…D-50 are proposed in `docs/DECISIONS.md`.

**Status: awaiting user approval of the plan and of D-44…D-50. D-47 (permissions) and D-49 §5
(live external LLM calls) are hard stops and need explicit approval. No code before
approval.**

## 5. Groq: what the user provides, and where

Nothing is needed until Phase 26 merges. CI never needs a key.

- **Local live demo:** in `backend/.env` (git-ignored, `.gitignore:5-7`):
  `AI_ENABLED=true`, `AI_SCENARIO_PROPOSALS_ENABLED=true`, `LLM_PROVIDER=groq`,
  `GROQ_API_KEY=<your key>`, `GROQ_MODEL=<model id>`, and optionally
  `LLM_TIMEOUT_SECONDS=20`. Use `ENV=local` with `DATA_CLASS=synthetic`.
- **Cloud demo (staging):** set the same variables as environment secrets in that
  environment's secret store. The network policy must allow HTTPS egress to `api.groq.com`.
  If egress goes through a TLS-intercepting proxy, configure its CA bundle through the
  environment. Never disable verification.
- **Never** paste the key into chat, an issue, a PR, a doc or a test. If it was ever exposed,
  rotate it in the Groq console.

## 5–6 Build

As in the master prompt, run through the CLAUDE.md phase build protocol (D-36) and
`.claude/workflows/phase-build.js`:
- one worktree per phase, e.g. `../MoodyMate-wt/stress-p24`, with DB copy
  `createdb -T moodymate_dev moodymate_stress_p24`, and per-agent test DBs and e2e ports;
- the backend commits the OpenAPI contract first, and the frontend starts from
  `npm run gen:api`;
- exactly one frontend agent owns `navigation.ts`, `features.ts` and the sidebar group.
