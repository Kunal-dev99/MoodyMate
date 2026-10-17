'use client'

import { Suspense } from 'react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { InstrumentCatalogue } from '@/components/domain/InstrumentCatalogue'
import { RegistryTabs } from '@/components/registry/RegistryTabs'
import { RULES_UI_ENABLED } from '@/config/features'

const TITLE = 'Financial Domain'
// FE-16.1: the registry is live behind NEXT_PUBLIC_RULES_UI; off, the Phase 15 text exactly (D-02).
const DESCRIPTION = RULES_UI_ENABLED
  ? 'Instruments projected from validated imports, with their immutable versions, attributes, cash flows and lineage. The registry tabs list the methodology rules, calculation modules and assumption sets.'
  : 'Instruments projected from validated imports, with their immutable versions, attributes, cash flows and lineage. Assumptions and the rule registry are planned (Phase 16).'

/** Financial Domain workspace (FE-14.1): the instrument catalogue. Cosmetic gate; the API enforces domain:read (D-04). */
export default function DomainPage() {
  return (
    <>
      <PageHeader title={TITLE} description={DESCRIPTION} />
      <RegistryTabs />
      <div className="px-6 pb-6">
        <RequirePermission permission="domain:read" fallback={<Forbidden permission="domain:read" />}>
          <Suspense fallback={<LoadingBlock rows={4} />}>
            <InstrumentCatalogue />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
