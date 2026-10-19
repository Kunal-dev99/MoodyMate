import { cn } from '@/lib/utils'
import { RATING_STATUS_STYLE, type ObligorRatingStatus } from './ratings'

/** `obligor_rating.status` (D-39 §2) as the server sent it. The UI never moves a status itself. */
export function RatingStatusPill({ status, className, testId = 'rating-status' }: { status: ObligorRatingStatus; className?: string; testId?: string }) {
  const style = RATING_STATUS_STYLE[status]
  return (
    <span
      className={cn('inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium', style.className, className)}
      title={style.description}
      data-testid={testId}
      data-status={status}
    >
      {style.label}
    </span>
  )
}
