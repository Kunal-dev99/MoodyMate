import { cn } from '@/lib/utils'
import { ModuleContracts, PlannedModuleBadge } from './PlannedModuleBadge'
import { ImplementationPill } from './RunStatusPill'
import { ValidationStatusBadge } from './ValidationStatusBadge'
import { PLANNED_MODULE_REASON, isSelectable, type ModuleVersion } from './calculations'

/**
 * One module version in the New-run picker (FE-15.1, FE-18.1). An AVAILABLE one is a radio
 * option. A PLANNED one is a disabled "Planned — extension point" entry with the reason and its
 * declared contracts: its radio is disabled and a change event never picks it. Cosmetic only:
 * the server refuses a run of it (409 module_not_available, D-04).
 */
export function ModuleOption({
  module: m,
  checked,
  busy,
  missing,
  onPick,
}: {
  module: ModuleVersion
  checked: boolean
  busy: boolean
  /** Schemas the module requires that the built snapshot lacks. */
  missing: readonly string[]
  onPick: (moduleVersionId: string) => void
}) {
  const selectable = isSelectable(m)
  const reasonId = `module-reason-${m.module_version_id}`
  return (
    <li
      className={cn('rounded-md border border-border px-3 py-2', checked && 'border-primary', !selectable && 'opacity-70')}
      data-testid="module-option"
      data-code={m.code}
      data-module-version-id={m.module_version_id}
      data-status={m.implementation_status}
      aria-disabled={selectable ? undefined : true}
    >
      <label className={cn('flex items-start gap-3', selectable ? 'cursor-pointer' : 'cursor-not-allowed')}>
        <input
          type="radio"
          name="module"
          className="mt-1"
          value={m.module_version_id}
          checked={checked}
          disabled={!selectable || busy}
          aria-describedby={selectable ? undefined : reasonId}
          onChange={() => {
            if (selectable && !busy) onPick(m.module_version_id)
          }}
          data-testid="module-radio"
        />
        <span className="min-w-0 flex-1 space-y-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{m.name}</span>
            <span className="font-mono text-xs text-muted-foreground">
              {m.code} v{m.version} · engine {m.engine_version}
            </span>
            {selectable ? <ImplementationPill status={m.implementation_status} /> : <PlannedModuleBadge />}
            {/* FE-30.1: the version's validation status (D-56), as in the module registry. */}
            <ValidationStatusBadge status={m.validation_status} testId="module-validation-status" />
            {selectable && !m.runnable && (
              <span className="text-xs text-destructive" data-testid="module-not-runnable">
                Not runnable now: a rule has no ACTIVE version or no baseline assumption set is ACTIVE (the server answers 409 module_not_available).
              </span>
            )}
          </span>
          {m.description && <span className="block text-xs text-muted-foreground">{m.description}</span>}
          {!selectable && (
            <span id={reasonId} className="block text-xs text-muted-foreground" data-testid="module-planned-reason">
              {PLANNED_MODULE_REASON}
            </span>
          )}
          <span className="block text-xs text-[hsl(var(--warning))]" data-testid="module-statement">
            {m.statement}
          </span>
          {checked && missing.length > 0 && (
            <span className="block text-xs text-destructive" data-testid="module-missing-schemas">
              The snapshot lacks {missing.join(', ')}, which this module requires: the server will end the run BLOCKED.
            </span>
          )}
        </span>
      </label>
      {!selectable && <ModuleContracts module={m} className="mt-2 pl-6" />}
    </li>
  )
}
