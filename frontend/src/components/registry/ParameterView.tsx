import { Fragment } from 'react'
import { Masked } from '@/components/common/Masked'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { StatusPill } from '@/components/common/StatusPill'
import {
  bucketRows,
  kindText,
  leafText,
  mapEntries,
  parameterKind,
  schemaConstraints,
  type ParameterChange,
  type ParameterObject,
} from './registry'

/** One parameter leaf: exact text in monospace, or the "masked" marker if the server withheld it (D-04). */
function Leaf({ value, testId }: { value: unknown; testId?: string }) {
  return (
    <Masked value={value} cell testId={testId}>
      {(v) => (
        <span className="font-mono text-sm" data-testid={testId}>
          {leafText(v)}
        </span>
      )}
    </Masked>
  )
}

/** A rule's `parameter_schema`: its closed kind (D-32 §1) and the constraints it declares. */
export function ParameterSchemaView({ schema, vocabulary }: { schema: ParameterObject; vocabulary?: readonly string[] }) {
  const kind = parameterKind(schema)
  return (
    <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm" data-testid="parameter-schema" data-kind={kind ?? ''}>
      <dt className="text-muted-foreground">Kind</dt>
      <dd>
        <span className="font-mono" data-testid="parameter-kind">
          {kind ?? '—'}
        </span>{' '}
        <span className="text-muted-foreground">{kindText(kind)}</span>
      </dd>
      {schemaConstraints(schema).map(([key, text]) => (
        <Fragment key={key}>
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="font-mono">{text}</dd>
        </Fragment>
      ))}
      {vocabulary && vocabulary.length > 0 && (
        <>
          <dt className="text-muted-foreground">Vocabulary</dt>
          <dd className="font-mono" data-testid="parameter-vocabulary">
            {vocabulary.join(', ')}
          </dd>
        </>
      )}
    </dl>
  )
}

/**
 * One version's parameters, laid out by their structure: DAY_BUCKETS as intervals, a map kind as
 * key → value rows, a single value as itself, anything else as the server's JSON. Values are
 * the server's exact text.
 */
export function ParameterValues({ parameters }: { parameters: ParameterObject }) {
  const buckets = bucketRows(parameters)
  if (buckets)
    return (
      <Table data-testid="parameter-buckets">
        <TableHeader>
          <TableRow>
            <TableHead>Bucket</TableHead>
            <TableHead>Label</TableHead>
            <TableHead>Calendar days after the reporting date</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {buckets.map((b, i) => (
            <TableRow key={`${b.code}-${i}`} data-testid="parameter-bucket" data-code={b.code}>
              <TableCell className="font-mono text-sm">{b.code}</TableCell>
              <TableCell className="font-mono text-sm">{b.label}</TableCell>
              <TableCell className="font-mono text-sm" data-testid="parameter-bucket-interval">
                {b.interval}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )
  const entries = mapEntries(parameters)
  if (entries)
    return (
      <Table data-testid="parameter-map">
        <TableHeader>
          <TableRow>
            <TableHead>Key</TableHead>
            <TableHead>Value</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map(([key, value]) => (
            <TableRow key={key} data-testid="parameter-map-entry" data-key={key}>
              <TableCell className="font-mono text-sm">{key}</TableCell>
              <TableCell>
                <Leaf value={value} testId="parameter-map-value" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )
  if ('value' in parameters && Object.keys(parameters).length === 1)
    return (
      <dl className="grid grid-cols-[160px_1fr] gap-y-1 text-sm">
        <dt className="text-muted-foreground">Value</dt>
        <dd>
          <Leaf value={parameters.value} testId="parameter-value" />
        </dd>
      </dl>
    )
  return (
    <pre className="overflow-x-auto rounded-md border border-border bg-surface-2 p-3 font-mono text-xs" data-testid="parameter-json">
      {JSON.stringify(parameters, null, 2)}
    </pre>
  )
}

const CHANGE_STYLE: Record<ParameterChange['change'], { label: string; className: string }> = {
  added: { label: 'Added', className: 'status-published' },
  removed: { label: 'Removed', className: 'status-failed' },
  changed: { label: 'Changed', className: 'status-testing' },
}

/** The leaves that differ between two versions' parameters (added, removed, changed), exact text. */
export function ParameterDiffTable({ changes }: { changes: readonly ParameterChange[] }) {
  if (changes.length === 0)
    return (
      <p className="text-sm text-muted-foreground" data-testid="parameter-diff-none">
        The two versions have the same parameters.
      </p>
    )
  return (
    <Table data-testid="parameter-diff">
      <TableHeader>
        <TableRow>
          <TableHead>Parameter</TableHead>
          <TableHead>Change</TableHead>
          <TableHead>From</TableHead>
          <TableHead>To</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {changes.map((c) => (
          <TableRow key={c.path} data-testid="parameter-diff-row" data-path={c.path} data-change={c.change}>
            <TableCell className="font-mono text-xs">{c.path || '(root)'}</TableCell>
            <TableCell>
              <StatusPill value={c.change} variant={CHANGE_STYLE[c.change]} attr={null} />
            </TableCell>
            <TableCell className="font-mono text-sm" data-testid="parameter-diff-from">
              {c.from ?? <span className="text-muted-foreground">—</span>}
            </TableCell>
            <TableCell className="font-mono text-sm" data-testid="parameter-diff-to">
              {c.to ?? <span className="text-muted-foreground">—</span>}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
