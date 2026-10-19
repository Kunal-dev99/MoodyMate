# Demo: Agentic Stress-Test Explorer (local only)

Everything here is ILLUSTRATIVE / SYNTHETIC (D-14). The seed refuses to run unless the backend
config is `ENV=local` with synthetic data.

## 1. Seed the demo data

Start the backend (`scripts/start-backend.bat`, `.ps1` or `.sh`), then from `backend/`:

```
# Windows
.venv\Scripts\python -m scripts.seed_demo
# macOS / Linux
.venv/bin/python -m scripts.seed_demo
```

Add `--api http://localhost:PORT` if the backend is not on port 8000. The script uses the
public API as the seeded dev users, so permissions, maker-checker and audit apply as in the
UI. It is safe to run again: steps already done are skipped.

It creates:
- **Projected data:** the synthetic instrument, position and cash-flow samples (25 rows each),
  mapped by data_steward, approved by approver, validated and projected. Instruments and cash
  flows share the source "Core banking instruments (demo)" (`demo_instruments`: contract v1
  for instruments, v2 for the cash-flow schedule), so each instrument's detail lists its cash
  flows. Positions come from "Positions (demo)" (`demo_positions`).
- **Credit data:** the counterparty and credit-exposure samples, from "Counterparty master
  (demo)" and "Credit exposures (demo)", the same way.
- **Ratings:** one ACTIVE obligor rating and one IN_REVIEW for the Ratings review queue.
- **Calculations:** a maturity ladder run and an LCR/HQLA run (reporting date 2026-12-20) on
  one snapshot of the instrument, position and cash-flow imports.

Sources and mappings have stable codes (`demo_instruments`, `demo_cashflows`, ...). A re-run
finds them by code and skips what is already projected.
- **"Two-week loan repayment delay"** (`DEMO_LOAN_DELAY`): delay 20% of LOAN inflows by 14
  days. risk_analyst confirmed it for exploration, so it shows under "My explorations".
- **"Combined funding stress"** (`DEMO_COMBINED_STRESS`): the loan delay, DEPOSIT outflows
  +15%, and one BOND funding source unavailable for 7 days. risk_analyst submitted it and
  approver approved it, so it shows in the Shared library.

## 2. Walk through it

1. Sign in as **Risk analyst** and open Advanced > Agentic Stress-Test Explorer. Show the
   Shared library ("Combined funding stress") and My explorations ("Two-week loan repayment
   delay", labelled "Exploration, not approved").
2. Open "Combined funding stress": three shocks, the author, the approver and the approval
   comment.
3. Click **New scenario** and build one live: Delayed inflows, LOAN, 20%, 14 days. Type 1.5
   as the fraction to show the bound check, then fix it. Add Deposit withdrawal, DEPOSIT, 15%.
   Save the draft and confirm it for exploration.
4. Submit it for approval and show that the author gets no Approve button (no
   self-approval).
5. Sign in as **Approver**, open the Review tab, and approve it with a comment. It appears in
   the Shared library.
6. Open the Audit page to show each step recorded.

Platform admin holds no scenario permissions, so it sees an empty explorer. Use Risk analyst
or Approver.
