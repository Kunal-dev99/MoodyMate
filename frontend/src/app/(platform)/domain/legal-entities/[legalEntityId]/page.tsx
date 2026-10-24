'use client'

import { useParams } from 'next/navigation'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { PageHeader } from '@/components/common/PageHeader'
import { LEGAL_ENTITY_CRUMBS, LegalEntityView } from '@/components/domain/LegalEntityView'

/** FE-31.1: one legal entity, its link counts and the links naming it (ids only, D-58 §7). */
export default function LegalEntityPage() {
  const { legalEntityId } = useParams<{ legalEntityId: string }>()
  return (
    <RequirePermission
      permission="domain:read"
      fallback={
        <>
          <PageHeader title="Legal entity" breadcrumbs={LEGAL_ENTITY_CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="domain:read" />
          </div>
        </>
      }
    >
      <LegalEntityView key={legalEntityId} legalEntityId={legalEntityId} />
    </RequirePermission>
  )
}
