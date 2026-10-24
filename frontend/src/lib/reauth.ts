/*
 * FE-35.4: re-authentication after a 401 `token_expired` (backend core/auth.py).
 *
 * - AUTH_MODE=oidc: a silent renew where the identity provider allows it (oidc-client-ts with a
 *   refresh token: no iframe, no navigation); otherwise one redirect to the provider that returns
 *   to the route (the sign-in state carries it; /auth/callback re-checks it with safeRedirectPath).
 * - dev: back to the persona sign-in, `/login?next=<route>`, which returns to the route.
 *
 * The route is the path, query and hash. One expiry episode per page acts once, however many
 * requests answer 401 at the same time. A redirect is remembered for REAUTH_WINDOW_MS in
 * sessionStorage; another expiry inside that window never redirects again (no loop): the session
 * ends on the plain sign-in page, which asks the user to act. The server decides every request
 * either way (D-04); this module only chooses where the browser goes.
 */
import type { SessionSource } from '@/lib/session'

/** sessionStorage: the last re-authentication redirect `{ at, route }` of this tab. */
export const REAUTH_MARK_KEY = 'moodymate_reauth'
/** A second expiry this soon after a re-authentication is not redirected again. */
export const REAUTH_WINDOW_MS = 60_000

/** Why the sign-in page opened (its `reason` query parameter). */
export type SignInReason = 'expired' | 'renew_failed'
export const SIGN_IN_REASONS: readonly SignInReason[] = ['expired', 'renew_failed']

export interface ReauthMark {
  at: number
  route: string
}

export type ReauthOutcome = 'renewed' | 'redirected' | 'stopped'

/** What re-authentication needs from the browser; tests pass fakes. */
export interface ReauthDeps {
  /** How the expired session was obtained. */
  source: SessionSource | null
  /** Where the user is: path, query and hash. */
  route: string
  now: () => number
  storage: Pick<Storage, 'getItem' | 'setItem'>
  /** OIDC: a new access token without navigating, or null where the provider allows none. */
  renewSilently: () => Promise<string | null>
  /** OIDC: the one redirect to the provider, back to `route`. */
  redirectToProvider: (route: string) => Promise<void>
  /** A full navigation that replaces the current history entry. */
  navigate: (url: string) => void
  setToken: (token: string) => void
  clear: () => void
}

/** The sign-in page that returns to `route` (dev persona sign-in; OIDC after a stop). */
export const signInUrl = (route: string, reason: SignInReason): string => `/login?next=${encodeURIComponent(route)}&reason=${reason}`

export function readMark(storage: Pick<Storage, 'getItem'>): ReauthMark | null {
  try {
    const value: unknown = JSON.parse(storage.getItem(REAUTH_MARK_KEY) ?? 'null')
    if (typeof value !== 'object' || value === null) return null
    const { at, route } = value as Record<string, unknown>
    return typeof at === 'number' && typeof route === 'string' ? { at, route } : null
  } catch {
    return null
  }
}

/** Whether an expiry at `now` follows a re-authentication too closely to redirect again. */
export const withinWindow = (mark: ReauthMark | null, now: number): boolean => mark !== null && now >= mark.at && now - mark.at < REAUTH_WINDOW_MS

let episode: Promise<ReauthOutcome> | null = null

/**
 * True from the first 401 `token_expired` of an episode until a silent renew ends it; after a
 * redirect it stays true (the page is going away). The session provider then leaves the
 * navigation to this module instead of sending the user to /login itself.
 */
export const reauthInProgress = (): boolean => episode !== null

/** Starts (or joins) this page's re-authentication. */
export function reauthenticate(deps: ReauthDeps): Promise<ReauthOutcome> {
  if (episode) return episode
  // The steps start on the next microtask, so the episode is visible before the session is cleared.
  const current = Promise.resolve().then(() => run(deps))
  episode = current
  current.then(
    (outcome) => {
      if (outcome === 'renewed' && episode === current) episode = null
    },
    () => {
      if (episode === current) episode = null
    },
  )
  return current
}

async function run(d: ReauthDeps): Promise<ReauthOutcome> {
  const now = d.now()
  if (withinWindow(readMark(d.storage), now)) {
    // Expired again right after a re-authentication: never redirect automatically again.
    d.clear()
    d.navigate(signInUrl(d.route, 'renew_failed'))
    return 'stopped'
  }
  d.storage.setItem(REAUTH_MARK_KEY, JSON.stringify({ at: now, route: d.route } satisfies ReauthMark))
  if (d.source === 'oidc') {
    const token = await d.renewSilently().catch(() => null)
    if (token) {
      // The session provider reloads identity and the route's data for the new token.
      d.setToken(token)
      return 'renewed'
    }
    d.clear()
    try {
      await d.redirectToProvider(d.route)
    } catch {
      // The provider could not be reached: the sign-in page, still returning to the route.
      d.navigate(signInUrl(d.route, 'expired'))
    }
    return 'redirected'
  }
  d.clear()
  d.navigate(signInUrl(d.route, 'expired'))
  return 'redirected'
}

/** A new page life (tests only: in the browser a redirect replaces the page). */
export function resetReauth(): void {
  episode = null
}
