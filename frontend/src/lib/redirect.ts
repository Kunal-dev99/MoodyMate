/**
 * Post-sign-in redirect targets (FE-2.3). Only same-origin paths are allowed. A string
 * check like "starts with / but not //" is not enough: browsers treat `\` as `/`, so
 * `/\evil.com` and `/%5Cevil.com` resolve to another origin.
 */
export const DEFAULT_LANDING = '/dashboard'

export function safeRedirectPath(raw: string | null | undefined, origin: string): string {
  if (!raw || !raw.startsWith('/')) return DEFAULT_LANDING
  let decoded = raw
  try {
    decoded = decodeURIComponent(raw)
  } catch {
    return DEFAULT_LANDING
  }
  // Reject anything a browser could read as protocol-relative, before or after decoding.
  if (/^[/\\]{2}/.test(decoded.replace(/[\t\n\r]/g, '')) || /^\/[\\]/.test(decoded)) {
    return DEFAULT_LANDING
  }
  try {
    const url = new URL(raw, origin)
    if (url.origin !== origin) return DEFAULT_LANDING
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return DEFAULT_LANDING
  }
}
