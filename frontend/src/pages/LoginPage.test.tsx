/**
 * Weg 1 von dreien: sich anmelden.
 *
 * Der einzige Weg, den **jeder** geht, und der einzige, hinter dem alles
 * andere liegt. Geprüft wird die Oberfläche gegen eine ersetzte API-Schicht:
 * ob das Formular die richtigen Daten schickt, ob ein Fehler ankommt, und ob
 * der Sonderfall „Adresse noch nicht bestätigt" den Ausweg zeigt statt einer
 * Fehlermeldung.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ⚠️ Muss **vor** dem Import der Seite stehen: `vi.mock` wird nach oben
// gezogen, der Ersatz greift also für alles, was danach lädt.
vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      upload: vi.fn(),
    },
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
    logout: vi.fn(),
    restoreSession: vi.fn(async () => false),
    setSessionLostHandler: vi.fn(),
  }
})

import { ApiError, api } from '../api/client'
import { rendern } from '../test/rendern'
import { LoginPage } from './LoginPage'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

/** Der Aufruf, den der AuthProvider beim Erscheinen macht. */
function einrichtungFertig(oidcAnbieter: object[] = [], passwordLogin = true) {
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return {
        needs_setup: false,
        mediaserver_login: false,
        mediaserver_login_ways: [],
        password_login: passwordLogin,
      }
    }
    if (pfad === '/api/auth/me') {
      return { id: 1, username: 'kim', role: 'user', language: 'de', theme: 'dark' }
    }
    if (pfad === '/api/oidc/providers') {
      return oidcAnbieter
    }
    throw new Error(`Unerwarteter Aufruf: ${pfad}`)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  einrichtungFertig()
})

async function felderFuellen(name = 'kim', passwort = 'geheim') {
  const nutzer = userEvent.setup()
  await waitFor(() => expect(screen.getByLabelText(/benutzername/i)).toBeInTheDocument())
  await nutzer.type(screen.getByLabelText(/benutzername/i), name)
  await nutzer.type(screen.getByLabelText(/^passwort$/i), passwort)
  return nutzer
}

describe('Anmelden', () => {
  it('schickt Benutzername und Passwort an die Anmeldung', async () => {
    schicken.mockResolvedValue({ access_token: 'abc', token_type: 'bearer', expires_in: 1800 })
    rendern(<LoginPage />)

    const nutzer = await felderFuellen('kim', 'geheim')
    await nutzer.click(screen.getByRole('button', { name: /^anmelden$/i }))

    await waitFor(() =>
      expect(schicken).toHaveBeenCalledWith(
        '/api/auth/login',
        { username: 'kim', password: 'geheim' },
        { auth: false },
      ),
    )
  })

  it('schneidet Leerzeichen im Benutzernamen ab, nicht im Passwort', async () => {
    // ⚠️ Der Unterschied ist Absicht: Ein versehentliches Leerzeichen hinter
    // dem Namen soll niemanden aussperren. Im Passwort darf es eines geben -
    // es abzuschneiden hieße, ein gültiges Passwort stillschweigend zu ändern.
    schicken.mockResolvedValue({ access_token: 'abc', token_type: 'bearer', expires_in: 1800 })
    rendern(<LoginPage />)

    const nutzer = await felderFuellen('  kim  ', ' geheim ')
    await nutzer.click(screen.getByRole('button', { name: /^anmelden$/i }))

    await waitFor(() =>
      expect(schicken).toHaveBeenCalledWith(
        '/api/auth/login',
        { username: 'kim', password: ' geheim ' },
        { auth: false },
      ),
    )
  })

  it('zeigt die Meldung des Servers, wenn das Passwort falsch ist', async () => {
    schicken.mockRejectedValue(
      new ApiError(401, 'Benutzername oder Passwort ist falsch.', 'bad_credentials'),
    )
    rendern(<LoginPage />)

    const nutzer = await felderFuellen()
    await nutzer.click(screen.getByRole('button', { name: /^anmelden$/i }))

    expect(await screen.findByText(/passwort ist falsch/i)).toBeInTheDocument()
  })

  it('nennt bei einem Netzwerkfehler nicht "falsches Passwort"', async () => {
    // Sonst sucht jemand den Fehler bei sich, obwohl der Server gar nicht da
    // ist.
    schicken.mockRejectedValue(new Error('Netzwerk weg'))
    rendern(<LoginPage />)

    const nutzer = await felderFuellen()
    await nutzer.click(screen.getByRole('button', { name: /^anmelden$/i }))

    await waitFor(() => expect(screen.queryByText(/passwort ist falsch/i)).toBeNull())
  })

  it('bietet bei unbestätigter Adresse den Ausweg statt einer Fehlermeldung', async () => {
    // ⚠️ Der Grund, warum der Server hier eine Kennung mitschickt: Die
    // Oberfläche soll mehr tun als den Text anzeigen.
    schicken.mockRejectedValue(
      new ApiError(403, 'Adresse noch nicht bestätigt.', 'email_unverified', {
        code: 'email_unverified',
        email: 'kim@beispiel.de',
      }),
    )
    rendern(<LoginPage />)

    const nutzer = await felderFuellen()
    await nutzer.click(screen.getByRole('button', { name: /^anmelden$/i }))

    expect(await screen.findByText(/kim@beispiel\.de/)).toBeInTheDocument()
  })

  it('meldet nicht zweimal an, wenn man zweimal klickt', async () => {
    // Ohne die Sperre liefen zwei Anmeldungen gleichzeitig - und die zweite
    // überschriebe den Zustand der ersten.
    let aufloesen: (wert: unknown) => void = () => {}
    schicken.mockReturnValue(new Promise((r) => (aufloesen = r)))
    rendern(<LoginPage />)

    const nutzer = await felderFuellen()
    const knopf = screen.getByRole('button', { name: /^anmelden$/i })
    await nutzer.click(knopf)
    await nutzer.click(knopf)

    expect(schicken).toHaveBeenCalledTimes(1)
    aufloesen({ access_token: 'abc', token_type: 'bearer', expires_in: 1800 })
  })
})

describe('Anmelden über OIDC-Anbieter', () => {
  it('zeigt je eingerichtetem Anbieter einen Knopf unter „oder“ - und ohne keinen', async () => {
    einrichtungFertig([
      { slug: 'firma', label: 'Firmen-SSO' },
      { slug: 'entra', label: 'Microsoft' },
    ])
    rendern(<LoginPage />)

    const knoepfe = await screen.findAllByRole('button', { name: /^mit .* anmelden$/i })
    expect(knoepfe.map((knopf) => knopf.textContent)).toEqual([
      'Mit Firmen-SSO anmelden',
      'Mit Microsoft anmelden',
    ])
    expect(screen.getByText('oder')).toBeInTheDocument()
    // Das Passwortformular steht darüber, solange die Anmeldung mit Passwort gilt.
    expect(screen.getByLabelText(/benutzername/i)).toBeInTheDocument()
  })

  it('bleibt ohne Anbieter die Anmeldeseite von immer', async () => {
    rendern(<LoginPage />)
    await waitFor(() => expect(screen.getByLabelText(/benutzername/i)).toBeInTheDocument())
    expect(screen.queryByText('oder')).toBeNull()
  })

  it('zeigt bei abgeschalteter Passwort-Anmeldung nur die Knöpfe und den Weg für den Betreiber', async () => {
    einrichtungFertig([{ slug: 'firma', label: 'Firmen-SSO' }], false)
    rendern(<LoginPage />)

    expect(await screen.findByRole('button', { name: 'Mit Firmen-SSO anmelden' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/benutzername/i)).toBeNull()
    expect(screen.queryByText('oder')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Als Betreiber mit Passwort anmelden' }))
    expect(await screen.findByLabelText(/benutzername/i)).toBeInTheDocument()
  })

  it('zeigt die Kennung aus einer gescheiterten Rückkehr - und räumt die Adresse', async () => {
    // Der Rückweg vom Anbieter ist eine Weiterleitung; sein Fehler steht in
    // der Adresse statt in einer API-Antwort.
    window.history.replaceState(null, '', '/login?error=oidc_no_account')
    try {
      rendern(<LoginPage />)

      expect(
        await screen.findByText(/für dich gibt es hier kein konto/i),
      ).toBeInTheDocument()
      // Einmal gezeigt, aus der Adresse verschwunden: Ein Neuladen soll die
      // alte Meldung nicht noch einmal bringen.
      expect(window.location.search).toBe('')
    } finally {
      window.history.replaceState(null, '', '/')
    }
  })

  it('zeigt nie einen rohen Code aus der Adresse', async () => {
    window.history.replaceState(null, '', '/login?error=%3Cb%3Ehallo%3C%2Fb%3E')
    try {
      rendern(<LoginPage />)
      await waitFor(() => expect(screen.getByLabelText(/benutzername/i)).toBeInTheDocument())
      expect(screen.queryByText(/hallo/)).toBeNull()
      expect(screen.queryByRole('alert')).toBeNull()
    } finally {
      window.history.replaceState(null, '', '/')
    }
  })
})
