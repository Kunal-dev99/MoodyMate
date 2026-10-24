'use client'

import Link from 'next/link'
import { GitMerge } from 'lucide-react'
import { api, call } from '@/api/client'
import { Forbidden } from '@/components/auth/Forbidden'
import { ActorLabel } from '@/components/common/ActorLabel'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { toastApiError } from '@/components/common/api-error-toast'
import { RefusalNotice } from '@/components/ingestion/RefusalNotice'
import { activeVersion, canDecideVersion, canSubmitVersion, mappingErrorTitle, mappingRefusal } from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { formatTimestamp } from '@/lib/format'
import { isAuthorOf } from '@/lib/maker-checker'
import { DecisionSection, SubmitSection } from './ApprovalSections'
import { RulesSection } from './RulesSection'
import { ValidateSection } from './ValidateSection'
import { VersionsSection } from './VersionsSection'
import { CRUMBS, Field } from './shared'
import { useMappingWorkbench } from './useMappingWorkbench'

/*
 * FE-10.1/10.2: the mapping workbench (split out of ingestion/mappings/[mappingId]/page.tsx by
 * FE-35.2, unchanged). The page keeps its switch (NEXT_PUBLIC_MAPPING_UI) and its mapping:read
 * gate; this component composes the parts beside it: the state (useMappingWorkbench.ts), the
 * versions, the rules draft, the dry run, and submit and decision. Every write's answer
 * (including 403/409/422) is authoritative: a refusal is shown and the mapping reloaded.
 */
export function MappingWorkbench({ mappingId }: { mappingId: string }) {
  const wb = useMappingWorkbench(mappingId)
  const { me, has, state, setState, load, scope, m, version, importId, built, summary, setSummary, setValidation, setBusy, setRefusal, comment, setComment, select } = wb

  if (state.kind === 'forbidden') return <Forbidden permission="mapping:read" correlationId={state.cid} />
  if (state.kind === 'failed')
    return (
      <LoadFailed
        icon={GitMerge}
        title="The mapping could not be loaded"
        cid={state.cid}
        onRetry={() => {
          setState({ kind: 'loading' })
          load()
        }}
      />
    )
  if (state.kind === 'not_found')
    return (
      <EmptyState
        icon={GitMerge}
        title="Mapping not found"
        description={`It does not exist or is not visible to you.${state.cid ? ` Correlation ID: ${state.cid}` : ''}`}
      />
    )
  if (!m || !version) return <LoadingBlock rows={6} />

  const active = activeVersion(m)
  const mine = isAuthorOf(version.author_user_id, me)
  const submitAllowed = canSubmitVersion(version, me.user_id)
  const decisionAllowed = canDecideVersion(version, me.user_id, has('mapping:approve'))
  const path = { mapping_id: m.id, version: version.version }

  /** Runs one write; the server's answer (including 403/409/422) is authoritative. */
  const act = async <T,>(label: string, fallback: string, run: () => Promise<T>, after: (value: T) => void) => {
    setBusy(label)
    setRefusal(null)
    try {
      after(await run())
    } catch (err) {
      toastApiError(err, mappingErrorTitle(err, fallback))
      setRefusal(mappingRefusal(err, fallback))
      load()
    } finally {
      setBusy(null)
    }
  }
  const afterDecision = () => {
    setComment('')
    load()
  }

  const saveDraft = () =>
    act(
      'save',
      'Could not save the draft',
      () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions', { params: { path: { mapping_id: m.id } }, body: { rules: built.rules, change_summary: summary.trim() || null } })),
      (saved) => {
        setSummary('')
        load()
        select({ version: saved.version }, true)
      },
    )

  const validate = () => {
    if (!importId) return
    setBusy('validate')
    setRefusal(null)
    setValidation(null)
    // Runs in the current scope: switching the import or version drops a late answer.
    scope.current.run(
      (signal) =>
        call(() =>
          api.POST('/api/v1/imports/{import_id}/mappings/validate', {
            params: { path: { import_id: importId } },
            body: { mapping_version_id: version.id },
            signal,
          }),
        ),
      (v) => {
        setValidation(v)
        setBusy(null)
        load()
      },
      (err) => {
        toastApiError(err, mappingErrorTitle(err, 'The dry run failed'))
        setRefusal(mappingRefusal(err, 'The dry run failed'))
        setBusy(null)
      },
    )
  }

  const submit = () =>
    act('submit', 'Could not submit', () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions/{version}/submit', { params: { path }, body: { comment: comment.trim() || null } })), afterDecision)
  const approve = () =>
    act(
      'approve',
      'Approval failed',
      () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions/{version}/approval', { params: { path }, body: { decision: 'APPROVE', comment: comment.trim() || null } })),
      afterDecision,
    )
  const reject = () =>
    act('reject', 'Rejection failed', () => call(() => api.POST('/api/v1/mappings/{mapping_id}/versions/{version}/approval', { params: { path }, body: { decision: 'REJECT', comment: comment.trim() } })), afterDecision)

  return (
    <>
      <PageHeader
        title={m.name}
        description={m.description ?? undefined}
        breadcrumbs={[...CRUMBS, { label: m.code }]}
        actions={
          <div className="flex items-center gap-2">
            {active ? (
              <Badge variant="success" data-testid="mapping-active-version" data-version={active.version}>
                Active v{active.version}
              </Badge>
            ) : (
              <Badge variant="secondary" data-testid="mapping-active-version" data-version="">
                No active version
              </Badge>
            )}
          </div>
        }
      />
      <div className="space-y-4 px-6 pb-6">
        <PageSection icon={GitMerge} title="Mapping">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="mapping-summary">
            <Field label="Code">
              <span className="font-mono">{m.code}</span>
            </Field>
            <Field label="Source">
              <Link href={`/ingestion/sources/${m.source_id}`} className="font-mono text-primary hover:underline">
                {m.source_code}
              </Link>{' '}
              <span className="text-muted-foreground">contract v{m.contract_version ?? '—'}</span>
            </Field>
            <Field label="Target schema">
              <span className="font-mono">{m.schema_code}</span>
            </Field>
            <Field label="Created">
              <span className="font-mono text-xs">
                <ActorLabel actor={m.created_by} /> · {formatTimestamp(m.created_at)}
              </span>
            </Field>
          </dl>
        </PageSection>

        <VersionsSection m={m} version={version} select={select} />
        <RulesSection wb={wb} m={m} version={version} onSave={saveDraft} />
        <ValidateSection wb={wb} version={version} onValidate={validate} />

        {wb.refusal && <RefusalNotice refusal={wb.refusal} />}

        {version.status === 'DRAFT' && mine && <SubmitSection wb={wb} version={version} submitAllowed={submitAllowed} onSubmit={submit} />}

        {version.status === 'IN_REVIEW' && (
          <DecisionSection wb={wb} m={m} version={version} active={active} mine={mine} decisionAllowed={decisionAllowed} onApprove={approve} onReject={reject} />
        )}
      </div>
    </>
  )
}
