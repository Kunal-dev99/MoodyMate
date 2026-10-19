'use client'

import { Suspense } from 'react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { CounterpartyCatalogue } from '@/components/credit/CounterpartyCatalogue'
import { RegistryTabs } from '@/components/registry/RegistryTabs'

/** FE-19.1: the Counterparties tab. Cosmetic gate; the API enforces domain:read (D-04). */
export default function CounterpartiesPage() {
  return (
    <>
      <PageHeader
        title="Counterparties"
        description="Counterparties and credit exposures projected from validated imports, with their immutable versions and lineage to each source row. Vocabularies are seeded, provisional and illustrative (D-37)."
      />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="domain:read" fallback={<Forbidden permission="domain:read" />}>
          <Suspense fallback={<LoadingBlock rows={4} />}>
            <CounterpartyCatalogue />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
