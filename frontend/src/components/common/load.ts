import { ApiError } from '@/api/client'
import { toastApiError } from '@/components/common/api-error-toast'

/** Why a page-level load did not produce data. */
type Failure<K extends string> = { kind: K; status: number | null; cid: string | null }
export type LoadFailure = Failure<'forbidden'> | Failure<'not_found'> | Failure<'failed'>

/**
 * Maps a load error to what the page shows. A 403 is a refusal and another client error
 * (404, 422) a definite answer that retrying will not change; anything else (an unexpected
 * throw, the network, a 5xx, a retryable envelope) can be retried.
 */
export function loadFailure(err: unknown): LoadFailure {
  if (!(err instanceof ApiError)) return { kind: 'failed', status: null, cid: null }
  const retryable = err.status === 0 || err.status >= 500 || err.envelope.retryable
  const at = { status: err.status, cid: err.correlationId }
  if (err.status === 403) return { kind: 'forbidden', ...at }
  return retryable ? { kind: 'failed', ...at } : { kind: 'not_found', ...at }
}

/**
 * Starts a cancellable load. A previous request is aborted first; an aborted request
 * never reports back, so a late answer cannot land.
 */
export function startLoad<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  handlers: { onReady: (value: T) => void; onFailure: (failure: LoadFailure) => void; failureTitle: string },
  previous?: AbortController | null,
): AbortController {
  previous?.abort()
  const controller = new AbortController()
  fetcher(controller.signal).then(
    (value) => {
      if (!controller.signal.aborted) handlers.onReady(value)
    },
    (err: unknown) => {
      if (controller.signal.aborted) return
      const failure = loadFailure(err)
      // Anything but a definite refusal or absence (network, 5xx, an unexpected throw)
      // is retryable: the caller shows an error state with "Try again".
      if (failure.kind === 'failed') toastApiError(err, handlers.failureTitle)
      handlers.onFailure(failure)
    },
  )
  return controller
}

/** A page-level load's state: loading, the value, or why it failed. */
export type Loaded<T> = { kind: 'loading' } | { kind: 'ready'; value: T } | LoadFailure

/**
 * `startLoad` with its state transitions and retry, outside React (FE-14.3). `load()` (also
 * the "Try again" action) shows loading and starts the fetch again, cancelling the previous
 * attempt; `abort()` cancels it on unmount. Pages hold one in `useMemo`; tests drive the
 * same transitions without a DOM.
 */
export function createLoader<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  setState: (state: Loaded<T>) => void,
  failureTitle: string,
): { load: () => void; abort: () => void } {
  let request: AbortController | null = null
  return {
    load() {
      setState({ kind: 'loading' })
      request = startLoad(fetcher, { onReady: (value) => setState({ kind: 'ready', value }), onFailure: setState, failureTitle }, request)
    },
    abort() {
      request?.abort()
    },
  }
}
