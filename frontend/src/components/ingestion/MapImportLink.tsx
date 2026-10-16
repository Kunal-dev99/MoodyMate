'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { GitMerge } from 'lucide-react'
import { api, call } from '@/api/client'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { Button } from '@/components/ui/button'

/**
 * "Map this import" (FE-10.1) for an import stopped at MAPPING_REQUIRED: opens the
 * workbench of the mapping for the import's contract with this import selected, or the
 * create page when there is none yet. Read-only lookup; hidden without mapping:read.
 */
export function MapImportLink({ importId, sourceId, contractId }: { importId: string; sourceId: string; contractId: string }) {
  const { has } = useCurrentSession()
  const canRead = has('mapping:read')
  const canPropose = has('mapping:propose')
  const [href, setHref] = useState<string | null>(null)

  useEffect(() => {
    if (!canRead) return
    const controller = new AbortController()
    call(() => api.GET('/api/v1/mappings', { params: { query: { source_id: sourceId, limit: 200 } }, signal: controller.signal }))
      .then((page) => {
        if (controller.signal.aborted) return
        const existing = page.items.find((m) => m.source_contract_id === contractId)
        if (existing) setHref(`/ingestion/mappings/${existing.id}?import=${importId}`)
        else if (canPropose) setHref(`/ingestion/mappings/new?import=${importId}`)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [canRead, canPropose, sourceId, contractId, importId])

  if (!href) return null
  return (
    <Button asChild size="sm" variant="outline" data-testid="import-map-link">
      <Link href={href}>
        <GitMerge className="mr-1 h-3.5 w-3.5" />
        Map this import
      </Link>
    </Button>
  )
}
