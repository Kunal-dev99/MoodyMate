'use client'

import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { LegalEntityCatalogue } from '@/components/domain/LegalEntityCatalogue'
import { RegistryTabs } from '@/components/registry/RegistryTabs'

/** FE-31.1: the Legal entities tab. Cosmetic gate; the API enforces domain:read (D-04). */
export default function LegalEntitiesPage() {
  return (
    <>
      <PageHeader
        title="Legal entities"
        description="Legal entity identities resolved from the positions' text keys: a stable id per source system and code, provisional and append-only. Their attributes and the source-of-truth rule are open client decisions."
      />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="domain:read" fallback={<Forbidden permission="domain:read" />}>
          <LegalEntityCatalogue />
        </RequirePermission>
      </div>
    </>
  )
}
