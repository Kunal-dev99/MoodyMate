import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { Masked } from '@/components/common/Masked'
import { startLoad, type Loaded } from '@/components/common/load'
import { Badge } from '@/components/ui/badge'
import {
  LINK_UNRESOLVED_TEXT,
  MATURITY_MISSING,
  MATURITY_NOT_STATED,
  TARGET_KIND_LABELS,
  fetchSourceLinks,
  maturityState,
  targetHref,
  type IdentityLink,
  type IdentitySourceKind,
  type IdentityTargetKind,
  type LinkState,
} from './identity'
import type { InstrumentVersion } from './domain'

/**
 * FE-31.1 (D-57): an instrument version's maturity. A typed contract (projector v2) shows the
 * server's ISO date text as sent, never re-formatted; instrument v1 keeps it as free text, so
 * "Not stated (schema v1 free text)": the value stays in the canonical record.
 */
export function MaturityValue({ version, testId }: { version: Pick<InstrumentVersion, 'maturity_date' | 'maturity_typed'>; testId?: string }) {
  const state = maturityState(version)
  if (state.kind === 'masked')
    return <Masked value={state.value} cell testId={testId} attrs={{ 'data-state': 'masked' }} />
  if (state.kind === 'typed')
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <span className="font-mono" data-testid={testId} data-state="typed">
          {state.date}
        </span>
        <Badge variant="info" data-testid="maturity-typed-badge" title="The pinned schema version types maturity_date as DATE (projector v2, D-57).">
          Typed date
        </Badge>
      </span>
    )
  return (
    <span
      className="text-muted-foreground"
      data-testid={testId}
      data-state={state.kind}
      data-empty="true"
      title={
        state.kind === 'v1-free-text'
          ? 'Instrument schema v1 keeps maturity_date as free text (TYPE_NOT_PROJECTED); the value stays in the canonical record.'
          : 'The typed contract was pinned but this source row gave no maturity; the server projected NULL and counted it.'
      }
    >
      {state.kind === 'v1-free-text' ? MATURITY_NOT_STATED : MATURITY_MISSING}
    </span>
  )
}

/**
 * FE-31.1 (D-58): a source row's linked identity: the target's kind and id, linking to its own
 * page; or "Unresolved (text key)" when the server holds no link (no identity, or several, has
 * the key in the tenant). A refused or failed read is shown as such, never as unresolved.
 */
export function LinkedIdentity({ state, testId = 'linked-identity' }: { state: LinkState; testId?: string }) {
  if (state.kind === 'loading')
    return (
      <span className="text-xs text-muted-foreground" data-testid={testId} data-state="loading">
        Reading link…
      </span>
    )
  if (state.kind === 'forbidden')
    return (
      <span className="text-xs text-muted-foreground" data-testid={testId} data-state="forbidden" title={state.cid ? `Correlation ID: ${state.cid}` : undefined}>
        Link not visible to you (domain:read)
      </span>
    )
  if (state.kind !== 'ready')
    return (
      <span className="text-xs text-destructive" data-testid={testId} data-state="failed" title={state.cid ? `Correlation ID: ${state.cid}` : undefined}>
        Link could not be read
      </span>
    )
  const link = state.link
  if (!link)
    return (
      <span
        className="text-xs text-muted-foreground"
        data-testid={testId}
        data-state="unresolved"
        title="No link: no identity, or several (e.g. two source systems), has this text key in your tenant (LINK_UNRESOLVED / LINK_AMBIGUOUS). The text key is still the reference."
      >
        {LINK_UNRESOLVED_TEXT}
      </span>
    )
  const href = targetHref(link.target_kind, link.target_id)
  const text = `${TARGET_KIND_LABELS[link.target_kind]} ${link.target_id.slice(0, 8)}`
  return (
    <span
      className="inline-flex items-center gap-1 text-xs"
      data-testid={testId}
      data-state="linked"
      data-target-kind={link.target_kind}
      data-target-id={link.target_id}
      title={`Linked by ${link.method} (resolver v${link.resolver_version}). Target ${link.target_id}`}
    >
      {href ? (
        <Link href={href} className="font-mono text-primary hover:underline">
          {text}
        </Link>
      ) : (
        <span className="font-mono">{text}</span>
      )}
    </span>
  )
}

/**
 * The links of some source rows to one target kind (GET /identity-links, read-only), loaded once
 * per set of rows. `enabled` false (the switch off) reads nothing. A 403 or failure is kept as
 * such, so each row shows it rather than "unresolved" (D-04).
 */
export function useSourceLinks(
  enabled: boolean,
  sourceKind: IdentitySourceKind,
  targetKind: IdentityTargetKind,
  rowIds: readonly string[],
  targetId?: string,
): Loaded<ReadonlyMap<string, IdentityLink | null>> {
  const key = rowIds.join(',')
  const ids = useMemo(() => (key ? key.split(',') : []), [key])
  const [state, setState] = useState<Loaded<ReadonlyMap<string, IdentityLink | null>>>({ kind: 'loading' })
  useEffect(() => {
    if (!enabled) return
    setState({ kind: 'loading' })
    const request = startLoad((signal) => fetchSourceLinks(sourceKind, targetKind, ids, signal, targetId), {
      onReady: (value) => setState({ kind: 'ready', value }),
      onFailure: setState,
      failureTitle: 'Could not read the identity links',
    })
    return () => request.abort()
  }, [enabled, sourceKind, targetKind, ids, targetId])
  return state
}
