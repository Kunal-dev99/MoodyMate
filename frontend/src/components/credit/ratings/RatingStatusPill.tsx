import { StatusPill } from '@/components/common/StatusPill'
import { RATING_STATUS_STYLE, type ObligorRatingStatus } from './ratings'

/** `obligor_rating.status` (D-39 §2) as the server sent it. The UI never moves a status itself. */
export function RatingStatusPill({ status, className, testId = 'rating-status' }: { status: ObligorRatingStatus; className?: string; testId?: string }) {
  return <StatusPill value={status} variant={RATING_STATUS_STYLE[status]} titled testId={testId} className={className} />
}
