/**
 * FE-29.1: the one `Idempotency-Key` source for every POST that takes one (D-21 §2: imports,
 * calculation runs, scenario runs). A fresh key per user attempt; a retry of the same attempt
 * reuses it, so the server can answer the replay with the original result.
 */

/** A fresh `Idempotency-Key` for one attempt (1–255 visible ASCII). */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `fe-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
