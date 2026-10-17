'use client'

import type { ReactNode } from 'react'
import { PlannedWorkspace } from '@/components/common/PlannedWorkspace'
import { findWorkspace } from '@/config/navigation'

/**
 * Keeps FE-15.1/15.2 reversible (D-02): while the `calculations` nav entry is marked `planned`
 * (NEXT_PUBLIC_CALCULATIONS_UI=off), every /calculations route shows the Phase 14 placeholder
 * instead of its pages.
 */
export function CalculationsGate({ children }: { children: ReactNode }) {
  if (findWorkspace('calculations')?.planned) return <PlannedWorkspace slug="calculations" />
  return <>{children}</>
}
