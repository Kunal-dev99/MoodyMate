'use client'

import Link from 'next/link'
import { KeyRound, ShieldCheck, UserRound, Users } from 'lucide-react'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-2 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm">{children}</span>
    </div>
  )
}

export default function AccessPage() {
  const { me, permissions, authConfig } = useCurrentSession()
  return (
    <>
      <PageHeader title="Access & Roles" description="Your identity and what the platform lets you do." />
      <div className="px-6 pb-6 space-y-4">
        <PageSection icon={UserRound} title="Identity" accent="primary">
          <Row label="Name">{me.display_name}</Row>
          <Row label="Email">{me.email ?? '—'}</Row>
          <Row label="Sign-in mode">
            <Badge variant={authConfig.mode === 'dev' ? 'warning' : 'secondary'}>{authConfig.mode}</Badge>
          </Row>
          <Row label="Tenant">
            <span className="font-mono text-xs">{me.tenant_id}</span>
          </Row>
        </PageSection>

        <PageSection icon={ShieldCheck} title="Roles">
          <div className="flex flex-wrap gap-2">
            {me.roles.length ? (
              me.roles.map((r) => (
                <Badge key={r} variant="info" className="font-mono">
                  {r}
                </Badge>
              ))
            ) : (
              <span className="text-helper">No roles assigned. Ask a platform administrator.</span>
            )}
          </div>
        </PageSection>

        <PageSection icon={KeyRound} title="Effective permissions" description="Enforced by the server on every request.">
          <div className="flex flex-wrap gap-2" data-testid="my-permissions">
            {permissions.size ? (
              [...permissions].sort().map((p) => (
                <Badge key={p} variant="secondary" className="font-mono">
                  {p}
                </Badge>
              ))
            ) : (
              <span className="text-helper">None.</span>
            )}
          </div>
        </PageSection>

        <RequirePermission permission="admin:read" fallback={null}>
          <PageSection
            icon={Users}
            title="Users"
            description="Read-only. Role assignment arrives with the platform admin tooling."
            actions={
              <Button asChild variant="outline" size="sm">
                <Link href="/access/users">Open users</Link>
              </Button>
            }
          >
            <p className="text-helper">Everyone in your tenant and the roles they hold.</p>
          </PageSection>
        </RequirePermission>
      </div>
    </>
  )
}
