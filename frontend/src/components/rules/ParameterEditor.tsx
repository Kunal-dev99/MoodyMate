'use client'

import { Minus, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { bucketLabel, editBuckets, type BucketDraft, type DecimalSpec, type Editor } from './rules'

const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm'

const specText = (s: DecimalSpec): string =>
  [s.min !== null ? `min ${s.min}` : null, s.max !== null ? `max ${s.max}` : null, s.scale !== null ? `scale ${s.scale}` : null].filter(Boolean).join(' · ')

function Buckets({ buckets, unit, onChange, disabled }: { buckets: BucketDraft[]; unit: string | null; onChange: (b: BucketDraft[]) => void; disabled: boolean }) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Half-open intervals (lower, upper]{unit ? ` in ${unit}` : ''}. Each bucket starts where the previous one ends, and the last one is open. Edit an
        upper bound and the next bucket follows.
      </p>
      <Table data-testid="bucket-table">
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>Code</TableHead>
            <TableHead>Lower (exclusive)</TableHead>
            <TableHead>Upper (inclusive)</TableHead>
            <TableHead>Label</TableHead>
            <TableHead className="w-12" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {buckets.map((b, i) => {
            const last = i === buckets.length - 1
            return (
              <TableRow key={i} data-testid="bucket-row" data-index={i} data-code={b.code}>
                <TableCell className="font-mono text-xs">{i + 1}</TableCell>
                <TableCell>
                  <Input
                    aria-label={`Bucket ${i + 1} code`}
                    className="h-8 w-24 font-mono"
                    value={b.code}
                    disabled={disabled}
                    onChange={(e) => onChange(editBuckets(buckets, { type: 'code', index: i, value: e.target.value }))}
                    data-testid="bucket-code"
                  />
                </TableCell>
                <TableCell>
                  {i === 0 ? (
                    <Input
                      aria-label="First bucket lower bound"
                      inputMode="numeric"
                      className="h-8 w-24 font-mono"
                      value={b.lower}
                      disabled={disabled}
                      onChange={(e) => onChange(editBuckets(buckets, { type: 'first_lower', value: e.target.value }))}
                      data-testid="bucket-lower"
                    />
                  ) : (
                    <span className="font-mono text-sm" data-testid="bucket-lower">
                      {b.lower || '—'}
                    </span>
                  )}
                </TableCell>
                <TableCell>
                  {last ? (
                    <span className="text-xs text-muted-foreground" data-testid="bucket-upper-open">
                      open (∞)
                    </span>
                  ) : (
                    <Input
                      aria-label={`Bucket ${i + 1} upper bound`}
                      inputMode="numeric"
                      className="h-8 w-24 font-mono"
                      value={b.upper}
                      disabled={disabled}
                      onChange={(e) => onChange(editBuckets(buckets, { type: 'upper', index: i, value: e.target.value }))}
                      data-testid="bucket-upper"
                    />
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs" data-testid="bucket-label">
                  {bucketLabel(b)}
                </TableCell>
                <TableCell>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Remove bucket ${i + 1}`}
                    disabled={disabled || buckets.length <= 1}
                    onClick={() => onChange(editBuckets(buckets, { type: 'remove', index: i }))}
                    data-testid="bucket-remove"
                  >
                    <Minus className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => onChange(editBuckets(buckets, { type: 'add' }))} data-testid="bucket-add">
        <Plus className="mr-2 h-4 w-4" />
        Add a bucket before the open one
      </Button>
    </div>
  )
}

function MapEntries({
  entries,
  vocabulary,
  onChange,
  disabled,
  values,
  spec,
}: {
  entries: Record<string, string>
  vocabulary: string[]
  onChange: (entries: Record<string, string>) => void
  disabled: boolean
  /** ENUM_MAP: the allowed values. */
  values?: string[]
  /** DECIMAL_MAP: the decimal bounds. */
  spec?: DecimalSpec
}) {
  return (
    <Table data-testid="map-table">
      <TableHeader>
        <TableRow>
          <TableHead>Key</TableHead>
          <TableHead>Value{spec ? ` (decimal text${specText(spec) ? `; ${specText(spec)}` : ''})` : ''}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Object.entries(entries).map(([key, value]) => (
          <TableRow key={key} data-testid="map-row" data-key={key}>
            <TableCell className="font-mono text-sm">
              {key}
              {vocabulary.length > 0 && !vocabulary.includes(key) && <span className="ml-2 text-xs text-destructive">not in the vocabulary</span>}
            </TableCell>
            <TableCell>
              {values ? (
                <select
                  aria-label={`Value for ${key}`}
                  className={selectClass}
                  value={value}
                  disabled={disabled}
                  onChange={(e) => onChange({ ...entries, [key]: e.target.value })}
                  data-testid="map-value"
                >
                  <option value="">(not mapped)</option>
                  {values.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              ) : (
                <Input
                  aria-label={`Value for ${key}`}
                  inputMode="decimal"
                  className="h-8 w-40 font-mono"
                  placeholder="(not set)"
                  value={value}
                  disabled={disabled}
                  onChange={(e) => onChange({ ...entries, [key]: e.target.value })}
                  data-testid="map-value"
                />
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/**
 * FE-16.2: a typed editor for one rule's parameters, one form per closed kind (D-32 §1). There
 * is no free-form JSON and no expression field. Decimal values are typed and kept as text.
 */
export function ParameterEditor({ editor, onChange, disabled = false }: { editor: Editor; onChange: (e: Editor) => void; disabled?: boolean }) {
  const e = editor
  return (
    <div data-testid="parameter-editor" data-kind={e.kind} className="space-y-2">
      {e.kind === 'DECIMAL' && (
        <label className="block max-w-xs space-y-1 text-sm">
          <span className="text-muted-foreground">Value (decimal text{specText(e.spec) ? `; ${specText(e.spec)}` : ''})</span>
          <Input
            inputMode="decimal"
            className="font-mono"
            value={e.value}
            disabled={disabled}
            onChange={(ev) => onChange({ ...e, value: ev.target.value })}
            data-testid="parameter-value"
          />
        </label>
      )}
      {e.kind === 'INTEGER' && (
        <label className="block max-w-xs space-y-1 text-sm">
          <span className="text-muted-foreground">
            Value (whole number{e.spec.min !== null ? `; min ${e.spec.min}` : ''}
            {e.spec.max !== null ? `; max ${e.spec.max}` : ''})
          </span>
          <Input
            inputMode="numeric"
            className="font-mono"
            value={e.value}
            disabled={disabled}
            onChange={(ev) => onChange({ ...e, value: ev.target.value })}
            data-testid="parameter-value"
          />
        </label>
      )}
      {e.kind === 'ENUM' && (
        <label className="block max-w-xs space-y-1 text-sm">
          <span className="text-muted-foreground">Value</span>
          <select className={selectClass} value={e.value} disabled={disabled} onChange={(ev) => onChange({ ...e, value: ev.target.value })} data-testid="parameter-value">
            {!e.values.includes(e.value) && <option value={e.value}>{e.value || '(none)'}</option>}
            {e.values.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      )}
      {e.kind === 'DAY_BUCKETS' && <Buckets buckets={e.buckets} unit={e.unit} disabled={disabled} onChange={(buckets) => onChange({ ...e, buckets })} />}
      {e.kind === 'ENUM_MAP' && (
        <MapEntries entries={e.entries} vocabulary={e.vocabulary} values={e.values} disabled={disabled} onChange={(entries) => onChange({ ...e, entries })} />
      )}
      {e.kind === 'DECIMAL_MAP' && (
        <MapEntries entries={e.entries} vocabulary={e.vocabulary} spec={e.spec} disabled={disabled} onChange={(entries) => onChange({ ...e, entries })} />
      )}
      {e.kind === 'UNSUPPORTED' && (
        <p className="text-sm text-destructive" data-testid="parameter-unsupported">
          {e.reason}
        </p>
      )}
    </div>
  )
}
