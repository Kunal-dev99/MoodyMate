'use client'

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { LogOut, RefreshCw, ShieldAlert } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { Button } from '@/components/ui/button'
import { oidcManager } from '@/lib/oidc'
import { useSession } from '@/lib/session'

export type Permission = Schemas['Permission']
type Me = Schemas['Me']
type AuthConfig = Schemas['AuthConfig']

interface SessionContextValue {
  me: Me
  permissions: ReadonlySet<Permission>
  authConfig: AuthConfig
  has: (permission: Permission) => boolean
  signOut: () => Promise<void>
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function useCurrentSession(): SessionContextValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useCurrentSession must be used inside <SessionProvider>')
  return value
}

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; me: Me; permissions: Permission[]; authConfig: AuthConfig }
  | { kind: 'blocked'; error: ApiError }

/**
 * Gate for the authenticated app (FE-2.1). No token → sign-in page. Identity and
 * permissions come from the backend on every load; the UI only mirrors them (D-04).
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const token = useSession((s) => s.token)
  const source = useSession((s) => s.source)
  const clear = useSession((s) => s.clear)
  const [hydrated, setHydrated] = useState(false)
  const [state, setState] = useState<State>({ kind: 'loading' })
  // Bumped by "Try again" to re-run the identity load without touching the token (FE-2.5).
  const [attempt, setAttempt] = useState(0)
  // Read through a ref so navigating between pages does not refetch identity.
  const pathnameRef = useRef(pathname)
  pathnameRef.current = pathname
  // An explicit sign-out goes to a plain sign-in page, not back to where the user was.
  const signingOut = useRef(false)

  // sessionStorage is only readable after mount.
  useEffect(() => setHydrated(true), [])

  useEffect(() => {
    if (!hydrated) return
    if (!token) {
      router.replace(
        signingOut.current
          ? '/login'
          : `/login?next=${encodeURIComponent(pathnameRef.current ?? '/dashboard')}`,
      )
      return
    }
    let cancelled = false
    setState({ kind: 'loading' })
    Promise.all([
      call(() => api.GET('/api/v1/me')),
      call(() => api.GET('/api/v1/me/permissions')),
      call(() => api.GET('/api/v1/auth/config')),
    ])
      .then(([me, perms, authConfig]) => {
        if (!cancelled) setState({ kind: 'ready', me, permissions: perms.permissions, authConfig })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // 401 already cleared the token (client middleware); the effect re-runs and redirects.
        if (err instanceof ApiError && err.status !== 401) setState({ kind: 'blocked', error: err })
      })
    return () => {
      cancelled = true
    }
  }, [hydrated, token, router, attempt])

  const signOut = useCallback(async () => {
    const config = state.kind === 'ready' ? state.authConfig : null
    signingOut.current = true
    clear()
    if (source === 'oidc' && config?.oidc) {
      await oidcManager(config.oidc).removeUser()
    }
    router.replace('/login')
  }, [clear, router, source, state])

  const value = useMemo<SessionContextValue | null>(() => {
    if (state.kind !== 'ready') return null
    const set = new Set(state.permissions)
    return {
      me: state.me,
      permissions: set,
      authConfig: state.authConfig,
      has: (p) => set.has(p),
      signOut,
    }
  }, [state, signOut])

  if (state.kind === 'blocked') {
    return (
      <div className="mx-auto mt-24 max-w-lg px-6">
        <EmptyState
          icon={ShieldAlert}
          title={
            state.error.envelope.code === 'account_not_provisioned'
              ? 'Account not set up'
              : state.error.envelope.retryable
                ? 'Temporarily unavailable'
                : 'Access blocked'
          }
          description={`${state.error.envelope.message}${state.error.correlationId ? ` Correlation ID: ${state.error.correlationId}` : ''}`}
          action={
            <div className="flex gap-2">
              {state.error.envelope.retryable && (
                // A retryable failure (e.g. 503 during a key rotation) keeps the session;
                // retrying reloads identity with the same token (FE-2.5).
                <Button onClick={() => setAttempt((n) => n + 1)}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  Try again
                </Button>
              )}
              <Button variant="outline" onClick={() => void signOut()}>
                <LogOut className="mr-2 h-4 w-4" />
                Sign out
              </Button>
            </div>
          }
        />
      </div>
    )
  }
  if (!value) {
    return (
      <div className="mx-auto mt-24 max-w-md px-6">
        <LoadingBlock rows={3} />
      </div>
    )
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
