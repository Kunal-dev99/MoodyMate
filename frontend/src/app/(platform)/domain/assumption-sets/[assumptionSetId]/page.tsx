'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { AssumptionSetView } from '@/components/registry/AssumptionSets'

const CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Assumption sets', href: '/domain/assumption-sets' },
]

/** FE-16.1: one assumption-set version, the id a run manifest pins. */
export default function AssumptionSetPage() {
  const { assumptionSetId } = useParams<{ assumptionSetId: string }>()
  return (
    <RequirePermission
      permission="rule:read"
      fallback={
        <>
          <PageHeader title="Assumption set" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="rule:read" />
          </div>
        </>
      }
    >
      <AssumptionSetView key={assumptionSetId} assumptionSetId={assumptionSetId} />
    </RequirePermission>
  )
}
