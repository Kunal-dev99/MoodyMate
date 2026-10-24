/**
 * Typed API client (FE-1.3).
 *
 * Types come only from `schema.d.ts`, generated from backend/openapi.json by
 * `npm run gen:api`. Never hand-write a type that mirrors an API payload.
 */
import createClient, { type Middleware } from 'openapi-fetch'
import { useSession } from '@/lib/session'
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

/**
 * Attaches the bearer token. A 401 ends the session so the app returns to sign-in, but
 * only when it answers the token that is still current (FE-2.4): a slow request sent
 * with an old token must not sign out a session the user has just renewed.
 */
export const authMiddleware: Middleware = {
  onRequest({ request }) {
    const { token } = useSession.getState()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  onResponse({ request, response }) {
    if (response.status !== 401) return response
    const sent = request.headers.get('Authorization')
    const { token, clear } = useSession.getState()
    if (token && sent === `Bearer ${token}`) clear()
    return response
  },
}

export const api = createClient<paths>({ baseUrl: API_BASE_URL })
api.use(correlation)
api.use(authMiddleware)

/** An error from the backend, always carrying the standard envelope (D-07). */
export class ApiError extends Error {
  readonly status: number
  readonly envelope: ErrorEnvelope
  /**
   * FE-33.1: the response's `Retry-After` in whole seconds (429 `rate_limited`, 503
   * `workers_busy` / `db_pool_exhausted`), or null when it sent none. The backend exposes the
   * header to the browser (CORS `expose_headers`).
   */
  readonly retryAfterSeconds: number | null

  constructor(status: number, envelope: ErrorEnvelope, retryAfterSeconds: number | null = null) {
    super(envelope.message)
    this.name = 'ApiError'
    this.status = status
    this.envelope = envelope
    this.retryAfterSeconds = retryAfterSeconds
  }

  get correlationId(): string | null {
    return this.envelope.correlation_id ?? null
  }
}

export const RETRY_AFTER_HEADER = 'Retry-After'

/**
 * `Retry-After` as whole seconds (RFC 9110: delay-seconds or an HTTP date); null when absent
 * or unreadable. A date in the past is 0.
 */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (value === null) return null
  const text = value.trim()
  if (/^\d+$/.test(text)) return Number(text)
  const at = Date.parse(text)
  if (Number.isNaN(at) || !/[a-z]/i.test(text)) return null
  return Math.max(0, Math.ceil((at - now) / 1000))
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
  throw new ApiError(response.status, envelope, parseRetryAfter(response.headers.get(RETRY_AFTER_HEADER)))
}

/**
 * Like `call`, but also returns the response, for callers that read its status or headers
 * (e.g. 200 versus 201 on an idempotent replay, or `X-Data-Class`).
 */
export async function callWithResponse<T>(
  request: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<{ data: T; response: Response }> {
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
  return { data: unwrap(result), response: result.response }
}

/** Wraps fetch failures (backend down, CORS) as an ApiError so callers handle one type. */
export async function call<T>(
  request: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  return (await callWithResponse(request)).data
}

/** The environment's data class, which the backend sends on every response (BE-6.1). */
export const DATA_CLASS_HEADER = 'X-Data-Class'
export type DataClass = Schemas['DataClass']

/** `X-Data-Class` from a response; null when it is missing or not a known class. */
export function dataClassOf(response: Response): DataClass | null {
  const value = response.headers.get(DATA_CLASS_HEADER)
  return value === 'synthetic' || value === 'client' ? value : null
}

/**
 * A `multipart/form-data` request body as generated from the OpenAPI schema.
 * openapi-typescript types `format: binary` fields as `string`; at runtime they carry a
 * Blob or File, so string fields also accept a Blob here.
 */
export type MultipartFields<T> = { [K in keyof T]: T[K] extends string | undefined ? T[K] | Blob : T[K] }

/**
 * Serialises multipart fields: a Blob is sent as a file part (a File keeps its name),
 * numbers and booleans as their text, and undefined or null fields are left out.
 */
export function toFormData(fields: Record<string, unknown>): FormData {
  const form = new FormData()
  for (const [name, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue
    if (value instanceof Blob) form.append(name, value, value instanceof File ? value.name : name)
    else if (typeof value === 'string') form.append(name, value)
    else if (typeof value === 'number' || typeof value === 'boolean') form.append(name, String(value))
    else throw new TypeError(`Multipart field ${name} must be text, a number, a boolean or a file.`)
  }
  return form
}

/**
 * The `body` and `bodySerializer` options for a multipart operation of the generated
 * client, e.g. `api.POST('/api/v1/imports', { params, ...multipart<Body>(fields) })`.
 * The body keeps the generated type; the serializer sends it as FormData, so the browser
 * sets the multipart Content-Type with its boundary.
 */
export function multipart<T extends object>(fields: MultipartFields<T>): { body: T; bodySerializer: () => FormData } {
  return { body: fields as unknown as T, bodySerializer: () => toFormData(fields as Record<string, unknown>) }
}
