'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { ScenarioRunsSection } from '@/components/advanced/ScenarioRunsList'
import { explorerHref, scenarioHref } from '@/components/advanced/scenarios'
import { PageHeader } from '@/components/common/PageHeader'

/** FE-25.2: one scenario's runs against a baseline, each linking to its comparison. Cosmetic gate; the API enforces calc:read (D-04). */
export default function ScenarioRunsPage() {
  const { scenarioId } = useParams<{ scenarioId: string }>()
  const crumbs = [{ label: 'Advanced' }, { label: 'Agentic Stress-Test Explorer', href: explorerHref }, { label: 'Scenario', href: scenarioHref(scenarioId) }, { label: 'Runs' }]
  return (
    <>
      <PageHeader title="Scenario runs" description="Baseline versus stress, per scenario run." breadcrumbs={crumbs} />
      <div className="px-6 pb-6">
        <RequirePermission permission="calc:read" fallback={<Forbidden permission="calc:read" />}>
          <ScenarioRunsSection key={scenarioId} scenarioId={scenarioId} />
        </RequirePermission>
      </div>
    </>
  )
}
