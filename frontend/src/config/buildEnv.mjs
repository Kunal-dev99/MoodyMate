/**
 * Build-time environment checks and response headers, used by next.config.mjs (FE-28.3, FE-28.5).
 *
 * Plain ESM so next.config.mjs can import it without a TypeScript step; types live in
 * buildEnv.d.mts. Vitest covers both functions (src/config/__tests__/buildEnv.test.ts).
 */

/** Next's phase name for `next build` (next/constants PHASE_PRODUCTION_BUILD). */
export const PRODUCTION_BUILD_PHASE = 'phase-production-build'

/** The development fallback; production builds must name their API explicitly. */
export const DEV_API_BASE_URL = 'http://localhost:8000'

/**
 * FE-28.3 production-build guard. NEXT_PUBLIC_API_BASE_URL is inlined into the client bundle
 * at build time, so a production build without it would silently point every browser at
 * http://localhost:8000. `next build` runs with NODE_ENV=production; in its phase an unset,
 * blank or non-http(s) value fails the build with a clear message (keyed on the phase, so a
 * non-standard NODE_ENV cannot skip it). `next dev` and `next start` keep the fallback
 * (`next start` serves what the build inlined).
 *
 * @param {{ phase: string, env: Record<string, string | undefined> }} input
 * @returns {string} the API base URL the build will use
 */
export function resolveApiBaseUrl({ phase, env }) {
  const raw = env.NEXT_PUBLIC_API_BASE_URL?.trim() ?? ''
  if (phase !== PRODUCTION_BUILD_PHASE) return raw || DEV_API_BASE_URL
  if (!raw) {
    throw new Error(
      'NEXT_PUBLIC_API_BASE_URL is not set. A production build (NODE_ENV=production) inlines the ' +
        'API base URL into the client bundle and would otherwise fall back to ' +
        `${DEV_API_BASE_URL}. Set it to the backend origin, e.g. ` +
        'NEXT_PUBLIC_API_BASE_URL=https://api.example.test npm run build',
    )
  }
  let url
  try {
    url = new URL(raw)
  } catch {
    throw new Error(`NEXT_PUBLIC_API_BASE_URL is not a valid URL: "${raw}". Use an absolute http(s) URL.`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`NEXT_PUBLIC_API_BASE_URL must be an http(s) URL, got "${raw}".`)
  }
  return raw
}

/**
 * FE-28.5 response headers for every route. The CSP is **Report-Only**: browsers report
 * violations to the console but block nothing. Enforcing it in production is a client decision.
 *
 * - script-src keeps 'unsafe-inline' because the App Router streams inline bootstrap scripts
 *   and the layout's pre-hydration theme script is inline; a nonce-based policy needs
 *   middleware and is not part of this change.
 * - style-src keeps 'unsafe-inline' for Radix/Next inline styles.
 * - Development adds 'unsafe-eval' (Next dev tooling uses eval) so dev consoles stay quiet.
 *
 * - OIDC (D-54 section 3): oidc-client-ts fetches the issuer's discovery, token and userinfo
 *   endpoints cross-origin and, with automaticSilentRenew (its default) and no refresh token,
 *   renews in a hidden iframe on the issuer. When `oidcIssuer` is set, its origin is added to
 *   connect-src and a frame-src 'self' <issuer> is emitted; unset leaves the policy unchanged.
 *   The browser learns the issuer at runtime from GET /api/v1/auth/config, but headers() are
 *   fixed at build time, so the build reads OIDC_ISSUER (the backend's setting name; set the
 *   same value for both). Endpoints the discovery document puts on another origin are not
 *   covered.
 *
 * Switch (D-02): NEXT_SECURITY_HEADERS=off at build time returns no headers, restoring the
 * pre-Phase-28 responses exactly. It is server-side only, never exposed to the browser.
 *
 * @param {{ apiBaseUrl: string, oidcIssuer?: string, dev?: boolean, env?: Record<string, string | undefined> }} input
 * @returns {{ key: string, value: string }[]}
 */
export function securityHeaders({ apiBaseUrl, oidcIssuer, dev = false, env = {} }) {
  if (env.NEXT_SECURITY_HEADERS === 'off') return []
  // Production builds have already validated the URL; elsewhere a malformed value only drops
  // that origin from the policy (Report-Only: reported, never blocked) instead of crashing.
  const apiOrigin = httpOrigin(apiBaseUrl)
  const issuerOrigin = httpOrigin(oidcIssuer)
  const connect = ["'self'", apiOrigin, issuerOrigin].filter((o, i, all) => o && all.indexOf(o) === i)
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ''}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connect.join(' ')}`,
    ...(issuerOrigin ? [`frame-src 'self' ${issuerOrigin}`] : []),
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ')
  return [
    { key: 'Content-Security-Policy-Report-Only', value: csp },
    { key: 'X-Frame-Options', value: 'DENY' },
    { key: 'X-Content-Type-Options', value: 'nosniff' },
    { key: 'Referrer-Policy', value: 'no-referrer' },
  ]
}

/**
 * The origin of an absolute http(s) URL, or '' when the value is unset or not one.
 * @param {string | undefined} value
 * @returns {string}
 */
function httpOrigin(value) {
  const raw = value?.trim()
  if (!raw) return ''
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : ''
  } catch {
    return ''
  }
}
