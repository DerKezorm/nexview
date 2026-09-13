/**
 * Der Einladungsassistent zeigt, was der Server über die Rechte sagt.
 *
 * ⚠️ **Geprüft wird die Anzeige, nicht die Regel.** Ob ein Haken frei ist,
 * entscheidet `services/kontorechte.py`, und das prüft das Backend. Hier geht es
 * darum, dass der Assistent die Antwort ehrlich wiedergibt: gesperrt heißt
 * gesperrt, mit Grund, und jede Änderung wird neu gefragt. Dasselbe gilt für die
 * Medienserver im Schritt „Zugang“.
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
import type { InvitationCreated, RechteBewertung, RechteWunsch, ServerAuswahl } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { EinladungsAssistent } from './EinladungsAssistent'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

/** Die Antwort des Servers - Serien wählt der Entscheider, 4K nur für Filme. */
function bewertung(wunsch: Partial<RechteWunsch>, abweichend: Partial<RechteBewertung> = {}): RechteBewertung {
  return {
    kontingent: { frei: true, wirkt: true, grund: null },
    auto_approve_movies: { frei: true, wirkt: Boolean(wunsch.auto_approve_movies), grund: null },
    auto_approve_series: { frei: false, wirkt: false, grund: 'approver_picks_target' },
    can_request_uhd_movies: { frei: true, wirkt: Boolean(wunsch.can_request_uhd_movies), grund: null },
    can_request_uhd_series: { frei: false, wirkt: false, grund: 'no_uhd_instance_tv' },
    auto_approve_uhd: { frei: false, wirkt: false, grund: 'uhd_needs_permission' },
    hausordnung: { frei: true, wirkt: Boolean(wunsch.hausordnung), grund: null },
    entfallen: [],
    ...abweichend,
  }
}

/** Plex ist verbunden, Emby nicht. */
const SERVER: ServerAuswahl[] = [
  {
    provider: 'plex',
    label: 'Plex',
    art: 'freigabe',
    stand: { frei: true, wirkt: false, grund: null },
    name: 'Wohnzimmer',
    bibliotheken: [
      { kennung: '11', name: 'Filme', art: 'movie' },
      { kennung: '12', name: 'Serien', art: 'show' },
    ],
    fehler: null,
  },
  {
    provider: 'emby',
    label: 'Emby',
    art: 'konto',
    stand: { frei: false, wirkt: false, grund: 'server_not_connected' },
    name: '',
    bibliotheken: [],
    fehler: null,
  },
]

const ANGELEGT: InvitationCreated = {
  id: 1,
  email: 'neu@example.com',
  role: 'user',
  created_at: '2026-09-12T10:00:00',
  expires_at: '2026-09-19T10:00:00',
  eingeloest_am: null,
  konto: null,
  entfallen: [],
  server: [],
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
    if (pfad === '/api/users/invitations/server') return Promise.resolve(SERVER)
    return Promise.resolve({})
  })
  schicken.mockImplementation((pfad: string, body?: unknown) => {
    if (pfad === '/api/users/rechte/bewerten') {
      return Promise.resolve(bewertung(body as Partial<RechteWunsch>, abweichend))
    }
    if (pfad === '/api/users/invitations') return Promise.resolve(angelegt)
    return Promise.reject(new Error(`unerwartete Adresse ${pfad}`))
  })
  rendernSchlicht(<EinladungsAssistent offen onSchliessen={() => {}} />)
}

const weiter = () => screen.getByRole('button', { name: 'Weiter' })

async function bisZumZugang() {
  fireEvent.change(screen.getByLabelText('E-Mail-Adresse'), { target: { value: 'neu@example.com' } })
  fireEvent.click(weiter())
  await screen.findByText('Wozu bekommt die Person Zugang?')
}

async function bisZuDenRechten() {
  await bisZumZugang()
  fireEvent.click(weiter())
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
  expect(screen.getByText(/Zielordner und Qualitätsprofil wählt der Entscheider/)).toBeTruthy()

  const filme = screen.getByRole('checkbox', { name: /Filme sofort freigeben/ })
  expect(filme).toHaveProperty('disabled', false)
})

it('nennt als Satz, warum sich das Kontingent nicht einstellen lässt', async () => {
  // Der Grund steht unter `rechte.grund`. Bis zum 12.09.2026 fragte der
  // Assistent hier einen umgezogenen Schlüssel ab und zeigte ihn roh an.
  einrichten({ abweichend: { kontingent: { frei: false, wirkt: false, grund: 'admin_no_quota' } } })
  await bisZuDenRechten()

  expect(screen.getByText('Administratoren haben kein Kontingent.')).toBeTruthy()
})

it('fragt bei jeder Änderung neu nach', async () => {
  einrichten()
  await bisZuDenRechten()

  fireEvent.click(screen.getByRole('checkbox', { name: /Filme sofort freigeben/ }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/users/rechte/bewerten',
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

  fireEvent.click(weiter())
  await screen.findByText('Was sieht die Person beim Einlösen?')
  fireEvent.click(weiter())
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
        // "Nur Nexview" ist die Vorgabe.
        server: [],
      }),
    )
  })
  expect(await screen.findByText('Die Einladung an neu@example.com ist unterwegs.')).toBeTruthy()
})

it('gibt nur verbundene Server frei und schickt die gewählten Bibliotheken mit', async () => {
  einrichten()
  await bisZumZugang()

  fireEvent.click(screen.getByRole('button', { name: 'Nexview und Medienserver' }))
  expect(await screen.findByRole('checkbox', { name: /Emby/ })).toHaveProperty('disabled', true)
  expect(screen.getByText('Dieser Medienserver ist nicht verbunden.')).toBeTruthy()
  expect(weiter()).toHaveProperty('disabled', true)

  // Ein Server ohne Bibliothek hält genauso an wie gar keiner.
  fireEvent.click(screen.getByRole('checkbox', { name: /Plex/ }))
  expect(screen.getByText('Ohne Bibliothek sieht die Person auf Plex nichts.')).toBeTruthy()
  expect(weiter()).toHaveProperty('disabled', true)

  fireEvent.click(screen.getByRole('button', { name: 'Serien' }))
  expect(weiter()).toHaveProperty('disabled', false)
  fireEvent.click(weiter())
  await screen.findByText('Was darf die Person?')
  fireEvent.click(weiter())
  await screen.findByText('Was sieht die Person beim Einlösen?')
  expect(screen.getByText('Plex verknüpfen')).toBeTruthy()
  fireEvent.click(weiter())
  await screen.findByText('Alles richtig?')
  expect(screen.getByText('Nexview, Plex (Serien)')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Einladung senden' }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      '/api/users/invitations',
      expect.objectContaining({ server: [{ provider: 'plex', bibliotheken: ['12'] }] }),
    )
  })
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
  fireEvent.click(weiter())
  await screen.findByText('Was sieht die Person beim Einlösen?')
  fireEvent.click(weiter())
  await screen.findByText('Alles richtig?')
  fireEvent.click(screen.getByRole('button', { name: 'Einladung senden' }))

  expect(await screen.findByText('Die Mail ging nicht raus')).toBeTruthy()
  expect(screen.getByText('https://nexview.example.com/einladung/abc')).toBeTruthy()
})

it('gibt den Link auch heraus, wenn die Mail rausging', async () => {
  // Wunsch aus Issue #8: Viele geben den Link lieber selbst weiter. Bis zum
  // 13.09.2026 stand er nur da, wenn die Mail scheiterte.
  einrichten({ angelegt: { ...ANGELEGT, manual_link: 'https://nexview.example.com/einladung/xyz' } })
  await bisZuDenRechten()
  fireEvent.click(weiter())
  await screen.findByText('Was sieht die Person beim Einlösen?')
  fireEvent.click(weiter())
  await screen.findByText('Alles richtig?')
  fireEvent.click(screen.getByRole('button', { name: 'Einladung senden' }))

  expect(await screen.findByText('Die Einladung an neu@example.com ist unterwegs.')).toBeTruthy()
  expect(screen.getByText('https://nexview.example.com/einladung/xyz')).toBeTruthy()
  expect(screen.getByText(/auch selbst weitergeben/)).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Link kopieren' })).toBeTruthy()
})
