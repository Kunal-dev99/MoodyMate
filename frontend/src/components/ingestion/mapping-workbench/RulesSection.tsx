'use client'

import Link from 'next/link'
import { Save, Sparkles, TableProperties } from 'lucide-react'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageSection } from '@/components/common/PageSection'
import { TruncationNote } from '@/components/common/ListPager'
import { ImportStatusPill } from '@/components/ingestion/ImportStatusPill'
import { MappingGrid } from '@/components/ingestion/MappingGrid'
import { MappingStatusPill } from '@/components/ingestion/MappingStatusPill'
import { applySuggestions, type MappingDetail, type MappingVersion } from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatTimestamp } from '@/lib/format'
import type { MappingWorkbenchState } from './useMappingWorkbench'
import { DISCARD, Field, selectClass } from './shared'

/**
 * The open version's rules as a local draft (FE-10.1): edits change the draft only, and saving
 * creates a new Draft version. Only a user who may propose edits, picks an import or saves; the
 * server checks mapping:propose on the write itself (D-04).
 */
export function RulesSection({ wb, m, version, onSave }: { wb: MappingWorkbenchState; m: MappingDetail; version: MappingVersion; onSave: () => void }) {
  const { mayPropose, importId, imports, importsNote, selectedImport, select, fields, profile, rows, setRows, baseline, summary, setSummary, dirty, built, busy } = wb
  const pinnedFields = fields?.[version.schema_version_id] ?? null
  const profiledTypes = profile.kind === 'ready' ? Object.fromEntries(profile.profile.columns.map((c) => [c.name, c.suggested_type])) : null
  const suggestions = profile.kind === 'ready' ? Object.fromEntries(profile.profile.columns.map((c) => [c.name, c.suggested_target_field])) : null
  const targeted = new Set(rows.filter((r) => r.policy === 'MAP').map((r) => r.target_field))
  const unmappedRequired = (pinnedFields ?? []).filter((f) => f.required && !targeted.has(f.name)).map((f) => f.name)
  const schemaMismatch = selectedImport && selectedImport.schema_version_id !== version.schema_version_id
  return (
    <PageSection
      icon={TableProperties}
      title="Rules"
      description="Edits change a local draft only. Saving creates a new Draft version; existing versions are never edited."
      headerRight={
        <div className="flex items-center gap-2" data-testid="mapping-version-badge" data-version={version.version} data-status={version.status}>
          <Badge variant="outline" className="font-mono">
            v{version.version}
          </Badge>
          <MappingStatusPill status={version.status} />
          {dirty && (
            <Badge variant="warning" data-testid="mapping-dirty">
              Unsaved edits
            </Badge>
          )}
        </div>
      }
    >
      <div className="mb-3 flex flex-wrap items-end gap-3">
        {/* Only a user who may propose runs a dry run, so only they pick an import (P-9). */}
        {mayPropose && (
        <div className="grid gap-1">
          <label htmlFor="mapping-import" className="text-xs text-muted-foreground">
            Import to validate against
          </label>
          <select
            id="mapping-import"
            className={selectClass}
            data-testid="mapping-import"
            value={importId ?? ''}
            onChange={(e) => select({ import: e.target.value || null })}
          >
            <option value="">{imports === null ? 'Loading imports…' : imports.length === 0 ? 'No import of this contract' : 'Choose an import…'}</option>
            {importId && !selectedImport && <option value={importId}>{importId.slice(0, 8)}</option>}
            {(imports ?? []).map((i) => (
              <option key={i.id} value={i.id}>
                {i.id.slice(0, 8)} · {i.status} · {i.artifact.original_filename ?? 'unnamed'} · {formatTimestamp(i.created_at)}
              </option>
            ))}
          </select>
          <TruncationNote note={importsNote} testId="mapping-import-truncated" />
        </div>
        )}
        {selectedImport && (
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <Link href={`/ingestion/imports/${selectedImport.id}`} className="font-mono text-primary hover:underline">
              {selectedImport.id.slice(0, 8)}
            </Link>
            <ImportStatusPill status={selectedImport.status} />
            {selectedImport.row_count} rows · schema {selectedImport.schema_code} v{selectedImport.schema_version}
          </span>
        )}
        {mayPropose && suggestions && (
          <Button variant="outline" size="sm" onClick={() => setRows(applySuggestions(rows, suggestions))} data-testid="mapping-apply-suggestions">
            <Sparkles className="mr-1 h-3.5 w-3.5" />
            Map unmapped columns to suggested targets
          </Button>
        )}
      </div>
      <dl className="mb-3 grid grid-cols-[200px_1fr] gap-y-1 text-xs">
        <Field label="Pinned schema version">
          <span className="font-mono">
            {m.schema_code} v{version.schema_version ?? '—'}
          </span>{' '}
          <span className="font-mono text-muted-foreground">{version.schema_version_id}</span>
        </Field>
        <Field label="Content SHA-256">
          <span className="break-all font-mono">{version.content_sha256}</span>
        </Field>
      </dl>
      {schemaMismatch && (
        <p className="mb-2 text-sm text-warning" data-testid="mapping-schema-mismatch">
          This import pins another schema version than v{version.version}; the dry run will refuse it.
        </p>
      )}
      {!pinnedFields && <p className="mb-2 text-xs text-muted-foreground">The target fields could not be read (schema:read); type them instead.</p>}
      {unmappedRequired.length > 0 && (
        <p className="mb-2 text-sm text-muted-foreground" data-testid="mapping-unmapped-required">
          Required fields not mapped in this draft: <span className="font-mono">{unmappedRequired.join(', ')}</span>. The dry run reports each as a BLOCKER.
        </p>
      )}
      {profile.kind === 'loading' ? (
        <LoadingBlock rows={4} />
      ) : (
        <MappingGrid rows={rows} onChange={setRows} fields={pinnedFields} profiledTypes={profiledTypes} editable={mayPropose} />
      )}
      {mayPropose && (
        <div className="mt-3 space-y-2">
          {dirty && built.problems.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-destructive" data-testid="mapping-draft-problems">
              {built.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Input className="max-w-md" placeholder="Change summary (optional)" value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={1000} aria-label="Change summary" />
            <Button onClick={onSave} disabled={!dirty || built.problems.length > 0 || busy !== null} data-testid="mapping-save-draft">
              <Save className="mr-1 h-3.5 w-3.5" />
              Save as new draft version
            </Button>
            <Button variant="outline" disabled={!dirty || busy !== null} onClick={() => window.confirm(DISCARD) && setRows(baseline)} data-testid="mapping-discard">
              Discard edits
            </Button>
          </div>
        </div>
      )}
    </PageSection>
  )
}
