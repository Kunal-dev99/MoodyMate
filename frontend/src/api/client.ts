/**
 * Typed API client (FE-1.3).
 *
 * Types come only from `schema.d.ts`, generated from backend/openapi.json by
 * `npm run gen:api`. Never hand-write a type that mirrors an API payload.
 */
import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

export type ErrorEnvelope = components['schemas']['ErrorEnvelope']
export type Schemas = components['schemas']

const CORRELATION_HEADER = 'X-Correlation-ID'

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'

function newCorrelationId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `fe-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const correlation: Middleware = {
  onRequest({ request }) {
    if (!request.headers.has(CORRELATION_HEADER)) {
      request.headers.set(CORRELATION_HEADER, newCorrelationId())
    }
    return request
  },
}

export const api = createClient<paths>({ baseUrl: API_BASE_URL })
api.use(correlation)

/** An error from the backend, always carrying the standard envelope (D-07). */
export class ApiError extends Error {
  readonly status: number
  readonly envelope: ErrorEnvelope

  constructor(status: number, envelope: ErrorEnvelope) {
    super(envelope.message)
    this.name = 'ApiError'
    this.status = status
    this.envelope = envelope
  }

  get correlationId(): string | null {
    return this.envelope.correlation_id ?? null
  }
}

function isEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { code?: unknown }).code === 'string' &&
    typeof (value as { message?: unknown }).message === 'string'
  )
}

/**
 * Unwraps an openapi-fetch result: returns `data` or throws an `ApiError`.
 * Non-envelope failures (proxy errors, network) are normalised into an envelope
 * that keeps the response's correlation ID when there is one.
 */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  const { data, error, response } = result
  if (response.ok && data !== undefined) return data
  const envelope: ErrorEnvelope = isEnvelope(error)
    ? error
    : {
        code: 'unexpected_response',
        message: `Unexpected response from the server (HTTP ${response.status}).`,
        details: null,
        correlation_id: response.headers.get(CORRELATION_HEADER),
        retryable: response.status >= 500,
      }
  throw new ApiError(response.status, envelope)
}

/** Wraps fetch failures (backend down, CORS) as an ApiError so callers handle one type. */
export async function call<T>(
  request: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response }
  try {
    result = await request()
  } catch {
    throw new ApiError(0, {
      code: 'network_error',
      message: 'The platform API could not be reached.',
      details: null,
      correlation_id: null,
      retryable: true,
    })
  }
  return unwrap(result)
}
