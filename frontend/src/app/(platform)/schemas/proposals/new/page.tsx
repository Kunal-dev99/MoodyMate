'use client'

import { Suspense, useEffect, useMemo, useState } from 'react'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { NewProposalView } from '@/components/schemas/ProposalEditor'
import { schemaListLoader, type SchemaListState } from '@/components/schemas/SchemaListLoad'

/** Loads the schemas the proposal can target; a failed load offers "Try again" (FE-14.3). */
function NewProposal() {
  const [state, setState] = useState<SchemaListState>({ kind: 'loading' })
  const loader = useMemo(() => schemaListLoader(setState), [])

  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  return <NewProposalView state={state} onRetry={loader.load} />
}

/** New schema change proposal (FE-5.1, step 1). */
export default function NewProposalPage() {
  return (
    <>
      <PageHeader
        title="Propose a schema change"
        description="Creates a draft. Run the impact analysis next; it must exist before the proposal can be submitted."
        breadcrumbs={[
          { label: 'Source & Schemas', href: '/schemas' },
          { label: 'Proposals', href: '/schemas/proposals' },
          { label: 'New' },
        ]}
      />
      <div className="px-6 pb-6">
        <RequirePermission permission="schema:propose">
          <Suspense fallback={<LoadingBlock rows={6} />}>
            <NewProposal />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
