import { ShieldX } from 'lucide-react'
import { EmptyState } from '@/components/common/EmptyState'

/** 403 state (FE-2.2). Shown when the backend or the mirrored permissions deny access. */
export function Forbidden({
  permission,
  correlationId,
}: {
  permission?: string
  correlationId?: string | null
}) {
  return (
    <EmptyState
      icon={ShieldX}
      title="You don't have access to this"
      description={
        <>
          {permission ? (
            <>
              This needs the <code className="font-mono">{permission}</code> permission.{' '}
            </>
          ) : null}
          Ask a platform administrator if you need it.
          {correlationId ? ` Correlation ID: ${correlationId}` : ''}
        </>
      }
    />
  )
}
