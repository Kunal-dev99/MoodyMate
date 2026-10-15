/**
 * Client session (FE-2.1). Holds only the bearer token and how it was obtained.
 * Identity, roles and permissions are always fetched from the backend (/me), which is
 * authoritative (D-04): this store never decides what a user may do.
 *
 * Stored in sessionStorage: cleared when the tab closes, not shared across tabs.
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

export type SessionSource = 'dev' | 'oidc'

interface SessionState {
  token: string | null
  source: SessionSource | null
  setToken: (token: string, source: SessionSource) => void
  clear: () => void
}

export const SESSION_STORAGE_KEY = 'moodymate_session'

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      token: null,
      source: null,
      setToken: (token, source) => set({ token, source }),
      clear: () => set({ token: null, source: null }),
    }),
    {
      name: SESSION_STORAGE_KEY,
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
)
