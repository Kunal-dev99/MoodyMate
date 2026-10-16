import type { ReactNode } from 'react'
import { IngestionGate } from '@/components/ingestion/IngestionGate'

export default function IngestionLayout({ children }: { children: ReactNode }) {
  return <IngestionGate>{children}</IngestionGate>
}
