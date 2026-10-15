'use client'

import { useEffect, useState, type ReactNode } from 'react'
import Image from 'next/image'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Search } from 'lucide-react'

/**
 * Generic top bar lifted from fp_reporting. The logo-on-dark plate, sticky
 * border, command-palette button, and right-aligned action slot are reusable.
 *
 * The original header also rendered notifications, workspace switcher, and
 * a user menu — those are app-specific and belong in the `actions` slot.
 */
export interface HeaderProps {
  /** Brand title shown to the right of the logo plate. */
  title: string
  /** URL or imported path for the logo image (white-knockout looks best). */
  logoSrc: string
  /** Alt text for the logo. */
  logoAlt?: string
  /** Optional command palette opener (Cmd/Ctrl+K). Omit to hide the search button. */
  onOpenPalette?: () => void
  /** Right-hand action slot — drop in workspace switcher, bell, avatar menu, etc. */
  actions?: ReactNode
}

export function Header({
  title,
  logoSrc,
  logoAlt = 'Logo',
  onOpenPalette,
  actions,
}: HeaderProps) {
  const [isMac, setIsMac] = useState(false)

  useEffect(() => {
    if (typeof navigator !== 'undefined') {
      setIsMac(/Mac|iPhone|iPad/i.test(navigator.platform))
    }
  }, [])

  return (
    <header className="sticky top-0 z-30 w-full border-b border-border bg-surface-1">
      <div className={cn('flex h-16 items-center gap-4 px-6')}>
        <div className="flex items-center gap-3 shrink-0">
          {/* Logo plate: white-knockout logo on a fixed dark slate background so
              the asset survives both light and dark themes without recoloring. */}
          <div className="flex items-center justify-center rounded-md bg-[#15171A] px-3 py-1.5 shadow-sm">
            <Image
              src={logoSrc}
              alt={logoAlt}
              width={140}
              height={28}
              priority
              className="h-7 w-auto"
            />
          </div>
          <div className="border-l border-border pl-3">
            <h1 className="text-base font-semibold tracking-tight text-foreground">{title}</h1>
          </div>
        </div>

        {onOpenPalette && (
          <button
            type="button"
            onClick={onOpenPalette}
            className="group ml-8 hidden md:flex items-center gap-2 rounded-md border border-border bg-surface-2 px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-foreground/20 hover:text-foreground w-full max-w-sm"
            aria-label="Open command palette (Ctrl+K / Cmd+K)"
          >
            <Search className="h-4 w-4" />
            <span className="flex-1 text-left">Search or jump to…</span>
            <kbd className="hidden sm:inline-flex items-center gap-0.5 rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium">
              <span className="text-xs">{isMac ? '⌘' : 'Ctrl'}</span>
              <span>K</span>
            </kbd>
          </button>
        )}

        {actions && <div className="flex items-center gap-2 ml-auto">{actions}</div>}
      </div>
    </header>
  )
}
