'use client'

import { ReactNode } from 'react'
import { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Card, CardContent } from '@/components/ui/card'

interface PageSectionProps {
  icon?: LucideIcon
  title: string
  description?: string
  actions?: ReactNode
  children: ReactNode
  /** Apply a colored accent rail (semantic color name) */
  accent?: 'primary' | 'accent' | 'warning' | 'success' | 'danger'
  /** Add subtle glow for "needs attention" surfaces */
  attention?: boolean
  className?: string
  contentClassName?: string
  headerRight?: ReactNode
  collapsible?: boolean
  defaultOpen?: boolean
  id?: string
}

const ACCENT_CLASSES: Record<NonNullable<PageSectionProps['accent']>, string> = {
  primary: 'before:bg-primary/70',
  accent: 'before:bg-accent/70',
  warning: 'before:bg-warning/70',
  success: 'before:bg-success/70',
  danger: 'before:bg-danger/70',
}

export function PageSection({
  icon: Icon,
  title,
  description,
  actions,
  children,
  accent,
  attention,
  className,
  contentClassName,
  headerRight,
  id,
}: PageSectionProps) {
  return (
    <Card
      id={id}
      className={cn(
        'card-elevated relative overflow-hidden',
        accent &&
          "before:content-[''] before:absolute before:left-0 before:top-0 before:bottom-0 before:w-[3px]",
        accent && ACCENT_CLASSES[accent],
        attention && 'ring-1 ring-warning/40 shadow-[0_0_0_1px_hsl(var(--warning)/0.15)]',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-4 px-6 pt-5 pb-3">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          {Icon && (
            <div
              className={cn(
                'flex h-9 w-9 items-center justify-center rounded-lg shrink-0',
                accent === 'accent'
                  ? 'bg-accent/15 text-accent'
                  : accent === 'warning'
                    ? 'bg-warning/15 text-warning'
                    : accent === 'success'
                      ? 'bg-success/15 text-success'
                      : accent === 'danger'
                        ? 'bg-danger/15 text-danger'
                        : 'bg-primary/15 text-primary',
              )}
            >
              <Icon className="h-[18px] w-[18px]" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-section-title">{title}</h2>
              {headerRight}
            </div>
            {description && (
              <p className="text-helper mt-0.5">{description}</p>
            )}
          </div>
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
      <div className="px-6 pb-5">
        <div className={cn('border-t border-border/40 pt-4', contentClassName)}>
          {children}
        </div>
      </div>
    </Card>
  )
}

// Inline variant without Card wrapper — for use inside an existing Card
export function SectionHeader({
  icon: Icon,
  title,
  description,
  actions,
  accent,
  className,
}: Pick<PageSectionProps, 'icon' | 'title' | 'description' | 'actions' | 'accent' | 'className'>) {
  return (
    <div className={cn('flex items-start justify-between gap-4', className)}>
      <div className="flex items-start gap-3 min-w-0 flex-1">
        {Icon && (
          <div
            className={cn(
              'flex h-9 w-9 items-center justify-center rounded-lg shrink-0',
              accent === 'accent'
                ? 'bg-accent/15 text-accent'
                : accent === 'warning'
                  ? 'bg-warning/15 text-warning'
                  : accent === 'success'
                    ? 'bg-success/15 text-success'
                    : accent === 'danger'
                      ? 'bg-danger/15 text-danger'
                      : 'bg-primary/15 text-primary',
            )}
          >
            <Icon className="h-[18px] w-[18px]" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <h3 className="text-section-title">{title}</h3>
          {description && <p className="text-helper mt-0.5">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  )
}
