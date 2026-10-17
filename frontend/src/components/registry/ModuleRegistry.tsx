'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { CalendarClock, Puzzle } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { ModuleContracts, PlannedModuleBadge } from '@/components/calculations/PlannedModuleBadge'
import { ImplementationPill } from '@/components/calculations/RunStatusPill'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { cn } from '@/lib/utils'
import { LabelBadges, VersionStatusPill } from './RegistryBadges'
import { IMPLEMENTATION_STATUSES, MODULE_GROUPS, assumptionSetHref, groupModules, ruleHref, type ModulePage, type ModuleVersion } from './registry'

const fetchModules = (signal: AbortSignal) => call(() => api.GET('/api/v1/calculation-modules', { signal }))

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

/** The rules a module version reads, each linked to its registry page with its ACTIVE version. */
function ModuleRules({ module: m }: { module: ModuleVersion }) {
  if (m.rules.length === 0) return <span className="text-muted-foreground">None</span>
  return (
    <ul className="space-y-0.5">
      {m.rules.map((r) => (
        <li key={r.rule_id} data-testid="module-rule" data-code={r.code}>
          <Link href={ruleHref(r.rule_id)} className="font-mono text-xs text-primary hover:underline">
            {r.code}
          </Link>{' '}
          {r.active_version ? (
            <span className="font-mono text-xs text-muted-foreground">v{r.active_version.version} ACTIVE</span>
          ) : (
            <span className="text-xs text-muted-foreground">no ACTIVE version</span>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * One module version. AVAILABLE ones are illustrative prototypes (D-35) and say whether they can
 * run now. PLANNED ones are declared extension points: shown as planned, never as ready, with no
 * run affordance (D-31 §1). FE-18.1: a PLANNED one carries the shared PlannedModuleBadge and its
 * declared contracts (ModuleContracts), as the New-run picker shows it.
 */
export function ModuleCard({ module: m }: { module: ModuleVersion }) {
  const planned = m.implementation_status === 'PLANNED'
  return (
    <li
      className={cn('rounded-md border px-4 py-3', planned ? 'border-dashed border-border bg-surface-2' : 'border-border')}
      data-testid="registry-module"
      data-code={m.code}
      data-status={m.implementation_status}
      data-module-version-id={m.module_version_id}
    >
      <div className="flex flex-wrap items-center gap-2">
        {planned && <CalendarClock className="h-4 w-4 text-muted-foreground" aria-hidden="true" />}
        <span className="font-medium">{m.name}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {m.code} v{m.version}
        </span>
        {planned ? <PlannedModuleBadge /> : <ImplementationPill status={m.implementation_status} />}
        <LabelBadges illustrative={m.illustrative} provisional={m.provisional} />
      </div>
      {m.description && <p className="mt-1 text-sm text-muted-foreground">{m.description}</p>}
      {planned ? (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="module-planned-note">
          Planned extension point: declared so runs and reports can name it, not implemented in this release. It cannot be selected for a run and is
          not production-ready.
        </p>
      ) : (
        <>
          <p className="mt-1 text-xs text-[hsl(var(--warning))]" data-testid="module-statement">
            {m.statement}
          </p>
          <p className={cn('mt-1 text-xs', m.runnable ? 'text-muted-foreground' : 'text-destructive')} data-testid="module-runnable" data-runnable={m.runnable}>
            {m.runnable
              ? 'Runnable now: every rule it reads has an ACTIVE version and a baseline assumption set is ACTIVE.'
              : 'Not runnable now: a rule it reads has no ACTIVE version or no baseline assumption set is ACTIVE (the server answers 409 module_not_available).'}
          </p>
        </>
      )}
      {planned && <ModuleContracts module={m} className="mt-3" />}
      <dl className="mt-3 grid grid-cols-[160px_1fr] gap-y-1 text-sm">
        {!planned && (
          <>
            <Row label="Engine">
              <span className="font-mono text-xs">{m.engine_version}</span>
            </Row>
            <Row label="Input schemas">
              {m.input_contract.schemas.length === 0 ? (
                <span className="text-muted-foreground">None declared</span>
              ) : (
                <span className="font-mono text-xs">
                  {m.input_contract.schemas.map((s) => `${s.schema_code}${s.required ? '' : ' (optional)'}`).join(', ')}
                </span>
              )}
            </Row>
          </>
        )}
        <Row label="Rules">
          <ModuleRules module={m} />
        </Row>
        {m.assumption_set && (
          <Row label="Assumption set">
            <Link href={assumptionSetHref(m.assumption_set.assumption_set_id)} className="font-mono text-xs text-primary hover:underline">
              {m.assumption_set.code} v{m.assumption_set.version}
            </Link>{' '}
            <VersionStatusPill status={m.assumption_set.status} testId="module-assumption-status" />
          </Row>
        )}
        <Row label="Module version ID">
          <span className="break-all font-mono text-xs">{m.module_version_id}</span>
        </Row>
      </dl>
    </li>
  )
}

/**
 * FE-16.1: the module registry (GET /calculation-modules), AVAILABLE and PLANNED versions in
 * separate groups so a planned extension point is never read as ready. Read-only.
 */
export function ModuleRegistry() {
  const [state, setState] = useState<Loaded<ModulePage>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchModules, setState, 'Could not load calculation modules'), [])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  if (state.kind === 'loading') return <LoadingBlock rows={4} />
  if (state.kind === 'forbidden') return <Forbidden permission="rule:read" correlationId={state.cid} />
  if (state.kind === 'failed') return <LoadFailed icon={Puzzle} title="Modules could not be loaded" cid={state.cid} onRetry={loader.load} />
  if (state.kind === 'not_found')
    return <EmptyState icon={Puzzle} title="Modules could not be listed" description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`} />

  const groups = groupModules(state.value.items)
  return (
    <div className="space-y-4">
      {IMPLEMENTATION_STATUSES.map((status) => (
        <PageSection
          key={status}
          icon={status === 'PLANNED' ? CalendarClock : Puzzle}
          title={`${MODULE_GROUPS[status].title} (${groups[status].length})`}
          description={MODULE_GROUPS[status].description}
        >
          {groups[status].length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid={`modules-none-${status}`}>
              {status === 'PLANNED' ? 'No planned module version is declared yet.' : 'No module version is available.'}
            </p>
          ) : (
            <ul className="space-y-3" data-testid={`modules-${status}`}>
              {groups[status].map((m) => (
                <ModuleCard key={m.module_version_id} module={m} />
              ))}
            </ul>
          )}
        </PageSection>
      ))}
    </div>
  )
}
