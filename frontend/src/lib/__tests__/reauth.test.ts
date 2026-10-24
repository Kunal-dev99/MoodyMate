import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { safeRedirectPath } from '@/lib/redirect'
import {
  REAUTH_MARK_KEY,
  REAUTH_WINDOW_MS,
  reauthInProgress,
  reauthenticate,
  readMark,
  resetReauth,
  signInUrl,
  type ReauthDeps,
} from '@/lib/reauth'
import type { SessionSource } from '@/lib/session'

/*
 * FE-35.4 Done-when (vitest half): a 401 token_expired re-authenticates with ONE redirect that
 * keeps the route, and never loops. The browser half is e2e/reauth.spec.ts.
 */

const ORIGIN = 'http://app.test'
const ROUTE = '/ingestion?tab=imports#history'

class MemoryStorage {
  data = new Map<string, string>()
  getItem = (k: string) => this.data.get(k) ?? null
  setItem = (k: string, v: string) => void this.data.set(k, v)
}

/** One page life of the browser: its calls, and the deps reauthenticate is given. */
function browser(storage: MemoryStorage, source: SessionSource, at: { now: number }, options: { silentToken?: string | null } = {}) {
  const calls = { navigate: [] as string[], provider: [] as string[], setToken: [] as string[], clear: 0, renew: 0 }
  const deps: ReauthDeps = {
    source,
    route: ROUTE,
    now: () => at.now,
    storage,
    renewSilently: async () => {
      calls.renew += 1
      return options.silentToken ?? null
    },
    redirectToProvider: async (route) => void calls.provider.push(route),
    navigate: (url) => void calls.navigate.push(url),
    setToken: (token) => void calls.setToken.push(token),
    clear: () => void (calls.clear += 1),
  }
  return { calls, deps }
}

beforeEach(() => resetReauth())
afterEach(() => {
  resetReauth()
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe('one redirect, keeping the route (FE-35.4)', () => {
  it('dev: any number of concurrent token_expired 401s send the user to the persona sign-in once, with the route', async () => {
    const storage = new MemoryStorage()
    const { calls, deps } = browser(storage, 'dev', { now: 1_000_000 })
    const outcomes = await Promise.all([reauthenticate(deps), reauthenticate(deps), reauthenticate(deps)])
    expect(outcomes).toEqual(['redirected', 'redirected', 'redirected'])
    expect(calls.navigate).toEqual([signInUrl(ROUTE, 'expired')])
    expect(calls.navigate[0]).toBe('/login?next=%2Fingestion%3Ftab%3Dimports%23history&reason=expired')
    expect(calls.clear).toBe(1)
    expect(calls.provider).toEqual([])
    expect(readMark(storage)).toEqual({ at: 1_000_000, route: ROUTE })
    // The page is going away: later 401s of this page join the same episode, never a second redirect.
    expect(reauthInProgress()).toBe(true)
    await reauthenticate(deps)
    expect(calls.navigate).toHaveLength(1)
  })

  it('the sign-in page returns to the same path, query and hash', () => {
    const next = new URL(signInUrl(ROUTE, 'expired'), ORIGIN).searchParams.get('next')
    expect(safeRedirectPath(next, ORIGIN)).toBe(ROUTE)
  })

  it('oidc without a refresh token: one redirect to the identity provider, back to the route', async () => {
    const storage = new MemoryStorage()
    const { calls, deps } = browser(storage, 'oidc', { now: 5_000 })
    await Promise.all([reauthenticate(deps), reauthenticate(deps)])
    expect(calls.renew).toBe(1)
    expect(calls.provider).toEqual([ROUTE])
    expect(calls.navigate).toEqual([])
    expect(calls.clear).toBe(1)
  })

  it('oidc with a refresh token: a silent renew, no navigation at all', async () => {
    const storage = new MemoryStorage()
    const { calls, deps } = browser(storage, 'oidc', { now: 5_000 }, { silentToken: 'renewed' })
    expect(await reauthenticate(deps)).toBe('renewed')
    expect(calls.setToken).toEqual(['renewed'])
    expect(calls.provider).toEqual([])
    expect(calls.navigate).toEqual([])
    expect(calls.clear).toBe(0)
    expect(reauthInProgress()).toBe(false)
  })

  it('a provider that cannot be reached still ends on the sign-in page with the route', async () => {
    const storage = new MemoryStorage()
    const { calls, deps } = browser(storage, 'oidc', { now: 5_000 })
    deps.redirectToProvider = async () => {
      throw new Error('network')
    }
    expect(await reauthenticate(deps)).toBe('redirected')
    expect(calls.navigate).toEqual([signInUrl(ROUTE, 'expired')])
  })
})

describe('no loop (FE-35.4)', () => {
  it('oidc: expired again right after coming back from the provider, it stops on the sign-in page instead of redirecting again', async () => {
    const storage = new MemoryStorage()
    const clock = { now: 10_000 }
    const first = browser(storage, 'oidc', clock)
    await reauthenticate(first.deps)
    expect(first.calls.provider).toEqual([ROUTE])

    // The provider sends the browser back (a new page life) and the new token is refused as expired again.
    resetReauth()
    clock.now += 5_000
    const second = browser(storage, 'oidc', clock)
    expect(await reauthenticate(second.deps)).toBe('stopped')
    expect(second.calls.provider).toEqual([])
    expect(second.calls.renew).toBe(0)
    expect(second.calls.navigate).toEqual([signInUrl(ROUTE, 'renew_failed')])
    expect(second.calls.clear).toBe(1)

    // Total over both page lives: exactly one redirect to the provider.
    expect([...first.calls.provider, ...second.calls.provider]).toHaveLength(1)
  })

  it('a silent renew whose token is refused at once also stops instead of renewing in a loop', async () => {
    const storage = new MemoryStorage()
    const clock = { now: 10_000 }
    const { calls, deps } = browser(storage, 'oidc', clock, { silentToken: 'renewed' })
    expect(await reauthenticate(deps)).toBe('renewed')
    clock.now += 1_000
    expect(await reauthenticate(deps)).toBe('stopped')
    expect(calls.renew).toBe(1)
    expect(calls.navigate).toEqual([signInUrl(ROUTE, 'renew_failed')])
  })

  it('dev: a second expiry inside the window ends on the sign-in page with the "could not be renewed" notice', async () => {
    const storage = new MemoryStorage()
    const clock = { now: 10_000 }
    await reauthenticate(browser(storage, 'dev', clock).deps)
    resetReauth()
    clock.now += REAUTH_WINDOW_MS - 1
    const again = browser(storage, 'dev', clock)
    expect(await reauthenticate(again.deps)).toBe('stopped')
    expect(again.calls.navigate).toEqual([signInUrl(ROUTE, 'renew_failed')])
  })

  it('a later, separate expiry (after the window) re-authenticates normally again', async () => {
    const storage = new MemoryStorage()
    const clock = { now: 10_000 }
    await reauthenticate(browser(storage, 'oidc', clock).deps)
    resetReauth()
    clock.now += REAUTH_WINDOW_MS
    const later = browser(storage, 'oidc', clock)
    expect(await reauthenticate(later.deps)).toBe('redirected')
    expect(later.calls.provider).toEqual([ROUTE])
  })

  it('an unreadable mark is ignored (fail closed to one redirect, never to none)', async () => {
    const storage = new MemoryStorage()
    storage.setItem(REAUTH_MARK_KEY, '{not json')
    const { calls, deps } = browser(storage, 'dev', { now: 1 })
    expect(await reauthenticate(deps)).toBe('redirected')
    expect(calls.navigate).toEqual([signInUrl(ROUTE, 'expired')])
  })
})

describe('the API client hands token_expired to the re-authentication (FE-35.4)', () => {
  async function client() {
    vi.resetModules()
    const { authMiddleware, setTokenExpiredHandler } = await import('@/api/client')
    const { useSession } = await import('@/lib/session')
    type OnResponse = NonNullable<typeof authMiddleware.onResponse>
    const respond = (sent: string | null, status: number, code: string | null) => {
      const headers = new Headers()
      if (sent) headers.set('Authorization', `Bearer ${sent}`)
      const body = code === null ? null : JSON.stringify({ code, message: 'm', details: null, correlation_id: null, retryable: false })
      const response = new Response(body, { status, headers: { 'Content-Type': 'application/json' } })
      return (authMiddleware.onResponse as OnResponse)({ request: new Request('http://api.test/api/v1/me', { headers }), response } as Parameters<OnResponse>[0]) as Promise<Response>
    }
    return { respond, setTokenExpiredHandler, useSession }
  }

  it('calls the handler (and leaves the token to it) for the current token; other 401 codes end the session as before', async () => {
    const { respond, setTokenExpiredHandler, useSession } = await client()
    const handler = vi.fn()
    setTokenExpiredHandler(handler)
    useSession.getState().setToken('current', 'dev')
    const response = await respond('current', 401, 'token_expired')
    expect(handler).toHaveBeenCalledTimes(1)
    expect(useSession.getState().token).toBe('current')
    // The caller still reads the body.
    expect((await response.json()).code).toBe('token_expired')

    await respond('stale', 401, 'token_expired')
    expect(handler).toHaveBeenCalledTimes(1)

    await respond('current', 401, 'invalid_token')
    expect(handler).toHaveBeenCalledTimes(1)
    expect(useSession.getState().token).toBeNull()
    setTokenExpiredHandler(null)
  })

  it('without a handler (outside the session provider) token_expired ends the session too', async () => {
    const { respond, useSession } = await client()
    useSession.getState().setToken('current', 'dev')
    await respond('current', 401, 'token_expired')
    expect(useSession.getState().token).toBeNull()
  })

  it('NEXT_PUBLIC_REAUTH_UI=off: token_expired ends the session exactly as before Phase 35 (D-02)', async () => {
    vi.stubEnv('NEXT_PUBLIC_REAUTH_UI', 'off')
    const { respond, setTokenExpiredHandler, useSession } = await client()
    const handler = vi.fn()
    setTokenExpiredHandler(handler)
    useSession.getState().setToken('current', 'dev')
    await respond('current', 401, 'token_expired')
    expect(handler).not.toHaveBeenCalled()
    expect(useSession.getState().token).toBeNull()
  })

  it('the session provider installs the handler and leaves the navigation to it', () => {
    const provider = readFileSync(join(__dirname, '..', '..', 'components', 'auth', 'SessionProvider.tsx'), 'utf8')
    expect(provider).toContain('setTokenExpiredHandler(() => void reauthenticateInBrowser())')
    expect(provider).toMatch(/if \(REAUTH_UI_ENABLED && reauthInProgress\(\)\) return\s+router\.replace\(/)
  })
})

describe('the sign-in notice (FE-35.4)', () => {
  const render = async (reason: string | null) => {
    const { SessionExpiredNotice } = await import('@/components/auth/SessionExpiredNotice')
    return renderToStaticMarkup(createElement(SessionExpiredNotice, { reason }))
  }

  it('says why the sign-in page opened, for the two reasons only', async () => {
    expect(await render('expired')).toContain('Your session expired')
    expect(await render('renew_failed')).toContain('could not be renewed')
    expect(await render(null)).toBe('')
    expect(await render('<script>')).toBe('')
  })

  it('is gone with NEXT_PUBLIC_REAUTH_UI=off (D-02)', async () => {
    vi.stubEnv('NEXT_PUBLIC_REAUTH_UI', 'off')
    vi.resetModules()
    expect(await render('expired')).toBe('')
  })
})
