import { NextResponse, type NextRequest } from 'next/server'

/**
 * FE-35.6: a segment under /advanced/stress-explorer/ that cannot be a scenario id answers HTTP
 * 404 before anything renders, so a retired explorer route is a real 404. The [scenarioId]
 * layout's notFound() shows the not-found page but cannot set the status: the (platform) layout's
 * SessionProvider renders only its loading block on the server, so the server-side HTML render
 * never reaches that segment and the shell goes out as 200. The rewrite serves Next's own
 * not-found page with a 404 status; the next.config.mjs security headers still apply.
 * Cosmetic, like the layout's check: the API refuses a malformed scenario id itself (D-04).
 */

/** The [scenarioId] layout's pattern (a vitest keeps the two equal): `format: uuid`, any case (Q19). */
export const SCENARIO_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** The explorer's static routes beside [scenarioId], which Next matches first (a vitest lists them from app/). */
export const STATIC_SEGMENTS: ReadonlySet<string> = new Set(['new'])

/**
 * Next's own 404 for an unmatched path is never cached. The prerendered not-found page would
 * otherwise go out with a one-year s-maxage, so a shared cache could keep this 404 that long.
 */
export const NOT_FOUND_CACHE_CONTROL = 'private, no-cache, no-store, max-age=0, must-revalidate'

export function middleware(request: NextRequest) {
  // '/advanced/stress-explorer/<segment>/…' splits to ['', 'advanced', 'stress-explorer', '<segment>', …].
  const segment = request.nextUrl.pathname.split('/')[3] ?? ''
  if (STATIC_SEGMENTS.has(segment) || SCENARIO_ID.test(segment)) return NextResponse.next()
  const response = NextResponse.rewrite(new URL('/_not-found', request.url))
  response.headers.set('Cache-Control', NOT_FOUND_CACHE_CONTROL)
  return response
}

// Only the explorer's sub-paths; /advanced/stress-explorer itself and every other route never run it.
export const config = { matcher: '/advanced/stress-explorer/:segment/:path*' }
