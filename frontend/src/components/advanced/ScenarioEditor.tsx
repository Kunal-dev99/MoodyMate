'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { ArrowDownToLine, ArrowUpFromLine, FileWarning, Layers, Plus, Save, Trash2 } from 'lucide-react'
import { useCurrentSession } from '@/components/auth/SessionProvider'
import { Forbidden } from '@/components/auth/Forbidden'
import { toastApiError } from '@/components/common/api-error-toast'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadFailed } from '@/components/common/LoadFailed'
import { LoadingBlock } from '@/components/common/LoadingBlock'
import { PageHeader } from '@/components/common/PageHeader'
import { PageSection } from '@/components/common/PageSection'
import { createLoader, type Loaded } from '@/components/common/load'
import type { Refusal } from '@/components/ingestion/mappings'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { fetchLimits } from '@/lib/limits'
import { InstrumentPicker } from './InstrumentPicker'
import { ScenarioRefusalNotice } from './ScenarioRefusalNotice'
import { explorerHref, fractionToPercent, scenarioHref } from './scenarios'
import {
  createScenario,
  createVersion,
  fetchScenario,
  fetchTemplates,
  scenarioRefusal,
  shockProblemLines,
  updateVersion,
  type ProblemLine,
  type ScenarioDetail,
  type ScenarioLimits,
  type TemplateField,
  type TemplateOut,
  type TemplatePage,
} from './scenarios'
import {
  buildShock,
  draftFromShock,
  editorProblems,
  fieldLabel,
  fieldProblem,
  fractionScaleOf,
  hasProblems,
  isPercent,
  newShock,
  percentScale,
  percentToFraction,
  type ShockDraft,
} from './editor'

/** What the editor writes: a new scenario, a new version (optionally adapting `parent`) or a DRAFT's shocks. */
export type EditorMode = { kind: 'create' } | { kind: 'version'; scenarioId: string; parent: number | null } | { kind: 'edit'; scenarioId: string; version: number }

const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm'

/** FE-35.3: `limits` are the scenario bounds GET config/limits serves (text lengths, shock count, fraction scale). */
type Inputs = { templates: TemplatePage; scenario: ScenarioDetail | null; limits: ScenarioLimits }

const loadInputs = (mode: EditorMode) => async (signal: AbortSignal): Promise<Inputs> => {
  const [templates, scenario, limits] = await Promise.all([
    fetchTemplates()(signal),
    mode.kind === 'create' ? Promise.resolve(null) : fetchScenario(mode.scenarioId)(signal),
    fetchLimits(),
  ])
  return { templates, scenario, limits: limits.scenario }
}

/** The page description; the shock count is the server's (GET config/limits) once it is read. */
const composeText = (limits: ScenarioLimits | null) =>
  `Compose ${limits ? `${limits.min_shocks} to ${limits.max_shocks} shocks` : 'shocks'} from the approved templates. A scenario is data, never code: no formulas, no expressions.`

const TITLES: Record<EditorMode['kind'], string> = { create: 'New scenario', version: 'New scenario version', edit: 'Edit draft' }

/**
 * FE-24.2: the scenario editor. Template cards come from GET scenario-templates; each shock has
 * typed inputs with the server's bounds (a fraction shown as a percentage and stored as exact
 * text at scale 4), an instrument picker for FUNDING_UNAVAILABLE and the common selector. Up to
 * `max_shocks` shocks combine. There is no free-text formula field. Saving writes a DRAFT; the
 * server validates every shock again (422 `invalid_shock`, shown as it came).
 */
export function ScenarioEditor({ mode }: { mode: EditorMode }) {
  const { has } = useCurrentSession()
  const [state, setState] = useState<Loaded<Inputs>>({ kind: 'loading' })
  const loader = useMemo(() => createLoader(loadInputs(mode), setState, 'Could not load the scenario templates'), [mode])
  useEffect(() => {
    loader.load()
    return loader.abort
  }, [loader])

  const title = TITLES[mode.kind]
  const header = <PageHeader title={title} description={composeText(state.kind === 'ready' ? state.value.limits : null)} />
  if (!has('scenario:propose'))
    return (
      <>
        {header}
        <div className="px-6 pb-6">
          <Forbidden permission="scenario:propose" />
        </div>
      </>
    )
  return (
    <>
      {header}
      <div className="space-y-4 px-6 pb-6">
        {state.kind === 'loading' && <LoadingBlock rows={4} />}
        {state.kind === 'failed' && <LoadFailed icon={Layers} title="Could not load the scenario templates" cid={state.cid} onRetry={loader.load} />}
        {state.kind === 'forbidden' && <Forbidden permission="calc:read" correlationId={state.cid} />}
        {state.kind === 'not_found' && <EmptyState icon={FileWarning} title="Scenario not found" description={state.cid ? `Correlation ID: ${state.cid}` : undefined} />}
        {state.kind === 'ready' && <EditorForm mode={mode} inputs={state.value} />}
      </div>
    </>
  )
}

function initialShocks(mode: EditorMode, inputs: Inputs): ShockDraft[] | 'missing' {
  if (mode.kind === 'create' || !inputs.scenario) return []
  const from = mode.kind === 'edit' ? mode.version : mode.parent
  if (from === null) return []
  const version = inputs.scenario.versions.find((v) => v.version === from)
  if (!version) return 'missing'
  const byName = new Map(inputs.templates.items.map((t) => [t.template, t]))
  return version.shocks.flatMap((s) => {
    const t = byName.get(s.template)
    return t ? [draftFromShock(t, s)] : []
  })
}

function EditorForm({ mode, inputs }: { mode: EditorMode; inputs: Inputs }) {
  const router = useRouter()
  const { templates, scenario, limits } = inputs
  const catalogue = templates.items
  const byName = useMemo(() => new Map(catalogue.map((t) => [t.template, t])), [catalogue])
  const initial = useMemo(() => initialShocks(mode, inputs), [mode, inputs])
  const [shocks, setShocks] = useState<ShockDraft[]>(initial === 'missing' ? [] : initial)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [summary, setSummary] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<{ refusal: Refusal; problems: ProblemLine[] } | null>(null)

  const problems = editorProblems(catalogue, limits, shocks, {
    name: mode.kind === 'create' ? name : undefined,
    description: mode.kind === 'create' ? description : undefined,
    summary,
  })
  const blocked = hasProblems(problems)
  const editingVersion = mode.kind === 'edit' ? scenario?.versions.find((v) => v.version === mode.version) : undefined

  if (initial === 'missing') return <EmptyState icon={FileWarning} title="That version does not exist" description="Open the scenario and pick a version listed there." />
  if (editingVersion && editingVersion.status !== 'DRAFT')
    return (
      <EmptyState
        icon={FileWarning}
        title="Only a DRAFT can be edited"
        description="This version has left DRAFT, so its shocks are frozen. Create a new version adapting it instead."
      />
    )

  const update = (key: string, change: (d: ShockDraft) => ShockDraft) => setShocks((all) => all.map((d) => (d.key === key ? change(d) : d)))

  async function save() {
    setTouched(true)
    if (blocked || busy) return
    setBusy(true)
    setRefusal(null)
    const built = shocks.map((d) => buildShock(byName.get(d.template)!, d, limits.fraction_scale))
    const changeSummary = summary.trim() || null
    try {
      if (mode.kind === 'create') {
        const created = await createScenario({ name: name.trim(), description: description.trim() || null, change_summary: changeSummary, shocks: built })
        router.push(scenarioHref(created.scenario_id))
      } else if (mode.kind === 'version') {
        await createVersion(mode.scenarioId, { shocks: built, change_summary: changeSummary, parent_version: mode.parent })
        router.push(scenarioHref(mode.scenarioId))
      } else {
        await updateVersion(mode.scenarioId, mode.version, { shocks: built, change_summary: changeSummary })
        router.push(scenarioHref(mode.scenarioId))
      }
    } catch (err) {
      const r = scenarioRefusal(err, 'Could not save the scenario')
      toastApiError(err, r.title)
      setRefusal({ refusal: r, problems: shockProblemLines(err) })
      setBusy(false)
    }
  }

  const show = (problem: string | null | undefined) => (touched ? problem : null)

  return (
    <div className="space-y-4" data-testid="scenario-editor" data-mode={mode.kind}>
      {scenario && (
        <p className="text-sm text-muted-foreground" data-testid="editor-scenario">
          Scenario <span className="font-medium text-foreground">{scenario.name}</span> (<span className="font-mono">{scenario.code}</span>)
          {mode.kind === 'version' && mode.parent !== null && <> · adapting v{mode.parent}</>}
          {mode.kind === 'edit' && <> · editing DRAFT v{mode.version}</>}
        </p>
      )}
      {scenario && !scenario.scenarios_enabled && <DisabledNotice />}

      {mode.kind === 'create' && (
        <PageSection icon={Layers} title="Scenario" description="The name the library shows. The code is assigned by the server.">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="scenario-name">Name (required)</Label>
              <Input id="scenario-name" value={name} maxLength={limits.max_name_length} onChange={(e) => setName(e.target.value)} onBlur={() => setTouched(true)} data-testid="scenario-name" />
              <FieldNote problem={show(problems.name)} testId="scenario-name-problem" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="scenario-description">Description</Label>
              <Textarea id="scenario-description" value={description} maxLength={limits.max_description_length} onChange={(e) => setDescription(e.target.value)} data-testid="scenario-description" />
              <FieldNote problem={show(problems.description)} />
            </div>
          </div>
        </PageSection>
      )}

      <PageSection
        icon={Layers}
        title={`Shocks (${shocks.length} of ${templates.max_shocks})`}
        description="Pick a template to add a shock. Several shocks combine into one scenario; each is checked against the server's bounds."
      >
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3" data-testid="template-cards">
            {catalogue.map((t) => (
              <TemplateCard key={t.template} template={t} disabled={shocks.length >= templates.max_shocks} onAdd={() => setShocks((all) => [...all, newShock(t)])} />
            ))}
          </div>
          <FieldNote problem={show(problems.count)} testId="shock-count-problem" />
          {shocks.map((d, i) => {
            const t = byName.get(d.template)
            return t ? (
              <ShockCard
                key={d.key}
                index={i}
                template={t}
                draft={d}
                limits={limits}
                problems={problems.shocks[i] ?? {}}
                touched={touched}
                onChange={(change) => update(d.key, change)}
                onRemove={() => setShocks((all) => all.filter((x) => x.key !== d.key))}
              />
            ) : null
          })}
        </div>
      </PageSection>

      <PageSection icon={Save} title="Save as DRAFT" description="Saving writes a DRAFT only you can edit. Confirming it for exploration, or submitting it for approval, happens on the scenario page.">
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="change-summary">Change summary</Label>
            <Textarea id="change-summary" value={summary} maxLength={limits.max_change_summary_length} onChange={(e) => setSummary(e.target.value)} placeholder="What this version explores" data-testid="change-summary" />
            <FieldNote problem={show(problems.summary)} />
          </div>
          {refusal && <ScenarioRefusalNotice refusal={refusal.refusal} problems={refusal.problems} />}
          {touched && blocked && (
            <p className="text-xs text-destructive" data-testid="editor-blocked">
              Fix the fields marked above before saving.
            </p>
          )}
          <div className="flex gap-2">
            <Button onClick={save} disabled={busy || (touched && blocked)} data-testid="save-scenario">
              <Save className="mr-2 h-4 w-4" />
              {busy ? 'Saving…' : 'Save draft'}
            </Button>
            <Button variant="outline" onClick={() => router.push(mode.kind === 'create' ? explorerHref : scenarioHref(mode.scenarioId))} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      </PageSection>
    </div>
  )
}

export function DisabledNotice() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="scenarios-disabled">
      Scenario changes are switched off (SCENARIOS_ENABLED=false): the server refuses writes with 409 scenarios_disabled. Reads keep working.
    </p>
  )
}

function FieldNote({ problem, testId }: { problem: string | null | undefined; testId?: string }) {
  if (!problem) return null
  return (
    <p className="text-xs text-destructive" data-testid={testId ?? 'field-problem'}>
      {problem}
    </p>
  )
}

function TemplateCard({ template, disabled, onAdd }: { template: TemplateOut; disabled: boolean; onAdd: () => void }) {
  const Icon = template.direction === 'INFLOW' ? ArrowDownToLine : ArrowUpFromLine
  return (
    <Card data-testid="template-card" data-template={template.template}>
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Icon className="h-4 w-4 text-muted-foreground" />
          {template.name}
        </CardTitle>
        <div className="flex flex-wrap gap-1">
          <Badge variant="outline" className="font-mono">
            {template.template}
          </Badge>
          <Badge variant="secondary">{template.direction}</Badge>
          {template.changes_amounts ? <Badge variant="warning">Changes amounts</Badge> : <Badge variant="info">Timing only</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-muted-foreground">{template.description}</p>
        <Button type="button" size="sm" variant="outline" onClick={onAdd} disabled={disabled} data-testid={`add-${template.template}`}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add shock
        </Button>
      </CardContent>
    </Card>
  )
}

function ShockCard({
  index,
  template,
  draft,
  limits,
  problems,
  touched,
  onChange,
  onRemove,
}: {
  index: number
  template: TemplateOut
  draft: ShockDraft
  limits: ScenarioLimits
  problems: Record<string, string>
  touched: boolean
  onChange: (change: (d: ShockDraft) => ShockDraft) => void
  onRemove: () => void
}) {
  const [seen, setSeen] = useState<Record<string, boolean>>({})
  const setParam = (name: string, value: string) => onChange((d) => ({ ...d, parameters: { ...d.parameters, [name]: value } }))
  const setSel = (name: string, value: string) => onChange((d) => ({ ...d, selector: { ...d.selector, [name]: value } }))
  const visible = (path: string) => (touched || seen[path] ? problems[path] : undefined)
  const blur = (path: string) => setSeen((s) => ({ ...s, [path]: true }))

  return (
    <Card data-testid="shock-card" data-index={index} data-template={template.template}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm">
          Shock {index + 1} · {template.name} <span className="font-mono text-xs text-muted-foreground">{template.template}</span>
        </CardTitle>
        <Button type="button" size="sm" variant="ghost" onClick={onRemove} aria-label={`Remove shock ${index + 1}`} data-testid="remove-shock">
          <Trash2 className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          {template.parameters.map((f) => (
            <FieldInput
              key={f.name}
              idPrefix={draft.key}
              path={`parameters.${f.name}`}
              field={f}
              limits={limits}
              value={draft.parameters[f.name] ?? ''}
              problem={visible(`parameters.${f.name}`)}
              onChange={(v) => setParam(f.name, v)}
              onBlur={() => blur(`parameters.${f.name}`)}
            />
          ))}
        </div>
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Selector</p>
          <div className="grid gap-4 md:grid-cols-2">
            {template.selector.map((f) =>
              f.kind === 'INSTRUMENT_KEYS' ? (
                <div key={f.name} className="space-y-1 md:col-span-2" data-testid="shock-field" data-field={`selector.${f.name}`}>
                  <Label>{fieldLabel(f.name)}</Label>
                  <p className="text-xs text-muted-foreground">{f.description}</p>
                  <InstrumentPicker
                    instrumentType={(draft.selector.instrument_type ?? '').trim()}
                    selected={draft.instrumentKeys}
                    masked={draft.maskedKeys}
                    max={f.max_items}
                    onChange={(keys) => onChange((d) => ({ ...d, instrumentKeys: keys }))}
                    onRemoveMasked={(i) => onChange((d) => ({ ...d, maskedKeys: d.maskedKeys.filter((_, j) => j !== i) }))}
                    testId={`instrument-picker-${index}`}
                  />
                  <FieldNote problem={visible(`selector.${f.name}`)} />
                </div>
              ) : (
                <FieldInput
                  key={f.name}
                  idPrefix={draft.key}
                  path={`selector.${f.name}`}
                  field={f}
                  limits={limits}
                  value={draft.selector[f.name] ?? ''}
                  problem={visible(`selector.${f.name}`)}
                  onChange={(v) => setSel(f.name, v)}
                  onBlur={() => blur(`selector.${f.name}`)}
                />
              ),
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

/** One typed input, by the template field's kind. There is no free-text formula field; values are checked as typed. */
function FieldInput({
  idPrefix,
  path,
  field,
  limits,
  value,
  problem,
  onChange,
  onBlur,
}: {
  idPrefix: string
  path: string
  field: TemplateField
  limits: ScenarioLimits
  value: string
  problem: string | undefined
  onChange: (v: string) => void
  onBlur: () => void
}) {
  const id = `${idPrefix}-${path}`
  const percent = isPercent(field)
  const name = path.replace('.', '-').replace(/_/g, '-')
  let control
  if (field.kind === 'ENUM' || field.kind === 'INSTRUMENT_TYPE') {
    const fixed = field.kind === 'ENUM' && !field.required && field.values.length === 1
    control = (
      <select id={id} className={selectClass} value={value} onChange={(e) => onChange(e.target.value)} onBlur={onBlur} data-testid={`field-${name}`}>
        {!fixed && <option value="">{field.required ? 'Choose…' : 'Any'}</option>}
        {field.values.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    )
  } else {
    const numeric = field.kind === 'DECIMAL' || field.kind === 'INTEGER'
    control = (
      <div className="flex items-center gap-2">
        <Input
          id={id}
          value={value}
          inputMode={field.kind === 'DECIMAL' ? 'decimal' : field.kind === 'INTEGER' ? 'numeric' : undefined}
          maxLength={field.kind === 'CURRENCY' ? 3 : numeric ? 12 : limits.max_instrument_key_length}
          onChange={(e) => onChange(field.kind === 'CURRENCY' ? e.target.value.toUpperCase() : e.target.value)}
          onBlur={onBlur}
          aria-invalid={!!problem}
          data-testid={`field-${name}`}
        />
        {percent && <span className="text-sm text-muted-foreground">%</span>}
        {field.kind === 'INTEGER' && field.unit && <span className="whitespace-nowrap text-xs text-muted-foreground">{field.unit}</span>}
      </div>
    )
  }
  return (
    <div className="space-y-1" data-testid="shock-field" data-field={path}>
      <Label htmlFor={id}>
        {fieldLabel(field.name)}
        {field.required && ' (required)'}
      </Label>
      {control}
      <p className="text-xs text-muted-foreground">
        {boundsText(field, limits.fraction_scale)}
        {percent && value.trim() && fieldProblem(field, value, limits.fraction_scale) === null && (
          <>
            {' '}
            · stored as <span className="font-mono" data-testid={`stored-${name}`}>{percentToFraction(value.trim(), fractionScaleOf(field, limits.fraction_scale))}</span>
          </>
        )}
      </p>
      <FieldNote problem={problem} testId={`problem-${name}`} />
    </div>
  )
}

/** The server's bounds of a field, in the unit the analyst types (`servedScale`: GET config/limits `fraction_scale`). */
function boundsText(field: TemplateField, servedScale: number): string {
  if (isPercent(field)) {
    const pct = (b: string | null) => (b === null ? '' : `${fractionToPercent(b)}%`)
    const scale = fractionScaleOf(field, servedScale)
    return `${field.min_exclusive ? 'More than' : 'From'} ${pct(field.min)} to ${pct(field.max)}, at most ${percentScale(scale)} decimals (scale ${scale}).`
  }
  if (field.kind === 'INTEGER') return `${field.min ?? '0'} to ${field.max ?? '…'} ${field.unit ?? ''}`.trim() + '.'
  if (field.kind === 'ENUM' && field.values.length === 1) return `${field.values[0]} only in v1.`
  return field.description
}
