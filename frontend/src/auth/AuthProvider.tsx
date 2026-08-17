/** Hält den Anmelde-Zustand der App und stellt ihn allen Seiten bereit. */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api, clearTokens, restoreSession, setSessionLostHandler, setTokens } from '../api/client'
import type { TokenPair } from '../api/client'
import type { SetupStatus, User } from '../api/types'
import { changeLanguage } from '../i18n'
import type { Language } from '../i18n'
import { AuthContext } from './context'
import type { AuthState, SetupPayload } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthState['status']>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [needsSetup, setNeedsSetup] = useState(false)

  const applyUser = useCallback((loaded: User) => {
    setUser(loaded)
    if (loaded.language === 'de' || loaded.language === 'en') {
      changeLanguage(loaded.language as Language)
    }
  }, [])

  /**
   * Zwischengespeicherte Antworten verwerfen.
   *
   * Ohne das sähe der nächste Benutzer am selben Rechner noch die Anfragen,
   * Kontingente und Benachrichtigungen seines Vorgängers - die Daten stammen
   * dann aus dem Zwischenspeicher, nicht vom Server.
   */
  const forgetCachedData = useCallback(() => queryClient.clear(), [queryClient])

  const logout = useCallback(() => {
    clearTokens()
    setUser(null)
    forgetCachedData()
  }, [forgetCachedData])

  // Beim Start: Ist die Einrichtung nötig? Und lässt sich eine alte Sitzung fortsetzen?
  useEffect(() => {
    let cancelled = false

    async function bootstrap() {
      try {
        const setup = await api.get<SetupStatus>('/api/setup/status', { auth: false })
        if (cancelled) return
        setNeedsSetup(setup.needs_setup)

        if (!setup.needs_setup && (await restoreSession())) {
          const me = await api.get<User>('/api/auth/me')
          if (!cancelled) applyUser(me)
        }
      } catch {
        // Backend nicht erreichbar - die Oberfläche zeigt dann die Anmeldung
        // mit einer Fehlermeldung beim ersten Versuch.
      } finally {
        if (!cancelled) setStatus('ready')
      }
    }

    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [applyUser])

  useEffect(() => {
    setSessionLostHandler(() => setUser(null))
    return () => setSessionLostHandler(null)
  }, [])

  const login = useCallback(
    async (username: string, password: string) => {
      const tokens = await api.post<TokenPair>(
        '/api/auth/login',
        { username, password },
        { auth: false },
      )
      // Erst aufräumen, dann den neuen Benutzer setzen.
      forgetCachedData()
      setTokens(tokens)
      applyUser(await api.get<User>('/api/auth/me'))
    },
    [applyUser, forgetCachedData],
  )

  const completeSetup = useCallback(
    async (payload: SetupPayload) => {
      const tokens = await api.post<TokenPair>('/api/setup/admin', payload, { auth: false })
      setTokens(tokens)
      // needsSetup bleibt true - der Assistent hat noch weitere Schritte.
      applyUser(await api.get<User>('/api/auth/me'))
    },
    [applyUser],
  )

  const finishSetup = useCallback(() => setNeedsSetup(false), [])

  const value = useMemo<AuthState>(
    () => ({
      status,
      user,
      needsSetup,
      login,
      completeSetup,
      finishSetup,
      logout,
      updateUser: applyUser,
    }),
    [status, user, needsSetup, login, completeSetup, finishSetup, logout, applyUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
