'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { InstrumentView } from '@/components/domain/InstrumentView'

const CRUMBS = [{ label: 'Financial Domain', href: '/domain' }]

/** One instrument (FE-14.1): versions, attributes, cash flows and lineage to each import row. */
export default function InstrumentPage() {
  const { instrumentId } = useParams<{ instrumentId: string }>()
  return (
    <RequirePermission
      permission="domain:read"
      fallback={
        <>
          <PageHeader title="Instrument" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="domain:read" />
          </div>
        </>
      }
    >
      <InstrumentView key={instrumentId} instrumentId={instrumentId} />
    </RequirePermission>
  )
}
