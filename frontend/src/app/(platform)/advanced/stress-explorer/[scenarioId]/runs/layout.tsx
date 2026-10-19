import type { ReactNode } from 'react'
import { CompareGate } from '@/components/advanced/CompareGate'

/** FE-25.2: a 404 while NEXT_PUBLIC_STRESS_COMPARE_UI=off, so the Phase 24 explorer is restored exactly (D-02). */
export default function ScenarioRunsLayout({ children }: { children: ReactNode }) {
  return <CompareGate>{children}</CompareGate>
}
