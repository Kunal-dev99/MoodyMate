'use client'

import { CheckCircle2, Gavel } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import type { MappingDetail, MappingVersion } from '@/components/ingestion/mappings'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { MappingWorkbenchState } from './useMappingWorkbench'

/*
 * FE-10.2 (split out of ingestion/mappings/[mappingId]/page.tsx by FE-35.2, unchanged): submit
 * and the approver's decision. Each gate here is cosmetic: the server checks mapping:propose and
 * mapping:approve, the author and the status on every write (403/409, D-04).
 */

/** The author submits a passing Draft for approval. */
export function SubmitSection({ wb, version, submitAllowed, onSubmit }: { wb: MappingWorkbenchState; version: MappingVersion; submitAllowed: boolean; onSubmit: () => void }) {
  const { comment, setComment, busy, dirty } = wb
  return (
    <PageSection icon={CheckCircle2} title="Submit for approval">
      <div className="flex flex-wrap items-center gap-2">
        <Input className="max-w-md" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} aria-label="Submit comment" />
        <Button data-testid="mapping-submit" disabled={!submitAllowed || busy !== null || dirty} title={submitAllowed ? undefined : 'Run a passing dry run of this version first'} onClick={onSubmit}>
          Submit v{version.version} for approval
        </Button>
      </div>
      {!submitAllowed && (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="mapping-submit-locked">
          Submit unlocks once the latest dry run of this version is ok (OK or WARNINGS).
        </p>
      )}
    </PageSection>
  )
}

/** An approver who is not the author approves (activating the version) or rejects with a comment. */
export function DecisionSection({
  wb,
  m,
  version,
  active,
  mine,
  decisionAllowed,
  onApprove,
  onReject,
}: {
  wb: MappingWorkbenchState
  m: MappingDetail
  version: MappingVersion
  active: MappingVersion | undefined
  mine: boolean
  decisionAllowed: boolean
  onApprove: () => void
  onReject: () => void
}) {
  const { comment, setComment, busy } = wb
  return (
    <PageSection icon={Gavel} title="Approver decision" accent="warning">
      {decisionAllowed ? (
        <div className="space-y-2" data-testid="mapping-decision-panel">
          <p className="text-sm text-muted-foreground">
            Approving activates v{version.version} for new imports of {m.source_code}
            {active ? ` and deprecates v${active.version}` : ''}. Its latest dry run was {version.last_validation?.outcome ?? 'not recorded'}.
          </p>
          <Textarea placeholder="Comment (required to reject)" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} aria-label="Decision comment" />
          <div className="flex gap-2">
            <Button data-testid="mapping-approve" disabled={busy !== null} onClick={onApprove}>
              Approve and activate v{version.version}
            </Button>
            <Button variant="destructive" data-testid="mapping-reject" disabled={busy !== null || !comment.trim()} onClick={onReject}>
              Reject
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground" data-testid="mapping-awaiting-approver">
          {mine ? 'Waiting for an approver. You cannot approve your own mapping.' : 'Waiting for a user with mapping approval rights.'}
        </p>
      )}
    </PageSection>
  )
}
