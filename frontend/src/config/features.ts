/**
 * Frontend feature switches (D-02). Each one, turned off, restores the previous UI exactly.
 * NEXT_PUBLIC_* values are fixed at build time.
 *
 * NEXT_PUBLIC_IMPORTS_UI=off (FE-8.1/8.2): the Imports tab shows the Phase 7 "planned"
 * empty state again, "New import" is hidden and the import routes show that same state.
 * The backend's own switch is INGESTION_ENABLED, which refuses new uploads (409).
 */
export const IMPORTS_UI_ENABLED = process.env.NEXT_PUBLIC_IMPORTS_UI !== 'off'

/**
 * NEXT_PUBLIC_IMPORT_PROFILE_UI=off (FE-9.1): the import page shows its Phase 8 layout
 * again (no Status/Profile tabs). The profile endpoint stays read-only either way.
 */
export const IMPORT_PROFILE_UI_ENABLED = process.env.NEXT_PUBLIC_IMPORT_PROFILE_UI !== 'off'

/**
 * NEXT_PUBLIC_MAPPING_UI=off (FE-10.1/10.2): the Mappings tab, the mapping routes and the
 * "Map this import" / source "Mappings" links disappear, restoring the Phase 9 UI exactly.
 * The backend's own switch is MAPPING_CHANGE_ENABLED, which refuses mapping writes (409).
 */
export const MAPPING_UI_ENABLED = process.env.NEXT_PUBLIC_MAPPING_UI !== 'off'

/**
 * NEXT_PUBLIC_VALIDATION_UI=off (FE-11.1/11.2): the import page loses its Validation and
 * Quarantine tabs and the wizard ignores `?retry_of=`, restoring the Phase 10 UI exactly.
 * The backend's own switch is INGESTION_ENABLED, which refuses validation and dispositions.
 */
export const VALIDATION_UI_ENABLED = process.env.NEXT_PUBLIC_VALIDATION_UI !== 'off'

/**
 * NEXT_PUBLIC_RECONCILIATION_UI=off (FE-12.1): the import page loses its Reconciliation tab,
 * restoring the Phase 11 UI exactly. Reconciliation itself is a control with no off switch:
 * validation still writes it and GET /imports/{id}/reconciliation stays readable.
 */
export const RECONCILIATION_UI_ENABLED = process.env.NEXT_PUBLIC_RECONCILIATION_UI !== 'off'

/**
 * NEXT_PUBLIC_DRIFT_UI=off (FE-12.2): the import page loses its Drift tab and the review
 * dialog, restoring the Phase 11 UI exactly. The backend's own switches are
 * DRIFT_DETECTION_ENABLED (no new events) and INGESTION_ENABLED (reviews refused, 409).
 */
export const DRIFT_UI_ENABLED = process.env.NEXT_PUBLIC_DRIFT_UI !== 'off'

/**
 * NEXT_PUBLIC_DOMAIN_UI=off (FE-14.1/14.2): the Financial Domain nav entry and /domain go back
 * to the "planned" placeholder, and the import page loses its "Project to domain" section,
 * restoring the Phase 13 UI exactly. The backend's own switch is DOMAIN_PROJECTION_ENABLED,
 * which refuses new projections (409); projected rows stay readable either way.
 */
export const DOMAIN_UI_ENABLED = process.env.NEXT_PUBLIC_DOMAIN_UI !== 'off'

/**
 * NEXT_PUBLIC_CALCULATIONS_UI=off (FE-15.1/15.2): the Calculations nav entry and every
 * /calculations route go back to the "planned" placeholder, restoring the Phase 14 UI exactly.
 * The backend's own switch is CALCULATIONS_ENABLED, which refuses new snapshots and runs (409
 * `calculations_disabled`); existing runs stay readable either way.
 */
export const CALCULATIONS_UI_ENABLED = process.env.NEXT_PUBLIC_CALCULATIONS_UI !== 'off'

/**
 * NEXT_PUBLIC_RULES_UI=off (FE-16.1): the Financial Domain workspace loses its registry tabs
 * (Rules, Modules, Assumption sets) and every /domain registry route is a 404 again, restoring
 * the Phase 15 UI exactly. The backend's own switch is RULE_CHANGE_ENABLED, which freezes the
 * rules at their ACTIVE versions (writes 409 `rule_change_disabled`); reads work either way.
 */
export const RULES_UI_ENABLED = process.env.NEXT_PUBLIC_RULES_UI !== 'off'

/**
 * NEXT_PUBLIC_LIQUIDITY_UI=off (FE-17.1): the run detail of an `lcr_hqla` run shows the Phase 15
 * layout again (the maturity-ladder section, which says the module has no ladder) instead of
 * the liquidity view, restoring the Phase 15 UI exactly. The backend's own reversal is retiring
 * the module version (hidden from "New run" by status); existing runs stay readable either way.
 */
export const LIQUIDITY_UI_ENABLED = process.env.NEXT_PUBLIC_LIQUIDITY_UI !== 'off'

/**
 * NEXT_PUBLIC_CREDIT_DOMAIN_UI=off (FE-19.1): the Financial Domain loses its Counterparties tab
 * and every /domain/counterparties route is a 404 again, restoring the Phase 18 /domain exactly.
 * The backend's own switch is CREDIT_PROJECTION_ENABLED, which refuses new counterparty and
 * credit-exposure projections (409 `projection_unsupported`); projected rows stay readable.
 * Off also hides the import page's 'Project to domain' action for counterparty and
 * credit_exposure imports, as in Phase 18.
 */
export const CREDIT_DOMAIN_UI_ENABLED = process.env.NEXT_PUBLIC_CREDIT_DOMAIN_UI !== 'off'
