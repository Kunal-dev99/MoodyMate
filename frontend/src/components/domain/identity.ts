import { api, call, type Schemas } from '@/api/client'
import type { operations } from '@/api/schema'
import type { LoadFailure } from '@/components/common/load'
import { CREDIT_DOMAIN_UI_ENABLED } from '@/config/features'
import { counterpartyHref } from '@/components/credit/credit'
import { instrumentHref, type InstrumentVersion } from './domain'

/*
 * FE-31.1 (D-57, D-58): the typed-maturity and identity-link helpers of the Financial Domain.
 * Every payload type is the generated one. Links carry ids only (D-58 §7): the UI shows the
 * target's id and links to the target's own page, whose route applies its classification.
 * Links are read-only here; the UI never writes, changes or hides one.
 */

export type LegalEntity = Schemas['LegalEntityOut']
export type LegalEntityDetail = Schemas['LegalEntityDetail']
export type LegalEntityPage = Schemas['LegalEntityPage']
export type IdentityLink = Schemas['IdentityLinkOut']
export type IdentityLinkPage = Schemas['IdentityLinkPage']
export type IdentitySourceKind = Schemas['IdentitySourceKind']
export type IdentityTargetKind = Schemas['IdentityTargetKind']

/** The instrument detail's text for a maturity the pinned schema does not type (instrument v1). */
export const MATURITY_NOT_STATED = 'Not stated (schema v1 free text)'
/** A typed contract whose row gave no maturity: projected as NULL and counted by the server. */
export const MATURITY_MISSING = 'Not stated in the source row'
/** A source row without a link: its text key is unresolved or ambiguous (LINK_UNRESOLVED / LINK_AMBIGUOUS). */
export const LINK_UNRESOLVED_TEXT = 'Unresolved (text key)'

/**
 * How one instrument version's maturity is shown. `typed`: the pinned schema types
 * `maturity_date` as DATE (projector v2) and the server sent its ISO text; `typed-missing`: typed
 * but absent (NULL); `masked`: withheld by the caller's clearance; `v1-free-text`: the pinned
 * schema keeps it as free text (TYPE_NOT_PROJECTED), so it stays in the canonical record.
 */
export type MaturityState =
  | { kind: 'typed'; date: string }
  | { kind: 'typed-missing' }
  | { kind: 'masked'; value: Schemas['MaskedValue'] }
  | { kind: 'v1-free-text' }

export function maturityState(v: Pick<InstrumentVersion, 'maturity_date' | 'maturity_typed'>): MaturityState {
  const value = v.maturity_date
  if (typeof value === 'object' && value !== null) return { kind: 'masked', value }
  if (!v.maturity_typed) return { kind: 'v1-free-text' }
  return value === null || value === '' ? { kind: 'typed-missing' } : { kind: 'typed', date: value }
}

/** What a link's source and target kinds are called (Record: kept equal to the OpenAPI enums). */
export const SOURCE_KIND_LABELS: Record<IdentitySourceKind, string> = {
  POSITION_VERSION: 'Position version',
  CASHFLOW_EVENT: 'Cash-flow event',
  CREDIT_EXPOSURE: 'Credit-exposure version',
}
export const TARGET_KIND_LABELS: Record<IdentityTargetKind, string> = {
  INSTRUMENT: 'Instrument',
  COUNTERPARTY: 'Counterparty',
  LEGAL_ENTITY: 'Legal entity',
}

export const legalEntitiesHref = '/domain/legal-entities'
export const legalEntityHref = (id: string): string => `${legalEntitiesHref}/${encodeURIComponent(id)}`

/**
 * The page of a link's target, or null when that page is not in this build (the Counterparties
 * routes are a 404 while NEXT_PUBLIC_CREDIT_DOMAIN_UI=off): the id is then shown as text.
 */
export function targetHref(kind: IdentityTargetKind, id: string, creditEnabled: boolean = CREDIT_DOMAIN_UI_ENABLED): string | null {
  if (kind === 'INSTRUMENT') return instrumentHref(id)
  if (kind === 'LEGAL_ENTITY') return legalEntityHref(id)
  return creditEnabled ? counterpartyHref(id) : null
}

/** One source row's link to a kind of identity: loading, the link (or null: unresolved), or why it failed. */
export type LinkState = { kind: 'loading' } | { kind: 'ready'; link: IdentityLink | null } | LoadFailure

/** The state of `rowId`'s link from the loaded map: a row absent from it has no link (unresolved). */
export function linkStateOf(
  links: { kind: 'loading' } | { kind: 'ready'; value: ReadonlyMap<string, IdentityLink | null> } | LoadFailure,
  rowId: string,
): LinkState {
  if (links.kind === 'ready') return { kind: 'ready', link: links.value.get(rowId) ?? null }
  return links
}

const LINK_PAGE = 200

type LinkQuery = NonNullable<operations['listIdentityLinks']['parameters']['query']>

/** Every link matching `query`, page by page (GET /identity-links, oldest first). */
export async function listAllLinks(query: Omit<LinkQuery, 'limit' | 'offset'>, signal?: AbortSignal): Promise<IdentityLink[]> {
  const out: IdentityLink[] = []
  for (let offset = 0; ; offset += LINK_PAGE) {
    const page = await call(() => api.GET('/api/v1/identity-links', { params: { query: { ...query, limit: LINK_PAGE, offset } }, signal }))
    out.push(...page.items)
    if (page.items.length === 0 || offset + page.items.length >= page.total) return out
  }
}

/**
 * The links of `rowIds` (of one source kind) to one target kind, keyed by source row id; null
 * for a row without one. With `targetId`, one paged read of the links to that identity covers
 * the common case; any row it does not cover is read on its own (its link may name another
 * identity, D-58 §5), so "no link" is always the server's answer for that row, never a guess.
 */
export async function fetchSourceLinks(
  sourceKind: IdentitySourceKind,
  targetKind: IdentityTargetKind,
  rowIds: readonly string[],
  signal?: AbortSignal,
  targetId?: string,
): Promise<Map<string, IdentityLink | null>> {
  const found = new Map<string, IdentityLink | null>()
  if (rowIds.length === 0) return found
  if (targetId)
    for (const link of await listAllLinks({ source_kind: sourceKind, target_kind: targetKind, target_id: targetId }, signal))
      found.set(link.source_row_id, link)
  const rest = rowIds.filter((id) => !found.has(id))
  const pages = await Promise.all(
    rest.map((id) =>
      call(() =>
        api.GET('/api/v1/identity-links', {
          params: { query: { source_kind: sourceKind, source_id: id, target_kind: targetKind, limit: 1 } },
          signal,
        }),
      ),
    ),
  )
  rest.forEach((id, i) => found.set(id, pages[i].items[0] ?? null))
  return new Map(rowIds.map((id) => [id, found.get(id) ?? null]))
}

/** A legal entity's link counts as sorted rows, every source kind present (zero kept). */
export function linkCountRows(counts: Record<string, number> | undefined): [IdentitySourceKind, number][] {
  return (Object.keys(SOURCE_KIND_LABELS) as IdentitySourceKind[]).map((k) => [k, counts?.[k] ?? 0])
}
