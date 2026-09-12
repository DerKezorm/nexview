/**
 * Eine Einladung einlösen: Willkommen, verknüpfen, Konto, Hausordnung, fertig.
 *
 * ⚠️ **Worauf es ankommt:** Angelegt wird erst nach dem letzten Schritt, und
 * die Entscheidung zur Hausordnung geht mit dem Anlegen an den Server. Würde
 * die Seite schon nach dem Passwort anlegen, gäbe es für die Entscheidung
 * keinen Weg mehr, außer über die angemeldete Adresse, und dort landete sie bei
 * einem Administrator, der im selben Browser noch angemeldet ist.
 *
 * Mit Medienservern kommt dazu: Plex wird vor dem Konto verknüpft, der Name
 * gilt auch auf Jellyfin und Emby, und scheitert dort ein Konto, bleibt das
 * Passwort im Formular stehen.
 *
 * Gerendert wird mit dem echten `AuthProvider`: Der Rahmen der Seite zeigt den
 * Sprachumschalter, und der fragt den Anmelde-Zustand.
 */

import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
    logout: vi.fn(() => Promise.resolve()),
    restoreSession: vi.fn(async () => false),
    setSessionLostHandler: vi.fn(),
  }
})

import { ApiError, api } from '../api/client'
import { rendern } from '../test/rendern'
import { InvitationPage } from './OnboardingPage'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

type Hausordnung = { titel: string; inhalt: string; quittierbar: boolean } | null
type Server = {
  provider: string
  label: string
  art: 'konto' | 'freigabe'
  bibliotheken: string[]
  zustand: string
  konto_name: string | null
  fehler: Record<string, unknown> | null
}
type Namen = { nexview: boolean; server: Record<string, boolean | null> }

const PLEX: Server = {
  provider: 'plex',
  label: 'Plex',
  art: 'freigabe',
  bibliotheken: ['Filme'],
  zustand: 'offen',
  konto_name: null,
  fehler: null,
}
const JELLYFIN: Server = { ...PLEX, provider: 'jellyfin', label: 'Jellyfin', art: 'konto' }

function zeigen(
  hausordnung: Hausordnung,
  { server = [], namen = { nexview: true, server: {} } }: { server?: Server[]; namen?: Namen } = {},
) {
  holen.mockImplementation((pfad: string) => {
    if (pfad.startsWith('/api/config')) return Promise.resolve({ min_password_length: 8 })
    // Vor der Einladung selbst: Beide Adressen fangen gleich an.
    if (pfad.includes('/namen?')) return Promise.resolve(namen)
    if (pfad.startsWith('/api/onboarding/invitation/')) {
      return Promise.resolve({ email: 'neu@example.com', role: 'user', hausordnung, server })
    }
    return Promise.resolve({})
  })
  rendern(
    <Routes>
      <Route path="/einladung/:token" element={<InvitationPage />} />
    </Routes>,
    { pfad: '/einladung/abc' },
  )
}

async function losGehts() {
  fireEvent.click(await screen.findByRole('button', { name: 'Los geht’s' }))
}

async function felderAusfuellen() {
  fireEvent.change(await screen.findByLabelText('Benutzername'), { target: { value: 'neuer' } })
  fireEvent.change(screen.getByLabelText('Passwort', { exact: true }), { target: { value: 'geheim-123' } })
  fireEvent.change(screen.getByLabelText('Passwort wiederholen'), { target: { value: 'geheim-123' } })
}

async function kontoAusfuellen() {
  await losGehts()
  await felderAusfuellen()
}

const REGELN = { titel: 'Bei uns zu Hause', inhalt: '## Regeln\n\nBitte lesen.', quittierbar: true }

beforeEach(() => {
  holen.mockReset()
  schicken.mockReset()
})

it('legt das Konto erst nach der Hausordnung an und schickt die Entscheidung mit', async () => {
  zeigen(REGELN)
  schicken.mockResolvedValue({ username: 'neuer' })
  await kontoAusfuellen()

  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  expect(await screen.findByText('Bei uns zu Hause')).toBeTruthy()
  expect(schicken).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Ablehnen' }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/onboarding/invitation/abc',
      expect.objectContaining({ username: 'neuer', hausordnung_akzeptiert: false }),
    )
  })
  expect(await screen.findByText('Konto eingerichtet')).toBeTruthy()
})

it('ohne Hausordnung legt schon der Konto-Schritt an, ohne Entscheidung', async () => {
  zeigen(null)
  schicken.mockResolvedValue({ username: 'neuer' })
  await kontoAusfuellen()

  fireEvent.click(screen.getByRole('button', { name: 'Konto einrichten' }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/onboarding/invitation/abc',
      expect.objectContaining({ username: 'neuer', hausordnung_akzeptiert: null }),
    )
  })
})

it('bei einer Hausordnung ohne Abhaken gibt es nur Weiter und keine Entscheidung', async () => {
  zeigen({ ...REGELN, quittierbar: false })
  schicken.mockResolvedValue({ username: 'neuer' })
  await kontoAusfuellen()
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Bei uns zu Hause')

  expect(screen.queryByRole('button', { name: 'Akzeptieren' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Konto einrichten' }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/onboarding/invitation/abc',
      expect.objectContaining({ hausordnung_akzeptiert: null }),
    )
  })
})

it('scheitert das Anlegen, geht es mit Meldung zurück zum Konto', async () => {
  zeigen(REGELN)
  schicken.mockRejectedValue(new Error('vergeben'))
  await kontoAusfuellen()
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Bei uns zu Hause')

  fireEvent.click(screen.getByRole('button', { name: 'Akzeptieren' }))

  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.getByLabelText('Benutzername')).toBeTruthy()
})

it('prüft die Passwortlänge vor der Hausordnung, nicht erst danach', async () => {
  zeigen(REGELN)
  fireEvent.click(await screen.findByRole('button', { name: 'Los geht’s' }))
  fireEvent.change(screen.getByLabelText('Benutzername'), { target: { value: 'neuer' } })
  fireEvent.change(screen.getByLabelText('Passwort', { exact: true }), { target: { value: 'kurz' } })
  fireEvent.change(screen.getByLabelText('Passwort wiederholen'), { target: { value: 'kurz' } })

  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))

  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('Bei uns zu Hause')).toBeNull()
  expect(schicken).not.toHaveBeenCalled()
})

it('mit Plex führt der Link erst durchs Verknüpfen, vorher geht es nicht weiter', async () => {
  vi.spyOn(window, 'open').mockReturnValue(null)
  zeigen(null, { server: [PLEX] })
  schicken.mockImplementation((pfad: string) => {
    if (pfad.endsWith('/server/plex/start')) {
      return Promise.resolve({ poll_token: 'vorgang', code: 'ABCD', auth_url: 'https://app.plex.tv/auth' })
    }
    if (pfad.endsWith('/server/plex/poll')) {
      return Promise.resolve({ status: 'ready', konto_name: 'gast-plex' })
    }
    return Promise.reject(new Error(`unerwartete Adresse ${pfad}`))
  })

  expect(await screen.findByText('Zugang zu Plex, mit deinem eigenen Plex-Konto')).toBeTruthy()
  await losGehts()
  expect(await screen.findByText('Plex verknüpfen')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Weiter' })).toHaveProperty('disabled', true)

  fireEvent.click(screen.getByRole('button', { name: /Mit Plex anmelden/ }))

  expect(await screen.findByText('Verknüpft als gast-plex', {}, { timeout: 5000 })).toBeTruthy()
  expect(schicken).toHaveBeenCalledWith(
    '/api/onboarding/invitation/abc/server/plex/poll',
    { poll_token: 'vorgang' },
    { auth: false },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  expect(await screen.findByLabelText('Benutzername')).toBeTruthy()
}, 10_000)

it('zeigt je Server, ob der Name frei ist, und legt bei einem vergebenen nicht an', async () => {
  zeigen(null, { server: [JELLYFIN], namen: { nexview: true, server: { jellyfin: false } } })
  await kontoAusfuellen()

  expect(await screen.findByText('Jellyfin: vergeben')).toBeTruthy()
  expect(screen.getByText('Nexview: frei')).toBeTruthy()
  expect(screen.getByText(/für dein neues Konto auf Jellyfin/)).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Konto einrichten' })).toHaveProperty('disabled', true)
})

it('scheitert ein Serverkonto, bleibt das Passwort stehen und es geht mit Nochmal weiter', async () => {
  zeigen(null, { server: [JELLYFIN] })
  schicken
    .mockRejectedValueOnce(
      new ApiError(502, 'Nicht jedes Konto ließ sich anlegen.', 'invite_server_incomplete', {
        server: [{ ...JELLYFIN, fehler: { code: 'mediaserver_timeout', service: 'Jellyfin' } }],
      }),
    )
    .mockResolvedValueOnce({ username: 'neuer', server: [{ ...JELLYFIN, zustand: 'fertig' }] })
  await kontoAusfuellen()

  fireEvent.click(screen.getByRole('button', { name: 'Konto einrichten' }))

  expect(await screen.findByText('Jellyfin antwortet nicht (Zeitüberschreitung).')).toBeTruthy()
  expect(screen.getByLabelText('Passwort', { exact: true })).toHaveProperty('value', 'geheim-123')
  fireEvent.click(screen.getByRole('button', { name: 'Nochmal versuchen' }))

  expect(await screen.findByText('Konto eingerichtet')).toBeTruthy()
  expect(schicken).toHaveBeenCalledTimes(2)
})

it('nach dem Einlösen steht jeder Server mit Stand da, eine fehlende Freigabe kommt später', async () => {
  zeigen(null, { server: [{ ...PLEX, konto_name: 'gast-plex' }] })
  schicken.mockResolvedValue({
    username: 'neuer',
    server: [
      {
        ...PLEX,
        zustand: 'fehlt',
        konto_name: 'gast-plex',
        fehler: { code: 'mediaserver_unreachable', message: 'plex.tv antwortet nicht.' },
      },
    ],
  })
  await losGehts()
  // Schon verknüpft, etwa nach einem Neuladen: Weiter geht sofort.
  expect(await screen.findByText('Verknüpft als gast-plex')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await felderAusfuellen()
  fireEvent.click(screen.getByRole('button', { name: 'Konto einrichten' }))

  expect(await screen.findByText('Fast fertig')).toBeTruthy()
  expect(screen.getByText('kommt später')).toBeTruthy()
  expect(screen.getByText(/holt die Freigabe auf Plex mit einem Klick nach/)).toBeTruthy()
})
