import { PageHeader } from '@/components/common/PageHeader'
import { DASHBOARD_TILES_ENABLED } from '@/config/features'
import { CountTiles } from './CountTiles'
import { PlatformHealth } from './PlatformHealth'

// P-13: product copy and count tiles; NEXT_PUBLIC_DASHBOARD_TILES=off restores the previous page exactly (D-02).
const DESCRIPTION = DASHBOARD_TILES_ENABLED
  ? 'Liquidity and credit risk on governed, versioned data: every figure traces back to its source row.'
  : 'Platform status. Workspaces become available as each delivery-plan phase ships.'

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Overview" description={DESCRIPTION} />
      <div className="px-6 pb-6 space-y-4">
        {DASHBOARD_TILES_ENABLED && <CountTiles />}
        <PlatformHealth />
      </div>
    </>
  )
}
