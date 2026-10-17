# Phase 16 — governed rule changes and the registry: checks

**Scope.** BE-16.1 to BE-16.4, FE-16.1 and FE-16.2 (FC-07, FC-08). Decisions D-31 and D-32
(both amended in this phase), D-33 and D-34 apply. The phase was built by five parallel
agents in one worktree. No migration was needed.

## What was built

- **BE-16.1 Parameter checks.** Rule parameters are typed data from a closed list of six
  kinds: DECIMAL, INTEGER, ENUM, DAY_BUCKETS, ENUM_MAP and DECIMAL_MAP.
  - Bucket lists hold at most 50 entries and maps at most 200. Buckets must be contiguous,
    and only the last bucket may be open. Scale runs from 0 to 18.
  - A merge patch that adds or removes keys is refused.
  - DECIMAL values are stored as exact decimal text. Any fractional JSON number is refused
    (`inexact_number`).
- **BE-16.2 Rule changes.**
  - `GET /rules` and `GET /rules/{id}` list versions with their author, decider and run
    counts.
  - Propose sends a JSON merge patch over the ACTIVE version. Only the author may submit,
    and the bucket definitions are written at submit. Approval is APPROVE or REJECT, with a
    comment.
  - Maker-checker refusals: `self_approval`, `not_author` and `stale_version`.
  - Approving supersedes the previous version in the same transaction. A per-rule lock and
    the one-ACTIVE index guard this.
  - The feature sits behind `RULE_CHANGE_ENABLED`.
- **BE-16.3 Pinning and re-runs.** A new run pins the newly ACTIVE version. `rerun_of`
  reproduces the original `manifest_sha256` and `result_sha256` on its SUPERSEDED pins.
  A result mismatch ends the run FAILED.
- **BE-16.4 Assumption sets.** `GET /assumption-sets` and `GET /assumption-sets/{id}` are
  read-only and return keys and hashes only, never values.
- **FE-16.1 Registry pages.** Under `/domain` there are now four tabs: Instruments, Rules,
  Modules and Assumption sets. The rule detail page shows the ILLUSTRATIVE badge, the
  version history, a parameter diff and the runs that pin each version. The pages are
  behind `NEXT_PUBLIC_RULES_UI`.
- **FE-16.2 Propose → submit → approve.** There is one typed editor per kind; buckets stay
  contiguous. Approve and reject open a dialog with a required comment, and the author never
  sees Approve.

## Checks (local)

| Check | Result |
|---|---|
| ruff, ruff format, mypy, OpenAPI drift | clean / 155 files / no issues / up to date |
| Backend suite (D-26) | 48 collected, 48 passed |
| Frontend lint, tsc, vitest | clean, clean, 327 passed |
| Playwright, full suite on a fresh DB | 38 passed (2.1 m), including `rules.spec.ts` (3) and `registry.spec.ts` (2) |

**Review.** One quick pass found no major issues. Two medium findings were fixed:
- a user holding both roles could submit someone else's DRAFT and then approve it;
- a re-run did not check that it reproduced the original `result_sha256`.

The low finding (422 body-validation refusals are not audited) applies to the whole app and
is left unchanged.

## Notes and open items

- **FE-16.2 self-approval.** The author's direct POST gets 403, because `risk_analyst` has
  no `rule:approve`. The 409 `self_approval` path, for a user who holds both roles, is
  covered in `test_rules.py`.
- **Re-run mismatches** reuse `POST_CHECK_FAILED` with `{"check": "RERUN_REPRODUCED"}`. A
  dedicated warning code would need a CHECK migration, so it would need a named decision.
- **Day 0 in the first bucket.** DAY_BUCKETS allows a first lower bound of -1, so day 0 can
  be bucketed once the methodology owner decides.
- **The ws3 marker** is applied to `test_rules.py` when Phase 18 merges.
- **Contract polish.** Parameters are untyped `dict` in the OpenAPI, and assumption sets
  carry no run count.
