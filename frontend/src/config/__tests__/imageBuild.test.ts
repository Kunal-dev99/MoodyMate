import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it } from 'vitest'
import nextConfig from '../../../next.config.mjs'
import { PRODUCTION_BUILD_PHASE } from '../buildEnv.mjs'

// FE-34.1: the standalone build and the image that packages it. The image build itself runs in
// CI (scripts/check-image-build-arg.sh); these cases pin the files that build depends on.
const FRONTEND = fileURLToPath(new URL('../../../', import.meta.url))
const read = (name: string) => readFileSync(`${FRONTEND}${name}`, 'utf8')

describe('standalone output (FE-34.1)', () => {
  const saved = process.env.NEXT_OUTPUT_STANDALONE
  afterEach(() => {
    if (saved === undefined) delete process.env.NEXT_OUTPUT_STANDALONE
    else process.env.NEXT_OUTPUT_STANDALONE = saved
  })

  it("builds output: 'standalone', traced from the frontend folder", () => {
    delete process.env.NEXT_OUTPUT_STANDALONE
    const config = nextConfig(PRODUCTION_BUILD_PHASE)
    expect(config.output).toBe('standalone')
    expect(config.outputFileTracingRoot?.replace(/\/$/, '')).toBe(FRONTEND.replace(/\/$/, ''))
  })
  it('NEXT_OUTPUT_STANDALONE=off restores the previous config exactly (D-02)', () => {
    delete process.env.NEXT_OUTPUT_STANDALONE
    const on = nextConfig(PRODUCTION_BUILD_PHASE)
    process.env.NEXT_OUTPUT_STANDALONE = 'off'
    const off = nextConfig(PRODUCTION_BUILD_PHASE)
    expect(off).not.toHaveProperty('output')
    expect(off).not.toHaveProperty('outputFileTracingRoot')
    const rest = Object.keys(on).filter((k) => k !== 'output' && k !== 'outputFileTracingRoot')
    expect(Object.keys(off).sort()).toEqual(rest.sort())
  })
})

describe('frontend image (FE-34.1)', () => {
  const dockerfile = read('Dockerfile')
  const stages = dockerfile.split(/^FROM /m).slice(1)
  const stage = (name: string) => stages.find((s) => s.split('\n')[0].includes(`AS ${name}`)) ?? ''

  it('requires NEXT_PUBLIC_API_BASE_URL as a build arg with no default, checked before the build', () => {
    const build = stage('build')
    expect(build).toMatch(/^ARG NEXT_PUBLIC_API_BASE_URL$/m)
    expect(dockerfile).not.toMatch(/ARG NEXT_PUBLIC_API_BASE_URL=/)
    const check = build.indexOf('build arg NEXT_PUBLIC_API_BASE_URL is required')
    expect(check).toBeGreaterThan(-1)
    expect(build.indexOf('exit 64')).toBeGreaterThan(check)
    expect(build.indexOf('npm run build')).toBeGreaterThan(check)
    // The CI step greps for the same message, so a failure for any other reason does not pass.
    expect(read('scripts/check-image-build-arg.sh')).toContain('build arg NEXT_PUBLIC_API_BASE_URL is required')
  })
  it('installs from the lockfile and runs the standalone server as a non-root user', () => {
    expect(stage('deps')).toMatch(/COPY package\.json package-lock\.json/)
    expect(stage('deps')).toMatch(/RUN npm ci\b/)
    const runner = stage('runner')
    expect(runner).toContain('COPY --from=build /app/.next/standalone ./')
    expect(runner).toContain('COPY --from=build /app/.next/static ./.next/static')
    const users = [...runner.matchAll(/^USER (\S+)/gm)].map((m) => m[1])
    expect(users).toEqual(['1000:1000'])
    expect(runner.indexOf('USER 1000:1000')).toBeLessThan(runner.indexOf('CMD ["node", "server.js"]'))
  })
  it('passes no secret into the image: only public build args, env files excluded from the context', () => {
    const args = [...dockerfile.matchAll(/^ARG (\w+)/gm)].map((m) => m[1]).sort()
    expect(args).toEqual(['NEXT_PUBLIC_API_BASE_URL', 'NODE_IMAGE', 'OIDC_ISSUER'])
    const ignored = read('.dockerignore').split('\n').map((l) => l.trim())
    for (const entry of ['.env', '.env.*', 'node_modules', '.next']) expect(ignored).toContain(entry)
  })
})
