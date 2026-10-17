# Domain validation pack (BE-18.2)

Every file here is a hand-computed **engineering** golden case for a calculation module
(D-35 §4). Each has the status `ENGINEERING — awaiting domain-expert approval`. None of
them is an "approved golden case". The results come from illustrative, PROVISIONAL rules
(D-35), not a regulatory calculation, and they claim no parity with any vendor system.

`app/tests/test_calculations.py` reads every `*.json` here in its golden loop. It runs each
case end to end (import, projection, snapshot, run, read) and compares the outputs with the
file. The loop refuses a file without the exact status above, and any JSON fraction:
amounts are exact decimal text.

## Who must approve

The client's **named financial owner** for liquidity risk, a financial-domain expert, signs
these cases off. This is the Arch §12 and §17 release gate: no production calculation
sign-off without a named financial owner, an approved specification, a golden dataset and a
tolerance policy. That person is **not yet named**. This is an open item for the client.

Once that person approves a case:

- its `status` changes;
- the approver and date are recorded;
- the seeded rule values it depends on (D-35) are confirmed or replaced through a rule
  version (D-32 §3).

Until then, nothing here may be presented as validated or used for client decisions.

## Tolerance policy

Tolerances are set **per metric**, with no global tolerance (Data Model §7). In v1 every
metric is **exact**: each file's `tolerance` map gives `"0"` for every metric it checks, and
the loop compares with equality on `Decimal`. Any rounding is a declared `quantize` inside
the module (D-31 §2), so the expected values already carry it. The financial owner sets the
real per-metric tolerances. Changing one means changing this file and the loop together.

## File format

One JSON object per case, UTF-8. All amounts are decimal strings, never JSON fractions.

| Key | Meaning |
| --- | --- |
| `name` | A short, unique name for the case. |
| `status` | `ENGINEERING — awaiting domain-expert approval`. |
| `module` | `{code, version}` of the module version the case runs. |
| `description` | What the case covers, and the rule set it assumes. |
| `tolerance` | Per-metric tolerance as decimal text. `"0"` means exact. |
| `inputs` | The synthetic input rows (`reporting_date`, then one list per input schema). Each row may carry a `note` explaining why it is there. Notes are not loaded. |
| `expected` | The expected outputs: run status, excluded inputs with their warning codes, the result rows, and the figures masked for an `internal` role (D-33). |

### Cases

| File | Module | Covers |
| --- | --- | --- |
| `maturity_ladder_v1_boundary_cases.json` | `maturity_ladder` v1 | Bucket edges, on the date, past due, unmapped type, missing instrument, zero amount, open last bucket, two currencies, both origins, D-33 minimum-aggregation masking. |

The `lcr_hqla` v1 case is added here by BE-17.2 when that module exists.
