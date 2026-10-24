// Types for next.config.mjs, so the FE-28.3/28.5 vitest cases can load the real config.
import type { NextConfig } from 'next'
declare function nextConfig(phase: string): NextConfig
export default nextConfig
