import { Clock } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { REAUTH_UI_ENABLED } from '@/config/features'
import { SIGN_IN_REASONS, type SignInReason } from '@/lib/reauth'

const TEXT: Record<SignInReason, { title: string; description: string }> = {
  expired: {
    title: 'Your session expired',
    description: 'Sign in again to return to the page you were on.',
  },
  renew_failed: {
    title: 'Your session could not be renewed',
    description: 'It expired again right after it was renewed, so you were not redirected again. Sign in to continue.',
  },
}

/**
 * FE-35.4: why the sign-in page opened after a 401 `token_expired` (its `reason` parameter).
 * Nothing for any other value, and nothing while NEXT_PUBLIC_REAUTH_UI=off (D-02).
 */
export function SessionExpiredNotice({ reason }: { reason: string | null }) {
  if (!REAUTH_UI_ENABLED || !SIGN_IN_REASONS.includes(reason as SignInReason)) return null
  const text = TEXT[reason as SignInReason]
  return (
    <Alert variant="warning" data-testid="session-expired" data-reason={reason}>
      <Clock className="h-4 w-4" />
      <AlertTitle>{text.title}</AlertTitle>
      <AlertDescription>{text.description}</AlertDescription>
    </Alert>
  )
}
