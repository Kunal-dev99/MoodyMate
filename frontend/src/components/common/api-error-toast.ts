import { toast } from '@/components/ui/use-toast'
import { ApiError } from '@/api/client'
import { RETRY_AFTER_UI_ENABLED } from '@/config/features'
import { retryAfterOf, retryText } from '@/lib/retry'

/**
 * Shows an API failure as a destructive toast (FE-1.4). The correlation ID is always
 * shown so users can quote it and support can find the matching backend log line.
 * FE-33.1: a refusal that sent `Retry-After` (429 `rate_limited`, 503 `workers_busy`) also
 * says when to retry, unless NEXT_PUBLIC_RETRY_AFTER_UI=off.
 */
export function toastApiError(err: unknown, title = 'Request failed', retryUi: boolean = RETRY_AFTER_UI_ENABLED) {
  const apiErr = err instanceof ApiError ? err : null
  const message = apiErr?.envelope.message ?? 'Something went wrong.'
  const cid = apiErr?.correlationId
  const retryAfter = retryUi ? retryAfterOf(err) : null
  const text = retryAfter === null ? message : `${message} ${retryText(retryAfter)}`
  toast({
    variant: 'destructive',
    title,
    description: cid ? `${text} Correlation ID: ${cid}` : text,
  })
}
