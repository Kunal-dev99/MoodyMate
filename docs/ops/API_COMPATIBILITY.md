# API compatibility

**Scope:** the rules for changing the HTTP API (D-62 §6, BE-34.7).
**Status:** D-62 is **proposed**; these rules apply as proposed. The checks named below exist
today. The deprecation headers are a rule only: no endpoint is deprecated, and no helper
sets the headers yet.

## The contract

- Every route is under `/api/v1` (84 paths as of Phase 34).
- The contract is `backend/openapi.json`. It is generated from the code
  (`python -m scripts.export_openapi`, from `backend/`) and never edited by hand (D-05).
- CI fails when the committed file differs from the code
  (`python -m scripts.export_openapi --check`) or when the frontend's generated types are
  stale (`npm run gen:api`, then `git diff --exit-code src/api/schema.d.ts`). The frontend
  uses the generated types only.
- The error envelope is `{code, message, details, correlation_id, retryable}` (D-07). Error
  `code` strings are part of the contract.

## Within `/api/v1`: additive changes only

Allowed without a new version:

- a new path, or a new method on an existing path;
- a new optional query parameter or optional request field;
- a new field in a response body or in the error envelope's `details`;
- a new response header.

Anything else counts as **breaking** unless a decision entry says otherwise. That includes:

- removing or renaming a path, field, parameter or error `code`;
- changing a field's type or format, or making an optional input required;
- narrowing the values a request accepts, or changing what a status code means;
- changing who may call a route (that is also a roles-and-permissions change, which goes to
  the user).

The drift check proves that the committed contract matches the code. It does not decide
whether a change is additive: the `backend/openapi.json` diff in the pull request is
reviewed against the lists above.

## Deprecation

A route or field that is to go away is first deprecated, in this order:

1. An entry in `docs/DECISIONS.md` names it, the replacement and the sunset date.
2. The OpenAPI operation or field is marked `deprecated: true`.
3. Responses carry a `Deprecation` header and a `Sunset` header with that date.
4. It is removed only in a new version path, never inside `/api/v1`.

## Breaking changes

A breaking change gets a new version path (for example `/api/v2/...`). It is added next to
`/api/v1`, which keeps working until its sunset (D-01: add alongside, never replace).

## Operational surfaces

- **Health probes.** `GET /api/v1/health/live` and `GET /api/v1/health/ready` are in the
  contract like any other route, and orchestrators depend on their status codes. The four
  readiness check names (`database`, `migrations`, `artifact_store`, `workers`) are treated
  as stable.
- **Metrics.** The internal listener's series names and labels are not in the OpenAPI
  contract. Dashboards and alerts depend on them, so a rename or a removed label is
  announced like a deprecation.
- **Access log fields** (`correlation_id`, `method`, `route`, `status`, `duration_ms`) are
  treated the same way as metric names.
