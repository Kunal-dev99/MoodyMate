import type { ReactNode } from 'react'
import { CreditGate } from '@/components/credit/CreditGate'

/** FE-19.1: a 404 while NEXT_PUBLIC_CREDIT_DOMAIN_UI=off, as in Phase 18 (D-02). */
export default function CounterpartiesLayout({ children }: { children: ReactNode }) {
  return <CreditGate>{children}</CreditGate>
}
