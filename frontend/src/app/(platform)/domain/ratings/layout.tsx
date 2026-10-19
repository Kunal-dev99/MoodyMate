import type { ReactNode } from 'react'
import { RatingsReviewGate } from '@/components/credit/review/RatingsReviewGate'

/** FE-20.2: a 404 while NEXT_PUBLIC_RATINGS_UI=off, as in Phase 19 (D-02). */
export default function RatingsLayout({ children }: { children: ReactNode }) {
  return <RatingsReviewGate>{children}</RatingsReviewGate>
}
