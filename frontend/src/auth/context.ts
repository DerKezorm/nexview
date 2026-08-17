/**
 * Definition des Anmelde-Zustands.
 *
 * Bewusst in einer eigenen Datei ohne React-Komponente: sonst kann der
 * Entwicklungsserver Änderungen nicht sauber nachladen und die App stürzt
 * beim Bearbeiten ab ("Fast Refresh").
 */

import { createContext } from 'react'

import type { User } from '../api/types'

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
  login: (username: string, password: string) => Promise<void>
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
