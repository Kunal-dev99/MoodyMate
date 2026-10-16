/**
 * Frontend feature switches (D-02). Each one, turned off, restores the previous UI exactly.
 * NEXT_PUBLIC_* values are fixed at build time.
 *
 * NEXT_PUBLIC_IMPORTS_UI=off (FE-8.1/8.2): the Imports tab shows the Phase 7 "planned"
 * empty state again, "New import" is hidden and the import routes show that same state.
 * The backend's own switch is INGESTION_ENABLED, which refuses new uploads (409).
 */
export const IMPORTS_UI_ENABLED = process.env.NEXT_PUBLIC_IMPORTS_UI !== 'off'
