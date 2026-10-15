# BE-1.8 — CI run on the Phase 1 PR

PR: https://github.com/Kunal-dev99/MoodyMate/pull/2 (head 54f0320)
Workflow run: https://github.com/Kunal-dev99/MoodyMate/actions/runs/38741589741

Check runs as returned by the GitHub API on 2026-10-15:

| Job | Status | Conclusion | Started | Completed | Link |
|---|---|---|---|---|---|
| backend | completed | **success** | 13:18:36Z | 13:19:32Z | https://github.com/Kunal-dev99/MoodyMate/actions/runs/38741589741/job/116444393138 |
| frontend | completed | **success** | 13:18:36Z | 13:19:36Z | https://github.com/Kunal-dev99/MoodyMate/actions/runs/38741589741/job/116444393373 |

The backend job runs: ruff + format check, mypy, migration round trip on a postgres:16
service, pytest with REQUIRE_DB=1, and the OpenAPI drift check.
The frontend job runs: npm ci, gen:api + `git diff --exit-code`, lint, typecheck, build.
(See .github/workflows/ci.yml.)
