'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { ComparisonView } from '@/components/advanced/ComparisonView'
import { PageHeader } from '@/components/common/PageHeader'

/** FE-25.2: baseline versus stress for one scenario run. Cosmetic gate; the API enforces calc:read (D-04). */
export default function ComparisonPage() {
  const { scenarioId, scenarioRunId } = useParams<{ scenarioId: string; scenarioRunId: string }>()
  return (
    <RequirePermission
      permission="calc:read"
      fallback={
        <>
          <PageHeader title="Baseline versus stress" />
          <div className="px-6 pb-6">
            <Forbidden permission="calc:read" />
          </div>
        </>
      }
    >
      <ComparisonView key={`${scenarioId}-${scenarioRunId}`} scenarioId={scenarioId} scenarioRunId={scenarioRunId} />
    </RequirePermission>
  )
}
