'use client'

import Link from 'next/link'
import { Suspense } from 'react'
import { Plus, Sparkles } from 'lucide-react'
import { Forbidden } from '@/components/auth/Forbidden'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { ScenarioLibrary } from '@/components/advanced/ScenarioLibrary'
import { newScenarioHref } from '@/components/advanced/scenarios'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { Button } from '@/components/ui/button'

/**
 * FE-24.2: the Agentic Stress-Test Explorer: the shared library, "My explorations" and the
 * Review queue, and "New scenario" for scenario:propose. Both gates are cosmetic; the API
 * enforces calc:read and scenario:propose (D-04). Illustrative, never a regulatory stress test.
 */
export default function StressExplorerPage() {
  return (
    <>
      <PageHeader
        title="Agentic Stress-Test Explorer"
        description="Author stress scenarios from the approved shock templates, confirm them for exploration, and promote them through maker-checker approval."
        actions={
          <div className="flex items-center gap-2">
          <Button asChild size="sm" variant="outline" data-testid="agent-demo">
            <Link href="/advanced/stress-explorer/agent-demo">
              <Sparkles className="mr-1 h-3.5 w-3.5" />
              Agent demo
            </Link>
          </Button>
          <RequirePermission permission="scenario:propose" fallback={null}>
            <Button asChild size="sm" data-testid="new-scenario">
              <Link href={newScenarioHref}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                New scenario
              </Link>
            </Button>
          </RequirePermission>
          </div>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        <RequirePermission
          permission="calc:read"
          fallback={<Forbidden permission="calc:read" />}
        >
          <Suspense fallback={<LoadingBlock rows={4} />}>
            <ScenarioLibrary />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
