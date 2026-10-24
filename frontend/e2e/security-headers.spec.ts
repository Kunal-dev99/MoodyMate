import { expect, test } from '@playwright/test'

/**
 * FE-28.5: the production build serves the Next.js security headers. The CSP is Report-Only
 * (enforcing it in production is a client decision), so this asserts the Report-Only header
 * and that no enforcing CSP is sent.
 */
test('the login page is served with the security headers (FE-28.5)', async ({ request }) => {
  const res = await request.get('/login')
  expect(res.status()).toBe(200)
  const h = res.headers()
  expect(h['x-frame-options']).toBe('DENY')
  expect(h['x-content-type-options']).toBe('nosniff')
  expect(h['referrer-policy']).toBe('no-referrer')
  expect(h['content-security-policy']).toBeUndefined()
  const csp = h['content-security-policy-report-only']
  expect(csp).toBeTruthy()
  expect(csp).toContain("default-src 'self'")
  expect(csp).toContain("frame-ancestors 'none'")
  // connect-src carries the API origin the build inlined (NEXT_PUBLIC_API_BASE_URL).
  const apiOrigin = new URL(process.env.NEXT_PUBLIC_API_BASE_URL ?? `http://localhost:${process.env.E2E_API_PORT ?? '8000'}`).origin
  expect(csp).toContain(`connect-src 'self' ${apiOrigin}`)
  expect(csp).not.toContain('unsafe-eval')
  // D-54 section 3: a build with OIDC_ISSUER adds the issuer to connect-src and frame-src;
  // the dev-auth e2e build sets none, so no frame-src is emitted.
  const issuer = process.env.OIDC_ISSUER?.trim()
  if (issuer) {
    const issuerOrigin = new URL(issuer).origin
    const connectSrc = csp.split(';').map((d) => d.trim()).find((d) => d.startsWith('connect-src ')) ?? ''
    expect(connectSrc.split(' ')).toContain(issuerOrigin)
    expect(csp).toContain(`frame-src 'self' ${issuerOrigin}`)
  } else {
    expect(csp).not.toContain('frame-src')
  }
})
