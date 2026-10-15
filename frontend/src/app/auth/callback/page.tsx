'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ShieldAlert } from 'lucide-react'
import { api, call } from '@/api/client'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { oidcManager, type SignInState } from '@/lib/oidc'
import { useSession } from '@/lib/session'

/** Completes the OIDC redirect (FE-2.1): exchanges the code, stores the access token. */
export default function AuthCallbackPage() {
  const router = useRouter()
  const setToken = useSession((s) => s.setToken)
  const [failed, setFailed] = useState<string | null>(null)
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return // React strict mode runs effects twice; the code is single-use
    ran.current = true
    ;(async () => {
      const config = await call(() => api.GET('/api/v1/auth/config'))
      if (!config.oidc) throw new Error('The platform is not configured for SSO.')
      const user = await oidcManager(config.oidc).signinRedirectCallback()
      setToken(user.access_token, 'oidc')
      const state = user.state as SignInState | undefined
      router.replace(state?.next ?? '/dashboard')
    })().catch((err: unknown) => setFailed(err instanceof Error ? err.message : 'Sign-in failed.'))
  }, [router, setToken])

  return (
    <div className="mx-auto mt-24 max-w-md px-6">
      {failed ? (
        <EmptyState icon={ShieldAlert} title="Sign-in failed" description={failed} />
      ) : (
        <LoadingBlock rows={3} />
      )}
    </div>
  )
}
