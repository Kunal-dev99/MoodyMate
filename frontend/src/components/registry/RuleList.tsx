'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { LabelBadges } from './RegistryBadges'
import { CATEGORY_TEXT, parameterKind, ruleHref, type RulePage } from './registry'

const fetchRules = (signal: AbortSignal) => call(() => api.GET('/api/v1/rules', { signal }))

/**
 * FE-16.1: the rule registry (GET /rules): every rule definition of the caller's tenant, by
 * code, with its parameter kind and the version a run submitted now pins. Read-only.
 */
export function RuleList() {
  const [state, setState] = useState<Loaded<RulePage>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(fetchRules, setState, 'Could not load the rules'), [])

  useEffect(() => {
    loader.load()
    return () => loader.abort()
  }, [loader])

  let body
  if (state.kind === 'loading') body = <LoadingBlock rows={4} />
  else if (state.kind === 'forbidden') body = <Forbidden permission="rule:read" correlationId={state.cid} />
  else if (state.kind === 'failed') body = <LoadFailed icon={SlidersHorizontal} title="The rules could not be loaded" cid={state.cid} onRetry={loader.load} />
  else if (state.kind === 'not_found')
    body = (
      <EmptyState
        icon={SlidersHorizontal}
        title="The rules could not be listed"
        description={`The request was refused.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  else if (state.value.items.length === 0)
    body = <EmptyState icon={SlidersHorizontal} title="No rule is registered" description="Rules are seeded by the platform." />
  else
    body = (
      <Table data-testid="rule-list">
        <TableHeader>
          <TableRow>
            <TableHead>Rule</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Parameter kind</TableHead>
            <TableHead>Module</TableHead>
            <TableHead>Active version</TableHead>
            <TableHead className="text-right">Versions</TableHead>
            <TableHead>Owner</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.value.items.map((r) => (
            <TableRow key={r.rule_id} data-testid="rule-row" data-code={r.code} data-rule-id={r.rule_id}>
              <TableCell>
                <Link href={ruleHref(r.rule_id)} className="font-medium text-primary hover:underline" data-testid="rule-link">
                  {r.name}
                </Link>
                <div className="font-mono text-xs text-muted-foreground">{r.code}</div>
                <LabelBadges illustrative={r.illustrative} provisional={r.provisional} className="mt-1" />
              </TableCell>
              <TableCell className="text-sm">{CATEGORY_TEXT[r.category]}</TableCell>
              <TableCell className="font-mono text-xs">{parameterKind(r.parameter_schema) ?? '—'}</TableCell>
              <TableCell className="font-mono text-xs">{r.target_module.code}</TableCell>
              <TableCell data-testid="rule-active-version">
                {r.active_version === null ? (
                  <span className="text-xs text-destructive">No ACTIVE version: a module reading it cannot run</span>
                ) : (
                  <span className="font-mono text-sm">v{r.active_version}</span>
                )}
              </TableCell>
              <TableCell className="text-right font-mono text-sm" data-testid="rule-version-count">
                {r.version_count}
              </TableCell>
              <TableCell className="text-xs">{r.owner}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )

  return (
    <PageSection
      icon={SlidersHorizontal}
      title={state.kind === 'ready' ? `Rules (${state.value.total})` : 'Rules'}
      description="Methodology parameters as data: each rule has one closed parameter kind and versions that change only through maker-checker approval. A run pins the ACTIVE version of every rule its module reads."
    >
      {body}
    </PageSection>
  )
}
