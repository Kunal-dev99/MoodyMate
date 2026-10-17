'use client'

import { useParams } from 'next/navigation'
import { Suspense } from 'react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { RunView } from '@/components/calculations/RunView'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'

const CRUMBS = [{ label: 'Calculations', href: '/calculations' }]

/** One calculation run (FE-15.2): manifest, warnings, maturity ladder and contributions. */
export default function RunPage() {
  const { runId } = useParams<{ runId: string }>()
  return (
    <RequirePermission
      permission="calc:read"
      fallback={
        <>
          <PageHeader title="Calculation run" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="calc:read" />
          </div>
        </>
      }
    >
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <RunView key={runId} runId={runId} />
      </Suspense>
    </RequirePermission>
  )
}
