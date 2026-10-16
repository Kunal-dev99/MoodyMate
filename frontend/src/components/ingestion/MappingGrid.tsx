'use client'

import { Plus, Trash2 } from 'lucide-react'
import type { Schemas } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import {
  COLUMN_TRANSFORMS,
  DATE_FORMATS,
  NULL_POLICIES,
  ON_MISSING,
  TRANSFORMS,
  constantRow,
  type DraftPolicy,
  type DraftRule,
  type ParamDraft,
  type TransformCode,
} from './mappings'

type Field = Schemas['SchemaFieldOut']

const selectClass = 'h-8 rounded-md border border-input bg-background px-2 text-xs disabled:opacity-60'
const POLICY_LABEL: Record<DraftPolicy, string> = { MAP: 'Map', PRESERVE: 'Preserve', UNSET: 'No rule yet' }
const NULL_LABEL: Record<string, string> = { ALLOW_NULL: 'Allow empty', REJECT_NULL: 'Reject empty', USE_DEFAULT: 'Use default' }

function Params({ row, set, disabled }: { row: DraftRule; set: (p: Partial<ParamDraft>) => void; disabled: boolean }) {
  const p = row.params
  const label = row.source_column ?? 'constant'
  switch (TRANSFORMS[row.transform].params) {
    case 'none':
      return null
    case 'decimal':
      return (
        <div className="flex gap-1">
          <select className={selectClass} aria-label={`Decimal separator for ${label}`} value={p.decimal_sep} disabled={disabled} onChange={(e) => set({ decimal_sep: e.target.value as ParamDraft['decimal_sep'] })}>
            <option value=".">decimal “.”</option>
            <option value=",">decimal “,”</option>
          </select>
          <select className={selectClass} aria-label={`Thousands separator for ${label}`} value={p.thousands_sep} disabled={disabled} onChange={(e) => set({ thousands_sep: e.target.value as ParamDraft['thousands_sep'] })}>
            <option value="">no grouping</option>
            <option value=",">group “,”</option>
            <option value=".">group “.”</option>
            <option value=" ">group space</option>
            <option value="'">group “&apos;”</option>
          </select>
        </div>
      )
    case 'scale':
      return <Input className="h-8 w-24 text-xs" aria-label={`Power of ten for ${label}`} value={p.power_of_ten} disabled={disabled} onChange={(e) => set({ power_of_ten: e.target.value })} />
    case 'date':
      return (
        <select className={selectClass} aria-label={`Date format for ${label}`} value={p.format} disabled={disabled} onChange={(e) => set({ format: e.target.value as ParamDraft['format'] })}>
          {DATE_FORMATS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      )
    case 'boolean':
      return (
        <div className="grid gap-1">
          <Input className="h-8 text-xs" placeholder="true values, comma-separated" aria-label={`True values for ${label}`} value={p.true_values} disabled={disabled} onChange={(e) => set({ true_values: e.target.value })} />
          <Input className="h-8 text-xs" placeholder="false values, comma-separated" aria-label={`False values for ${label}`} value={p.false_values} disabled={disabled} onChange={(e) => set({ false_values: e.target.value })} />
        </div>
      )
    case 'table':
      return (
        <div className="grid gap-1">
          <Textarea className="min-h-16 font-mono text-xs" placeholder={'one per line: source=target'} aria-label={`Value table for ${label}`} value={p.table} disabled={disabled} onChange={(e) => set({ table: e.target.value })} />
          <select className={selectClass} aria-label={`When a value is missing for ${label}`} value={p.on_missing} disabled={disabled} onChange={(e) => set({ on_missing: e.target.value as ParamDraft['on_missing'] })}>
            {ON_MISSING.map((o) => (
              <option key={o} value={o}>
                unlisted: {o.toLowerCase()}
              </option>
            ))}
          </select>
        </div>
      )
    case 'value':
      return <Input className="h-8 text-xs" placeholder="value" aria-label={`Value for ${label}`} value={p.value} disabled={disabled} onChange={(e) => set({ value: e.target.value })} />
  }
}

/**
 * The mapping workbench grid (FE-10.1): one row per rule, plus a "No rule yet" row for each
 * column of the selected import no rule reads. Edits change only the local draft; saving it
 * creates a new Draft version (versions are never edited in place).
 */
export function MappingGrid({
  rows,
  onChange,
  fields,
  profiledTypes,
  editable,
}: {
  rows: DraftRule[]
  onChange: (rows: DraftRule[]) => void
  /** The pinned schema version's fields; null when they could not be read (free-text target). */
  fields: Field[] | null
  /** Column → the profile's suggested type, for the selected import. */
  profiledTypes: Record<string, string | null> | null
  editable: boolean
}) {
  const byName = new Map((fields ?? []).map((f) => [f.name, f]))
  const update = (key: string, patch: Partial<DraftRule>) => onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const disabled = !editable

  return (
    <div className="space-y-2">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>#</TableHead>
            <TableHead>Source column</TableHead>
            <TableHead>Profiled type</TableHead>
            <TableHead>Policy</TableHead>
            <TableHead>Target field</TableHead>
            <TableHead>Transform and params</TableHead>
            <TableHead>Empty values</TableHead>
            <TableHead>Required</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => {
            const target = byName.get(row.target_field)
            const isConstant = row.source_column === null
            const name = row.source_column ?? 'constant'
            const profiled = row.source_column && profiledTypes ? profiledTypes[row.source_column] : undefined
            return (
              <TableRow key={row.key} data-testid="mapping-grid-row" data-column={row.source_column ?? ''} data-policy={row.policy}>
                <TableCell className="font-mono text-xs text-muted-foreground">{i + 1}</TableCell>
                <TableCell className="font-mono text-sm">
                  {isConstant ? <span className="text-muted-foreground">(constant)</span> : row.source_column}
                  {row.source_column && profiledTypes && !(row.source_column in profiledTypes) && (
                    <Badge variant="warning" className="ml-1">
                      Not in this import
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  {profiled ? <Badge variant="info">Suggested: {profiled}</Badge> : <span className="text-muted-foreground">—</span>}
                </TableCell>
                <TableCell>
                  {isConstant ? (
                    <span className="text-xs">Map</span>
                  ) : (
                    <select
                      className={selectClass}
                      aria-label={`Policy for ${name}`}
                      data-testid="mapping-policy"
                      value={row.policy}
                      disabled={disabled}
                      onChange={(e) => {
                        const policy = e.target.value as DraftPolicy
                        update(row.key, policy === 'MAP' ? { policy } : { policy, target_field: '', transform: 'COPY', null_policy: 'ALLOW_NULL', default_value: '' })
                      }}
                    >
                      {(['MAP', 'PRESERVE', 'UNSET'] as DraftPolicy[]).map((p) => (
                        <option key={p} value={p}>
                          {POLICY_LABEL[p]}
                        </option>
                      ))}
                    </select>
                  )}
                </TableCell>
                <TableCell>
                  {row.policy !== 'MAP' ? (
                    <span className="text-xs text-muted-foreground">{row.policy === 'PRESERVE' ? 'kept as it is' : '—'}</span>
                  ) : fields ? (
                    <select
                      className={selectClass}
                      aria-label={`Target field for ${name}`}
                      data-testid="mapping-target"
                      value={row.target_field}
                      disabled={disabled}
                      onChange={(e) => update(row.key, { target_field: e.target.value })}
                    >
                      <option value="">Choose…</option>
                      {row.target_field && !target && <option value={row.target_field}>{row.target_field} (not in schema)</option>}
                      {fields.map((f) => (
                        <option key={f.name} value={f.name}>
                          {f.name} · {f.data_type}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Input className="h-8 font-mono text-xs" aria-label={`Target field for ${name}`} value={row.target_field} disabled={disabled} onChange={(e) => update(row.key, { target_field: e.target.value })} />
                  )}
                </TableCell>
                <TableCell>
                  {row.policy !== 'MAP' ? (
                    <span className="text-xs text-muted-foreground">{row.policy === 'PRESERVE' ? 'Copy' : '—'}</span>
                  ) : (
                    <div className="grid gap-1">
                      {isConstant ? (
                        <span className="text-xs">Constant</span>
                      ) : (
                        <select
                          className={selectClass}
                          aria-label={`Transform for ${name}`}
                          data-testid="mapping-transform"
                          value={row.transform}
                          disabled={disabled}
                          onChange={(e) => update(row.key, { transform: e.target.value as TransformCode })}
                        >
                          {COLUMN_TRANSFORMS.map((t) => (
                            <option key={t} value={t}>
                              {TRANSFORMS[t].label}
                            </option>
                          ))}
                        </select>
                      )}
                      <Params row={row} disabled={disabled} set={(p) => update(row.key, { params: { ...row.params, ...p } })} />
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  {row.policy === 'MAP' && !isConstant ? (
                    <div className="grid gap-1">
                      <select
                        className={selectClass}
                        aria-label={`Empty values for ${name}`}
                        value={row.null_policy}
                        disabled={disabled}
                        onChange={(e) => update(row.key, { null_policy: e.target.value as DraftRule['null_policy'] })}
                      >
                        {NULL_POLICIES.map((n) => (
                          <option key={n} value={n}>
                            {NULL_LABEL[n]}
                          </option>
                        ))}
                      </select>
                      {row.null_policy === 'USE_DEFAULT' && (
                        <Input className="h-8 text-xs" placeholder="default value" aria-label={`Default value for ${name}`} value={row.default_value} disabled={disabled} onChange={(e) => update(row.key, { default_value: e.target.value })} />
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="text-xs">
                  {row.policy === 'MAP' && target ? (
                    target.required ? (
                      <Badge variant="warning" data-testid="mapping-required">
                        Required
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground">Optional</span>
                    )
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                  {isConstant && editable && (
                    <Button variant="ghost" size="sm" className="ml-1 h-7 px-2" aria-label="Remove constant rule" onClick={() => onChange(rows.filter((r) => r.key !== row.key))}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
      {editable && (
        <Button variant="outline" size="sm" onClick={() => onChange([...rows, constantRow()])} data-testid="mapping-add-constant">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add constant rule
        </Button>
      )}
    </div>
  )
}
