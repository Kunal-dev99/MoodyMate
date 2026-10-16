# Phase 9 — quick verification (BE-9.1…9.3, FE-9.1)

This was a single-pass review at the user's request for speed, read at `07c437b`. All four
Done-whens are met, with no BLOCKER.

| Task | Verdict | Proof |
|---|---|---|
| BE-9.1 | Met | `test_profile_tables.py`: append-only UPDATE/DELETE/TRUNCATE, no session bypass, round trip, downgrade refusal |
| BE-9.2 | Met | `test_profiler.py`: golden profile of `instrument.csv`, same hash twice, no float, RECEIVED→PROFILING→MAPPING_REQUIRED audited with metadata only |
| BE-9.3 | Met | `test_import_profile.py`: masking across 5 roles, `in_contract:false`, registry and contract rows unchanged, 403, same 404 |
| FE-9.1 | Met | `e2e/profile.spec.ts`: Suggested labels, one Not-in-contract flag, no non-GET calls; `profile.test.ts` grep |

## Defects

| ID | Severity | Status |
|---|---|---|
| P9-1 | MAJOR: no off switch; a downgrade would break uploads (D-02) | **Fixed**: `IMPORT_PROFILING_ENABLED`, `test_profiling_can_be_switched_off` |
| P9-2 | MINOR: a failed profiling run was never retried | **Fixed**: a replay re-profiles, `test_a_replay_profiles_an_import_whose_profiling_failed` |
| P9-3 | MINOR: the masking branch for mapped columns is untested (no mappings before Phase 10) | Tracked for Phase 10 |

## Accepted deviations

- Uploads now end MAPPING_REQUIRED until Phase 10 provides mappings (D-21). The Phase 8 e2e
  test is updated to match.
- min and max are masked even when null, for readers who are not cleared (fails closed).
- The frontend looks up `schema_id` via `GET /schemas`. Adding `schema_id` to the profile
  response is a Phase 10 nicety.
