import type { Schemas } from '@/api/client'
import { explorerHref, scenarioHref } from '@/lib/scenario-links'

/*
 * FE-35.1: the run and scenario helpers that both the Calculations and the Advanced workspaces
 * use. They live here, below both, so components/calculations never imports
 * components/advanced (a vitest import-graph check guards this). This module imports only the
 * generated types and the explorer's base links (scenario-links.ts, a leaf). Nothing here
 * decides that a run succeeded: `hasResults` reads the status the server sent (D-31 §3), and an
 * exploration is never shown as approved (D-47 §3).
 *
 * The home modules re-export these for their own workspace's files: calculations.ts
 * (`RESULT_STATUSES`, `hasResults`, `runHref`), scenario-runs.ts (`approvalVariant`,
 * `memberLabel`) and scenarios.ts (`explorerHref`, `scenarioHref`, from scenario-links.ts). A
 * file in another workspace imports them from here.
 */

export type RunStatus = Schemas['CalculationRunStatus']
export type ApprovalState = Schemas['ApprovalState']
export type RunScenarioRef = Schemas['RunScenarioRef']
type ScenarioRunMember = Schemas['ScenarioRunMember']

// --- run status ---------------------------------------------------------------------------------

/** The statuses whose outputs are results (the server's word, D-31 §3). */
export const RESULT_STATUSES: readonly RunStatus[] = ['SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS']
export const hasResults = (status: RunStatus): boolean => RESULT_STATUSES.includes(status)

// --- links --------------------------------------------------------------------------------------

export const runHref = (runId: string): string => `/calculations/runs/${encodeURIComponent(runId)}`

/** The explorer and a scenario (the stressed-run badge links to its scenario). */
export { explorerHref, scenarioHref }

// --- stressed-run labels ------------------------------------------------------------------------

/** The badge of a stressed run: an exploration is never shown as approved (D-47 §3). */
export const approvalVariant = (state: ApprovalState): 'warning' | 'success' => (state === 'APPROVED' ? 'success' : 'warning')

/** A member run in words: "Shock 1 alone", or "Combined (shocks 1 + 2)". */
export function memberLabel(m: Pick<ScenarioRunMember, 'combined' | 'shock_subset'>): string {
  const subset = [...m.shock_subset].sort((a, b) => a - b)
  if (m.combined) return subset.length === 1 ? `Shock ${subset[0]}` : `Combined (shocks ${subset.join(' + ')})`
  return subset.length === 1 ? `Shock ${subset[0]} alone` : `Shocks ${subset.join(' + ')}`
}
