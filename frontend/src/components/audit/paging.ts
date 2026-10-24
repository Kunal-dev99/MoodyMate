/**
 * Keyset paging for the audit timeline. Each entry in `cursors` is the `before` id that
 * loads one page; the list is tied to the timeline's scope (resource + filters), so a
 * cursor from one scope is never sent with another.
 */
export interface Paging {
  scope: string
  cursors: string[]
}

export function cursorsFor(paging: Paging, scope: string): string[] {
  return paging.scope === scope ? paging.cursors : []
}

/**
 * The server is asked for one row more than a page; the extra row only tells us whether
 * an older page exists, so "Older" is never offered when it would be empty.
 */
export function pageView<T>(items: T[], pageSize: number): { shown: T[]; hasOlder: boolean } {
  return { shown: items.slice(0, pageSize), hasOlder: items.length > pageSize }
}

/**
 * FE-33.1 (Q17): the timeline's continuation. The timeline keeps its keyset `before` paging,
 * which stays stable while every read appends an audit event at the top (D-10); `next_cursor`
 * (an offset) would shift rows. `has_more` says an older row exists past this page (the one-row
 * probe above: the server's own `has_more` counts against a `total` that ignores `before`), and
 * only then is Older offered, sending the last shown event's id as `before`.
 */
export function keysetNext<T extends { id: string }>(shown: readonly T[], hasOlder: boolean): { has_more: boolean; before: string | null } {
  const last = shown.at(-1)
  return hasOlder && last ? { has_more: true, before: last.id } : { has_more: false, before: null }
}
