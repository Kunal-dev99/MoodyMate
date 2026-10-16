'use client'

import type { ReactNode } from 'react'
import { PlannedWorkspace } from '@/components/common/PlannedWorkspace'
import { findWorkspace } from '@/config/navigation'

/**
 * Keeps FE-7.1 reversible (D-02): while the `ingestion` nav entry is marked `planned`,
 * every Ingestion Workbench route shows the Phase 6 placeholder instead of its pages.
 */
export function IngestionGate({ children }: { children: ReactNode }) {
  if (findWorkspace('ingestion')?.planned) return <PlannedWorkspace slug="ingestion" />
  return <>{children}</>
}
