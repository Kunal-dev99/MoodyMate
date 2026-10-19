# Agentic Stress-Test Explorer: product design (from the user, 2026-10-19)

This file is the user's own design for Feature 05, kept as written. `docs/DELIVERY_PLAN.md`,
under "Feature 05", turns it into phases and tasks.

**Coordinator note on the figures:** the numbers in §5 are the user's illustrative example. They
are not seed data and not a methodology.

---

## Core idea

The existing maturity ladder becomes a scenario simulation system:
- an AI agent proposes financial stress situations;
- it configures controlled changes to cash flows;
- it runs the existing calculation engine;
- it explains how the bank's liquidity position changes.

**The AI does not calculate the maturity ladder itself.** It orchestrates scenarios around the
existing deterministic engine.

## 1. What problem it solves

The current maturity ladder answers: "Given the financial data we have, what are our expected
inflows, outflows, and liquidity gaps across time buckets?"

The Stress-Test Explorer answers a different question: "What would happen to those gaps if our
assumptions about the future turned out to be wrong?" For example:
- 20% of loan repayments expected this month arrive late;
- customers withdraw more deposits than expected;
- a funding facility becomes temporarily unavailable;
- several of these happen together.

The agent helps identify relevant scenarios, translates them into structured parameters, and
coordinates their execution.

## 2. Architecture

1. **Analyst request.** For example: "Stress-test our 30-day liquidity if 20% of inflows are
   delayed by 14 days."
2. **AI scenario planner.** It interprets the intent, selects approved stress templates,
   identifies the parameters, and asks about anything ambiguous.
3. **Scenario validator and policy gate.** It checks bounds, permissions, data availability,
   units and scenario compatibility.
4. **Deterministic scenario engine.** It applies controlled adjustments to cash flows without
   overwriting the original records.
5. **Existing maturity ladder calculator.** It recalculates inflows, outflows, net gaps and
   cumulative gaps by bucket and currency.
6. **Comparison and explanation.** It compares baseline against stress, highlights the largest
   changes, and links them to evidence.

There are two intelligence layers:
- **AI layer:** understands requests, proposes scenarios, selects tools and explains results.
- **Financial calculation layer:** applies validated adjustments and does exact arithmetic
  using the existing methodology.

The scenario engine, the validation gate and the comparison workflow are new capabilities.

## 3. Agent internals: a controlled workflow, not one unrestricted LLM call

- **A. Intent interpretation.** The analyst's natural-language request becomes a structured
  scenario specification. For example, "Delay 20% of loan inflows by two weeks" becomes an
  inflow delay with a fraction of `0.20` and a delay of `14` calendar days.
- **B. Scenario selection.** The agent picks from approved templates: delayed receivables,
  elevated deposit withdrawals, reduced funding availability. If a key parameter is missing,
  it asks the user instead of guessing.
- **C. Validation.** The selected cash flows must exist, the percentage must be within the
  configured bounds, and the delay must have valid units. The user must be permitted to run
  the scenario.
- **D. Tool orchestration.** The agent calls specific application APIs: create the scenario,
  execute the simulation, retrieve the result, and compare it with the baseline. It has no
  direct database write access.
- **E. Evidence-backed explanation.** The agent uses the calculation output and source lineage
  to explain which buckets changed, why, and which cash flows contributed.

## 4. A scenario is data, not generated code

A scenario is stored as a versioned configuration object. The AI generates or selects it, and
the backend validates it against a strict schema. An illustrative proposed contract:

```json
{
  "scenario_type": "DELAYED_INFLOWS",
  "name": "Two-week loan repayment delay",
  "base_snapshot_id": "snapshot-123",
  "reporting_date": "2026-10-19",
  "parameters": {
    "cashflow_type": "LOAN_REPAYMENT",
    "affected_fraction": "0.20",
    "delay_days": 14,
    "delay_calendar": "CALENDAR_DAYS"
  },
  "methodology_version": 1,
  "status": "PENDING_APPROVAL"
}
```

The backend also records:
- the scenario version and its author;
- approval details;
- execution status;
- the resulting run id.

Amounts and percentages are exact decimals, never binary floats.

## 5. Worked example: delay 20% of loan repayments by 14 days

The baseline for one currency (₹ lakh, illustrative):

| Bucket | Inflows | Outflows | Net gap |
|---|---|---|---|
| Up to 7 days | 100 | 80 | +20 |
| 8 to 30 days | 60 | 70 | −10 |
| 31 to 90 days | 90 | 40 | +50 |

Of bucket 1's inflows, 50 are eligible loan repayments. 20% of those is 10, and that 10 moves
from bucket 1 into the 8-to-30-day bucket.

The stressed result:

| Bucket | Inflows | Net gap |
|---|---|---|
| Up to 7 days | 90 | +10 |
| 8 to 30 days | 70 | 0 |
| 31 to 90 days | (unchanged) | +50 |

The scenario changes when the money arrives, not the total received. The explanation has to
come from the actual run output, never from invented figures.

**Combined scenarios.** For example:
- delay 20% of loan repayments by 14 days;
- add 15% to deposit outflows;
- make a specified funding source unavailable for 7 days.

Each shock has an explicit application rule. The engine applies the shocks to a
scenario-specific copy of the inputs, and shows each shock's impact on its own and the
combined impact.

## 6. What makes it agentic

1. **Plans:** finds plausible scenarios from the analyst's objective and the available data.
2. **Acts through tools:** creates validated scenario configurations and calls the approved
   simulation APIs.
3. **Observes:** checks the results and whether the runs completed.
4. **Adapts:** "What if the delay were 30 days?" creates a new scenario version and runs the
   comparison again.
5. **Explains:** finds the largest liquidity changes and traces them to the contributing
   records.

**Advanced, later:** a bounded search that proposes follow-up scenarios, for example raising a
withdrawal shock until a liquidity threshold is crossed. It needs explicit stopping conditions
and approved parameters.

The agent never silently changes the bank's original data, never approves its own restricted
actions, and never executes real funding transactions.

## 7. Components

| Component | Responsibility |
|---|---|
| Scenario API | Create, version, validate, approve and retrieve scenarios |
| Scenario engine | Apply defined adjustments to a snapshot's eligible cash flows |
| Calculation adapter | Send the transformed inputs through the existing maturity ladder methodology |
| Run orchestrator | Track execution status, failures, retries and resulting run ids |
| Comparison service | Baseline-versus-stress differences per bucket and currency |
| AI agent | Interpret requests, select tools, propose scenarios, explain verified results |
| Audit layer | Keep the snapshot, scenario definition, methodology version, approvals and result fingerprints |

A stress scenario never mutates the snapshot. It creates a separate, traceable scenario
execution based on the original snapshot, and records its adjustments explicitly.

## 8. MVP

- **Three templates:** delayed loan repayments, increased deposit withdrawals, reduced
  availability of a specified funding source.
- **Agent scope:** the agent proposes scenarios, and they run after the user confirms them.
  Multi-scenario exploration and optimisation come later.
- **Success metrics:**
  - time taken to configure a stress test;
  - the share of scenario outputs reproduced exactly;
  - the share of explanations backed by evidence;
  - whether scenarios uncover liquidity gaps the baseline misses.
- **Build order:** the deterministic scenario engine first, then the AI agent on top of it.
