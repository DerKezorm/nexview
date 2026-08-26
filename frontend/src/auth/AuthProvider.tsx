/** Hält den Anmelde-Zustand der App und stellt ihn allen Seiten bereit. */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import {
  api,
  logout as sitzungBeenden,
  restoreSession,
  setSessionLostHandler,
  setTokens,
} from '../api/client'
import type { TokenPair } from '../api/client'
import type { LoginWay, SetupStatus, User } from '../api/types'
import { changeLanguage } from '../i18n'
import type { Language } from '../i18n'
import { istTheme, themeAnwenden } from '../lib/theme'
import { AuthContext } from './context'
import type { AuthState, SetupPayload } from './context'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthState['status']>('loading')
  const [user, setUser] = useState<User | null>(null)
  const [needsSetup, setNeedsSetup] = useState(false)
  // Ob es die Anmeldung über den Media-Server gibt, muss vor dem Anmelden
  // feststehen - und dort darf noch niemand die Einstellungen lesen.
  const [mediaServerLogin, setMediaServerLogin] = useState(false)
  const [mediaServerWays, setMediaServerWays] = useState<LoginWay[]>([])

  const applyUser = useCallback((loaded: User) => {
    setUser(loaded)
    if (loaded.language === 'de' || loaded.language === 'en') {
      changeLanguage(loaded.language as Language)
    }
    // Die Darstellung haengt am Konto: wer sich an einem anderen Geraet
    // anmeldet, findet seine Einstellung wieder. Der Browser merkt sie sich
    // nur zusaetzlich, damit beim Laden nichts aufblitzt (siehe index.html).
    themeAnwenden(istTheme(loaded.theme) ? loaded.theme : 'dark')
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
    // ⚠️ Der Server muss mit: Die Sitzung haengt seit 0.21 an einem
    // HttpOnly-Cookie, das dieses Skript nicht loeschen kann. Wer nur den
    // Arbeitsspeicher leert, ist beim naechsten Neuladen wieder angemeldet.
    // Die Oberflaeche wartet trotzdem nicht darauf - abgemeldet ist man
    // sofort, das Cookie faellt eine Anfrage spaeter.
    void sitzungBeenden()
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
        setMediaServerLogin(setup.mediaserver_login)
        setMediaServerWays(setup.mediaserver_login_ways ?? [])

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

  /**
   * Aus fertigen Token eine Sitzung machen.
   *
   * Der gemeinsame Abschluss aller Anmeldewege. Ob das Passwort geprüft wurde
   * oder der Media-Server für die Identität gebürgt hat, spielt ab hier keine
   * Rolle mehr - und genau deshalb steht es an einer Stelle.
   */
  const loginWithTokens = useCallback(
    async (tokens: TokenPair) => {
      // Erst aufräumen, dann den neuen Benutzer setzen.
      forgetCachedData()
      setTokens(tokens)
      applyUser(await api.get<User>('/api/auth/me'))
    },
    [applyUser, forgetCachedData],
  )

  const login = useCallback(
    async (username: string, password: string) => {
      const tokens = await api.post<TokenPair>(
        '/api/auth/login',
        { username, password },
        { auth: false },
      )
      await loginWithTokens(tokens)
    },
    [loginWithTokens],
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
      mediaServerLogin,
      mediaServerWays,
      login,
      loginWithTokens,
      completeSetup,
      finishSetup,
      logout,
      updateUser: applyUser,
    }),
    [
      status,
      user,
      needsSetup,
      mediaServerLogin,
      mediaServerWays,
      login,
      loginWithTokens,
      completeSetup,
      finishSetup,
      logout,
      applyUser,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
