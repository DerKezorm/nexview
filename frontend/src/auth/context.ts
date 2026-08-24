/**
 * Definition des Anmelde-Zustands.
 *
 * Bewusst in einer eigenen Datei ohne React-Komponente: sonst kann der
 * Entwicklungsserver Änderungen nicht sauber nachladen und die App stürzt
 * beim Bearbeiten ab ("Fast Refresh").
 */

import { createContext } from 'react'

import type { TokenPair } from '../api/client'
import type { LoginWay, User } from '../api/types'

export type SetupPayload = {
  username: string
  password: string
  /** Pflicht: ohne Adresse gäbe es keinen Weg zurück ins eigene Konto. */
  email: string
  display_name?: string
  language: string
}

export type AuthState = {
  status: 'loading' | 'ready'
  user: User | null
  needsSetup: boolean
  /** Ist ein Media-Server verbunden? Steuert den Knopf auf der Anmeldeseite. */
  mediaServerLogin: boolean
  /**
   * Welche Anmeldewege es gibt – je Anbieter einer.
   *
   * Die Anmeldeseite zeigt daraus die Logos. Ohne diese Liste müsste sie
   * raten, und geraten hat sie „Plex".
   */
  mediaServerWays: LoginWay[]
  login: (username: string, password: string) => Promise<void>
  /**
   * Aus fertigen Token eine Sitzung machen.
   *
   * Für Anmeldewege, bei denen nicht die App das Passwort prüft, sondern der
   * Media-Server für die Identität bürgt.
   */
  loginWithTokens: (tokens: TokenPair) => Promise<void>
  /**
   * Legt den ersten Administrator an und meldet ihn gleich an.
   *
   * ``needsSetup`` bleibt danach bewusst noch true: der Assistent hat weitere
   * Schritte (TMDB, Radarr, Sonarr) und würde sonst mitten drin verschwinden.
   * Erst ``finishSetup`` schaltet auf die eigentliche App um.
   */
  completeSetup: (payload: SetupPayload) => Promise<void>
  finishSetup: () => void
  logout: () => void
  updateUser: (user: User) => void
}

export const AuthContext = createContext<AuthState | null>(null)
