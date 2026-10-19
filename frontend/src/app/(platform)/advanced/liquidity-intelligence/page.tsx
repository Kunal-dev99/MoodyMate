'use client'

import { LiquidityIntel } from '@/components/advanced/LiquidityIntel'
import { PageHeader } from '@/components/common/PageHeader'
import { Badge } from '@/components/ui/badge'

/**
 * Liquidity Intelligence prototype: explain the LCR movement between two immutable runs, then
 * optionally test a 25% deposit-outflow shock as a separate scenario run. All mock, synthetic data,
 * no API calls; illustrative, not a regulatory calculation.
 */
export default function LiquidityIntelligencePage() {
  return (
    <>
      <PageHeader
        title="Liquidity Intelligence"
        description="Investigation: why did the LCR change between the last two runs? Scenario: a 25% deposit-outflow shock against the same baseline."
        actions={<Badge variant="warning">Prototype · mock agent · synthetic data</Badge>}
      />
      <div className="px-6 pb-6">
        <LiquidityIntel />
      </div>
    </>
  )
}
