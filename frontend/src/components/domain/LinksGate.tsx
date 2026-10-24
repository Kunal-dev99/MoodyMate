import type { ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { DOMAIN_LINKS_UI_ENABLED } from '@/config/features'

/**
 * Keeps FE-31.1 reversible (D-02): while NEXT_PUBLIC_DOMAIN_LINKS_UI=off, every
 * /domain/legal-entities route is a 404, as in Phase 30. The rest of /domain is untouched.
 * Cosmetic: the API enforces `domain:read` (D-04).
 */
export function LinksGate({ children }: { children: ReactNode }) {
  if (!DOMAIN_LINKS_UI_ENABLED) notFound()
  return <>{children}</>
}
