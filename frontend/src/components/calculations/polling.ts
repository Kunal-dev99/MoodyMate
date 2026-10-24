import { ApiError } from '@/api/client'
import { BACKGROUND_RUNS_UI_ENABLED } from '@/config/features'
import { retryAfterOf } from '@/lib/retry'
import type { RunDetail, RunStatus } from './calculations'

/**
 * FE-33.1: following a background run (D-59 §2). `Prefer: respond-async` answers 202 with the
 * run RUNNING; the UI then reads GET /calculation-runs/{run_id} until the server reports a
 * terminal status, waiting 0.5 s, 1 s, 2 s, 4 s and then 5 s between reads. Nothing here decides
 * an outcome: every status shown is the one the server returned, and a run is never shown as
 * succeeded before the server says so.
 */

export const POLL_FIRST_MS = 500
export const POLL_MAX_MS = 5000

/** Statuses that are not an outcome yet (QUEUED is reserved, D-31 §3, but never treated as one). */
export const PENDING_STATUSES: readonly RunStatus[] = ['RUNNING', 'QUEUED']
export const isPending = (status: RunStatus): boolean => PENDING_STATUSES.includes(status)

/**
 * Whether the UI follows a run until the server ends it: background runs on and the server's
 * status not an outcome yet. The run page then reads the run again (polling) and holds its result
 * panels (ladder, liquidity) until the run ends, instead of reading results that do not exist yet.
 * Off (NEXT_PUBLIC_BACKGROUND_RUNS_UI=off): never, exactly as in Phase 32.
 */
export const followsRun = (run: { status: RunStatus } | null, enabled: boolean = BACKGROUND_RUNS_UI_ENABLED): boolean =>
  enabled && run !== null && isPending(run.status)

/** The wait before poll `attempt` (0-based): doubling from 0.5 s, capped at 5 s. */
export function pollDelay(attempt: number): number {
  return Math.min(POLL_MAX_MS, POLL_FIRST_MS * 2 ** Math.max(0, attempt))
}

/**
 * Whether a failed read is worth repeating: the network, a 5xx, 429, or any refusal the server
 * marks retryable. A 403 or 404 is a definite answer: polling stops and the page shows it.
 */
export function isRetryableRead(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  if (err.status === 403 || err.status === 404) return false
  return err.status === 0 || err.status === 429 || err.status >= 500 || err.envelope.retryable
}

/** What the poller reports while it follows a run. */
export type PollEvent =
  | { kind: 'waiting'; ms: number; attempt: number }
  /** A read failed retryably; the next read is after `ms` (at least the server's `Retry-After`). */
  | { kind: 'retrying'; ms: number; attempt: number; error: unknown; retryAfterSeconds: number | null }
  | { kind: 'run'; run: RunDetail }
  | { kind: 'failed'; error: unknown }

export interface PollOptions {
  /** Reads the run once (GET /calculation-runs/{run_id}). */
  fetchRun: (signal: AbortSignal) => Promise<RunDetail>
  onEvent: (event: PollEvent) => void
  /** Injected in tests; resolves after `ms`, or early once `signal` aborts. */
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve()
    const timer = setTimeout(done, ms)
    function done() {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    signal.addEventListener('abort', done)
  })
}

/**
 * Follows `initial` until the server reports a terminal status, then resolves with that run.
 * Resolves with the last run read if aborted (the caller stops listening), and with null if a
 * read fails for good (reported as `failed`). A terminal `initial` is returned at once, unread.
 */
export async function pollRun(initial: RunDetail, signal: AbortSignal, { fetchRun, onEvent, sleep = defaultSleep }: PollOptions): Promise<RunDetail | null> {
  let run = initial
  let attempt = 0
  let wait = pollDelay(attempt)
  // A retry's wait is announced as 'retrying'; it is not announced again as 'waiting'.
  let announced = false
  while (isPending(run.status) && !signal.aborted) {
    if (!announced) onEvent({ kind: 'waiting', ms: wait, attempt })
    announced = false
    await sleep(wait, signal)
    if (signal.aborted) break
    attempt += 1
    try {
      const next = await fetchRun(signal)
      if (signal.aborted) break
      if (next.run_id !== run.run_id) throw new Error('The server answered with another run.')
      run = next
      onEvent({ kind: 'run', run })
      wait = pollDelay(attempt)
    } catch (error) {
      if (signal.aborted) break
      if (!isRetryableRead(error)) {
        onEvent({ kind: 'failed', error })
        return null
      }
      const retryAfterSeconds = retryAfterOf(error)
      wait = Math.max(pollDelay(attempt), (retryAfterSeconds ?? 0) * 1000)
      onEvent({ kind: 'retrying', ms: wait, attempt, error, retryAfterSeconds })
      announced = true
    }
  }
  return run
}
