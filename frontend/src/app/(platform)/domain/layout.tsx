import type { ReactNode } from 'react'
import { DomainGate } from '@/components/domain/DomainGate'

export default function DomainLayout({ children }: { children: ReactNode }) {
  return <DomainGate>{children}</DomainGate>
}
