'use client'

import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { AssumptionSetListView } from '@/components/registry/AssumptionSets'
import { RegistryTabs } from '@/components/registry/RegistryTabs'

/** FE-16.1: the assumption sets (read-only in Feature 03). Cosmetic gate; the API enforces rule:read (D-04). */
export default function AssumptionSetsPage() {
  return (
    <>
      <PageHeader title="Assumption sets" description="Versioned assumption sets with their provenance, owner, effective dates and status. Every run pins one." />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="rule:read" fallback={<Forbidden permission="rule:read" />}>
          <AssumptionSetListView />
        </RequirePermission>
      </div>
    </>
  )
}
