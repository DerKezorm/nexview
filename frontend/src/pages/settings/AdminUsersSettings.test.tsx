/**
 * Der Kontodialog zeigt, was der Server über die Rechte sagt.
 *
 * ⚠️ **Geprüft wird die Anzeige, nicht die Regel.** Ob ein Haken frei ist,
 * entscheidet `services/kontorechte.py`, und das prüft das Backend. Hier geht es
 * um das, was der Dialog bis zum 12.09.2026 selbst rechnete und dabei
 * danebenlag: die Rolle aus dem Entwurf, der eigene Haken statt des
 * errechneten, die Kurzzeile je Medienart und die Sperrlisten.
 */

import { beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
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

import { api } from '../../api/client'
import type {
  AppConfig,
  Invitation,
  RechteBewertung,
  RechteStand,
  RechteWunsch,
  User,
} from '../../api/types'
import { rendern } from '../../test/rendern'
import { AdminUsersSettings } from './AdminUsersSettings'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

const BEWERTEN = '/api/users/rechte/bewerten'

/** Ein Benutzerkonto, wie die Liste es liefert. */
function konto(abweichend: Partial<User> = {}): User {
  return {
    id: 7,
    username: 'eva',
    display_name: 'eva',
    role: 'user',
    is_active: true,
    is_betreiber: false,
    auto_approve: false,
    effective_auto_approve: false,
    auto_approve_movies: null,
    auto_approve_series: null,
    effective_auto_approve_movies: false,
    effective_auto_approve_series: false,
    can_approve: false,
    quota_movies_limit: 'standard',
    quota_series_limit: 'standard',
    storage_limit_gb: 'standard',
    blocked_movie_profiles: [],
    blocked_series_profiles: [],
    fassung_rechte: [],
    can_request_uhd_movies: false,
    can_request_uhd_series: false,
    auto_approve_uhd: false,
    effective_auto_approve_uhd: false,
    blocked_movie_uhd_profiles: [],
    blocked_series_uhd_profiles: [],
    avatar_url: null,
    created_at: '2026-09-01T10:00:00',
    last_login_at: null,
    quota_reset_at: null,
    hausordnung_gelesen_am: null,
    quota_movies_used: 0,
    quota_series_used: 0,
    email: 'eva@example.com',
    email_verified: true,
    has_password: true,
    mediaserver_provider: null,
    mediaserver_username: null,
    mediaserver_linked: false,
    mediaserver_accounts: [],
    oidc_links: [],
    can_manage_children: false,
    parent_id: null,
    ...abweichend,
  } as unknown as User
}

const KONFIGURATION = {
  mail_configured: true,
  public_url_set: true,
  radarr_configured: true,
  sonarr_configured: false,
  radarr_uhd_configured: false,
  sonarr_uhd_configured: false,
  approver_picks_target_movie: false,
  approver_picks_target_tv: false,
  min_password_length: 4,
}

/** Die Antwort des Servers: Rollen sperren aus der Rolle, sonst ist alles frei, 4K gibt es nicht. */
function bewertung(wunsch: RechteWunsch, abweichend: Partial<RechteBewertung>): RechteBewertung {
  const ausRolle =
    wunsch.role === 'admin' || wunsch.role === 'approver'
      ? { frei: false, wirkt: true, grund: `role_${wunsch.role}` }
      : null
  const haken = (wert: boolean): RechteStand => ausRolle ?? { frei: true, wirkt: wert, grund: null }
  return {
    kontingent: { frei: true, wirkt: true, grund: null },
    auto_approve_movies: haken(wunsch.auto_approve_movies),
    auto_approve_series: haken(wunsch.auto_approve_series),
    // Keine Fassung, die erst erlaubt werden müsste: Ohne 4K-Instanz sagt
    // der Server hier nichts, und die Zeile bleibt weg.
    fassungen: {},
    hausordnung: { frei: false, wirkt: false, grund: 'no_house_rules' },
    entfallen: [],
    ...abweichend,
  }
}

function einrichten(
  benutzer: User,
  {
    abweichend = {},
    konfiguration = {},
    einladungen = [],
  }: {
    abweichend?: Partial<RechteBewertung>
    konfiguration?: Partial<AppConfig>
    einladungen?: Invitation[]
  } = {},
) {
  holen.mockImplementation((pfad: string) => {
    if (pfad === '/api/users') return Promise.resolve([benutzer])
    if (pfad.startsWith('/api/users/invitations')) return Promise.resolve(einladungen)
    if (pfad.startsWith('/api/config')) return Promise.resolve({ ...KONFIGURATION, ...konfiguration })
    if (pfad.startsWith('/api/settings')) {
      return Promise.resolve({
        quota_period: 'week',
        quota_default_movies: null,
        quota_default_series: null,
        storage_default_limit_gb: null,
        movie_profile_mode: 'user',
        series_profile_mode: 'user',
      })
    }
    if (pfad === '/api/arr/movie/options') {
      return Promise.resolve({
        quality_profiles: [
          { id: 1, name: 'HD-1080p' },
          { id: 2, name: 'SD' },
        ],
        root_folders: [],
      })
    }
    if (pfad.startsWith('/api/hausordnung/verwaltung')) return Promise.resolve({ veroeffentlicht: false })
    if (pfad.startsWith('/api/setup/status')) {
      return Promise.resolve({ needs_setup: false, mediaserver_login: false })
    }
    return Promise.resolve({})
  })
  schicken.mockImplementation((pfad: string, body?: unknown) => {
    if (pfad === BEWERTEN) return Promise.resolve(bewertung(body as RechteWunsch, abweichend))
    if (pfad.endsWith('/nachholen')) return Promise.resolve(einladungen[0])
    return Promise.reject(new Error(`unerwartete Adresse ${pfad}`))
  })
  rendern(<AdminUsersSettings />)
}

async function oeffnen() {
  fireEvent.click(await screen.findByRole('button', { name: 'Bearbeiten' }))
}

const filmeHaken = () => screen.findByRole('checkbox', { name: /Filme automatisch freigeben/ })

beforeEach(() => {
  holen.mockReset()
  schicken.mockReset()
})

it('holt eine gescheiterte Freigabe aus der Einladungsliste nach', async () => {
  // Die Liste sagt, was fehlt und warum; der Knopf fragt genau diesen Server nach.
  const einladung: Invitation = {
    id: 3,
    email: 'sam@example.com',
    role: 'user',
    created_at: '2026-09-12T10:00:00',
    expires_at: '2026-09-19T10:00:00',
    eingeloest_am: '2026-09-12T11:00:00',
    konto: 'sam',
    entfallen: [],
    server: [
      {
        provider: 'plex',
        label: 'Plex',
        bibliotheken: ['Filme'],
        zustand: 'fehlt',
        fehler: { code: 'mediaserver_unreachable', message: 'plex.tv antwortet nicht.' },
        nachholbar: true,
      },
    ],
  }
  einrichten(konto(), { einladungen: [einladung] })

  expect(
    await screen.findByText('Plex fehlt: plex.tv antwortet nicht. Gelöscht wurde nichts.'),
  ).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Plex nachholen' }))

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith('/api/users/invitations/3/server/plex/nachholen', {})
  })
  expect(
    await screen.findByText('Plex ist nachgeholt, die Bibliotheken sind freigegeben.'),
  ).toBeTruthy()
})

it('fragt den Server mit der Rolle aus dem Entwurf, nicht mit der gespeicherten', async () => {
  einrichten(konto())
  await oeffnen()
  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(BEWERTEN, expect.objectContaining({ role: 'user' }))
  })

  fireEvent.change(screen.getByDisplayValue('Benutzer'), { target: { value: 'approver' } })

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(BEWERTEN, expect.objectContaining({ role: 'approver' }))
  })
  await waitFor(async () => {
    expect(await filmeHaken()).toHaveProperty('disabled', true)
  })
  expect(screen.getAllByText('Entscheider haben das immer.').length).toBeGreaterThan(0)
})

it('zeigt den Grund vom Server und ohne 4K-Instanz keine 4K-Zeile', async () => {
  einrichten(konto(), {
    abweichend: { auto_approve_series: { frei: false, wirkt: false, grund: 'approver_picks_target' } },
  })
  await oeffnen()

  const serien = await screen.findByRole('checkbox', { name: /Serien automatisch freigeben/ })
  expect(serien).toHaveProperty('disabled', true)
  expect(
    screen.getByText('Zielordner und Qualitätsprofil wählt der Entscheider. Jede Anfrage wartet ohnehin auf ihn.'),
  ).toBeTruthy()
  expect(await filmeHaken()).toHaveProperty('disabled', false)
  expect(screen.queryByRole('checkbox', { name: /4K/ })).toBeNull()
})

it('nimmt beim Herabstufen den gespeicherten Haken, nicht den aus der Rolle', async () => {
  // Ein Entscheider: Die Freigabe wirkt aus der Rolle, gespeichert ist für Filme "aus".
  einrichten(
    konto({
      role: 'approver',
      can_approve: true,
      effective_auto_approve: true,
      auto_approve_movies: false,
      effective_auto_approve_movies: true,
      effective_auto_approve_series: true,
    }),
  )
  await oeffnen()

  fireEvent.change(await screen.findByDisplayValue('Entscheider'), { target: { value: 'user' } })

  await waitFor(() => {
    expect(schicken).toHaveBeenCalledWith(
      BEWERTEN,
      expect.objectContaining({ role: 'user', auto_approve_movies: false }),
    )
  })
  await waitFor(async () => {
    const filme = await filmeHaken()
    expect(filme).toHaveProperty('disabled', false)
    expect(filme).toHaveProperty('checked', false)
  })
})

it('nennt in der Kurzzeile die Freigabe je Medienart', async () => {
  einrichten(
    konto({ auto_approve_movies: true, effective_auto_approve_movies: true, effective_auto_approve: false }),
  )

  expect(await screen.findByText(/Filme sofort, Serien nach Freigabe/)).toBeTruthy()
})

it('sagt in der Kurzzeile "Freigabe nötig", wenn im Haus der Entscheider das Ziel wählt', async () => {
  einrichten(konto({ auto_approve_movies: true, effective_auto_approve_movies: true }), {
    konfiguration: { approver_picks_target_movie: true },
  })

  expect(await screen.findByText(/Freigabe nötig/)).toBeTruthy()
  expect(screen.queryByText(/Filme sofort/)).toBeNull()
})

it('meldet nach Ab- und wieder Anhaken eines Profils keine ungespeicherte Änderung', async () => {
  einrichten(konto({ blocked_movie_profiles: [1, 2] }))
  await oeffnen()

  fireEvent.click(await screen.findByRole('checkbox', { name: 'HD-1080p' }))
  expect(screen.getAllByText('noch nicht gespeichert').length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('checkbox', { name: 'HD-1080p' }))

  await waitFor(() => {
    expect(screen.queryByText('noch nicht gespeichert')).toBeNull()
  })
})

it('zeigt die Sperrlisten nach der Rolle im Entwurf', async () => {
  einrichten(
    konto({
      role: 'approver',
      can_approve: true,
      effective_auto_approve: true,
      effective_auto_approve_movies: true,
      effective_auto_approve_series: true,
    }),
  )
  await oeffnen()
  await filmeHaken()
  expect(screen.queryByRole('checkbox', { name: 'HD-1080p' })).toBeNull()

  fireEvent.change(screen.getByDisplayValue('Entscheider'), { target: { value: 'user' } })

  expect(await screen.findByRole('checkbox', { name: 'HD-1080p' })).toBeTruthy()
})
