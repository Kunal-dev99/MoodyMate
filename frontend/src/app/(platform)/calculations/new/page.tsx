'use client'

import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { IllustrativeBanner } from '@/components/calculations/IllustrativeBanner'
import { NewRunPanel } from '@/components/calculations/NewRunPanel'
import { PageHeader } from '@/components/common/PageHeader'

const CRUMBS = [{ label: 'Calculations', href: '/calculations' }, { label: 'New run' }]

/**
 * The New-run panel (FE-15.1), for calc:run. Cosmetic gate: the API refuses a snapshot or a run
 * without calc:run (403), and decides everything else (D-04).
 */
export default function NewRunPage() {
  return (
    <>
      <PageHeader
        title="New run"
        description="Pin a dataset snapshot, pick an available module and a reporting date, review what the run will pin, then submit."
        breadcrumbs={CRUMBS}
      />
      <div className="space-y-4 px-6 pb-6">
        <RequirePermission permission="calc:run" fallback={<Forbidden permission="calc:run" />}>
          <IllustrativeBanner />
          <NewRunPanel />
        </RequirePermission>
      </div>
    </>
  )
}
