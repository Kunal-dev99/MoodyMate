'use client'

import { useEffect, useRef, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api, call, type Schemas } from '@/api/client'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { toastApiError } from '@/components/common/api-error-toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { columnsFromFields, emptyColumn, type ColumnDraft, type ContractDraft } from './sources'

const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

/**
 * Edits one full source contract version (FE-7.2): the target canonical schema and the
 * expected columns, with business-key and control-total flags. Used to register a source
 * (v1) and to add the next version.
 */
export function ContractEditor({
  draft,
  onChange,
  showChangeSummary,
}: {
  draft: ContractDraft
  onChange: (d: ContractDraft) => void
  showChangeSummary?: boolean
}) {
  const [schemas, setSchemas] = useState<Schemas['SchemaSummary'][] | null>(null)
  const [copying, setCopying] = useState(false)

  useEffect(() => {
    let current = true
    call(() => api.GET('/api/v1/schemas'))
      .then((list) => current && setSchemas(list.items))
      .catch((err: unknown) => current && toastApiError(err, 'Could not load schemas'))
    return () => {
      current = false
    }
  }, [])

  // The latest draft, for the async copy below (props change between renders).
  const latest = useRef(draft)
  latest.current = draft
  // Copying the target's fields belongs to the schema chosen when it started: changing
  // the schema (or leaving) cancels it, so a slower response never fills these columns.
  const copyRequest = useRef<AbortController | null>(null)
  useEffect(() => {
    copyRequest.current?.abort()
    setCopying(false)
  }, [draft.schemaId])
  useEffect(() => () => copyRequest.current?.abort(), [])

  const setColumns = (columns: ColumnDraft[]) => onChange({ ...draft, columns })
  const update = (i: number, patch: Partial<ColumnDraft>) =>
    setColumns(draft.columns.map((c, j) => (j === i ? { ...c, ...patch } : c)))

  const copyFromSchema = () => {
    const schemaId = draft.schemaId
    if (!schemaId) return
    if (draft.columns.some((c) => c.name.trim()) && !window.confirm('Replace the expected columns with the target schema fields?')) return
    copyRequest.current?.abort()
    const request = new AbortController()
    copyRequest.current = request
    setCopying(true)
    call(() =>
      api.GET('/api/v1/schemas/{schema_id}/versions', { params: { path: { schema_id: schemaId } }, signal: request.signal }),
    )
      .then((data) => {
        if (request.signal.aborted || latest.current.schemaId !== schemaId) return
        const effective = data.schema.effective_version?.version
        const fields = data.items.find((v) => v.version === effective)?.fields ?? []
        onChange({ ...latest.current, columns: columnsFromFields(fields) })
        setCopying(false)
      })
      .catch((err: unknown) => {
        if (request.signal.aborted) return
        setCopying(false)
        toastApiError(err, 'Could not load the schema fields')
      })
  }

  if (!schemas) return <LoadingBlock rows={4} />

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-[200px_1fr]">
        <label className="text-sm font-medium" htmlFor="target-schema">
          Target canonical schema
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <select
            id="target-schema"
            className={selectClass}
            value={draft.schemaId}
            onChange={(e) => onChange({ ...draft, schemaId: e.target.value })}
          >
            <option value="">Choose a schema…</option>
            {schemas.map((s) => (
              <option key={s.id} value={s.id} disabled={!s.effective_version}>
                {s.name} ({s.code}){s.effective_version ? ` · v${s.effective_version.version} active` : ' · no active version'}
              </option>
            ))}
          </select>
          <Button type="button" size="sm" variant="outline" disabled={!draft.schemaId || copying} onClick={copyFromSchema}>
            Start from schema fields
          </Button>
        </div>
        {showChangeSummary && (
          <>
            <label className="text-sm font-medium" htmlFor="change-summary">
              Change summary
            </label>
            <Textarea
              id="change-summary"
              value={draft.changeSummary}
              onChange={(e) => onChange({ ...draft, changeSummary: e.target.value })}
              placeholder="What changed in the files, and why"
              maxLength={1000}
            />
          </>
        )}
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Column (as in the file header)</TableHead>
            <TableHead>Required</TableHead>
            <TableHead>Business key</TableHead>
            <TableHead>Control total</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {draft.columns.map((c, i) => (
            <TableRow key={i} data-testid="column-row" data-name={c.name}>
              <TableCell>
                <Input
                  className="h-8 font-mono"
                  aria-label={`Column ${i + 1} name`}
                  value={c.name}
                  maxLength={200}
                  onChange={(e) => update(i, { name: e.target.value })}
                />
              </TableCell>
              <TableCell>
                <input type="checkbox" aria-label={`Column ${i + 1} required`} checked={c.required} onChange={(e) => update(i, { required: e.target.checked })} />
              </TableCell>
              <TableCell>
                <input type="checkbox" aria-label={`Column ${i + 1} business key`} checked={c.businessKey} onChange={(e) => update(i, { businessKey: e.target.checked })} />
              </TableCell>
              <TableCell>
                <input type="checkbox" aria-label={`Column ${i + 1} control total`} checked={c.controlTotal} onChange={(e) => update(i, { controlTotal: e.target.checked })} />
              </TableCell>
              <TableCell>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Remove column ${i + 1}`}
                  onClick={() => setColumns(draft.columns.filter((_, j) => j !== i))}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Button type="button" size="sm" variant="outline" onClick={() => setColumns([...draft.columns, emptyColumn()])} disabled={draft.columns.length >= 200}>
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add column
      </Button>
    </div>
  )
}
