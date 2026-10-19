'use client'

import { ActorLabel, SEED_LABEL_ILLUSTRATIVE, isSeedActor } from '@/components/common/ActorLabel'

/**
 * Actors on a governed registry version (rule, assumption set), demo polish blocker 3.
 *
 * A version a migration seeded has no maker-checker step: its author, approver and decider
 * are all the migration tag (e.g. `0018_calculations`). Printing that tag three times reads
 * like a self-approval, so the author shows as "Platform seed (illustrative)" and the
 * approval as "Seeded, no approval step". A real person's approval is always shown as-is.
 */

export const SEEDED_NO_APPROVAL = 'Seeded, no approval step'

/** True when the version came from a seed, by the server's flag or its seed author. */
export const isSeededVersion = (v: { authored_by: string; seeded?: boolean }) => v.seeded === true || isSeedActor(v.authored_by)

export function AuthorActor({ actor, testId }: { actor: string; testId?: string }) {
  return <ActorLabel actor={actor} seedLabel={SEED_LABEL_ILLUSTRATIVE} testId={testId} />
}

/** An approver or decider. `seeded` is the version's own seed status. */
export function ApprovalActor({ actor, seeded, testId }: { actor: string | null | undefined; seeded: boolean; testId?: string }) {
  if (seeded && (!actor || isSeedActor(actor)))
    return (
      <span className="italic text-muted-foreground" title={actor ?? undefined} data-actor-kind="seed" data-testid={testId}>
        {SEEDED_NO_APPROVAL}
      </span>
    )
  return <ActorLabel actor={actor} seedLabel={SEED_LABEL_ILLUSTRATIVE} testId={testId} />
}
