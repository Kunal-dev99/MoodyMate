'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { CREDIT_CRUMBS } from '@/components/credit/CreditParts'
import { CounterpartyView } from '@/components/credit/CounterpartyView'

/** FE-19.1: one counterparty: versions with lineage, and the credit exposures naming it. */
export default function CounterpartyPage() {
  const { counterpartyId } = useParams<{ counterpartyId: string }>()
  return (
    <RequirePermission
      permission="domain:read"
      fallback={
        <>
          <PageHeader title="Counterparty" breadcrumbs={CREDIT_CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="domain:read" />
          </div>
        </>
      }
    >
      <CounterpartyView key={counterpartyId} counterpartyId={counterpartyId} />
    </RequirePermission>
  )
}
