'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api, call, ApiError, type Schemas } from '@/api/client'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { draftProblems, emptyField, type FieldIn } from '@/components/schemas/proposals'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'

const TYPES: Schemas['FieldType'][] = ['STRING', 'INTEGER', 'DECIMAL', 'BOOLEAN', 'DATE', 'TIMESTAMP']
const CLASSIFICATIONS = ['public', 'internal', 'confidential', 'restricted']
const selectClass = 'h-8 rounded-md border border-input bg-background px-1 text-sm'

/** Empty → null; anything but digits → NaN, which draftProblems reports (never silently null). */
const toInt = (v: string): number | null => (v.trim() === '' ? null : /^\d+$/.test(v.trim()) ? Number(v.trim()) : Number.NaN)

function Editor() {
  const router = useRouter()
  const params = useSearchParams()
  const [schemas, setSchemas] = useState<Schemas['SchemaSummary'][] | null>(null)
  const [schemaId, setSchemaId] = useState(params.get('schema') ?? '')
  const [title, setTitle] = useState('')
  const [rationale, setRationale] = useState('')
  const [fields, setFields] = useState<FieldIn[] | null>(null)
  const [baseVersion, setBaseVersion] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    call(() => api.GET('/api/v1/schemas'))
      .then((list) => {
        setSchemas(list.items)
        setSchemaId((id) => id || list.items[0]?.id || '')
      })
      .catch((err: unknown) => toastApiError(err, 'Could not load schemas'))
  }, [])

  // Start from the contract in effect: the proposal is the full new contract.
  useEffect(() => {
    if (!schemaId) return
    // A slower response for a previously selected schema must never fill this editor.
    let current = true
    setFields(null)
    setLoadFailed(false)
    call(() => api.GET('/api/v1/schemas/{schema_id}/versions', { params: { path: { schema_id: schemaId } } }))
      .then((data) => {
        if (!current) return
        const effective = data.schema.effective_version?.version
        const base = data.items.find((v) => v.version === effective)
        setBaseVersion(effective ?? null)
        setFields(
          (base?.fields ?? []).map((f) => ({
            name: f.name,
            data_type: f.data_type,
            required: f.required,
            unit: f.unit,
            precision: f.precision,
            scale: f.scale,
            classification: f.classification,
            extension_eligible: f.extension_eligible,
            description: f.description,
          })),
        )
      })
      .catch((err: unknown) => {
        if (!current) return
        setLoadFailed(true)
        toastApiError(err, 'Could not load the schema')
      })
    return () => {
      current = false
    }
  }, [schemaId])

  const [dirty, setDirty] = useState(false)
  const chooseSchema = (id: string) => {
    if (dirty && !window.confirm('Switching schema discards your edits to this contract. Continue?')) return
    setDirty(false)
    setSchemaId(id)
  }

  const update = (i: number, patch: Partial<FieldIn>) => {
    setDirty(true)
    setFields((fs) => fs && fs.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  }

  const problems = fields ? draftProblems(fields) : []
  const ready = !!fields && problems.length === 0 && title.trim().length >= 3 && baseVersion != null

  const create = async () => {
    if (!fields) return
    setSaving(true)
    try {
      const proposal = await call(() =>
        api.POST('/api/v1/schema-change-proposals', {
          body: { schema_id: schemaId, title: title.trim(), rationale: rationale.trim() || null, fields },
        }),
      )
      router.push(`/schemas/proposals/${proposal.id}`)
    } catch (err) {
      setSaving(false)
      if (err instanceof ApiError && err.envelope.code === 'no_change') toastApiError(err, 'Nothing changed')
      else toastApiError(err, 'Could not create the proposal')
    }
  }

  if (!schemas) return <LoadingBlock rows={6} />

  return (
    <div className="space-y-4">
      <PageSection title="Proposal">
        <div className="grid gap-3 md:grid-cols-[200px_1fr]">
          <label className="text-sm font-medium" htmlFor="schema">
            Schema
          </label>
          <select id="schema" className="h-9 rounded-md border border-input bg-background px-2 text-sm" value={schemaId} onChange={(e) => chooseSchema(e.target.value)}>
            {schemas.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} ({s.code})
              </option>
            ))}
          </select>
          <label className="text-sm font-medium" htmlFor="title">
            Title
          </label>
          <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What changes, in one line" maxLength={200} />
          <label className="text-sm font-medium" htmlFor="rationale">
            Rationale
          </label>
          <Textarea id="rationale" value={rationale} onChange={(e) => setRationale(e.target.value)} placeholder="Why, and who asked for it" maxLength={2000} />
        </div>
      </PageSection>

      <PageSection
        title={`Proposed contract${baseVersion != null ? ` (starting from v${baseVersion})` : ''}`}
        description="Edit the full contract. The impact analysis compares it with the version in effect."
        headerRight={
          <Button size="sm" variant="outline" onClick={() => {
              setDirty(true)
              setFields((fs) => [...(fs ?? []), emptyField()])
            }} disabled={!fields}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add field
          </Button>
        }
      >
        {loadFailed ? (
          <p className="text-sm text-destructive">The schema could not be loaded. Choose it again to retry.</p>
        ) : !fields ? (
          <LoadingBlock rows={6} />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Precision</TableHead>
                <TableHead>Scale</TableHead>
                <TableHead>Unit</TableHead>
                <TableHead>Required</TableHead>
                <TableHead>Classification</TableHead>
                <TableHead>Ext.</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {fields.map((f, i) => (
                <TableRow key={i} data-testid="field-row" data-name={f.name}>
                  <TableCell>
                    <Input className="h-8 font-mono" aria-label={`Field ${i + 1} name`} value={f.name} onChange={(e) => update(i, { name: e.target.value })} />
                  </TableCell>
                  <TableCell>
                    <select className={selectClass} aria-label={`${f.name} type`} value={f.data_type} onChange={(e) => update(i, { data_type: e.target.value as FieldIn['data_type'] })}>
                      {TYPES.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </TableCell>
                  <TableCell>
                    <Input className="h-8 w-16" inputMode="numeric" aria-label={`${f.name} precision`} value={f.precision == null || Number.isNaN(f.precision) ? '' : f.precision} onChange={(e) => update(i, { precision: toInt(e.target.value) })} />
                  </TableCell>
                  <TableCell>
                    <Input className="h-8 w-16" inputMode="numeric" aria-label={`${f.name} scale`} value={f.scale == null || Number.isNaN(f.scale) ? '' : f.scale} onChange={(e) => update(i, { scale: toInt(e.target.value) })} />
                  </TableCell>
                  <TableCell>
                    <Input className="h-8 w-24" aria-label={`${f.name} unit`} value={f.unit ?? ''} onChange={(e) => update(i, { unit: e.target.value || null })} />
                  </TableCell>
                  <TableCell>
                    <input type="checkbox" aria-label={`${f.name} required`} checked={f.required} onChange={(e) => update(i, { required: e.target.checked })} />
                  </TableCell>
                  <TableCell>
                    <select className={selectClass} aria-label={`${f.name} classification`} value={f.classification} onChange={(e) => update(i, { classification: e.target.value })}>
                      {CLASSIFICATIONS.map((c) => (
                        <option key={c}>{c}</option>
                      ))}
                    </select>
                  </TableCell>
                  <TableCell>
                    <input type="checkbox" aria-label={`${f.name} extension eligible`} checked={f.extension_eligible ?? false} onChange={(e) => update(i, { extension_eligible: e.target.checked })} />
                  </TableCell>
                  <TableCell>
                    <Button size="sm" variant="ghost" aria-label={`Remove ${f.name || 'field'}`} onClick={() => {
                        setDirty(true)
                        setFields((fs) => fs && fs.filter((_, j) => j !== i))
                      }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {problems.length > 0 && (
          <ul className="mt-3 list-disc pl-5 text-sm text-destructive" data-testid="draft-problems">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
      </PageSection>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => router.back()}>
          Cancel
        </Button>
        <Button onClick={create} disabled={!ready || saving}>
          Create proposal
        </Button>
      </div>
    </div>
  )
}

/** New schema change proposal (FE-5.1, step 1). */
export default function NewProposalPage() {
  return (
    <>
      <PageHeader
        title="Propose a schema change"
        description="Creates a draft. Run the impact analysis next; it must exist before the proposal can be submitted."
        breadcrumbs={[
          { label: 'Source & Schemas', href: '/schemas' },
          { label: 'Proposals', href: '/schemas/proposals' },
          { label: 'New' },
        ]}
      />
      <div className="px-6 pb-6">
        <RequirePermission permission="schema:propose">
          <Suspense fallback={<LoadingBlock rows={6} />}>
            <Editor />
          </Suspense>
        </RequirePermission>
      </div>
    </>
  )
}
