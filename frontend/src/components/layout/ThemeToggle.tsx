'use client'

import { useEffect } from 'react'
import { Sun, Moon, Monitor } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useThemeStore, type ThemeMode } from '@/lib/theme'
import { cn } from '@/lib/utils'

interface ThemeToggleProps {
  className?: string
  variant?: 'icon' | 'compact'
}

export function ThemeToggle({ className, variant = 'icon' }: ThemeToggleProps) {
  const { mode, resolved, setMode, hydrate } = useThemeStore()

  useEffect(() => {
    hydrate()
  }, [hydrate])

  const Icon = mode === 'system' ? Monitor : resolved === 'dark' ? Moon : Sun

  const items: Array<{ key: ThemeMode; label: string; icon: typeof Sun }> = [
    { key: 'light', label: 'Light', icon: Sun },
    { key: 'dark', label: 'Dark', icon: Moon },
    { key: 'system', label: 'System', icon: Monitor },
  ]

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size={variant === 'icon' ? 'icon' : 'sm'}
          className={cn(variant === 'icon' && 'h-7 w-7', className)}
          aria-label="Toggle theme"
        >
          <Icon className="h-4 w-4" />
          {variant === 'compact' && <span className="ml-2 text-xs capitalize">{mode}</span>}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        {items.map((item) => {
          const ItemIcon = item.icon
          const isActive = mode === item.key
          return (
            <DropdownMenuItem
              key={item.key}
              onClick={() => setMode(item.key)}
              className={cn(isActive && 'bg-muted text-foreground')}
            >
              <ItemIcon className="mr-2 h-4 w-4" />
              <span>{item.label}</span>
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
