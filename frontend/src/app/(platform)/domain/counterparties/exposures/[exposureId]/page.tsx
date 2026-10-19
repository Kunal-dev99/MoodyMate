'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { CREDIT_CRUMBS } from '@/components/credit/CreditParts'
import { ExposureView } from '@/components/credit/ExposureView'

/** FE-19.1: one credit exposure: versions with their lineage to an import and source row. */
export default function ExposurePage() {
  const { exposureId } = useParams<{ exposureId: string }>()
  return (
    <RequirePermission
      permission="domain:read"
      fallback={
        <>
          <PageHeader title="Credit exposure" breadcrumbs={CREDIT_CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="domain:read" />
          </div>
        </>
      }
    >
      <ExposureView key={exposureId} exposureId={exposureId} />
    </RequirePermission>
  )
}
