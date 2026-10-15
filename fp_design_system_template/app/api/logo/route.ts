import { NextResponse } from 'next/server'
import path from 'path'
import { promises as fs } from 'fs'

/**
 * Serves the brand logo PNG. Adapted from fp_reporting, which stores the
 * logo at <repo-root>/logo/ (one level above the Next.js app). Adjust
 * `logoPath` for wherever you choose to place the asset.
 *
 * Two safe options:
 *   1. Put the PNG in `public/brand/logo.png` and use a normal <Image
 *      src="/brand/logo.png" /> — delete this route entirely.
 *   2. Keep this route + put the PNG one level above `process.cwd()` so it
 *      lives outside the Next.js public/ tree (useful if you want to share
 *      it with other services or restrict caching headers per route).
 */
export async function GET() {
  try {
    const logoPath = path.join(process.cwd(), '..', 'logo', 'fp-logo-oraclepartner-l.png')
    const data = await fs.readFile(logoPath)
    return new NextResponse(data, {
      status: 200,
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=31536000, immutable',
      },
    })
  } catch (error) {
    return new NextResponse('Logo not found', { status: 404 })
  }
}
