'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { Database, FileCode2, ListChecks } from 'lucide-react'
import { api, call } from '@/api/client'
import { RequirePermission } from '@/components/auth/RequirePermission'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { ContractEditor } from '@/components/ingestion/ContractEditor'
import {
  CLASSIFICATIONS,
  DELIMITERS,
  FORMATS,
  SOURCE_KINDS,
  emptySourceDraft,
  errorTitle,
  sourceProblems,
  toSourceCreate,
  type CsvDelimiter,
  type ProfileDraft,
  type SourceDraft,
  type SourceFormat,
  type SourceKind,
} from '@/components/ingestion/sources'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'

const selectClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

function RegisterForm() {
  const router = useRouter()
  const { me } = useCurrentSession()
  const [draft, setDraft] = useState<SourceDraft>(() => emptySourceDraft(me.display_name))
  const [saving, setSaving] = useState(false)

  const profile = (patch: Partial<ProfileDraft>) => setDraft((d) => ({ ...d, profile: { ...d.profile, ...patch } }))
  const problems = sourceProblems(draft)

  const register = async () => {
    setSaving(true)
    try {
      const source = await call(() => api.POST('/api/v1/sources', { body: toSourceCreate(draft) }))
      router.push(`/ingestion/sources/${source.id}`)
    } catch (err) {
      setSaving(false)
      toastApiError(err, errorTitle(err, 'Could not register the source'))
    }
  }

  return (
    <div className="space-y-4">
      <PageSection icon={Database} title="Source system">
        <div className="grid gap-3 md:grid-cols-[200px_1fr]">
          <label className="text-sm font-medium" htmlFor="code">
            Code
          </label>
          <Input
            id="code"
            className="font-mono"
            value={draft.code}
            onChange={(e) => setDraft({ ...draft, code: e.target.value })}
            placeholder="core_banking"
            maxLength={100}
          />
          <label className="text-sm font-medium" htmlFor="name">
            Name
          </label>
          <Input id="name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} maxLength={200} />
          <label className="text-sm font-medium" htmlFor="description">
            Description
          </label>
          <Textarea
            id="description"
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            maxLength={1000}
          />
          <label className="text-sm font-medium" htmlFor="kind">
            Kind
          </label>
          <select id="kind" className={selectClass} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as SourceKind })}>
            {Object.entries(SOURCE_KINDS).map(([value, k]) => (
              <option key={value} value={value} disabled={!k.usable}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
      </PageSection>

      <PageSection
        icon={FileCode2}
        title="Source profile"
        description="How files from this source arrive. Encoding is UTF-8 only in this release."
        headerRight={<Badge variant="warning">Provisional</Badge>}
      >
        <div className="grid gap-3 md:grid-cols-[200px_1fr]">
          <label className="text-sm font-medium" htmlFor="format">
            Format
          </label>
          <select id="format" className={selectClass} value={draft.profile.format} onChange={(e) => profile({ format: e.target.value as SourceFormat })}>
            {FORMATS.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
          {draft.profile.format === 'CSV' && (
            <>
              <label className="text-sm font-medium" htmlFor="delimiter">
                Delimiter
              </label>
              <select
                id="delimiter"
                className={selectClass}
                value={draft.profile.delimiter}
                onChange={(e) => profile({ delimiter: e.target.value as CsvDelimiter })}
              >
                {Object.entries(DELIMITERS).map(([value, label]) => (
                  <option key={label} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </>
          )}
          {draft.profile.format === 'XLSX' && (
            <>
              <label className="text-sm font-medium" htmlFor="sheet">
                Sheet name
              </label>
              <Input
                id="sheet"
                value={draft.profile.sheetName}
                onChange={(e) => profile({ sheetName: e.target.value })}
                placeholder="First sheet when empty"
                maxLength={31}
              />
            </>
          )}
          <label className="text-sm font-medium" htmlFor="cadence">
            Cadence
          </label>
          <Input id="cadence" value={draft.profile.cadence} onChange={(e) => profile({ cadence: e.target.value })} placeholder="e.g. daily" maxLength={100} />
          <label className="text-sm font-medium" htmlFor="owner">
            Owner
          </label>
          <Input id="owner" value={draft.profile.owner} onChange={(e) => profile({ owner: e.target.value })} maxLength={200} />
          <label className="text-sm font-medium" htmlFor="classification">
            Default classification
          </label>
          <select
            id="classification"
            className={selectClass}
            value={draft.profile.defaultClassification}
            onChange={(e) => profile({ defaultClassification: e.target.value })}
          >
            {CLASSIFICATIONS.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </div>
      </PageSection>

      <PageSection
        icon={ListChecks}
        title="Source contract v1"
        description="The columns the files must carry and the canonical schema they feed. It starts as Provisional."
      >
        <ContractEditor draft={draft.contract} onChange={(contract) => setDraft((d) => ({ ...d, contract }))} />
      </PageSection>

      {problems.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-destructive" data-testid="draft-problems">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={() => router.back()}>
          Cancel
        </Button>
        <Button onClick={register} disabled={problems.length > 0 || saving}>
          Register source
        </Button>
      </div>
    </div>
  )
}

/** Register a source system, its profile and contract v1 (FE-7.2). */
export default function NewSourcePage() {
  return (
    <>
      <PageHeader
        title="Register a source"
        description="Creates the source system, its provisional profile and contract v1 in one step."
        breadcrumbs={[{ label: 'Ingestion Workbench', href: '/ingestion' }, { label: 'Sources' }, { label: 'New' }]}
      />
      <div className="px-6 pb-6">
        <RequirePermission permission="source:manage">
          <RegisterForm />
        </RequirePermission>
      </div>
    </>
  )
}
