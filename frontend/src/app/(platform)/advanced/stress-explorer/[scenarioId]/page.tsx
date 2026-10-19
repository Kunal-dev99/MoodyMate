'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { ScenarioView } from '@/components/advanced/ScenarioView'
import { PageHeader } from '@/components/common/PageHeader'

/** FE-24.2: one scenario with its versions and lifecycle actions. Cosmetic gate; the API enforces calc:read (D-04). */
export default function ScenarioPage() {
  const { scenarioId } = useParams<{ scenarioId: string }>()
  return (
    <RequirePermission
      permission="calc:read"
      fallback={
        <>
          <PageHeader title="Scenario" />
          <div className="px-6 pb-6">
            <Forbidden permission="calc:read" />
          </div>
        </>
      }
    >
      <ScenarioView key={scenarioId} scenarioId={scenarioId} />
    </RequirePermission>
  )
}
