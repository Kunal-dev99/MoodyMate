'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, call, type Schemas } from '@/api/client'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { toastApiError } from '@/components/common/api-error-toast'
import { startLoad, type LoadFailure } from '@/components/common/load'
import {
  RequestScope,
  buildRules,
  draftFromVersion,
  isDirty,
  type DraftRule,
  type MappingDetail,
  type MappingValidation,
  type MappingVersion,
  type Refusal,
} from '@/components/ingestion/mappings'
import { truncationOf, type Truncation } from '@/lib/paging'
import { DISCARD } from './shared'

/*
 * FE-10.1/10.2 (split out of ingestion/mappings/[mappingId]/page.tsx by FE-35.2, unchanged): the
 * workbench's state. It loads the mapping (mapping:read), the source's imports under its
 * contract, the pinned schema's fields (best effort, schema:read) and the selected import's
 * profile, and keeps the local draft. Every write's answer, 403/409/422 included, is the
 * server's; the hook only holds what it sent back.
 */

type State = { kind: 'loading' } | { kind: 'ready'; m: MappingDetail } | LoadFailure
type Profile = Schemas['ImportProfile']
type ImportSummary = Schemas['ImportSummary']
type SchemaField = Schemas['SchemaFieldOut']
/** What the selected import contributes: loading, its profile, or none (no import / no profile). */
type ProfileState = { kind: 'none' } | { kind: 'loading' } | { kind: 'ready'; profile: Profile }

export function useMappingWorkbench(mappingId: string) {
  const router = useRouter()
  const params = useSearchParams()
  const { me, has } = useCurrentSession()
  const mayPropose = has('mapping:propose')
  const [state, setState] = useState<State>({ kind: 'loading' })
  const request = useRef<AbortController | null>(null)
  // Requests tied to the selected import and version; a switch cancels them (FE-10.1).
  const scope = useRef(new RequestScope())

  const [imports, setImports] = useState<ImportSummary[] | null>(null)
  // Q18: the imports are one capped page of the source's imports; set when the server cut it.
  const [importsNote, setImportsNote] = useState<Truncation | null>(null)
  const [fields, setFields] = useState<Record<string, SchemaField[]> | null>(null)
  const [profile, setProfile] = useState<ProfileState>({ kind: 'none' })
  const [rows, setRows] = useState<DraftRule[]>([])
  const [baseline, setBaseline] = useState<DraftRule[]>([])
  const [summary, setSummary] = useState('')
  const [validation, setValidation] = useState<MappingValidation | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const [comment, setComment] = useState('')

  const load = useCallback(() => {
    request.current = startLoad(
      (signal) => call(() => api.GET('/api/v1/mappings/{mapping_id}', { params: { path: { mapping_id: mappingId } }, signal })),
      { onReady: (m) => setState({ kind: 'ready', m }), onFailure: setState, failureTitle: 'Could not load the mapping' },
      request.current,
    )
  }, [mappingId])

  useEffect(() => {
    setState({ kind: 'loading' })
    load()
    return () => request.current?.abort()
  }, [load])

  const m = state.kind === 'ready' ? state.m : null
  const latest = m ? m.versions[m.versions.length - 1] : undefined
  const requested = Number(params.get('version'))
  const version: MappingVersion | undefined = m?.versions.find((v) => v.version === requested) ?? latest
  const importId = params.get('import')
  const selectedImport = imports?.find((i) => i.id === importId) ?? null

  // The source's imports under this mapping's contract (the only ones a dry run accepts).
  const sourceId = m?.source_id
  const contractId = m?.source_contract_id
  const schemaId = m?.schema_id
  useEffect(() => {
    if (!sourceId) return
    const controller = new AbortController()
    call(() => api.GET('/api/v1/imports', { params: { query: { source_id: sourceId, limit: 100 } }, signal: controller.signal }))
      .then((page) => {
        if (controller.signal.aborted) return
        setImports(page.items.filter((i) => i.source_contract_id === contractId))
        setImportsNote(truncationOf(page, 'imports of this source (newest first)'))
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setImports([])
        toastApiError(err, 'Could not load the imports')
      })
    return () => controller.abort()
  }, [sourceId, contractId])

  // The target schema's versions, for the target fields of the pinned one. Best effort:
  // without schema:read the target is typed instead (the API still checks it).
  const canReadSchema = has('schema:read')
  useEffect(() => {
    if (!schemaId || !canReadSchema) return
    const controller = new AbortController()
    call(() => api.GET('/api/v1/schemas/{schema_id}/versions', { params: { path: { schema_id: schemaId } }, signal: controller.signal }))
      .then((list) => !controller.signal.aborted && setFields(Object.fromEntries(list.items.map((v) => [v.id, v.fields]))))
      .catch(() => undefined)
    return () => controller.abort()
  }, [schemaId, canReadSchema])

  // Switching the import or the version cancels everything in flight for the previous
  // pair and clears its validation result, which no longer applies.
  const versionId = version?.id
  useEffect(() => {
    scope.current.switch()
    setValidation(null)
    setRefusal(null)
    if (!importId) {
      setProfile({ kind: 'none' })
      return
    }
    setProfile({ kind: 'loading' })
    scope.current.run(
      (signal) => call(() => api.GET('/api/v1/imports/{import_id}/profile', { params: { path: { import_id: importId } }, signal })),
      (p) => setProfile({ kind: 'ready', profile: p }),
      () => setProfile({ kind: 'none' }),
    )
  }, [importId, versionId])
  useEffect(() => () => scope.current.switch(), [])

  // The draft starts from the selected version's rules plus the import's uncovered columns.
  const columnsKey = profile.kind === 'ready' ? profile.profile.columns.map((c) => c.name).join('\u0000') : profile.kind
  const versionRules = version?.rules
  useEffect(() => {
    if (!versionRules || profile.kind === 'loading') return
    const columns = columnsKey && profile.kind === 'ready' ? columnsKey.split('\u0000') : []
    const fresh = draftFromVersion(versionRules, columns)
    setRows(fresh)
    setBaseline(fresh)
    // Only a new version or another import's columns reset the draft (not a reload).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId, columnsKey])

  const dirty = isDirty(rows, baseline)
  const built = useMemo(() => buildRules(rows), [rows])

  // Leaving the page with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  /** Moves to another version or import; unsaved edits prompt before they are discarded. */
  const select = (next: { version?: number; import?: string | null }, force = false) => {
    if (!force && dirty && !window.confirm(DISCARD)) return false
    const q = new URLSearchParams(params.toString())
    if (next.version !== undefined) q.set('version', String(next.version))
    if (next.import !== undefined) {
      if (next.import) q.set('import', next.import)
      else q.delete('import')
    }
    router.replace(`/ingestion/mappings/${mappingId}?${q.toString()}`, { scroll: false })
    return true
  }

  return {
    me,
    has,
    mayPropose,
    state,
    setState,
    load,
    scope,
    m,
    version,
    importId,
    imports,
    importsNote,
    selectedImport,
    fields,
    profile,
    rows,
    setRows,
    baseline,
    summary,
    setSummary,
    validation,
    setValidation,
    busy,
    setBusy,
    refusal,
    setRefusal,
    comment,
    setComment,
    dirty,
    built,
    select,
  }
}

export type MappingWorkbenchState = ReturnType<typeof useMappingWorkbench>
