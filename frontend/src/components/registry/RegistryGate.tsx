import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { RULES_UI_ENABLED } from '@/config/features'

/**
 * Keeps FE-16.1 reversible (D-02): while NEXT_PUBLIC_RULES_UI=off, every /domain registry route
 * (rules, modules, assumption sets) is a 404, as before Phase 16. The instrument catalogue and
 * the rest of /domain are untouched. Cosmetic: the API enforces `rule:read` (D-04).
 */
export function RegistryGate({ children }: { children: ReactNode }) {
  if (!RULES_UI_ENABLED) notFound()
  return <>{children}</>
}
