'use client'

import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { FlaskConical, Inbox, Library } from 'lucide-react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScenarioStatusPill } from './ScenarioStatusPill'
import { PAGE_SIZE, TAB_LABELS, fetchScenarios, libraryQuery, scenarioHref, tabOf, tabsFor, type ExplorerTab, type ScenarioPage } from './scenarios'

const TAB_TEXT: Record<ExplorerTab, { title: string; description: string; empty: string }> = {
  shared: {
    title: 'Shared library',
    description: 'Scenarios with a version approved for test by an approver who is not its author. Exploration versions are never listed here.',
    empty: 'No scenario has been approved for test yet.',
  },
  mine: {
    title: 'My explorations',
    description: 'Scenarios with versions you authored, in every status. A version you confirmed yourself is an exploration, not approved.',
    empty: 'You have not authored a scenario yet.',
  },
  review: {
    title: 'Review',
    description: 'Versions submitted for promotion. Approve or reject them on the scenario page, with a comment; you cannot decide your own.',
    empty: 'Nothing is waiting for review.',
  },
}

/**
 * FE-24.2: the explorer's library, in three tabs: the shared library (the server's default,
 * APPROVED_FOR_TEST only), "My explorations" (`mine=true`) and, for `scenario:approve`, the
 * Review queue (IN_REVIEW). The tab is in the URL (`?tab=`). Reads need `calc:read`; the server
 * filters, the UI only asks.
 */
export function ScenarioLibrary() {
  const { has } = useCurrentSession()
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const tab = tabOf(params.get('tab'), has)
  const [offset, setOffset] = useState(0)
  const [showClosed, setShowClosed] = useState(false)
  const [state, setState] = useState<Loaded<ScenarioPage>>({ kind: 'loading' })
  const query = useMemo(() => libraryQuery(tab, offset, showClosed), [tab, offset, showClosed])
  const loader = useMemo(() => createLoader(fetchScenarios(query), setState, 'Could not load the scenarios'), [query])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  const choose = (next: string) => {
    setOffset(0)
    const search = new URLSearchParams(params.toString())
    if (next === 'shared') search.delete('tab')
    else search.set('tab', next)
    const qs = search.toString()
    router.replace(qs ? `${pathname}?${qs}` : pathname)
  }

  const text = TAB_TEXT[tab]
  return (
    <div className="space-y-4" data-testid="scenario-library" data-tab={tab}>
      <Tabs value={tab} onValueChange={choose}>
        <TabsList>
          {tabsFor(has).map((t) => (
            <TabsTrigger key={t} value={t} data-testid={`explorer-tab-${t}`}>
              {TAB_LABELS[t]}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <PageSection
        icon={tab === 'review' ? Inbox : tab === 'mine' ? FlaskConical : Library}
        title={text.title}
        description={text.description}
        headerRight={
          tab === 'mine' ? (
            <div className="flex items-center gap-2">
              <Checkbox
                id="show-closed"
                checked={showClosed}
                onCheckedChange={(v) => {
                  setOffset(0)
                  setShowClosed(v === true)
                }}
                data-testid="show-closed"
              />
              <Label htmlFor="show-closed" className="text-xs">
                Show closed versions
              </Label>
            </div>
          ) : undefined
        }
      >
        {state.kind === 'loading' && <LoadingBlock rows={4} />}
        {state.kind === 'failed' && <LoadFailed icon={Library} title="Could not load the scenarios" cid={state.cid} onRetry={loader.load} />}
        {state.kind === 'forbidden' && <Forbidden permission="calc:read" correlationId={state.cid} />}
        {state.kind === 'not_found' && <EmptyState icon={Library} title="The scenario list was refused" description={state.cid ? `Correlation ID: ${state.cid}` : undefined} />}
        {state.kind === 'ready' && <LibraryTable page={state.value} tab={tab} empty={text.empty} onPage={setOffset} />}
      </PageSection>
    </div>
  )
}

function LibraryTable({ page, tab, empty, onPage }: { page: ScenarioPage; tab: ExplorerTab; empty: string; onPage: (offset: number) => void }) {
  if (page.items.length === 0) return <EmptyState icon={Library} title={empty} />
  return (
    <div className="space-y-3">
      <Table data-testid="scenario-table">
        <TableHeader>
          <TableRow>
            <TableHead>Scenario</TableHead>
            <TableHead>Code</TableHead>
            <TableHead>Latest version</TableHead>
            <TableHead>{tab === 'shared' ? 'Approved version' : 'Matching versions'}</TableHead>
            <TableHead>Created by</TableHead>
            <TableHead>Created</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {page.items.map((s) => (
            <TableRow key={s.scenario_id} data-testid="scenario-row" data-id={s.scenario_id} data-status={s.latest_status}>
              <TableCell>
                <Link className="font-medium hover:underline" href={scenarioHref(s.scenario_id)} data-testid="scenario-link">
                  {s.name}
                </Link>
                {s.description && <div className="max-w-md truncate text-xs text-muted-foreground">{s.description}</div>}
              </TableCell>
              <TableCell className="font-mono text-xs">{s.code}</TableCell>
              <TableCell>
                <span className="mr-2 font-mono text-xs">v{s.latest_version}</span>
                <ScenarioStatusPill status={s.latest_status} testId="scenario-row-status" />
              </TableCell>
              <TableCell>
                {tab === 'shared' ? (
                  s.approved_version !== null ? (
                    <span className="flex items-center gap-2">
                      <span className="font-mono text-xs" data-testid="scenario-row-approved">
                        v{s.approved_version}
                      </span>
                      <Badge variant="success">Approved scenario (illustrative)</Badge>
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )
                ) : (
                  <span className="font-mono text-xs" data-testid="scenario-row-matching">
                    {s.matching_versions.map((v) => `v${v}`).join(', ')}
                  </span>
                )}
              </TableCell>
              <TableCell className="font-mono text-xs">{s.created_by}</TableCell>
              <TableCell className="text-xs">{new Date(s.created_at).toLocaleString()}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {page.total > page.limit && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            {page.offset + 1}–{Math.min(page.offset + page.items.length, page.total)} of {page.total}
          </span>
          <span className="flex gap-2">
            <Button size="sm" variant="outline" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - PAGE_SIZE))}>
              Previous
            </Button>
            <Button size="sm" variant="outline" disabled={page.offset + page.items.length >= page.total} onClick={() => onPage(page.offset + PAGE_SIZE)}>
              Next
            </Button>
          </span>
        </div>
      )}
    </div>
  )
}
