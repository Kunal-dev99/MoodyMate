'use client'

import { useEffect, useState } from 'react'
import Image from 'next/image'
import { EnvironmentBanner } from '@/components/layout/EnvironmentBanner'
import { useRouter, useSearchParams } from 'next/navigation'
import { KeyRound, LogIn, ServerCrash } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { SessionExpiredNotice } from '@/components/auth/SessionExpiredNotice'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { toastApiError } from '@/components/common/api-error-toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { oidcManager, type SignInState } from '@/lib/oidc'
import { safeRedirectPath } from '@/lib/redirect'
import { useSession } from '@/lib/session'

type AuthConfig = Schemas['AuthConfig']
type Persona = Schemas['DevPersona']

export function SignIn() {
  const router = useRouter()
  const params = useSearchParams()
  const rawNext = params.get('next')
  // FE-35.4: set by the re-authentication after a 401 token_expired.
  const reason = params.get('reason')
  // Resolved when used (client only): needs window.location.origin.
  const next = () => safeRedirectPath(rawNext, window.location.origin)
  const setToken = useSession((s) => s.setToken)
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [personas, setPersonas] = useState<Persona[]>([])
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    call(() => api.GET('/api/v1/auth/config'))
      .then(async (cfg) => {
        setConfig(cfg)
        if (cfg.mode === 'dev' && cfg.dev_sessions) {
          setPersonas(await call(() => api.GET('/api/v1/dev/personas')))
        }
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err : null))
  }, [])

  const signInAs = async (role: string) => {
    setBusy(role)
    try {
      const session = await call(() => api.POST('/api/v1/dev/sessions', { body: { role } }))
      setToken(session.access_token, 'dev')
      router.replace(next())
    } catch (err) {
      toastApiError(err, 'Sign-in failed')
      setBusy(null)
    }
  }

  const signInWithSso = async () => {
    if (!config?.oidc) return
    setBusy('sso')
    const state: SignInState = { next: next() }
    await oidcManager(config.oidc).signinRedirect({ state })
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Unknown while loading is not shown; unknown after a failure is (fail closed). */}
      {(config || error) && <EnvironmentBanner environment={config?.environment ?? null} />}
      <div className="px-6 py-16">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center rounded-md bg-[#15171A] px-3 py-1.5 shadow-sm">
            <Image src="/brand/logo.png" alt="Fusion Practices" width={140} height={28} priority className="h-7 w-auto" />
          </div>
          <div className="border-l border-border pl-3">
            <h1 className="text-base font-semibold tracking-tight text-foreground">Financial Risk Platform</h1>
          </div>
        </div>

        <SessionExpiredNotice reason={reason} />

        {error && (
          <EmptyState
            icon={ServerCrash}
            title="Sign-in is unavailable"
            description={`${error.envelope.message}${error.correlationId ? ` Correlation ID: ${error.correlationId}` : ''}`}
          />
        )}
        {!config && !error && <LoadingBlock rows={4} />}

        {config?.mode === 'oidc' && (
          <Card className="card-elevated">
            <CardHeader>
              <CardTitle>Sign in</CardTitle>
              <CardDescription>Use your organisation account.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={() => void signInWithSso()} disabled={busy !== null}>
                <LogIn className="mr-2 h-4 w-4" />
                Sign in with SSO
              </Button>
            </CardContent>
          </Card>
        )}

        {config?.mode === 'dev' && (
          <Card className="card-elevated" data-testid="dev-user-picker">
            <CardHeader>
              <div className="flex items-center gap-2">
                <CardTitle>Development sign-in</CardTitle>
                <Badge variant="warning">Dev mode — synthetic users</Badge>
              </div>
              <CardDescription>
                Pick a seeded role. This picker exists only when the backend runs with
                AUTH_MODE=dev on a local or test environment.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid items-start gap-3 sm:grid-cols-2">
              {personas.map((p) => (
                <button
                  key={p.role}
                  type="button"
                  onClick={() => void signInAs(p.role)}
                  disabled={busy !== null}
                  className="card-interactive rounded-md border border-border p-4 text-left disabled:opacity-60"
                >
                  <div className="flex items-center gap-2">
                    <KeyRound className="h-4 w-4 text-primary" />
                    <span className="text-sm font-medium">{p.name}</span>
                  </div>
                  {p.description && <p className="text-helper mt-1">{p.description}</p>}
                  <div className="mt-2 flex flex-wrap gap-1">
                    {p.permissions.map((perm) => (
                      <Badge key={perm} variant="secondary" className="font-mono">
                        {perm}
                      </Badge>
                    ))}
                  </div>
                </button>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
      </div>
    </div>
  )
}
