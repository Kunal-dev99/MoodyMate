'use client'

import type { ReactNode } from 'react'
import { PlannedWorkspace } from '@/components/common/PlannedWorkspace'
import { findWorkspace } from '@/config/navigation'

/**
 * Keeps FE-14.1 reversible (D-02): while the `domain` nav entry is marked `planned`
 * (NEXT_PUBLIC_DOMAIN_UI=off), every /domain route shows the Phase 13 placeholder instead
 * of its pages.
 */
export function DomainGate({ children }: { children: ReactNode }) {
  if (findWorkspace('domain')?.planned) return <PlannedWorkspace slug="domain" />
  return <>{children}</>
}
