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

/**
 * NEXT_PUBLIC_RATINGS_UI=off (FE-20.1/20.2): the counterparty detail loses its Ratings panel and
 * "Propose rating" dialog, the Financial Domain loses its Ratings tab and /domain/ratings is a 404
 * again, restoring the Phase 19 UI exactly. Only meaningful while NEXT_PUBLIC_CREDIT_DOMAIN_UI is
 * on (ratings belong to counterparties). The backend's own switch is RATING_CHANGE_ENABLED, which
 * freezes ratings (writes 409 `rating_change_disabled`, audited); reads and runs keep working.
 */
export const RATINGS_UI_ENABLED = process.env.NEXT_PUBLIC_RATINGS_UI !== 'off'

/**
 * NEXT_PUBLIC_STRESS_EXPLORER_UI=off (FE-24.1/24.2): the sidebar loses its "Advanced" section
 * (the Agentic Stress-Test Explorer) and every /advanced route is a 404 again, restoring the
 * Phase 23 sidebar exactly. The backend's own switch is SCENARIOS_ENABLED, which refuses
 * scenario writes (409 `scenarios_disabled`, audited); reads keep working either way.
 */
export const STRESS_EXPLORER_UI_ENABLED = process.env.NEXT_PUBLIC_STRESS_EXPLORER_UI !== 'off'

/**
 * NEXT_PUBLIC_DASHBOARD_TILES=off (demo polish P-13): the Overview page loses its count tiles and
 * shows its previous description again, restoring the earlier dashboard exactly. The tiles only
 * read existing list endpoints (`limit=1`, the page's `total`); nothing is written.
 */
export const DASHBOARD_TILES_ENABLED = process.env.NEXT_PUBLIC_DASHBOARD_TILES !== 'off'

/**
 * NEXT_PUBLIC_STRESS_COMPARE_UI=off (FE-25.1/25.2): the explorer loses the "Run against baseline"
 * panel on runnable versions (FE-25.1) and the baseline-versus-stress comparison view (FE-25.2),
 * and the sidebar keeps its Phase 24 description, restoring the Phase 24 explorer exactly. Only
 * meaningful while NEXT_PUBLIC_STRESS_EXPLORER_UI is on (the explorer itself), so off there means
 * off here. The backend's own switches are SCENARIOS_ENABLED and CALCULATIONS_ENABLED, which
 * refuse new stressed runs (409 `scenarios_disabled` / `calculations_disabled`, audited); existing
 * stressed runs stay readable, and the calculation run list and run detail keep labelling them
 * ("Exploration, not approved" / "Approved scenario") whatever this switch says.
 */
export const STRESS_COMPARE_UI_ENABLED = STRESS_EXPLORER_UI_ENABLED && process.env.NEXT_PUBLIC_STRESS_COMPARE_UI !== 'off'

/**
 * NEXT_PUBLIC_DOMAIN_LINKS_UI=off (FE-31.1): the Financial Domain loses its Legal entities tab and
 * every /domain/legal-entities route is a 404 again; the instrument detail shows its Phase 30
 * maturity text ("Not captured in instrument schema v1") whatever the pinned schema types, and
 * the cash-flow and exposure details lose their "Linked identity" column and field, restoring the
 * Phase 30 UI and nav exactly. Only meaningful while NEXT_PUBLIC_DOMAIN_UI is on (the workspace
 * itself), so off there means off here. The backend's own switches are
 * INSTRUMENT_PROJECTOR_V2_ENABLED (typed imports refused, 409 `projection_unsupported`) and
 * IDENTITY_LINKS_ENABLED (no new links; the reconcile answers 409 `identity_links_disabled`,
 * audited). Links are read-only here: the UI never writes one (D-58).
 */
export const DOMAIN_LINKS_UI_ENABLED = DOMAIN_UI_ENABLED && process.env.NEXT_PUBLIC_DOMAIN_LINKS_UI !== 'off'
