'use client'

import { useParams } from 'next/navigation'
import { Suspense } from 'react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { MappingsPlanned } from '@/components/ingestion/MappingsPlanned'
import { MappingWorkbench } from '@/components/ingestion/mapping-workbench/MappingWorkbench'
import { CRUMBS } from '@/components/ingestion/mapping-workbench/shared'
import { MAPPING_UI_ENABLED } from '@/config/features'

/**
 * The mapping workbench (FE-10.1) with its versions and approval (FE-10.2). FE-35.2: the
 * workbench's parts live in components/ingestion/mapping-workbench/; this page keeps the switch
 * and the mapping:read gate.
 */
export default function MappingPage() {
  const { mappingId } = useParams<{ mappingId: string }>()
  if (!MAPPING_UI_ENABLED)
    return (
      <>
        <PageHeader title="Mapping" breadcrumbs={CRUMBS} />
        <div className="px-6 pb-6">
          <MappingsPlanned />
        </div>
      </>
    )
  return (
    <RequirePermission
      permission="mapping:read"
      fallback={
        <>
          <PageHeader title="Mapping" breadcrumbs={CRUMBS} />
          <div className="px-6 pb-6">
            <Forbidden permission="mapping:read" />
          </div>
        </>
      }
    >
      <Suspense fallback={<LoadingBlock rows={6} />}>
        <MappingWorkbench mappingId={mappingId} />
      </Suspense>
    </RequirePermission>
  )
}
