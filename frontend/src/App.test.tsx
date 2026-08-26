/**
 * Weg 3 von dreien: die Kinderansicht — und wer sie bekommt.
 *
 * ⚠️ **Die wichtigste Weiche der ganzen Anwendung.** Ein Kinderkonto bekommt
 * einen **eigenen Seitenbaum**, nicht denselben mit ausgeblendeten Punkten.
 * Was dort nicht steht, existiert für ein Kind nicht — auch nicht über die
 * Adresszeile.
 *
 * Der eigentliche Schutz sitzt im Backend (`require_adult` an allen
 * Erwachsenen-Routern, geprüft von `test_child_permissions.py` über die ganze
 * Routentabelle). Hier wird geprüft, dass die **Ansicht** dazu passt: Wer die
 * Sperre nur im Backend hat, zeigt einem Kind eine Seite voller Fehlermeldungen
 * statt einer Seite, die es nicht gibt.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'

vi.mock('./api/client', async () => {
  const echt = await vi.importActual<typeof import('./api/client')>('./api/client')
  return {
    ...echt,
    api: {
      // ⚠️ **Listen antworten mit einer Liste.** Ein pauschales ``{}`` sieht
      // harmlos aus und lässt jedes Bauteil scheitern, das ``.map`` darauf
      // aufruft - die Merkliste in der Kopfzeile etwa. Der Test prüft dann
      // nicht mehr die Weiche, sondern stirbt vorher.
      get: vi.fn(async (pfad: string) => {
        if (pfad === '/api/config') return { radarr_configured: true, sonarr_configured: true }
        if (pfad === '/api/auth/me') return zustand.user ?? {}
        return []
      }),
      post: vi.fn(async () => ({})),
      put: vi.fn(async () => ({})),
      patch: vi.fn(async () => ({})),
      delete: vi.fn(async () => ({})),
    },
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
    logout: vi.fn(),
    restoreSession: vi.fn(async () => false),
    setSessionLostHandler: vi.fn(),
  }
})

// Den Anmelde-Zustand setzen wir hier von Hand: Geprüft wird die **Weiche**,
// nicht der Weg dorthin. Den prüft `LoginPage.test.tsx`.
const zustand = {
  status: 'ready' as 'loading' | 'ready',
  user: null as null | Record<string, unknown>,
  needsSetup: false,
  mediaServerLogin: false,
  mediaServerWays: [],
  login: vi.fn(),
  loginWithTokens: vi.fn(),
  completeSetup: vi.fn(),
  finishSetup: vi.fn(),
  logout: vi.fn(),
  updateUser: vi.fn(),
}
vi.mock('./auth/useAuth', () => ({ useAuth: () => zustand }))

import App from './App'

function zeigen(pfad: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[pfad]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const KIND = {
  id: 5,
  username: 'kind1',
  role: 'child',
  language: 'de',
  theme: 'dark',
  can_approve: false,
}

const ERWACHSEN = { ...KIND, id: 2, username: 'kim', role: 'user' }

beforeEach(() => {
  vi.clearAllMocks()
  zustand.user = null
  zustand.status = 'ready'
  zustand.needsSetup = false
})

describe('Wer welchen Seitenbaum bekommt', () => {
  it('ohne Anmeldung die Anmeldeseite', async () => {
    zeigen('/')
    expect(await screen.findByRole('button', { name: /^anmelden$/i })).toBeInTheDocument()
  })

  it('ein Kind die Kinderansicht', async () => {
    zustand.user = KIND
    zeigen('/')
    // Die Kinderansicht hat keine Erwachsenen-Navigation - kein „Stöbern",
    // kein „Kalender", keine „Einstellungen".
    await waitFor(() => {
      expect(screen.queryAllByRole('link', { name: 'Stöbern' })).toHaveLength(0)
      expect(screen.queryAllByRole('link', { name: 'Kalender' })).toHaveLength(0)
    })
  })

  it('⚠️ ein Kind kommt auch über die Adresszeile nicht in die Einstellungen', async () => {
    // Der Fall, der zählt: Wer die Sperre nur in der Navigation hätte, käme
    // hier durch. Der Kinder-Seitenbaum kennt den Pfad gar nicht und leitet
    // auf die Startseite um.
    zustand.user = KIND
    zeigen('/admin/settings')

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /einstellungen/i })).toBeNull()
    })
  })

  it('⚠️ ein Kind kommt auch nicht in die Statistik', async () => {
    zustand.user = KIND
    zeigen('/admin/stats')

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /statistik|was liegt/i })).toBeNull()
    })
  })

  it('ein gewöhnliches Konto sieht die normale Oberfläche', async () => {
    zustand.user = ERWACHSEN
    zeigen('/')
    // Die Erwachsenen-Navigation ist da.
    //
    // ⚠️ **In der Mehrzahl gesucht.** Die Kopfzeile trägt die Navigation
    // zweimal: einmal breit, einmal als schmale Zeile darunter. Welche man
    // sieht, entscheidet allein die Bildschirmbreite - im Baum stehen immer
    // beide. Eine Suche im Singular scheitert daran, ohne dass etwas kaputt
    // wäre.
    expect((await screen.findAllByRole('link', { name: 'Stöbern' })).length).toBeGreaterThan(0)
  })

  it('ein gewöhnliches Konto wird aus den Einstellungen umgeleitet', async () => {
    // Auch das ist nur die Ansicht - der Schutz sitzt im Backend. Aber eine
    // Seite, die sich öffnen lässt und dann nur Fehler zeigt, ist schlechter
    // als eine, die gar nicht erst aufgeht.
    zustand.user = ERWACHSEN
    zeigen('/admin/settings')

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: /^einstellungen$/i })).toBeNull()
    })
  })

  it('während des Ladens weder das eine noch das andere', async () => {
    // Sonst blitzt für einen Moment die Anmeldeseite auf, obwohl man
    // angemeldet ist - das sieht aus, als wäre man hinausgeflogen.
    zustand.status = 'loading'
    zeigen('/')

    expect(screen.queryByRole('button', { name: /^anmelden$/i })).toBeNull()
  })

  it('beim allerersten Start der Einrichtungsassistent', async () => {
    zustand.needsSetup = true
    zeigen('/')

    expect(screen.queryByRole('button', { name: /^anmelden$/i })).toBeNull()
  })
})
