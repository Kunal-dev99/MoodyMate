import { notFound } from 'next/navigation'
import { PlannedWorkspace } from '@/components/common/PlannedWorkspace'
import { adminWorkspaces, findWorkspace, mainWorkspaces } from '@/config/navigation'

export function generateStaticParams() {
  return [...mainWorkspaces, ...adminWorkspaces]
    .filter((w) => w.planned)
    .map((w) => ({ workspace: w.slug }))
}

export const dynamicParams = false

// Next 15: route params arrive as a Promise in server components (FE-28.1).
export default async function WorkspacePage({ params }: { params: Promise<{ workspace: string }> }) {
  const { workspace: slug } = await params
  const workspace = findWorkspace(slug)
  if (!workspace?.planned) notFound()
  return <PlannedWorkspace slug={workspace.slug} />
}
