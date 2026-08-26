/**
 * Eine Seite so rendern, wie sie in der Anwendung hängt.
 *
 * Jede Seite braucht dieselben drei Dinge — Router, Query-Client und den
 * Anmelde-Zustand. Das dreimal abzuschreiben hieße, dass die drei Fassungen
 * nach dem ersten Feinschliff auseinanderlaufen.
 *
 * ⚠️ **Der `AuthProvider` ist der echte, nicht nachgebaut.** Er entscheidet,
 * was nach einer Anmeldung passiert, räumt den Zwischenspeicher und hält den
 * Benutzer — genau das soll ein Test prüfen. Nachgebaut würde er in jedem
 * Test das Richtige tun und in der Anwendung womöglich nicht.
 *
 * Ersetzt wird stattdessen die **API-Schicht** darunter (`vi.mock` auf
 * `../api/client` in der jeweiligen Testdatei): Dort endet unsere
 * Zuständigkeit, und ein Test, der einen echten Server bräuchte, liefe nicht
 * in Sekunden.
 */

import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'

import { AuthProvider } from '../auth/AuthProvider'

/** Frischer Query-Client je Test, ohne Wiederholungen. */
function neuerClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // ⚠️ Ohne das wartet ein Test, der einen Fehlerfall prüft, auf drei
        // Wiederholungsversuche - und läuft in seine Zeitgrenze, statt den
        // Fehler zu zeigen.
        retry: false,
        gcTime: 0,
      },
      mutations: { retry: false },
    },
  })
}

export function huellen(kinder: ReactNode, { pfad = '/' } = {}) {
  const client = neuerClient()
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[pfad]}>
        <AuthProvider>{kinder}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

/** Wie `render`, nur mit allem drum herum. */
export function rendern(element: ReactElement, optionen: { pfad?: string } = {}) {
  return render(huellen(element, optionen))
}

/**
 * Ohne Anmelde-Zustand — für Bauteile, die ihn gar nicht brauchen.
 *
 * Spart den Aufruf von `/api/setup/status`, den der `AuthProvider` beim
 * Erscheinen macht, und damit eine Attrappe, die nichts prüft.
 */
export function rendernSchlicht(element: ReactElement, { pfad = '/' } = {}) {
  const client = neuerClient()
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[pfad]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  )
}
