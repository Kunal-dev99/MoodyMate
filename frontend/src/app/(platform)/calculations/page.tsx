'use client'

import Link from 'next/link'
import { Suspense } from 'react'
import { Play } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { IllustrativeBanner } from '@/components/calculations/IllustrativeBanner'
import { RunList } from '@/components/calculations/RunList'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { Button } from '@/components/ui/button'

const TITLE = 'Calculations'
const DESCRIPTION =
  'Runs of the illustrative maturity ladder on pinned dataset snapshots: every run records the versions it pinned, its warnings and results with lineage to source rows.'

/**
 * Calculations workspace (FE-15.1): the run list, and "New run" for calc:run. Both gates are
 * cosmetic; the API enforces calc:read and calc:run (D-04).
 */
export default function CalculationsPage() {
  return (
    <>
      <PageHeader
        title={TITLE}
        description={DESCRIPTION}
        actions={
          <RequirePermission permission="calc:run" fallback={null}>
            <Button asChild size="sm" data-testid="new-run">
              <Link href="/calculations/new">
                <Play className="mr-1 h-3.5 w-3.5" />
                New run
              </Link>
            </Button>
          </RequirePermission>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        <RequirePermission permission="calc:read" fallback={<Forbidden permission="calc:read" />}>
          <IllustrativeBanner />
          <Suspense fallback={<LoadingBlock rows={4} />}>
            <RunList />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
