import Link from 'next/link'
import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { counterpartiesHref, importHref, type CounterpartyVersion } from './credit'

/* FE-19.1: the pieces the counterparty and the exposure detail share. */

export const CREDIT_CRUMBS = [
  { label: 'Financial Domain', href: '/domain' },
  { label: 'Counterparties', href: counterpartiesHref },
]

export function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </>
  )
}

export const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

/** The lineage fields every projected credit version carries: import, source row, canonical record (D-29). */
export function Lineage({
  v,
  schema,
}: {
  v: Pick<CounterpartyVersion, 'import_id' | 'source_ordinal' | 'canonical_record_id' | 'source_record_id' | 'schema_version' | 'schema_version_id' | 'projection_id' | 'payload_sha256' | 'version_id'>
  schema: string
}) {
  return (
    <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="version-lineage">
      <Field label="Import" testId="version-import">
        <Link href={importHref(v.import_id)} className="font-mono text-xs text-primary hover:underline">
          {v.import_id}
        </Link>
      </Field>
      <Field label="Source row ordinal" testId="version-source-ordinal">
        <span className="font-mono">{v.source_ordinal}</span>
      </Field>
      <Field label="Canonical record" testId="version-canonical-record">
        <Mono>{v.canonical_record_id}</Mono>
      </Field>
      <Field label="Source record" testId="version-source-record">
        <Mono>{v.source_record_id}</Mono>
      </Field>
      <Field label="Schema version">
        <span className="font-mono">{v.schema_version != null ? `${schema} v${v.schema_version}` : '—'}</span>{' '}
        <span className="font-mono text-xs text-muted-foreground">{v.schema_version_id}</span>
      </Field>
      <Field label="Projection">
        <Mono>{v.projection_id}</Mono>
      </Field>
      <Field label="Payload SHA-256">
        <Mono>{v.payload_sha256}</Mono>
      </Field>
      <Field label="Version ID">
        <Mono>{v.version_id}</Mono>
      </Field>
    </dl>
  )
}

/** The link from a version to its import and source row (FE-19.1 lineage). */
export function ImportRowLink({ importId, ordinal, testId }: { importId: string; ordinal: number; testId: string }) {
  return (
    <Link href={importHref(importId)} className="font-mono text-xs text-primary hover:underline" data-testid={testId} data-import-id={importId}>
      Import {importId.slice(0, 8)} · row {ordinal}
    </Link>
  )
}

/** A badge for a value given outside its seeded vocabulary (modules exclude or warn on it). */
export function NotInVocabulary({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <Badge variant="outline" data-testid="not-in-vocabulary">
      Not in vocabulary: modules warn on it
    </Badge>
  )
}
