import type { ReactNode } from 'react'
import { BookOpenCheck, Info } from 'lucide-react'
import { PageSection } from '@/components/common/PageSection'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { ValidationStatusBadge } from './ValidationStatusBadge'
import { CONTEXT_FIELD_TEXT, CURRENCY_BASIS_TEXT, contextMismatches, type ContextPins, type ResultContext } from './validation'

function Item({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0" data-testid={testId}>
        {children}
      </dd>
    </>
  )
}

/**
 * The server's context block, field by field. Every value is the server's text, never derived.
 * `units` text is rendered as-is and never branched on (only a null ratio, "not applicable", is
 * left out).
 */
function ContextFields({ context: c }: { context: ResultContext }) {
  return (
    <dl className="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1 text-xs">
      <Item label="As of (reporting date)" testId="context-as-of-date">
        <span className="font-mono">{c.as_of_date}</span>
      </Item>
      <Item label="Validation status">
        <ValidationStatusBadge status={c.validation_status} testId="context-validation-status" />
      </Item>
      <Item label="Methodology" testId="context-module">
        <span className="font-mono">
          {c.methodology.module_code} v{c.methodology.module_version}
        </span>
      </Item>
      <Item label="Engine" testId="context-engine">
        <span className="font-mono">{c.methodology.engine_version}</span>
      </Item>
      <Item label="Rule versions">
        {c.methodology.rule_versions.length === 0 ? (
          <span className="text-muted-foreground" data-testid="context-rule-versions-none">
            None pinned
          </span>
        ) : (
          <ul className="flex flex-wrap gap-x-3 gap-y-0.5" data-testid="context-rule-versions">
            {c.methodology.rule_versions.map((r) => (
              <li key={r.rule_version_id} className="font-mono" title={`Rule version ${r.rule_version_id}`} data-testid="context-rule-version" data-code={r.rule_code}>
                {r.rule_code} v{r.version}
              </li>
            ))}
          </ul>
        )}
      </Item>
      <Item label="Currency" testId="context-currency">
        <span className="font-mono" data-testid="context-currency-codes">
          {c.currency.codes.length > 0 ? c.currency.codes.join(', ') : '—'}
        </span>
        <span className="ml-2 text-muted-foreground" data-testid="context-currency-basis" data-basis={c.currency.basis}>
          {CURRENCY_BASIS_TEXT[c.currency.basis] ?? c.currency.basis}
        </span>
      </Item>
      <Item label="Amounts" testId="context-units-amount">
        {c.units.amount}
      </Item>
      <Item label="Days" testId="context-units-days">
        {c.units.days}
      </Item>
      {c.units.ratio !== null && (
        <Item label="Ratio" testId="context-units-ratio">
          {c.units.ratio}
        </Item>
      )}
      <Item label="Statement" testId="context-statement">
        <span className="text-[hsl(var(--warning))]">{c.statement}</span>
      </Item>
    </dl>
  )
}

/**
 * Shown when the context block and the run's pins disagree: both are shown, neither is picked.
 * Display only and non-blocking (the result stays on screen); neutral wording, as the server's
 * checks, not this note, are the control (D-04).
 */
function Mismatch({ context, pins }: { context: ResultContext; pins: ContextPins }) {
  const fields = contextMismatches(context, pins)
  if (fields.length === 0) return null
  return (
    <Alert variant="default" data-testid="context-mismatch" data-fields={fields.join(' ')}>
      <Info className="h-4 w-4" />
      <AlertTitle>The result context differs from the run&apos;s record</AlertTitle>
      <AlertDescription className="text-muted-foreground">
        It differs on the {fields.map((f) => CONTEXT_FIELD_TEXT[f]).join(', ')}. Both are shown as sent; the run&apos;s manifest is the record. Quote the run ID if
        you raise it with support.
      </AlertDescription>
    </Alert>
  )
}

/**
 * FE-30.1 (D-56 §5): the "Result context" of a result read (run detail, maturity ladder, LCR,
 * comparison): the as-of date, the validation status, the methodology pins (module, engine and
 * rule versions), the currency basis, the units and the D-35 statement, exactly as the server
 * built them from the run's pins. With `pins` (the run detail it belongs to) a disagreement is
 * shown, never resolved. `compact` renders inside an existing section; otherwise its own one.
 * A response without the block (an older server) says so instead of showing nothing.
 */
export function ResultContextPanel({
  context,
  pins,
  compact = false,
  source,
  className,
}: {
  context: ResultContext | null | undefined
  pins?: ContextPins | null
  compact?: boolean
  /** Which read the block came from, for tests and support (e.g. "run", "maturity-ladder"). */
  source: string
  className?: string
}) {
  const body = !context ? (
    <p className="text-xs text-muted-foreground" data-testid="context-none">
      The server sent no result context with this read, so its validation status is treated as not validated.
    </p>
  ) : (
    <div className="space-y-2">
      {pins && <Mismatch context={context} pins={pins} />}
      <ContextFields context={context} />
    </div>
  )
  if (compact)
    return (
      <div
        className={cn('space-y-1 rounded-md border border-border bg-surface-2 px-3 py-2', className)}
        data-testid="result-context"
        data-source={source}
        data-as-of-date={context?.as_of_date}
        data-validation-status={context?.validation_status}
      >
        <div className="flex items-center gap-1.5 text-xs font-medium">
          <BookOpenCheck className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
          Result context
        </div>
        {body}
      </div>
    )
  return (
    <PageSection
      icon={BookOpenCheck}
      title="Result context"
      description="Built by the server from the run's pins, never computed: what the figures are measured in, as of when, under which methodology versions, and how far that methodology is validated."
      className={className}
    >
      <div data-testid="result-context" data-source={source} data-as-of-date={context?.as_of_date} data-validation-status={context?.validation_status}>
        {body}
      </div>
    </PageSection>
  )
}
