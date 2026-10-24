import { describe, expect, it } from 'vitest'
import nextConfig from '../../../next.config.mjs'
import { DEV_API_BASE_URL, PRODUCTION_BUILD_PHASE, resolveApiBaseUrl, securityHeaders } from '../buildEnv.mjs'

const BUILD = PRODUCTION_BUILD_PHASE
const DEV = 'phase-development-server'
const START = 'phase-production-server'

describe('production-build guard (FE-28.3)', () => {
  it('fails a production build when NEXT_PUBLIC_API_BASE_URL is unset or blank', () => {
    for (const value of [undefined, '', '   ']) {
      expect(() => resolveApiBaseUrl({ phase: BUILD, env: { NODE_ENV: 'production', NEXT_PUBLIC_API_BASE_URL: value } })).toThrow(
        /NEXT_PUBLIC_API_BASE_URL is not set/,
      )
    }
  })
  it('keys on the build phase, so a non-standard NODE_ENV cannot skip the guard', () => {
    expect(() => resolveApiBaseUrl({ phase: BUILD, env: { NODE_ENV: 'development' } })).toThrow(/not set/)
  })
  it('rejects a value that is not an absolute http(s) URL', () => {
    expect(() => resolveApiBaseUrl({ phase: BUILD, env: { NEXT_PUBLIC_API_BASE_URL: 'api.example.test' } })).toThrow(/not a valid URL/)
    expect(() => resolveApiBaseUrl({ phase: BUILD, env: { NEXT_PUBLIC_API_BASE_URL: 'ftp://api.example.test' } })).toThrow(/http\(s\)/)
  })
  it('passes a production build with the URL set', () => {
    expect(resolveApiBaseUrl({ phase: BUILD, env: { NODE_ENV: 'production', NEXT_PUBLIC_API_BASE_URL: 'https://api.example.test' } })).toBe(
      'https://api.example.test',
    )
  })
  it('keeps http://localhost:8000 in dev and under next start', () => {
    expect(DEV_API_BASE_URL).toBe('http://localhost:8000')
    expect(resolveApiBaseUrl({ phase: DEV, env: { NODE_ENV: 'development' } })).toBe('http://localhost:8000')
    expect(resolveApiBaseUrl({ phase: START, env: { NODE_ENV: 'production' } })).toBe('http://localhost:8000')
  })
  it('is wired into next.config.mjs at build-ID time, so loading the config (next lint) never throws', async () => {
    const saved = process.env.NEXT_PUBLIC_API_BASE_URL
    try {
      delete process.env.NEXT_PUBLIC_API_BASE_URL
      expect(() => nextConfig(BUILD)).not.toThrow()
      await expect(nextConfig(BUILD).generateBuildId?.()).rejects.toThrow(/NEXT_PUBLIC_API_BASE_URL is not set/)
      await expect(nextConfig(DEV).generateBuildId?.()).resolves.toBeNull()
      process.env.NEXT_PUBLIC_API_BASE_URL = 'https://api.example.test'
      await expect(nextConfig(BUILD).generateBuildId?.()).resolves.toBeNull()
    } finally {
      if (saved === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL
      else process.env.NEXT_PUBLIC_API_BASE_URL = saved
    }
  })
})

describe('security headers (FE-28.5)', () => {
  const byKey = (h: { key: string; value: string }[]) => Object.fromEntries(h.map((x) => [x.key, x.value]))

  it('sends a Report-Only CSP (never enforcing) with the API origin in connect-src', () => {
    const h = byKey(securityHeaders({ apiBaseUrl: 'https://api.example.test/base/' }))
    expect(h['Content-Security-Policy']).toBeUndefined()
    const csp = h['Content-Security-Policy-Report-Only']
    expect(csp).toContain("default-src 'self'")
    expect(csp).toContain("connect-src 'self' https://api.example.test;")
    expect(csp).toContain("frame-ancestors 'none'")
    expect(csp).toContain("object-src 'none'")
    expect(csp).not.toContain('unsafe-eval')
    expect(csp).not.toContain('frame-src')
    // D-54 section 3: with OIDC, the issuer origin joins connect-src (discovery, token,
    // userinfo) and frame-src (silent renew iframe); a malformed issuer is dropped, not thrown.
    const oidc = byKey(securityHeaders({ apiBaseUrl: 'https://api.example.test', oidcIssuer: 'https://idp.example.test/realms/mm/' }))[
      'Content-Security-Policy-Report-Only'
    ]
    expect(oidc).toContain("connect-src 'self' https://api.example.test https://idp.example.test;")
    expect(oidc).toContain("frame-src 'self' https://idp.example.test;")
    expect(oidc).toContain("frame-ancestors 'none'")
    const bad = byKey(securityHeaders({ apiBaseUrl: 'https://api.example.test', oidcIssuer: 'not a url' }))['Content-Security-Policy-Report-Only']
    expect(bad).toContain("connect-src 'self' https://api.example.test;")
    expect(bad).not.toContain('frame-src')
  })
  it('sends X-Frame-Options, nosniff and Referrer-Policy', () => {
    const h = byKey(securityHeaders({ apiBaseUrl: 'http://localhost:8000' }))
    expect(h['X-Frame-Options']).toBe('DENY')
    expect(h['X-Content-Type-Options']).toBe('nosniff')
    expect(h['Referrer-Policy']).toBe('no-referrer')
  })
  it("adds 'unsafe-eval' only for the dev server", () => {
    const h = byKey(securityHeaders({ apiBaseUrl: 'http://localhost:8000', dev: true }))
    expect(h['Content-Security-Policy-Report-Only']).toContain("'unsafe-eval'")
  })
  it('drops only the API origin, without throwing, when the URL is malformed outside a build', () => {
    const h = byKey(securityHeaders({ apiBaseUrl: 'not a url' }))
    expect(h['Content-Security-Policy-Report-Only']).toContain("connect-src 'self';")
  })
  it('NEXT_SECURITY_HEADERS=off restores the previous responses exactly (D-02)', async () => {
    expect(securityHeaders({ apiBaseUrl: 'http://localhost:8000', env: { NEXT_SECURITY_HEADERS: 'off' } })).toEqual([])
    const saved = process.env.NEXT_SECURITY_HEADERS
    const savedIssuer = process.env.OIDC_ISSUER
    try {
      process.env.NEXT_SECURITY_HEADERS = 'off'
      expect(await nextConfig(DEV).headers?.()).toEqual([])
      delete process.env.NEXT_SECURITY_HEADERS
      const rules = (await nextConfig(DEV).headers?.()) ?? []
      expect(rules).toHaveLength(1)
      expect(rules[0].source).toBe('/:path*')
      // next.config.mjs passes the build-time OIDC_ISSUER through (D-54 section 3).
      process.env.OIDC_ISSUER = 'https://idp.example.test/realms/mm'
      const oidcRules = (await nextConfig(DEV).headers?.()) ?? []
      expect(oidcRules[0].headers.find((h) => h.key === 'Content-Security-Policy-Report-Only')?.value).toContain(
        "frame-src 'self' https://idp.example.test",
      )
    } finally {
      if (saved === undefined) delete process.env.NEXT_SECURITY_HEADERS
      else process.env.NEXT_SECURITY_HEADERS = saved
      if (savedIssuer === undefined) delete process.env.OIDC_ISSUER
      else process.env.OIDC_ISSUER = savedIssuer
    }
  })
})
