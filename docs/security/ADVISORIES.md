# Dependency advisory register (D-55 §4, FE-28.2)

Source: `npm audit --omit=dev --json` on 2026-10-24, saved as
`docs/evidence/phase-28/npm-audit-omit-dev.json`. Totals: critical 0, high 0,
moderate 4, low 0.

**Gate.** CI fails on any high or critical advisory not listed in `frontend/audit-allowlist.json`
(currently empty) and on any expired entry (`backend/scripts/check_audit_allowlist.py`). Moderate
and low findings do not gate; they are recorded here and reviewed by the date shown.

**Remediated in Phase 28.**
- `next` 14.2.35 → 15.5.28: clears the critical RCE advisories (GHSA-2464-fhr4-w9xw,
  GHSA-2xp9-vwfh-vxw4, GHSA-p293-qw3h-jr36) and the high DoS/SSRF advisories ≤15.5.27.
- `postcss` inside `next` pinned to 8.5.30 through `overrides` (was 8.4.31): clears
  GHSA-6g55-p6wh-862q and GHSA-r28c-9q8g-f849 (high) without accepting them.
- Python: fastapi 0.162 / starlette 1.9 (PYSEC-2026-161, -248, -249, -1941, -1942, -2280, -2281),
  pytest 9.1 (PYSEC-2026-1845), certifi 2026.10.23 (PYSEC-2026-4320). CI runs `pip-audit`.

**Remaining (moderate, accepted pending an owner).** Owners are a client decision (hard stop).

| Package | Severity | Advisory | Exposure | Mitigation | Owner | Review by |
|---|---|---|---|---|---|---|
| `postcss-nested` | moderate | via postcss-selector-parser | build-time (Tailwind/PostCSS toolchain; not shipped to the browser or the API) | none upstream for Tailwind 3; revisit with the Tailwind 4 migration | UNASSIGNED (client to name) | 2026-11-23 |
| `postcss-selector-parser` | moderate | GHSA-rj75-hqrm-r3gf | build-time (Tailwind/PostCSS toolchain; not shipped to the browser or the API) | none upstream for Tailwind 3; revisit with the Tailwind 4 migration | UNASSIGNED (client to name) | 2026-11-23 |
| `tailwindcss` | moderate | via postcss-nested, postcss-selector-parser | build-time (Tailwind/PostCSS toolchain; not shipped to the browser or the API) | none upstream for Tailwind 3; revisit with the Tailwind 4 migration | UNASSIGNED (client to name) | 2026-11-23 |
| `tailwindcss-animate` | moderate | via tailwindcss | build-time (Tailwind/PostCSS toolchain; not shipped to the browser or the API) | none upstream for Tailwind 3; revisit with the Tailwind 4 migration | UNASSIGNED (client to name) | 2026-11-23 |
