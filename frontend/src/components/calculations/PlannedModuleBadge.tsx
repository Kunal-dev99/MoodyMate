import { cn } from '@/lib/utils'
import { ImplementationPill } from './RunStatusPill'
import { PLANNED_MODULE_REASON, type ModuleVersion } from './calculations'

/**
 * FE-18.1: the "Planned — extension point" badge of a PLANNED module version (D-31 §1), for
 * the New-run picker and the registry pages (FE-16.1). It is the implementation pill in its
 * PLANNED style, so it never reads as ready; its title says why it cannot be picked.
 */
export function PlannedModuleBadge({ className }: { className?: string }) {
  return <ImplementationPill status="PLANNED" className={className} title={PLANNED_MODULE_REASON} testId="planned-module-badge" />
}

const List = ({ items, testId }: { items: readonly string[]; testId: string }) => (
  <dd className="font-mono text-xs" data-testid={testId}>
    {items.length > 0 ? items.join(', ') : <span className="text-muted-foreground">—</span>}
  </dd>
)

/**
 * A module version's declared contracts, as GET /calculation-modules sends them: what it reads
 * (schemas, rules, assumption keys, run parameters) and what it writes (results, intermediates,
 * warning codes). For a PLANNED module these are the extension point's fixed contract.
 */
export function ModuleContracts({ module, className }: { module: Pick<ModuleVersion, 'input_contract' | 'output_contract'>; className?: string }) {
  const { input_contract: input, output_contract: output } = module
  return (
    <dl className={cn('grid grid-cols-[140px_1fr] gap-x-3 gap-y-1 text-xs', className)} data-testid="module-contracts">
      <dt className="text-muted-foreground">Reads schemas</dt>
      <List
        items={input.schemas.map((s) => `${s.schema_code}${s.required ? ' (required)' : ' (optional)'}${s.fields.length ? `: ${s.fields.join(' ')}` : ''}`)}
        testId="contract-schemas"
      />
      <dt className="text-muted-foreground">Reads rules</dt>
      <List items={input.rules} testId="contract-rules" />
      <dt className="text-muted-foreground">Assumption keys</dt>
      <List items={input.assumption_keys} testId="contract-assumption-keys" />
      <dt className="text-muted-foreground">Run parameters</dt>
      <List items={input.parameters} testId="contract-parameters" />
      <dt className="text-muted-foreground">Writes results</dt>
      <List items={output.results} testId="contract-results" />
      <dt className="text-muted-foreground">Intermediates</dt>
      <List items={output.intermediates} testId="contract-intermediates" />
      <dt className="text-muted-foreground">Warning codes</dt>
      <List items={output.warning_codes} testId="contract-warning-codes" />
    </dl>
  )
}
