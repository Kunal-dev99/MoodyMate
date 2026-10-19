import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { STRESS_COMPARE_UI_ENABLED } from '@/config/features'

/**
 * Keeps FE-25.2 reversible (D-02): while NEXT_PUBLIC_STRESS_COMPARE_UI=off (or the explorer is
 * off), every /advanced/stress-explorer/{id}/runs route is a 404 and the Phase 24 explorer is
 * what remains. Cosmetic: the API enforces `calc:read` on every comparison read (D-04).
 */
export function CompareGate({ children }: { children: ReactNode }) {
  if (!STRESS_COMPARE_UI_ENABLED) notFound()
  return <>{children}</>
}
