'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { Calculator, FlaskConical, Landmark, Users, type LucideIcon } from 'lucide-react'
import { api, call } from '@/api/client'
import { useCurrentSession, type Permission } from '@/components/auth/SessionProvider'
import { explorerHref } from '@/components/advanced/scenarios'
import { Skeleton } from '@/components/ui/skeleton'
import { groupDecimal } from '@/lib/format'
import {
  CALCULATIONS_UI_ENABLED,
  CREDIT_DOMAIN_UI_ENABLED,
  DOMAIN_UI_ENABLED,
  STRESS_EXPLORER_UI_ENABLED,
} from '@/config/features'

/**
 * Overview count tiles (P-13). Each tile reads the `total` of an existing list endpoint with
 * `limit=1`, typed from the generated OpenAPI client. A tile shows only when the user holds its
 * read permission (cosmetic, D-04); a 403, any other error, or a switched-off workspace hides
 * it. Counts are the server's, never computed here.
 */

type Tile = {
  key: string
  label: string
  icon: LucideIcon
  href: string
  permission: Permission
  enabled: boolean
  load: (signal: AbortSignal) => Promise<number>
}

const TILES: Tile[] = [
  {
    key: 'instruments',
    label: 'Instruments',
    icon: Landmark,
    href: '/domain',
    permission: 'domain:read',
    enabled: DOMAIN_UI_ENABLED,
    load: (signal) => call(() => api.GET('/api/v1/instruments', { params: { query: { limit: 1 } }, signal })).then((p) => p.total),
  },
  {
    key: 'counterparties',
    label: 'Counterparties',
    icon: Users,
    href: '/domain/counterparties',
    permission: 'domain:read',
    enabled: DOMAIN_UI_ENABLED && CREDIT_DOMAIN_UI_ENABLED,
    load: (signal) => call(() => api.GET('/api/v1/counterparties', { params: { query: { limit: 1 } }, signal })).then((p) => p.total),
  },
  {
    key: 'runs',
    label: 'Calculation runs',
    icon: Calculator,
    href: '/calculations',
    permission: 'calc:read',
    enabled: CALCULATIONS_UI_ENABLED,
    load: (signal) => call(() => api.GET('/api/v1/calculation-runs', { params: { query: { limit: 1 } }, signal })).then((p) => p.total),
  },
  {
    key: 'scenarios',
    label: 'Approved stress scenarios',
    icon: FlaskConical,
    href: explorerHref,
    permission: 'calc:read',
    enabled: STRESS_EXPLORER_UI_ENABLED,
    // No status filter: the server's default is APPROVED_FOR_TEST only (the shared library).
    load: (signal) => call(() => api.GET('/api/v1/scenarios', { params: { query: { limit: 1 } }, signal })).then((p) => p.total),
  },
]

type TileState = { kind: 'loading' } | { kind: 'ready'; total: number } | { kind: 'hidden' }

function CountTile({ tile }: { tile: Tile }) {
  const [state, setState] = useState<TileState>({ kind: 'loading' })
  useEffect(() => {
    const controller = new AbortController()
    tile
      .load(controller.signal)
      .then((total) => !controller.signal.aborted && setState({ kind: 'ready', total }))
      // 403, 404, 5xx or network: the tile disappears rather than showing a guess.
      .catch(() => !controller.signal.aborted && setState({ kind: 'hidden' }))
    return () => controller.abort()
  }, [tile])

  if (state.kind === 'hidden') return null
  const Icon = tile.icon
  return (
    <Link
      href={tile.href}
      className="rounded-md border border-border bg-surface-2 px-4 py-3 transition-colors hover:border-primary/50"
      data-testid="count-tile"
      data-tile={tile.key}
    >
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {tile.label}
      </div>
      {state.kind === 'loading' ? (
        <Skeleton className="mt-2 h-7 w-16" />
      ) : (
        <div className="mt-1 font-mono text-2xl font-semibold" data-testid="count-tile-total">
          {groupDecimal(String(state.total))}
        </div>
      )}
    </Link>
  )
}

export function CountTiles() {
  const { has } = useCurrentSession()
  const visible = TILES.filter((t) => t.enabled && has(t.permission))
  if (visible.length === 0) return null
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="count-tiles">
      {visible.map((t) => (
        <CountTile key={t.key} tile={t} />
      ))}
    </div>
  )
}
