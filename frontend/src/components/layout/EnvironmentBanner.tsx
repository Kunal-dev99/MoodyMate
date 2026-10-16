import type { Schemas } from '@/api/client'
import { cn } from '@/lib/utils'

type EnvironmentInfo = Schemas['EnvironmentInfo']

/** "LOCAL · SYNTHETIC DATA"; null in production, where no banner is shown (FE-6.1). */
export function environmentLabel(env: EnvironmentInfo): string | null {
  if (env.env === 'production') return null
  const data = env.data_class === 'client' ? 'CLIENT DATA' : 'SYNTHETIC DATA'
  return `${env.env.toUpperCase()} · ${data}`
}

/**
 * Always-visible environment banner outside production (FE-6.1, DG-06), from the
 * backend's own settings, so nobody mistakes a test environment for the real one or
 * synthetic data for client data.
 */
export function EnvironmentBanner({ environment }: { environment: EnvironmentInfo }) {
  const label = environmentLabel(environment)
  if (!label) return null
  return (
    <div
      role="status"
      data-testid="environment-banner"
      className={cn(
        'sticky top-0 z-40 w-full border-b px-4 py-1 text-center text-xs font-semibold tracking-wide',
        environment.data_class === 'client'
          ? 'border-[hsl(var(--destructive)/0.3)] bg-[hsl(var(--destructive)/0.12)] text-[hsl(var(--destructive))]'
          : 'border-[hsl(var(--warning)/0.3)] bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning))]',
      )}
    >
      {label}
    </div>
  )
}
