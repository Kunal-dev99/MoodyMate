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
