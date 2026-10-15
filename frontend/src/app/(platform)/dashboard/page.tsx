import { PageHeader } from '@/components/common/PageHeader'
import { PlatformHealth } from './PlatformHealth'

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        description="Platform status. Workspaces become available as each delivery-plan phase ships."
      />
      <div className="px-6 pb-6 space-y-4">
        <PlatformHealth />
      </div>
    </>
  )
}
