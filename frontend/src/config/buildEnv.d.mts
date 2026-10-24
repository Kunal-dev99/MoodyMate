// Types for buildEnv.mjs (a build-config helper, not an API payload).
export declare const PRODUCTION_BUILD_PHASE: 'phase-production-build'
export declare const DEV_API_BASE_URL: string
export declare function resolveApiBaseUrl(input: { phase: string; env: Record<string, string | undefined> }): string
export declare function securityHeaders(input: {
  apiBaseUrl: string
  oidcIssuer?: string
  dev?: boolean
  env?: Record<string, string | undefined>
}): { key: string; value: string }[]
