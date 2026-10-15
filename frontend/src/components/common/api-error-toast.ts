import { toast } from '@/components/ui/use-toast'
import { ApiError } from '@/api/client'

/**
 * Shows an API failure as a destructive toast (FE-1.4). The correlation ID is always
 * shown so users can quote it and support can find the matching backend log line.
 */
export function toastApiError(err: unknown, title = 'Request failed') {
  const apiErr = err instanceof ApiError ? err : null
  const message = apiErr?.envelope.message ?? 'Something went wrong.'
  const cid = apiErr?.correlationId
  toast({
    variant: 'destructive',
    title,
    description: cid ? `${message} Correlation ID: ${cid}` : message,
  })
}
