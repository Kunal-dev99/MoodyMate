import type { ReactNode } from 'react'
import { RegistryGate } from '@/components/registry/RegistryGate'

/** FE-16.1: a 404 while NEXT_PUBLIC_RULES_UI=off, as before Phase 16 (D-02). */
export default function RulesLayout({ children }: { children: ReactNode }) {
  return <RegistryGate>{children}</RegistryGate>
}
