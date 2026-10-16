# Phase 8 — independent verification (BE-8.1…8.6, FE-8.1…8.2)

Reviewed 2026-10-16 by the solution architect in worktree `MoodyMate-wt/ws2-phase8`
(branch `feat/ws2-phase8`, base `1c38eeb`, checks at `8b9d006`). The review read the code and
tests directly and ran no commands. Pass/fail comes from `checks.txt`:

- ruff and mypy clean
- 932 passed (31 warnings), plus the ws1 matrix with 8 passed; it includes the import routes
- openapi and samples `--check` pass
- round trip 0012→0011→0012 passes
- frontend: no diff from gen:api; typecheck, lint, 90 vitest and the build pass
- Playwright 15/15

All eight Done-whens are met and there is no BLOCKER. Three MAJOR defects are to be fixed before
merge: D1 and D2 undercut the "bounded" import goal, and D3 breaks BE-8.6's rule.

## Verdict per task

| Task | Verdict | Evidence |
|---|---|---|
| BE-8.1 | Met. Follow-up D15 | 0012: append-only and TRUNCATE triggers on artifact, batch and record; `import_job_guard` (content frozen, no DELETE/TRUNCATE, transitions checked against a frozen copy of the table); same-tenant trigger; CHECKs. `test_import_tables.py` |
| BE-8.2 | Met. D6, D7 | `test_artifacts.py`: same bytes give one object, a tampered object fails the read, objects are never rewritten, startup is refused without the setting, the store may not sit inside the repo |
| BE-8.3 | Met. MAJOR D1, D2; D4, D5, D12, D14 | `test_adapters.py` names a test for every Done-when bullet, plus XML entity attacks and defusedxml |
| BE-8.4 | Met. D7, D8, D9, D13 | `test_imports.py`: one fixture per format reaches RECEIVED; 413 with nothing left behind; streaming cut-off proven at the ASGI level; idempotency replay, 409 conflict and a concurrent race; attestation 422; 403 with a DENIED event; same 404 |
| BE-8.5 | Met | 100 pairs walked in the service and in the DB; the trigger table equals the service table |
| BE-8.6 | Met. MAJOR D3 | `test_import_no_leak.py`: sentinel absent from logs (DEBUG plus raw stdout/stderr), bodies and audit details; present in `source_record` |
| FE-8.1 | Met. D10, D11 | `e2e/imports.spec.ts:61` (RECEIVED, 25 rows, SHA-256), `:137` (backend 413 toast with the correlation ID), `:195` (auditor) |
| FE-8.2 | Met | the pill-enum test against `openapi.json`, plus a `Record<ImportStatus>` compile check; `e2e/imports.spec.ts:117` |

## What held up

- **Fail closed.** The permission and the switch are checked before any body byte is read.
  Refusals are audited through `ImportService.refuse` with only the reason (D-24 §4).
- **The streaming limit is real.** Content-Length is checked before reading. The stream cut
  closes the spooled files. `max_files=1` and `max_fields=10`. The exact size check is in
  `parse_upload`.
- **Content checks.** XLSX type is detected from content (OLE, macro type and VBA refused).
  The zip guard uses the declared sizes, and `ZipExtFile` truncates output to them. openpyxl
  runs `read_only`/`data_only` with defusedxml, and formulas are never evaluated. JSON uses
  `Decimal`, refuses NaN, duplicate keys and deep nesting. The storage path comes only from
  the hash.
- **ArtifactStore.** Write-once through a hard link. Every read and re-put re-hashes. Files
  are written before the DB rows, so the DB never points at a missing file.
- **Idempotency.** Unique per (tenant, actor, key). The race is tested with 4 apps.
- **Tenancy and the same 404.** Tested, with a DB trigger as backstop.
- **Masking of control totals.** Tested.
- **D-14 attestation.** Backend and DB CHECK; the frontend fails closed.

## Defects

| ID | Severity | Where | Failure | Fix (test first) |
|---|---|---|---|---|
| D1 | MAJOR | XLSX adapter, `base.collect`, `service.py:642` | **Shared-string amplification.** One 4,000-character shared string referenced by about 500k cells in a 10 MiB stored workbook decodes to about 2 GB of `raw_values` in one transaction. That is an OOM, and if it commits the rows can never be removed. | Running total of decoded characters; refuse above a ceiling with its own code (needs a D-16 addendum). `test_xlsx_shared_string_amplification_is_refused` |
| D2 | MAJOR | openpyxl `_read_only.py`, `base.collect` | **Row-number gap.** `<row r="2000000000">` makes the parser yield about 2e9 empty rows, twice: a worker is held for about an hour by a few KB. | Refuse r above 1,048,576 as `xlsx_invalid`; stop `collect` after `max_rows` blank rows. `test_xlsx_row_number_gap_is_refused_quickly` |
| D3 | MAJOR | openpyxl `worksheet/_reader.py:222`, `core/logging.py` | **Cell values in logs.** The warning for an out-of-range date serial includes the value and goes to raw stderr. Pytest captures warnings, so the leak test cannot see it. | Process-wide `warnings.filterwarnings("ignore", module="openpyxl…")`; make openpyxl UserWarnings errors in pytest. `test_openpyxl_warnings_never_escape` |
| D4 | MINOR | openpyxl `_read_only.py:95` | A row number that does not increase is silently skipped, so rows are dropped (D-20). | Require strictly increasing row numbers, else `xlsx_invalid`. |
| D5 | MINOR | as D3 | An out-of-range date is stored as `#VALUE!` with `CELL_ERROR`, which looks like a genuine Excel error. | A distinct `DATE_OUT_OF_RANGE` flag. |
| D6 | MINOR | `artifacts.py`, `config.py` | Objects are 0444 and directories follow the umask. The `/tmp` default is shared between users and worktrees. The directory is not fsynced after the link. | Directories 0700, files 0400, fsync the directory, a per-user default. `test_objects_are_private` |
| D7 | MINOR (design) | `service.py` | A failed transaction after `put` leaves an orphan file that nothing detects. Deduplication is cross-tenant. | BE-13.3 restore check reports orphans; decide tenant prefixes before S3. |
| D8 | MINOR | `service.py`, `artifacts.py` | A `get_schema` refusal is not audited. A non-key IntegrityError and an `os.link` OSError become unaudited 500s. | Wrap them; map to `artifact_store_unavailable`. |
| D9 | MINOR | `pyproject.toml` | `max_part_size` needs Starlette 0.40 or later; the FastAPI range may admit an older Starlette. | Pin `starlette>=0.40,<0.42`. |
| D10 | MINOR (FE) | `imports/new/page.tsx` | The upload POST has no AbortController, so leaving mid-upload can still navigate or toast. | Abort on unmount. Vitest case. |
| D11 | MINOR (FE tests) | `new/page.tsx`, `features.ts` | The attempt-key logic and the `NEXT_PUBLIC_IMPORTS_UI=off` reversal are untested. | Extract a reducer and test it; add a switch-off test. |
| D12 | MINOR (tests) | `test_adapters.py` | Nothing proves the private `_get_row` hook is active. | A structural test with a spy. |
| D13 | MINOR | `router.py:283-292` | File names allow bidi/format characters (U+202E), and there is no NFC normalisation. | `SafeText` rules plus NFC. |
| D14 | MINOR | `adapters/__init__.py` | A bare JSON scalar gets the misleading `format_mismatch`. | `json_invalid_shape` when the profile is JSON. |
| D15 | MINOR | 0012 same-tenant trigger | `schema_version_id` is not checked against the contract's schema, and an INSERT may start in any status. | Add both checks (amend 0012 in place). |

**The 31 warnings.** `xlsx_kit` workbooks lack a Normal cell style, so openpyxl warns once
per parse. That is harmless noise, but it proves openpyxl warnings fire on the request path
(see D3). Fix: add the style, and make openpyxl warnings errors in pytest.

**Carry-over.** The app and migrations share one DB role. A least-privilege runtime role
belongs in pre-production hardening.

**Design notes.**
- `original_filename` is unmasked for all `import:read` holders.
- XLSX pads short rows with `""`, while CSV omits the missing keys. Phase 11 must treat `""`
  as missing. Document both in ADAPTERS.md.

## Engineer-reported points: recommendations

1. **Adapters sub-package and `core/artifacts.py`.** Accept; record as D-25 §1.
2. **A refused upload persists nothing.** Accept. D-20's FAILED applies to validation in
   Phase 11. Record as D-25 §2.
3. **`retention_state` = RETAINED only.** Accept, as D-25 §3 rather than an edit to D-23.
4. **Row semantics.** Accept: MISSING_CELLS, EXTRA_CELLS, BLANK_ROW, NULL_VALUE, and the XLSX
   marker read from the sheet name or the workbook title. Record as D-25 §4. A bare JSON scalar
   becomes `json_invalid_shape` (D14).
5. **Private openpyxl hook.** Accept, with the structural test (D12).
6. **Stream cut at the limit plus 64 KiB, then an exact check.** Accept.
7. **The fingerprint excludes the file name; a replay is not audited.** Accept. Record in
   D-25.
8. **Retry column only; `import_batch` append-only; extra columns.** Accept.
9. **The `NEXT_PUBLIC_IMPORTS_UI` switch.** Accept, with a test (D11).
10. **Loosened Phase 7 e2e assertion.** Accept.

## Deferred

- The retry flow (Phase 12).
- ADAPTERS.md (BE-13.1) and RETENTION rows (BE-13.3).
- Validation-time FAILED imports (Phase 11).
- A least-privilege DB role.
- The S3 store and tenant prefixes.
