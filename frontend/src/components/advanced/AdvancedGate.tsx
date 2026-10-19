import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { STRESS_EXPLORER_UI_ENABLED } from '@/config/features'

/**
 * Keeps FE-24.1 reversible (D-02): while NEXT_PUBLIC_STRESS_EXPLORER_UI=off, every /advanced
 * route is a 404, as in Phase 23. Cosmetic: the API enforces each scenario route's permission
 * and SCENARIOS_ENABLED on the server (D-04).
 */
export function AdvancedGate({ children }: { children: ReactNode }) {
  if (!STRESS_EXPLORER_UI_ENABLED) notFound()
  return <>{children}</>
}
