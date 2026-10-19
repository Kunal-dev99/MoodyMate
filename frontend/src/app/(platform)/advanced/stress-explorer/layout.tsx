import type { ReactNode } from 'react'
import { StressExplorerBanner } from '@/components/advanced/StressExplorerBanner'

/**
 * The Agentic Stress-Test Explorer's shell (FE-24.1, pages FE-24.2): the D-44 §8 label above
 * every explorer page. The 404 while NEXT_PUBLIC_STRESS_EXPLORER_UI=off is the parent
 * /advanced layout's AdvancedGate (D-02).
 */
export default function StressExplorerLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <div className="px-6 pt-4">
        <StressExplorerBanner />
      </div>
      {children}
    </>
  )
}
