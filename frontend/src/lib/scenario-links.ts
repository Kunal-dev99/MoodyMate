/*
 * FE-35.1: the Agentic Stress-Test Explorer's base links, below both workspaces. '@/lib/runs'
 * re-exports them for the Calculations workspace (a stressed run links to its scenario); the
 * explorer's own files get them from here through scenarios.ts. FE-24.2's code-hygiene vitest
 * keeps any run path, the text "/runs" included, out of the explorer's Phase 24 files, so those
 * files never name the runs helpers module itself. This module imports nothing.
 */

export const explorerHref = '/advanced/stress-explorer'
export const scenarioHref = (scenarioId: string) => `${explorerHref}/${scenarioId}`
