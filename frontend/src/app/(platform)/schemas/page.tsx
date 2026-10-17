'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '@/components/common/PageHeader'
import { SchemaCatalogueView } from '@/components/schemas/SchemaCatalogue'
import { schemaListLoader, type SchemaListState } from '@/components/schemas/SchemaListLoad'
import { Button } from '@/components/ui/button'

/** Schema catalogue (FE-4.1): every registered canonical schema in the tenant. */
export default function SchemaCataloguePage() {
  const [state, setState] = useState<SchemaListState>({ kind: 'loading' })
  // FE-14.3: a failed load ends in "Try again", never an endless loading block.
  const loader = useMemo(() => schemaListLoader(setState), [])

  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  return (
    <>
      <PageHeader
        title="Schema catalogue"
        description="Versioned canonical schemas: owner, lifecycle status, the version in effect and what depends on each."
        actions={
          <Button asChild size="sm" variant="outline">
            <Link href="/schemas/proposals">Change proposals</Link>
          </Button>
        }
      />
      <div className="px-6 pb-6">
        <SchemaCatalogueView state={state} onRetry={loader.load} />
      </div>
    </>
  )
}
