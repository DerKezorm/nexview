/**
 * Der Einladungsassistent zeigt, was der Server über die Rechte sagt.
 *
 * ⚠️ **Geprüft wird die Anzeige, nicht die Regel.** Ob ein Haken frei ist,
 * entscheidet `services/kontorechte.py`, und das prüft das Backend. Hier geht es
 * darum, dass der Assistent die Antwort ehrlich wiedergibt: gesperrt heißt
 * gesperrt, mit Grund, und jede Änderung wird neu gefragt.
 */

import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../../api/client'
import type { InvitationCreated, RechteBewertung, RechteWunsch } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { EinladungsAssistent } from './EinladungsAssistent'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

/** Die Antwort des Servers - Serien wählt der Entscheider, 4K nur für Filme. */
function bewertung(wunsch: Partial<RechteWunsch>, abweichend: Partial<RechteBewertung> = {}): RechteBewertung {
  return {
    kontingent: { frei: true, wirkt: true, grund: null },
    auto_approve_movies: { frei: true, wirkt: Boolean(wunsch.auto_approve_movies), grund: null },
    auto_approve_series: { frei: false, wirkt: false, grund: 'approver_picks_target_tv' },
    can_request_uhd_movies: { frei: true, wirkt: Boolean(wunsch.can_request_uhd_movies), grund: null },
    can_request_uhd_series: { frei: false, wirkt: false, grund: 'no_uhd_instance_tv' },
    auto_approve_uhd: { frei: false, wirkt: false, grund: 'uhd_needs_permission' },
    hausordnung: { frei: true, wirkt: Boolean(wunsch.hausordnung), grund: null },
    entfallen: [],
    ...abweichend,
  }
}

const ANGELEGT: InvitationCreated = {
  id: 1,
  email: 'neu@example.com',
  role: 'user',
  created_at: '2026-09-12T10:00:00',
  expires_at: '2026-09-19T10:00:00',
  eingeloest_am: null,
  konto: null,
  entfallen: [],
  mail_sent: true,
  mail_error: null,
  manual_link: null,
}

function einrichten({
  abweichend = {},
  angelegt = ANGELEGT,
}: { abweichend?: Partial<RechteBewertung>; angelegt?: InvitationCreated } = {}) {
  holen.mockImplementation((pfad: string) => {
    if (pfad.startsWith('/api/settings')) {
      return Promise.resolve({ quota_default_movies: 10, quota_default_series: 5, storage_default_limit_gb: null })
    }
    return Promise.resolve({})
  })
  schicken.mockImplementation((pfad: string, body?: unknown) => {
    if (pfad === '/api/users/invitations/bewerten') {
      return Promise.resolve(bewertung(body as Partial<RechteWunsch>, abweichend))
    }
    if (pfad === '/api/users/invitations') return Promise.resolve(angelegt)
    return Promise.reject(new Error(`unerwartete Adresse ${pfad}`))
  })
  rendernSchlicht(<EinladungsAssistent offen onSchliessen={() => {}} />)
}

async function bisZuDenRechten() {
  fireEvent.change(screen.getByLabelText('E-Mail-Adresse'), { target: { value: 'neu@example.com' } })
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Was darf die Person?')
}

beforeEach(() => {
  holen.mockReset()
  schicken.mockReset()
})

it('zeigt einen gesperrten Haken als gesperrt, samt Grund vom Server', async () => {
  einrichten()
  await bisZuDenRechten()

  const serien = screen.getByRole('checkbox', { name: /Serien sofort freigeben/ })
  expect(serien).toHaveProperty('disabled', true)
  expect(screen.getByText(/Ordner oder Profil wählt bei Serien der Entscheider/)).toBeTruthy()

  const filme = screen.getByRole('checkbox', { name: /Filme sofort freigeben/ })
  expect(filme).toHaveProperty('disabled', false)
})

it('fragt bei jeder Änderung neu nach', async () => {
  einrichten()
  await bisZuDenRechten()

  fireEvent.click(screen.getByRole('checkbox', { name: /Filme sofort freigeben/ }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/users/invitations/bewerten',
      expect.objectContaining({ auto_approve_movies: true }),
    )
  })
})

it('lässt 4K weg, wenn es gar keine 4K-Instanz gibt', async () => {
  einrichten({
    abweichend: {
      can_request_uhd_movies: { frei: false, wirkt: false, grund: 'no_uhd_instance_movie' },
      auto_approve_uhd: { frei: false, wirkt: false, grund: 'no_uhd_instance' },
    },
  })
  await bisZuDenRechten()

  expect(screen.queryByRole('checkbox', { name: /4K sofort freigeben/ })).toBeNull()
  // Und nicht einfach alles: Die Freigabe steht weiter da.
  expect(screen.getByRole('checkbox', { name: /Filme sofort freigeben/ })).toBeTruthy()
})

it('schickt die Einladung mit Rechten und Grenzen', async () => {
  einrichten()
  await bisZuDenRechten()
  fireEvent.click(screen.getByRole('checkbox', { name: /Filme sofort freigeben/ }))
  await waitFor(() => {
    expect(screen.getByRole('checkbox', { name: /Filme sofort freigeben/ })).toHaveProperty('checked', true)
  })

  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Was sieht die Person beim Einlösen?')
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Alles richtig?')
  fireEvent.click(screen.getByRole('button', { name: 'Einladung senden' }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/users/invitations',
      expect.objectContaining({
        email: 'neu@example.com',
        role: 'user',
        auto_approve_movies: true,
        hausordnung: true,
        quota_movies_limit: 'standard',
        storage_limit_gb: 'standard',
      }),
    )
  })
  expect(await screen.findByText('Die Einladung an neu@example.com ist unterwegs.')).toBeTruthy()
})

it('gibt den Link zum Weitergeben heraus, wenn die Mail nicht rausging', async () => {
  einrichten({
    angelegt: {
      ...ANGELEGT,
      mail_sent: false,
      mail_error: 'Verbindung abgelehnt',
      manual_link: 'https://nexview.example.com/einladung/abc',
    },
  })
  await bisZuDenRechten()
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Was sieht die Person beim Einlösen?')
  fireEvent.click(screen.getByRole('button', { name: 'Weiter' }))
  await screen.findByText('Alles richtig?')
  fireEvent.click(screen.getByRole('button', { name: 'Einladung senden' }))

  expect(await screen.findByText('Die Mail ging nicht raus')).toBeTruthy()
  expect(screen.getByText('https://nexview.example.com/einladung/abc')).toBeTruthy()
})
