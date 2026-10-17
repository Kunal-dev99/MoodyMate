import type { ReactNode } from 'react'
import { CalculationsGate } from '@/components/calculations/CalculationsGate'

export default function CalculationsLayout({ children }: { children: ReactNode }) {
  return <CalculationsGate>{children}</CalculationsGate>
}
