import { beforeEach, describe, expect, it } from 'vitest'
import { authMiddleware } from '@/api/client'
import { useSession } from '@/lib/session'

type OnResponse = NonNullable<typeof authMiddleware.onResponse>

function respond(sentToken: string | null, status: number) {
  const headers = new Headers()
  if (sentToken) headers.set('Authorization', `Bearer ${sentToken}`)
  const request = new Request('http://api.test/api/v1/me', { headers })
  const response = new Response(null, { status })
  return (authMiddleware.onResponse as OnResponse)({ request, response } as Parameters<OnResponse>[0])
}

describe('auth middleware 401 handling (FE-2.4)', () => {
  beforeEach(() => useSession.getState().clear())

  it('clears the session when the 401 answers the current token', async () => {
    useSession.getState().setToken('current', 'dev')
    await respond('current', 401)
    expect(useSession.getState().token).toBeNull()
  })

  it('keeps a newer token when a late 401 answers an old one', async () => {
    useSession.getState().setToken('renewed', 'oidc')
    await respond('stale', 401)
    expect(useSession.getState().token).toBe('renewed')
  })

  it('ignores 401s for requests sent without a token', async () => {
    useSession.getState().setToken('current', 'dev')
    await respond(null, 401)
    expect(useSession.getState().token).toBe('current')
  })

  it('ignores other statuses', async () => {
    useSession.getState().setToken('current', 'dev')
    await respond('current', 403)
    expect(useSession.getState().token).toBe('current')
  })

  it('attaches the current token to outgoing requests', async () => {
    useSession.getState().setToken('abc', 'dev')
    const onRequest = authMiddleware.onRequest as NonNullable<typeof authMiddleware.onRequest>
    const out = (await onRequest({ request: new Request('http://api.test/x') } as Parameters<typeof onRequest>[0])) as Request
    expect(out.headers.get('Authorization')).toBe('Bearer abc')
  })
})
