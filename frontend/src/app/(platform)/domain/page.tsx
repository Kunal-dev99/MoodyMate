'use client'

import { Suspense } from 'react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { InstrumentCatalogue } from '@/components/domain/InstrumentCatalogue'

const TITLE = 'Financial Domain'
const DESCRIPTION =
  'Instruments projected from validated imports, with their immutable versions, attributes, cash flows and lineage. Assumptions and the rule registry are planned (Phase 16).'

/** Financial Domain workspace (FE-14.1): the instrument catalogue. Cosmetic gate; the API enforces domain:read (D-04). */
export default function DomainPage() {
  return (
    <>
      <PageHeader title={TITLE} description={DESCRIPTION} />
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
