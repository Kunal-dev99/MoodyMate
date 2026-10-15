'use client'

import { create } from 'zustand'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'fp_theme'

function getSystemDark(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function resolveDark(mode: ThemeMode): boolean {
  if (mode === 'dark') return true
  if (mode === 'light') return false
  return getSystemDark()
}

function applyTheme(mode: ThemeMode) {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  if (resolveDark(mode)) {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
}

function readSaved(): ThemeMode {
  if (typeof window === 'undefined') return 'light'
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY) as ThemeMode | null
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  } catch {
    // ignore
  }
  return 'light'
}

interface ThemeStore {
  mode: ThemeMode
  resolved: 'light' | 'dark'
  setMode: (mode: ThemeMode) => void
  hydrate: () => void
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  mode: 'light',
  resolved: 'light',
  setMode: (mode) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, mode)
    } catch {
      // ignore
    }
    applyTheme(mode)
    set({ mode, resolved: resolveDark(mode) ? 'dark' : 'light' })
  },
  hydrate: () => {
    const mode = readSaved()
    applyTheme(mode)
    set({ mode, resolved: resolveDark(mode) ? 'dark' : 'light' })

    // Listen for OS theme changes when in system mode.
    if (typeof window !== 'undefined' && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      const onChange = () => {
        if (get().mode === 'system') {
          applyTheme('system')
          set({ resolved: getSystemDark() ? 'dark' : 'light' })
        }
      }
      try {
        mq.addEventListener('change', onChange)
      } catch {
        // Safari < 14 fallback
        mq.addListener(onChange)
      }
    }
  },
}))
