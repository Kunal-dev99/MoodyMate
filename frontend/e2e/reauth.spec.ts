import { createHmac } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { API } from './support'

/*
 * FE-35.4 Done-when (browser half) against the real stack (backend ENV=test, AUTH_MODE=dev):
 * a dev token re-signed with a short TTL expires on a route; the next API call answers 401
 * token_expired; the app redirects ONCE to the persona sign-in, keeping the route (path and
 * query), and signing in again returns to that route. The vitest half (one redirect, no loop,
 * OIDC) is src/lib/__tests__/reauth.test.ts. Needs a frontend built with NEXT_PUBLIC_REAUTH_UI
 * on (its default).
 *
 * The short TTL: the backend's dev TTL is whole minutes (AUTH_DEV_TOKEN_TTL_MINUTES), and the
 * suite shares one backend, so this test re-issues the persona's real dev token with a 5-second
 * TTL, signed as the dev issuer with the dev secret of local/test (E2E_AUTH_DEV_SECRET, else the
 * backend's own DEV_SECRET_DEFAULT). The server then refuses it exactly as an expired session
 * (after its leeway, read from backend/app/core/tokens.py). Q17: the wait is about 35 s, so the
 * test is marked slow, the polling is bounded by the expiry plus the leeway plus a margin, and
 * the refusal's code must be token_expired (any other refusal fails at once).
 */

const SESSION_STORAGE_KEY = 'moodymate_session'
const TTL_SECONDS = 5
/** How long past `exp` + leeway the server may take to refuse the token before the test fails. */
const MARGIN_SECONDS = 15
const ROUTE = '/ingestion?tab=imports'

function devSecret(): string {
  if (process.env.E2E_AUTH_DEV_SECRET) return process.env.E2E_AUTH_DEV_SECRET
  const config = readFileSync(join(__dirname, '../../backend/app/core/config.py'), 'utf8')
  const m = /^DEV_SECRET_DEFAULT = "([^"]+)"/m.exec(config)
  if (!m) throw new Error('DEV_SECRET_DEFAULT not found in backend/app/core/config.py')
  return m[1]
}

/** The verifier's `exp` leeway in seconds (`leeway=` in tokens.py `_decode`). */
function leewaySeconds(): number {
  const tokens = readFileSync(join(__dirname, '../../backend/app/core/tokens.py'), 'utf8')
  const m = /\bleeway=(\d+)/.exec(tokens)
  if (!m) throw new Error('leeway= not found in backend/app/core/tokens.py')
  return Number(m[1])
}

const b64url = (text: string) => Buffer.from(text).toString('base64url')

/** The same claims as `token`, re-issued (HS256, the dev issuer's algorithm) to expire `ttl` seconds from now. */
function shortLived(token: string, ttl: number): { token: string; exp: number } {
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8'))
  const now = Math.floor(Date.now() / 1000)
  const exp = now + ttl
  const head = b64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const body = b64url(JSON.stringify({ ...claims, iat: now, exp }))
  const signature = createHmac('sha256', devSecret()).update(`${head}.${body}`).digest('base64url')
  return { token: `${head}.${body}.${signature}`, exp }
}

test('FE-35.4: a short-lived dev session expires, redirects once to the persona sign-in and returns to the route', async ({ page, request }) => {
  // Q17: about TTL + leeway (35 s) of waiting; slow() triples the 60 s default timeout.
  test.slow()

  const issued = await request.post(`${API}/api/v1/dev/sessions`, { data: { role: 'risk_analyst' } })
  expect(issued.ok()).toBe(true)
  const short = shortLived((await issued.json()).access_token, TTL_SECONDS)
  const me = (token: string) => request.get(`${API}/api/v1/me`, { headers: { Authorization: `Bearer ${token}` } })
  expect((await me(short.token)).status()).toBe(200)

  // Signed in with the short session, on a route with a query.
  await page.goto('/login')
  await page.evaluate(
    ([key, token]) => sessionStorage.setItem(key, JSON.stringify({ state: { token, source: 'dev' }, version: 0 })),
    [SESSION_STORAGE_KEY, short.token],
  )
  await page.goto(ROUTE)
  await expect(page.getByRole('heading', { name: 'Ingestion Workbench' })).toBeVisible()
  await expect(page.getByRole('tab', { name: /Imports/ })).toHaveAttribute('aria-selected', 'true')

  // Document loads of /login (a redirect, or a loop of them), and every /login URL the tab shows
  // (a client-side redirect from the shell would add a second, query-less one).
  const loginLoads: string[] = []
  const loginUrls = new Set<string>()
  page.on('request', (r) => {
    if (r.isNavigationRequest() && r.frame() === page.mainFrame() && new URL(r.url()).pathname === '/login') loginLoads.push(r.url())
  })
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame() && new URL(frame.url()).pathname === '/login') loginUrls.add(frame.url())
  })

  // The server's own answer once the TTL (and its leeway) has passed. Bounded: the expiry plus
  // the leeway plus a margin. The first refusal must be 401 token_expired, and not before `exp`.
  const bound = (short.exp + leewaySeconds() + MARGIN_SECONDS) * 1000 - Date.now()
  const refusals: { status: number; code: unknown; at: number }[] = []
  await expect
    .poll(
      async () => {
        const r = await me(short.token)
        if (r.status() === 200) return 200
        refusals.push({ status: r.status(), code: (await r.json().catch(() => ({}))).code, at: Date.now() / 1000 })
        return r.status()
      },
      { timeout: bound, intervals: [2_000] },
    )
    .not.toBe(200)
  expect(refusals).toHaveLength(1)
  expect(refusals[0]).toMatchObject({ status: 401, code: 'token_expired' })
  expect(refusals[0].at).toBeGreaterThanOrEqual(short.exp)

  // The next API call of the page carries the expired token (the page itself polls nothing).
  if (new URL(page.url()).pathname !== '/login') await page.reload()
  await page.waitForURL((url) => url.pathname === '/login')
  const at = new URL(page.url())
  expect(at.searchParams.get('next')).toBe(ROUTE)
  expect(at.searchParams.get('reason')).toBe('expired')
  await expect(page.getByTestId('session-expired')).toContainText('Your session expired')
  // The expired session was ended, not kept.
  expect(await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) ?? '{}').state?.token ?? null, SESSION_STORAGE_KEY)).toBeNull()

  // Persona sign-in, back to the route.
  await page.getByRole('button', { name: /Risk analyst/ }).click()
  await page.waitForURL((url) => url.pathname === '/ingestion' && url.search === '?tab=imports')
  await expect(page.getByRole('heading', { name: 'Ingestion Workbench' })).toBeVisible()
  await expect(page.getByRole('tab', { name: /Imports/ })).toHaveAttribute('aria-selected', 'true')
  const renewed = await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) ?? '{}').state?.token ?? null, SESSION_STORAGE_KEY)
  expect(renewed).toBeTruthy()
  expect(renewed).not.toBe(short.token)

  // Exactly one redirect to sign-in: no loop, and no second (query-less) redirect from the shell.
  expect(loginLoads).toHaveLength(1)
  expect([...loginUrls]).toEqual(loginLoads)
})
