'use client'

import { useOptionalSession } from '@/components/auth/SessionProvider'
import { cn } from '@/lib/utils'

/**
 * Human-readable rendering of an audit actor string (demo polish P-1).
 *
 * The backend records actors as `user:<uuid>` for people and `system:<what>` or a
 * migration tag such as `0018_calculations` for platform seed data. The raw string stays
 * in the `title` tooltip so nothing is hidden; this is cosmetic only (D-04).
 */

export type ActorKind = 'you' | 'user' | 'seed' | 'other'

export interface ActorDescription {
  kind: ActorKind
  label: string
  /** The full raw actor, for the tooltip. */
  title: string
}

/** A migration tag like `0018_calculations`, or a `system:` / `seed:` actor. */
const SEED = /^(system:|seed:|migration:)|^\d{4}_[a-z0-9_]+$/i

export const isSeedActor = (actor: string | null | undefined): boolean => !!actor && SEED.test(actor.trim())

export const SEED_LABEL = 'Platform seed'
export const SEED_LABEL_ILLUSTRATIVE = 'Platform seed (illustrative)'

/** Pure: how an actor string should read. `myUserId` is the signed-in user's id, if known. */
export function describeActor(
  actor: string,
  myUserId: string | null | undefined,
  opts: { seedLabel?: string } = {},
): ActorDescription {
  const raw = actor.trim()
  if (isSeedActor(raw)) return { kind: 'seed', label: opts.seedLabel ?? SEED_LABEL, title: raw }
  const id = raw.startsWith('user:') ? raw.slice('user:'.length) : null
  if (id !== null) {
    if (myUserId && id === myUserId) return { kind: 'you', label: 'You', title: raw }
    return { kind: 'user', label: `User · ${id.slice(0, 8)}`, title: raw }
  }
  return { kind: 'other', label: raw, title: raw }
}

/** Plain-string form, for places that compose a sentence (e.g. a page description). */
export function actorText(actor: string | null | undefined, myUserId: string | null | undefined): string {
  if (!actor) return '—'
  return describeActor(actor, myUserId).label
}

/** Hook form of actorText, bound to the current session. */
export function useActorText(): (actor: string | null | undefined) => string {
  const session = useOptionalSession()
  const myUserId = session?.me.user_id ?? null
  return (actor) => actorText(actor, myUserId)
}

export function ActorLabel({
  actor,
  seedLabel,
  className,
  testId,
  you,
}: {
  actor: string | null | undefined
  /** Force "You" when the caller knows better (e.g. matched by a separate user-id field). */
  you?: boolean
  /** Override the seed wording, e.g. "Platform seed (illustrative)" on governed registries. */
  seedLabel?: string
  className?: string
  testId?: string
}) {
  const session = useOptionalSession()
  if (!actor) {
    return (
      <span className={cn('text-muted-foreground', className)} data-testid={testId}>
        —
      </span>
    )
  }
  const described = describeActor(actor, session?.me.user_id ?? null, { seedLabel })
  const d: ActorDescription = you ? { ...described, kind: 'you', label: 'You' } : described
  const title =
    d.kind === 'you' && session ? `${session.me.display_name} (${d.title})` : d.title
  return (
    <span
      className={cn(d.kind === 'user' && 'font-mono', d.kind === 'seed' && 'italic text-muted-foreground', className)}
      title={title}
      data-actor-kind={d.kind}
      data-testid={testId}
    >
      {d.label}
    </span>
  )
}
