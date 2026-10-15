/**
 * Post-sign-in redirect targets (FE-2.3). Only same-origin paths are allowed.
 *
 * The check runs on the path the browser will actually use, AFTER URL normalisation:
 * dot segments are resolved (`/.//evil` → `//evil`) and `\` counts as `/`. Checking the
 * raw string is not enough. A result that starts with two slashes (or slash-backslash) is
 * protocol-relative and leaves the site, so it is rejected.
 */
export const DEFAULT_LANDING = '/dashboard'

const PROTOCOL_RELATIVE = /^[/\\][/\\]/

export function safeRedirectPath(raw: string | null | undefined, origin: string): string {
  if (!raw || !raw.startsWith('/')) return DEFAULT_LANDING
  // Encoded separators (%2F, %5C) are not decoded by the URL parser, but other layers
  // might decode them. Reject them if decoding is possible; a bare `%` is not an attack.
  let decoded = raw
  try {
    decoded = decodeURIComponent(raw)
  } catch {
    // keep the raw value
  }
  const compact = (s: string) => s.replace(/[\t\n\r]/g, '')
  if (PROTOCOL_RELATIVE.test(compact(raw)) || PROTOCOL_RELATIVE.test(compact(decoded))) {
    return DEFAULT_LANDING
  }
  let url: URL
  try {
    url = new URL(raw, origin)
  } catch {
    return DEFAULT_LANDING
  }
  if (url.origin !== origin) return DEFAULT_LANDING
  // Re-check the normalised result, and the result after one decode, since that is
  // what router.replace() and the browser will see.
  const result = `${url.pathname}${url.search}${url.hash}`
  let resultDecoded = result
  try {
    resultDecoded = decodeURIComponent(url.pathname)
  } catch {
    // keep
  }
  if (PROTOCOL_RELATIVE.test(result) || PROTOCOL_RELATIVE.test(resultDecoded)) {
    return DEFAULT_LANDING
  }
  if (new URL(result, origin).origin !== origin) return DEFAULT_LANDING
  return result
}
