'use client'

import { useCallback, useEffect, useState } from 'react'
import { Activity, RefreshCw, ServerCrash } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { PageSection } from '@/components/common/PageSection'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { toastApiError } from '@/components/common/api-error-toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

type Health = Schemas['HealthResponse']
type State =
  | { kind: 'loading' }
  | { kind: 'ready'; health: Health }
  | { kind: 'error'; error: ApiError | null }

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm">{children}</span>
    </div>
  )
}

export function PlatformHealth() {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      const health = await call(() => api.GET('/api/v1/health'))
      setState({ kind: 'ready', health })
    } catch (err) {
      toastApiError(err, 'Could not load platform health')
      setState({ kind: 'error', error: err instanceof ApiError ? err : null })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const refresh = (
    <Button variant="outline" size="sm" onClick={() => void load()} disabled={state.kind === 'loading'}>
      <RefreshCw className="mr-2 h-3.5 w-3.5" />
      Refresh
    </Button>
  )

  return (
    <PageSection icon={Activity} title="Platform health" accent="primary" actions={refresh}>
      {state.kind === 'loading' && <LoadingBlock rows={4} />}
      {state.kind === 'error' && (
        <EmptyState
          icon={ServerCrash}
          title="Platform API unavailable"
          description={
            state.error?.correlationId
              ? `${state.error.envelope.message} Correlation ID: ${state.error.correlationId}`
              : state.error?.envelope.message ?? 'Unknown error.'
          }
          action={refresh}
        />
      )}
      {state.kind === 'ready' && (
        <div>
          <Row label="Status">
            <Badge variant={state.health.status === 'ok' ? 'success' : 'warning'}>
              {state.health.status === 'ok' ? 'Operational' : 'Degraded'}
            </Badge>
          </Row>
          <Row label="Database">
            <Badge variant={state.health.database === 'ok' ? 'success' : 'warning'}>
              {state.health.database === 'ok' ? 'Reachable' : 'Unreachable'}
            </Badge>
          </Row>
          <Row label="Environment">
            <span className="font-mono">{state.health.env}</span>
          </Row>
          <Row label="API version">
            <span className="font-mono">{state.health.version}</span>
          </Row>
        </div>
      )}
    </PageSection>
  )
}
