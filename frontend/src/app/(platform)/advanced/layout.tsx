import type { ReactNode } from 'react'
import { AdvancedGate } from '@/components/advanced/AdvancedGate'

/** FE-24.1: a 404 while NEXT_PUBLIC_STRESS_EXPLORER_UI=off, as in Phase 23 (D-02). */
export default function AdvancedLayout({ children }: { children: ReactNode }) {
  return <AdvancedGate>{children}</AdvancedGate>
}
