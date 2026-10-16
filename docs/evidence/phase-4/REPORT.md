# Phase 4 — engineer report (BE-4.1…4.3, FE-4.1…4.2)

Built in worktree `../MoodyMate-wt/ws1-phase4` (branch `feat/ws1-phase4`, from `master` a40b737) against DB copy `moodymate_ws1_p4`; tests ran on `moodymate_test` (D-03).

Evidence:
- `checks.txt`: 172 backend tests; 40 frontend tests; lint, types and build; migration round trip; seed contents.
- `browser-phase4.json` and `browser-phase4.mjs`: the real stack in dev mode, as `risk_analyst`.
- Screenshots 01–02.

| Task | Done-when (verbatim) | Evidence |
|---|---|---|
| BE-4.1 | the round trip passes; a test proves an UPDATE to an ACTIVE version's contract is rejected (DB trigger or service guard plus test). | Migration `0006` creates `schema_definition` (tenant-scoped), `schema_version` (version number, status per D-06, effective date, compatibility vs previous, content SHA-256), `schema_field` (type, required, unit, precision/scale, classification, extension eligibility) and `schema_dependency`. Two triggers make a version immutable once it leaves DRAFT: its fields, hash, number and schema cannot change, it cannot be deleted, and it cannot go back to DRAFT. `test_active_version_contract_cannot_change` covers 8 statements (field update, insert and delete; hash, version and DRAFT changes; version delete), each rejected with the row unchanged. `test_status_can_still_move_forward` and `test_draft_version_fields_are_editable` show what stays allowed. The round trip is in checks.txt and `test_migrations`. |
| BE-4.2 | `GET /schemas` lists three schemas, each with status and a `provisional: true` marker. | Migration `0007` seeds `instrument` (v1 ACTIVE, v2 DRAFT), `position` and `cashflow_event` (v1 ACTIVE), plus 6 illustrative dependents. Descriptions say "ILLUSTRATIVE seed from Data Model §4; not client-approved". Every row is tagged `0007_schema_seed`, and the downgrade removes only tagged rows. `test_list_schemas`: three schemas, each `provisional: true`, status ACTIVE, effective v1, owner, dependents. `test_seed_is_tagged_and_provisional`. `test_seeded_content_hashes_match_the_service`: the frozen hash copy in the migration equals the service's. |
| BE-4.3 | diff tests cover an added optional field (additive), a widened scale (compatible), and a removed field, a newly required field and a type change (breaking). | `GET /api/v1/schemas`, `GET /schemas/{id}/versions` and `GET /schemas/{id}/diff?from=&to=` all require `schema:read` and are scoped to the caller's tenant; an unknown or foreign schema returns 404. `test_change_classification` has 13 rule cases: optional add, required add, remove, newly required, now optional, type, unit, widened scale, widened precision, narrowed scale, fewer integer digits, classification and extension eligibility. `test_seeded_diff_fixture` checks the seeded v1→v2 fixture: exactly one ADDITIVE, one COMPATIBLE and three BREAKING changes, with overall BREAKING. `test_diff_rejects_unknown_versions` and `test_unknown_and_foreign_schemas_are_not_found` cover the error cases. |
| FE-4.1 | the catalogue shows the three seeded schemas with platform status pills (not the bundle's draft/published set). | browser-phase4.json: `catalogue_codes` lists the 3 schemas; every pill's `data-status` is in the platform set (`status_values_all_platform: true`); 3 Provisional badges; the "planned" placeholder is gone; the nav shows Source & Schemas (screenshot 01). `StatusPill` maps all 7 platform statuses, and a unit test asserts the full set. |
| FE-4.2 | comparing two seeded versions renders every change from the BE-4.3 fixture with the correct badge. | browser-phase4.json: the detail page compares v1→v2 by default; `diff_changes` lists 5 changes, each with its badge; `ui_matches_api_fixture: true` (field and badge equal the API response); overall Breaking; the side-by-side view shows 10 rows (9 fields plus 1 removed); comparing a version with itself shows "Identical" (screenshot 02). |

## Design notes

- **Immutability is enforced in the database.** A seed downgrade may remove its own tagged rows only by setting `moodymate.schema_seed_removal` (`SET LOCAL`) in its own transaction.
  - A NULL-handling bug in that guard let seeded rows be deleted. `test_active_version_contract_cannot_change` found it during the build, and it was fixed with `coalesce` before this report.
  - The same database-owner caveat as Phase 3 applies: the owner can disable the triggers.
- **Content hash:** SHA-256 of the canonical JSON of each field's contract attributes, in order. Descriptions are excluded because they are documentation, but they are still frozen with the version.
- **Diff rules:**
  - Adding an optional field is ADDITIVE.
  - Adding a required field, removing a field, a type change, a unit change, a newly required field, or a narrower precision or scale is BREAKING.
  - A wider precision or scale, required becoming optional, a classification change or an extension-eligibility change is COMPATIBLE.
  - Overall is the most severe change, or null when the versions are identical.
  - The rules are provisional; the client's domain owners should confirm them.
- **Reads are not audited.** Every role holds `schema:read` and the registry holds no classified values, so these are not privileged reads. Phase 5 writes (proposals and approvals) will be audited.
- **No masking needed.** Field metadata carries a classification *code*, not classified values, so these responses have no classified fields to mask.
- **0006 downgrade** refuses while `schema_definition` has rows. 0007's downgrade removes the seed first; anything left is user data. The offline `--sql` script carries the same guard.

## Not done / follow-ups

- Phase 5 adds proposals, impact analysis, maker-checker approval and the status transitions, so the registry is read-only until then.
- Dependents are seeded illustrations until WS2–WS4 register real mappings, rules, modules and reports.
