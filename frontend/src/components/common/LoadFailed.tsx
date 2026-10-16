import type { ComponentType } from 'react'
import { RefreshCw } from 'lucide-react'
import { EmptyState } from '@/components/common/EmptyState'
import { Button } from '@/components/ui/button'

/**
 * A load that failed for a retryable reason (network, 5xx): says so and offers
 * "Try again", in the same form as the session's retryable state (FE-2.5).
 */
export function LoadFailed({
  icon,
  title,
  cid,
  onRetry,
}: {
  icon: ComponentType<{ className?: string }>
  title: string
  cid?: string | null
  onRetry: () => void
}) {
  return (
    <EmptyState
      icon={icon}
      title={title}
      description={`The request did not complete.${cid ? ` Correlation ID: ${cid}` : ''}`}
      action={
        <Button size="sm" variant="outline" onClick={onRetry} data-testid="load-retry">
          <RefreshCw className="mr-2 h-4 w-4" />
          Try again
        </Button>
      }
    />
  )
}
