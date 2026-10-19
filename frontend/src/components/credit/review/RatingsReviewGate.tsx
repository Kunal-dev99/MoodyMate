import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { CREDIT_DOMAIN_UI_ENABLED, RATINGS_UI_ENABLED } from '@/config/features'

/**
 * Keeps FE-20.2 reversible (D-02): while NEXT_PUBLIC_RATINGS_UI=off (or the Counterparties tab
 * itself is off), /domain/ratings is a 404, as in Phase 19. Cosmetic: the API enforces
 * `domain:read` on the reads and `rating:approve` on every decision (D-04).
 */
export function RatingsReviewGate({ children }: { children: ReactNode }) {
  if (!CREDIT_DOMAIN_UI_ENABLED || !RATINGS_UI_ENABLED) notFound()
  return <>{children}</>
}
