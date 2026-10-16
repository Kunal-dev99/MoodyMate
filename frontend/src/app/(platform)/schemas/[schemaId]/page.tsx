'use client'

import { useParams } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { FileStack, GitCompare, ListTree } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { CompatibilityBadge } from '@/components/schemas/CompatibilityBadge'
import { StatusPill } from '@/components/schemas/StatusPill'
import { alignFields, typeLabel, valueLabel, type Field } from '@/components/schemas/registry'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cn } from '@/lib/utils'

type Versions = Schemas['SchemaVersionList']
type Diff = Schemas['SchemaDiffOut']
type Change = Schemas['SchemaFieldChange']
type State = { kind: 'loading' } | { kind: 'ready'; data: Versions } | { kind: 'error'; status: number; cid: string | null }

const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

function FieldCell({ field, changes }: { field?: Field; changes: Change[] }) {
  if (!field) return <TableCell className="text-muted-foreground">—</TableCell>
  const changed = new Set(changes.map((c) => c.attribute))
  return (
    <TableCell className="text-sm">
      <span className={cn('font-mono', (changed.has('data_type') || changed.has('scale') || changed.has('unit')) && 'font-semibold')}>
        {typeLabel(field)}
      </span>
      <span className={cn('ml-2 text-xs', changed.has('required') ? 'font-semibold' : 'text-muted-foreground')}>
        {field.required ? 'required' : 'optional'}
      </span>
      <span className={cn('ml-2 text-xs', changed.has('classification') ? 'font-semibold' : 'text-muted-foreground')}>
        {field.classification}
      </span>
    </TableCell>
  )
}

/** Schema detail and version compare (FE-4.2). */
export default function SchemaDetailPage() {
  const { schemaId } = useParams<{ schemaId: string }>()
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [selected, setSelected] = useState<number | null>(null)
  const [compare, setCompare] = useState<{ from: number; to: number } | null>(null)
  const [diff, setDiff] = useState<Diff | null>(null)

  useEffect(() => {
    call(() => api.GET('/api/v1/schemas/{schema_id}/versions', { params: { path: { schema_id: schemaId } } }))
      .then((data) => {
        setState({ kind: 'ready', data })
        const effective = data.schema.effective_version?.version ?? data.schema.latest_version.version
        setSelected(effective)
        if (data.items.length > 1) setCompare({ from: effective, to: data.schema.latest_version.version })
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError) setState({ kind: 'error', status: err.status, cid: err.correlationId })
        else toastApiError(err, 'Could not load the schema')
      })
  }, [schemaId])

  useEffect(() => {
    if (!compare) return
    setDiff(null)
    call(() =>
      api.GET('/api/v1/schemas/{schema_id}/diff', {
        params: { path: { schema_id: schemaId }, query: { from: compare.from, to: compare.to } },
      }),
    )
      .then(setDiff)
      .catch((err: unknown) => toastApiError(err, 'Could not compare versions'))
  }, [compare, schemaId])

  const byVersion = useMemo(
    () => new Map(state.kind === 'ready' ? state.data.items.map((v) => [v.version, v]) : []),
    [state],
  )

  if (state.kind === 'error') {
    if (state.status === 403) return <Forbidden permission="schema:read" correlationId={state.cid} />
    return (
      <div className="p-6">
        <EmptyState icon={FileStack} title="Schema not found" description="It does not exist or is not visible to you." />
      </div>
    )
  }
  if (state.kind === 'loading') return <LoadingBlock rows={6} />

  const { schema, items } = state.data
  const shown = selected != null ? byVersion.get(selected) : undefined
  const fromFields = compare ? byVersion.get(compare.from)?.fields ?? [] : []
  const toFields = compare ? byVersion.get(compare.to)?.fields ?? [] : []
  const changesFor = (name: string) => diff?.changes.filter((c) => c.field === name) ?? []

  return (
    <>
      <PageHeader
        title={schema.name}
        description={schema.description ?? undefined}
        breadcrumbs={[{ label: 'Source & Schemas', href: '/schemas' }, { label: schema.code }]}
        actions={
          <div className="flex items-center gap-2">
            {schema.provisional && <Badge variant="warning">Provisional</Badge>}
            <StatusPill status={schema.status} />
          </div>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={ListTree} title="Versions" description={`Owner: ${schema.owner}`}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Effective from</TableHead>
                <TableHead>Change vs previous</TableHead>
                <TableHead>Content hash</TableHead>
                <TableHead>Summary</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((v) => (
                <TableRow
                  key={v.id}
                  className={cn('cursor-pointer', v.version === selected && 'bg-surface-2')}
                  onClick={() => setSelected(v.version)}
                  data-testid="version-row"
                >
                  <TableCell className="font-mono">v{v.version}</TableCell>
                  <TableCell>
                    <StatusPill status={v.status} />
                  </TableCell>
                  <TableCell className="text-sm">{v.effective_from ?? '—'}</TableCell>
                  <TableCell>{v.compatibility ? <CompatibilityBadge value={v.compatibility} /> : '—'}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground" title={v.content_sha256}>
                    {v.content_sha256.slice(0, 12)}…
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">{v.change_summary ?? '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {shown && (
            <div className="mt-4">
              <h3 className="mb-2 text-sm font-medium">Fields of v{shown.version}</h3>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Field</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Required</TableHead>
                    <TableHead>Classification</TableHead>
                    <TableHead>Extension</TableHead>
                    <TableHead>Description</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {shown.fields.map((f) => (
                    <TableRow key={f.name}>
                      <TableCell className="font-mono text-sm">{f.name}</TableCell>
                      <TableCell className="font-mono text-sm">{typeLabel(f)}</TableCell>
                      <TableCell className="text-sm">{f.required ? 'Yes' : 'No'}</TableCell>
                      <TableCell className="text-sm">{f.classification}</TableCell>
                      <TableCell className="text-sm">{f.extension_eligible ? 'Eligible' : '—'}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{f.description ?? '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </PageSection>

        <PageSection icon={GitCompare} title="Compare versions" accent="primary">
          {items.length < 2 || !compare ? (
            <p className="text-sm text-muted-foreground">Only one version exists, so there is nothing to compare yet.</p>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <label className="flex items-center gap-2">
                  From
                  <select
                    className={selectClass}
                    aria-label="Compare from version"
                    value={compare.from}
                    onChange={(e) => setCompare({ ...compare, from: Number(e.target.value) })}
                  >
                    {items.map((v) => (
                      <option key={v.version} value={v.version}>
                        v{v.version} ({v.status})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2">
                  to
                  <select
                    className={selectClass}
                    aria-label="Compare to version"
                    value={compare.to}
                    onChange={(e) => setCompare({ ...compare, to: Number(e.target.value) })}
                  >
                    {items.map((v) => (
                      <option key={v.version} value={v.version}>
                        v{v.version} ({v.status})
                      </option>
                    ))}
                  </select>
                </label>
                {diff && (
                  <span className="ml-auto flex items-center gap-2" data-testid="diff-overall">
                    Overall
                    {diff.overall ? <CompatibilityBadge value={diff.overall} /> : <Badge variant="outline">Identical</Badge>}
                  </span>
                )}
              </div>

              {!diff ? (
                <LoadingBlock rows={4} />
              ) : (
                <>
                  <ul className="space-y-1" aria-label="Changes">
                    {diff.changes.map((c) => (
                      <li
                        key={`${c.field}:${c.change}:${c.attribute ?? ''}`}
                        className="flex flex-wrap items-center gap-2 text-sm"
                        data-testid="diff-change"
                        data-field={c.field}
                      >
                        <CompatibilityBadge value={c.compatibility} />
                        <span className="font-mono">{c.field}</span>
                        <span className="text-xs uppercase text-muted-foreground">
                          {c.change.toLowerCase()}
                          {c.attribute ? ` · ${c.attribute}` : ''}
                        </span>
                        {c.change === 'MODIFIED' && (
                          <span className="font-mono text-xs">
                            {valueLabel(c.from_value)} → {valueLabel(c.to_value)}
                          </span>
                        )}
                        <span className="text-xs text-muted-foreground">{c.reason}</span>
                      </li>
                    ))}
                  </ul>

                  <Table aria-label="Side-by-side fields">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Field</TableHead>
                        <TableHead>v{compare.from}</TableHead>
                        <TableHead>v{compare.to}</TableHead>
                        <TableHead>Change</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {alignFields(fromFields, toFields).map((row) => {
                        const changes = changesFor(row.name)
                        return (
                          <TableRow
                            key={row.name}
                            data-testid="diff-row"
                            className={cn(
                              changes.some((c) => c.compatibility === 'BREAKING') && 'bg-[hsl(var(--destructive)/0.05)]',
                            )}
                          >
                            <TableCell className="font-mono text-sm">{row.name}</TableCell>
                            <FieldCell field={row.from} changes={changes} />
                            <FieldCell field={row.to} changes={changes} />
                            <TableCell>
                              <div className="flex flex-wrap gap-1">
                                {changes.map((c) => (
                                  <CompatibilityBadge key={`${c.change}:${c.attribute ?? ''}`} value={c.compatibility} />
                                ))}
                              </div>
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </>
              )}
            </div>
          )}
        </PageSection>
      </div>
    </>
  )
}
