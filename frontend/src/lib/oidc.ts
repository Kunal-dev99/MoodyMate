/**
 * OIDC authorization-code + PKCE flow for AUTH_MODE=oidc (FE-2.1, D-09).
 * Issuer, client ID and scopes come from GET /api/v1/auth/config; nothing is hard-coded.
 */
import { UserManager, WebStorageStateStore } from 'oidc-client-ts'
import type { Schemas } from '@/api/client'

type OidcClientConfig = Schemas['OidcClientConfig']

let manager: UserManager | null = null
let managerKey = ''

export function oidcManager(config: OidcClientConfig): UserManager {
  const key = `${config.issuer}|${config.client_id}`
  if (manager && managerKey === key) return manager
  const origin = window.location.origin
  manager = new UserManager({
    authority: config.issuer,
    client_id: config.client_id,
    redirect_uri: `${origin}/auth/callback`,
    post_logout_redirect_uri: `${origin}/login`,
    response_type: 'code',
    scope: config.scopes,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  })
  managerKey = key
  return manager
}

/** Remembered across the redirect so the user lands back where they started. */
export interface SignInState {
  next: string
}
