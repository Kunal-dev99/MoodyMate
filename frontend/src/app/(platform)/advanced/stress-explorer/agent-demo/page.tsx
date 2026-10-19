'use client'

import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'
import { AgentDemo } from '@/components/advanced/AgentDemo'
import { PageHeader } from '@/components/common/PageHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

/**
 * Demo mode: the full agentic workflow (request, proposal, run against baseline, compare,
 * explain, adapt) on a fixed synthetic cash-flow set with a mock agent. No API calls. It previews
 * Phases 25-27 until the real engine and agent ship; illustrative only.
 */
export default function AgentDemoPage() {
  return (
    <>
      <PageHeader
        title="Agent demo"
        description="Ask in plain English, review the agent's proposal, run it against the baseline maturity ladder, and read an explanation backed by the cash flows that moved."
        actions={
          <div className="flex items-center gap-2">
            <Badge variant="warning">Demo mode · mock agent · synthetic data</Badge>
            <Button asChild size="sm" variant="outline">
              <Link href="/advanced/stress-explorer">
                <ArrowLeft className="mr-1 h-3.5 w-3.5" />
                Scenario library
              </Link>
            </Button>
          </div>
        }
      />
      <div className="px-6 pb-6">
        <AgentDemo />
      </div>
    </>
  )
}
