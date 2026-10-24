import { fileURLToPath } from 'node:url'
import { DEV_API_BASE_URL, resolveApiBaseUrl, securityHeaders } from './src/config/buildEnv.mjs'

// FE-34.1: trace from this folder, so the standalone server sits at <distDir>/standalone/server.js
// whatever lockfiles exist above it (the Docker build context is frontend/ only).
const FRONTEND_ROOT = fileURLToPath(new URL('.', import.meta.url))

/**
 * @param {string} phase Next's config phase (dev server, production build, production server)
 * @returns {import('next').NextConfig}
 */
export default function nextConfig(phase) {
  return {
    // Parallel agents build into separate folders (CLAUDE.md step 3); unset keeps `.next`.
    distDir: process.env.NEXT_DIST_DIR || '.next',
    reactStrictMode: true,
    poweredByHeader: false,
    // FE-34.1: a self-contained server for the Docker image (frontend/Dockerfile). `next start`
    // still serves the build (it logs a warning). NEXT_OUTPUT_STANDALONE=off at build time
    // restores the pre-Phase-34 config exactly (D-02).
    ...(process.env.NEXT_OUTPUT_STANDALONE === 'off'
      ? {}
      : { output: 'standalone', outputFileTracingRoot: FRONTEND_ROOT }),
    // FE-28.3: fails `next build` (NODE_ENV=production) when NEXT_PUBLIC_API_BASE_URL is unset.
    // Run here, not at config load: `next lint` loads this file in the same build phase but
    // never asks for a build ID. Returning null keeps Next's default build ID.
    async generateBuildId() {
      resolveApiBaseUrl({ phase, env: process.env })
      return null
    },
    // FE-28.5: security headers, CSP Report-Only. NEXT_SECURITY_HEADERS=off removes them (D-02).
    // OIDC_ISSUER (build time, same value as the backend's) adds the issuer to connect-src and
    // frame-src for AUTH_MODE=oidc (D-54 section 3); unset leaves the policy unchanged.
    async headers() {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || DEV_API_BASE_URL
      const headers = securityHeaders({
        apiBaseUrl,
        oidcIssuer: process.env.OIDC_ISSUER,
        dev: phase === 'phase-development-server',
        env: process.env,
      })
      return headers.length ? [{ source: '/:path*', headers }] : []
    },
  }
}
