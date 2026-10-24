import type { ReactNode } from 'react'

/*
 * FE-10.1/10.2 (split out of ingestion/mappings/[mappingId]/page.tsx by FE-35.2, unchanged):
 * what the mapping workbench's parts share.
 */

export const CRUMBS = [
  { label: 'Ingestion Workbench', href: '/ingestion' },
  { label: 'Mappings', href: '/ingestion?tab=mappings' },
]
export const selectClass = 'h-9 max-w-full rounded-md border border-input bg-background px-2 text-sm'
export const DISCARD = 'You have unsaved edits to this draft. Discard them?'

export const OUTCOME_VARIANT = { OK: 'success', WARNINGS: 'warning', FAILED: 'destructive' } as const
export const SEVERITY_VARIANT = { BLOCKER: 'destructive', ERROR: 'destructive', WARNING: 'warning' } as const

export function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}
