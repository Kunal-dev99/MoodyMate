import type { ReactNode } from 'react'
import { FileLock2, Fingerprint } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { SnapshotMembers } from './SnapshotMembers'
import { INPUT_REF_TEXT, type RunDetail } from './calculations'

function Field({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0" data-testid={testId}>
        {children}
      </dd>
    </>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="break-all font-mono text-xs">{children}</span>

function Sub({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </div>
  )
}

/**
 * FE-15.2: the run's manifest (D-31 §4), every pinned id, version and hash as the server wrote
 * it once with the run: module and engine, the content-addressed rule set and each rule version
 * with its parameters, the assumption set, the dataset snapshot and each member's import
 * manifest, schema, mapping and projection, plus the `calculation_input_ref` rows.
 */
export function ManifestPanel({ run }: { run: RunDetail }) {
  const m = run.manifest
  return (
    <PageSection
      icon={FileLock2}
      title="Manifest"
      description="Everything this run pinned, written once with the run. manifest_sha256 covers the pinned content only, so a run on the same pins has the same hash (D-31 §4)."
      headerRight={
        <span className="flex gap-2">
          {m.labels.illustrative && <Badge variant="warning">Illustrative</Badge>}
          {m.labels.provisional && <Badge variant="warning">Provisional</Badge>}
        </span>
      }
    >
      <div className="space-y-6" data-testid="run-manifest" data-manifest-sha256={run.manifest_sha256}>
        <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm">
          <Field label="Manifest SHA-256" testId="manifest-sha256">
            <Mono>{run.manifest_sha256}</Mono>
          </Field>
          <Field label="Result SHA-256" testId="result-sha256">
            {run.result_sha256 ? <Mono>{run.result_sha256}</Mono> : <span className="text-muted-foreground">Set when the run ends.</span>}
          </Field>
          <Field label="Manifest version">
            <Mono>v{m.manifest_version}</Mono>
          </Field>
          <Field label="Reporting date" testId="manifest-reporting-date">
            <Mono>{m.reporting_date}</Mono>
          </Field>
          <Field label="Engine" testId="manifest-engine">
            <Mono>{m.engine_version}</Mono>
          </Field>
          <Field label="Module" testId="manifest-module">
            <Mono>
              {m.module.code} v{m.module.version} · module version {m.module.module_version_id} · module {m.module.module_id} · declares engine{' '}
              {m.module.engine_version}
            </Mono>
          </Field>
          <Field label="Labels">
            <span className="text-xs">{m.labels.statement}</span>
          </Field>
        </dl>

        <Sub title="Rule set">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm">
            <Field label="Rule set ID" testId="manifest-rule-set-id">
              <Mono>{m.rule_set.rule_set_id}</Mono>
            </Field>
            <Field label="Content SHA-256">
              <Mono>{m.rule_set.content_sha256}</Mono>
            </Field>
          </dl>
          <Table data-testid="manifest-rules">
            <TableHeader>
              <TableRow>
                <TableHead>Rule</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>IDs</TableHead>
                <TableHead>Parameters SHA-256</TableHead>
                <TableHead>Parameters</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {m.rule_set.members.map((r) => (
                <TableRow key={r.rule_version_id} data-testid="manifest-rule" data-rule={r.rule_code} data-rule-version-id={r.rule_version_id}>
                  <TableCell className="font-mono text-sm">{r.rule_code}</TableCell>
                  <TableCell className="font-mono text-sm">v{r.version}</TableCell>
                  <TableCell className="space-y-0.5 text-xs">
                    <div>
                      <span className="text-muted-foreground">version </span>
                      <span className="font-mono">{r.rule_version_id}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">rule </span>
                      <span className="font-mono">{r.rule_id}</span>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[200px]">
                    <span className="break-all font-mono text-[11px]">{r.parameters_sha256}</span>
                  </TableCell>
                  <TableCell>
                    <details>
                      <summary className="cursor-pointer text-xs text-primary">Show</summary>
                      <pre className="mt-1 max-h-60 max-w-[420px] overflow-auto rounded-md bg-surface-2 p-2 font-mono text-[11px]">
                        {JSON.stringify(r.parameters, null, 2)}
                      </pre>
                    </details>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Sub>

        <Sub title="Assumption set">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm" data-testid="manifest-assumption-set">
            <Field label="Assumption set">
              <Mono>
                {m.assumption_set.code} v{m.assumption_set.version}
              </Mono>
            </Field>
            <Field label="ID">
              <Mono>{m.assumption_set.assumption_set_id}</Mono>
            </Field>
            <Field label="Content SHA-256">
              <Mono>{m.assumption_set.content_sha256}</Mono>
            </Field>
          </dl>
        </Sub>

        <Sub title="Dataset snapshot">
          <dl className="grid grid-cols-[200px_1fr] gap-y-1 text-sm">
            <Field label="Snapshot ID" testId="manifest-snapshot-id">
              <Mono>{m.dataset_snapshot.dataset_snapshot_id}</Mono>
            </Field>
            <Field label="Content SHA-256">
              <Mono>{m.dataset_snapshot.content_sha256}</Mono>
            </Field>
          </dl>
          <SnapshotMembers members={m.dataset_snapshot.members} testId="manifest-members" />
        </Sub>

        <Sub title={`Input references (${run.input_refs.length})`}>
          <Table data-testid="input-refs">
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">#</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Pin</TableHead>
                <TableHead>ID</TableHead>
                <TableHead>SHA-256</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {run.input_refs.map((r) => (
                <TableRow key={r.sequence} data-testid="input-ref" data-type={r.ref_type}>
                  <TableCell className="font-mono text-xs">{r.sequence}</TableCell>
                  <TableCell className="text-xs">
                    <span className="inline-flex items-center gap-1">
                      <Fingerprint className="h-3 w-3 text-muted-foreground" />
                      {INPUT_REF_TEXT[r.ref_type]}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{r.label}</TableCell>
                  <TableCell className="font-mono text-xs">{r.ref_id ?? '—'}</TableCell>
                  <TableCell className="max-w-[220px]">
                    {r.sha256 ? <span className="break-all font-mono text-[11px]">{r.sha256}</span> : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Sub>
      </div>
    </PageSection>
  )
}
