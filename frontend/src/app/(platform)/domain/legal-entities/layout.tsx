import type { ReactNode } from 'react'
import { LinksGate } from '@/components/domain/LinksGate'

/** FE-31.1: a 404 while NEXT_PUBLIC_DOMAIN_LINKS_UI=off, as in Phase 30 (D-02). */
export default function LegalEntitiesLayout({ children }: { children: ReactNode }) {
  return <LinksGate>{children}</LinksGate>
}
