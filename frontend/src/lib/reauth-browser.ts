import { api, call } from '@/api/client'
import { oidcManager, type SignInState } from '@/lib/oidc'
import { reauthenticate, signInUrl, type ReauthOutcome } from '@/lib/reauth'
import { useSession } from '@/lib/session'

/*
 * FE-35.4: the browser side of `reauthenticate` (src/lib/reauth.ts): the session store, the
 * location and the OIDC client. The session provider installs it as the API client's
 * `token_expired` handler.
 */

/** Where the user is: path, query and hash. */
const currentRoute = (): string => `${window.location.pathname}${window.location.search}${window.location.hash}`

/** The OIDC client settings (GET auth/config, public), or null when SSO is not configured or not reachable. */
async function oidcConfig() {
  try {
    return (await call(() => api.GET('/api/v1/auth/config'))).oidc ?? null
  } catch {
    return null
  }
}

/**
 * A new access token without navigating, where the provider allows it: oidc-client-ts uses the
 * stored refresh token. Without one it would need a hidden iframe on our callback page, which the
 * security headers forbid (X-Frame-Options DENY, frame-ancestors 'none'), so none is tried.
 */
async function renewSilently(): Promise<string | null> {
  const config = await oidcConfig()
  if (!config) return null
  const manager = oidcManager(config)
  const user = await manager.getUser()
  if (!user?.refresh_token) return null
  const renewed = await manager.signinSilent()
  return renewed?.access_token ?? null
}

async function redirectToProvider(route: string): Promise<void> {
  const config = await oidcConfig()
  if (!config) {
    window.location.replace(signInUrl(route, 'expired'))
    return
  }
  const state: SignInState = { next: route }
  await oidcManager(config).signinRedirect({ state })
}

/** Re-authenticates this page's expired session (one episode however many 401s arrive). */
export function reauthenticateInBrowser(): Promise<ReauthOutcome> {
  const { source, setToken, clear } = useSession.getState()
  const route = currentRoute()
  return reauthenticate({
    source,
    route,
    now: Date.now,
    storage: window.sessionStorage,
    renewSilently,
    redirectToProvider,
    navigate: (url) => window.location.replace(url),
    setToken: (token) => setToken(token, 'oidc'),
    clear,
  }).catch(() => {
    // Storage or the provider failed in an unexpected way: end the session, keep the route (fail closed).
    clear()
    window.location.replace(signInUrl(route, 'expired'))
    return 'redirected' as const
  })
}
