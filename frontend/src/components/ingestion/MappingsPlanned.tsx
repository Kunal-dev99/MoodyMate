import { GitMerge } from 'lucide-react'
import { EmptyState } from '@/components/common/EmptyState'

/** The mapping routes while NEXT_PUBLIC_MAPPING_UI=off (D-02): nothing but this note. */
export function MappingsPlanned() {
  return (
    <EmptyState
      icon={GitMerge}
      title="The mapping workbench is switched off"
      description="This environment hides the mapping UI. ACTIVE mapping versions still apply to imports."
    />
  )
}
