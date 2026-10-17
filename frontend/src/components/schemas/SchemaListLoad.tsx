import type { ReactNode } from 'react'
import { FileStack } from 'lucide-react'
import { api, call, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { createLoader, type Loaded } from '@/components/common/load'

export type SchemaList = Schemas['SchemaList']
export type SchemaListState = Loaded<SchemaList>

/** GET /schemas, cancellable. */
export const fetchSchemaList = (signal: AbortSignal) => call(() => api.GET('/api/v1/schemas', { signal }))

/**
 * The schema list load shared by `/schemas` and `/schemas/proposals/new` (FE-14.3): a network
 * or server failure ends in "Try again", never an endless loading block.
 */
export const schemaListLoader = (setState: (state: SchemaListState) => void) =>
  createLoader(fetchSchemaList, setState, 'Could not load schemas')

/**
 * Renders the schema list load: loading, the 403 state, a retryable failure with "Try again",
 * a definite refusal, or `children` with the list once the backend has answered.
 */
export function SchemaListGate({
  state,
  onRetry,
  rows = 4,
  children,
}: {
  state: SchemaListState
  onRetry: () => void
  rows?: number
  children: (list: SchemaList) => ReactNode
}) {
  switch (state.kind) {
    case 'loading':
      return <LoadingBlock rows={rows} />
    case 'forbidden':
      return <Forbidden permission="schema:read" correlationId={state.cid} />
    case 'failed':
      return <LoadFailed icon={FileStack} title="The schemas could not be loaded" cid={state.cid} onRetry={onRetry} />
    case 'not_found':
      return (
        <EmptyState
          icon={FileStack}
          title="The schemas are not available"
          description={`The request was refused${state.status ? ` (HTTP ${state.status})` : ''}.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
        />
      )
    case 'ready':
      return <>{children(state.value)}</>
  }
}
