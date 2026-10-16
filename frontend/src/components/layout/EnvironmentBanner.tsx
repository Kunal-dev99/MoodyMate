import type { Schemas } from '@/api/client'
import { cn } from '@/lib/utils'

type EnvironmentInfo = Schemas['EnvironmentInfo']

/**
 * "LOCAL · SYNTHETIC DATA"; null in production, where no banner is shown (FE-6.1). When the
 * environment cannot be read, the banner says so rather than disappearing (fail closed).
 */
export function environmentLabel(env: EnvironmentInfo | null): string | null {
  if (env === null) return 'ENVIRONMENT UNKNOWN · TREAT DATA AS REAL'
  if (env.env === 'production') return null
  const data = env.data_class === 'client' ? 'CLIENT DATA' : 'SYNTHETIC DATA'
  return `${env.env.toUpperCase()} · ${data}`
}

/**
 * Always-visible environment banner outside production (FE-6.1, DG-06), from the
 * backend's own settings, so nobody mistakes a test environment for the real one or
 * synthetic data for client data. Opaque, with body-text contrast; the colour bar only
 * adds emphasis (red for client data or unknown, amber for synthetic). Callers keep it in
 * view: the app shell pins it together with the header.
 */
export function EnvironmentBanner({ environment }: { environment: EnvironmentInfo | null }) {
  const label = environmentLabel(environment)
  if (!label) return null
  const alarming = environment === null || environment.data_class === 'client'
  return (
    <div
      role="region"
      aria-label="Environment"
      data-testid="environment-banner"
      className={cn(
        'w-full border-b border-l-4 bg-surface-1 px-4 py-1 text-center text-xs font-semibold tracking-wide text-foreground',
        // A tint layered over the opaque surface colour: nothing scrolls through it.
        alarming
          ? 'border-l-[hsl(var(--destructive))] bg-[linear-gradient(hsl(var(--destructive)/0.14),hsl(var(--destructive)/0.14))]'
          : 'border-l-[hsl(var(--warning))] bg-[linear-gradient(hsl(var(--warning)/0.16),hsl(var(--warning)/0.16))]',
      )}
    >
      {label}
    </div>
  )
}
