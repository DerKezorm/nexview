/**
 * Eine Einladung einlösen: Willkommen, Konto, Hausordnung, fertig.
 *
 * ⚠️ **Worauf es ankommt:** Angelegt wird erst nach dem letzten Schritt, und
 * die Entscheidung zur Hausordnung geht mit dem Anlegen an den Server. Würde
 * die Seite schon nach dem Passwort anlegen, gäbe es für die Entscheidung
 * keinen Weg mehr, außer über die angemeldete Adresse, und dort landete sie bei
 * einem Administrator, der im selben Browser noch angemeldet ist.
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

import { api } from '../api/client'
import { rendern } from '../test/rendern'
import { InvitationPage } from './OnboardingPage'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

type Hausordnung = { titel: string; inhalt: string; quittierbar: boolean } | null

function zeigen(hausordnung: Hausordnung) {
  holen.mockImplementation((pfad: string) => {
    if (pfad.startsWith('/api/config')) return Promise.resolve({ min_password_length: 8 })
    if (pfad.startsWith('/api/onboarding/username-available')) return Promise.resolve({ available: true })
    if (pfad.startsWith('/api/onboarding/invitation/')) {
      return Promise.resolve({ email: 'neu@example.com', role: 'user', hausordnung })
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

async function kontoAusfuellen() {
  fireEvent.click(await screen.findByRole('button', { name: 'Los geht’s' }))
  fireEvent.change(screen.getByLabelText('Benutzername'), { target: { value: 'neuer' } })
  fireEvent.change(screen.getByLabelText('Passwort', { exact: true }), { target: { value: 'geheim-123' } })
  fireEvent.change(screen.getByLabelText('Passwort wiederholen'), { target: { value: 'geheim-123' } })
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
