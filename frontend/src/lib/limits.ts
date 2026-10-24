import { api, call, type Schemas } from '@/api/client'

/*
 * FE-35.3: the rule, scenario and upload bounds, read from GET config/limits (BE-35.3). The server
 * serves the very objects its validators enforce, so the editors' pre-checks never mirror a
 * literal: lowering IMPORT_MAX_BYTES on the server lowers the wizard's limit too. Every check that
 * reads them is cosmetic and saves a pointless request only; the server decides (D-04).
 */

export type ConfigLimits = Schemas['ConfigLimits']
export type RuleLimits = Schemas['RuleLimits']
export type ScenarioLimits = Schemas['ScenarioLimits']
export type UploadLimits = Schemas['UploadLimits']

let served: Promise<ConfigLimits> | null = null

/**
 * GET config/limits (`authenticated`), read once per page life and shared by every editor. A
 * failed read is forgotten, so the next caller (an editor's "Try again") asks again.
 */
export function fetchLimits(): Promise<ConfigLimits> {
  if (served) return served
  const request = call(() => api.GET('/api/v1/config/limits'))
  served = request
  request.catch(() => {
    if (served === request) served = null
  })
  return request
}

/** Drops the read value (tests; a sign-out keeps it: the limits are the deployment's, not a user's). */
export function forgetLimits(): void {
  served = null
}
