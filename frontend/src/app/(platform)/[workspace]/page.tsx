import { notFound } from 'next/navigation'
import { PlannedWorkspace } from '@/components/common/PlannedWorkspace'
import { adminWorkspaces, findWorkspace, mainWorkspaces } from '@/config/navigation'

export function generateStaticParams() {
  return [...mainWorkspaces, ...adminWorkspaces]
    .filter((w) => w.planned)
    .map((w) => ({ workspace: w.slug }))
}

export const dynamicParams = false

export default function WorkspacePage({ params }: { params: { workspace: string } }) {
  const workspace = findWorkspace(params.workspace)
  if (!workspace?.planned) notFound()
  return <PlannedWorkspace slug={workspace.slug} />
}
