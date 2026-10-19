import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { CREDIT_DOMAIN_UI_ENABLED } from '@/config/features'

/**
 * Keeps FE-19.1 reversible (D-02): while NEXT_PUBLIC_CREDIT_DOMAIN_UI=off, every
 * /domain/counterparties route is a 404, as in Phase 18. The rest of /domain is untouched.
 * Cosmetic: the API enforces `domain:read` (D-04).
 */
export function CreditGate({ children }: { children: ReactNode }) {
  if (!CREDIT_DOMAIN_UI_ENABLED) notFound()
  return <>{children}</>
}
