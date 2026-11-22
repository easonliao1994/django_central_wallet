import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { getToken, onUnauthorized, setToken } from '../api/client'
import * as api from '../api/endpoints'
import type { User } from '../api/types'

interface AuthState {
  token: string | null
  user: User | undefined
  /** True while the profile of a stored token is being loaded. */
  loading: boolean
  /** Set when the token stopped working (shown on the login page). */
  expired: boolean
  signIn: (token: string, user: User) => void
  signOut: () => Promise<void>
  /** Replace the token (after a password change the backend issues a new one). */
  replaceToken: (token: string) => void
  setUser: (user: User) => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [token, setTokenState] = useState<string | null>(() => getToken())
  const [expired, setExpired] = useState(false)

  const me = useQuery({
    queryKey: ['me', token],
    queryFn: api.getMe,
    enabled: !!token,
    staleTime: 30_000,
  })

  const clear = useCallback(() => {
    setToken(null)
    setTokenState(null)
    queryClient.clear()
  }, [queryClient])

  useEffect(
    () =>
      onUnauthorized(() => {
        setExpired(true)
        clear()
      }),
    [clear],
  )

  const value = useMemo<AuthState>(
    () => ({
      token,
      user: me.data,
      loading: !!token && me.isPending,
      expired,
      signIn(newToken, user) {
        setToken(newToken)
        setExpired(false)
        queryClient.setQueryData(['me', newToken], user)
        setTokenState(newToken)
      },
      async signOut() {
        try {
          await api.logout()
        } catch {
          // The token is dropped locally either way.
        }
        clear()
      },
      replaceToken(newToken) {
        setToken(newToken)
        const user = queryClient.getQueryData<User>(['me', token])
        if (user) queryClient.setQueryData(['me', newToken], user)
        setTokenState(newToken)
      },
      setUser(user) {
        queryClient.setQueryData(['me', token], user)
      },
    }),
    [token, me.data, me.isPending, expired, queryClient, clear],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

/** The signed-in user; only call inside routes guarded by RequireAuth. */
export function useUser(): User {
  const { user } = useAuth()
  if (!user) throw new Error('useUser called without a signed-in user')
  return user
}
