/** Anfragenliste des Admins: Abzeichen "Fassung gibt es nicht". */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../api/client'
import { rendern } from '../test/rendern'
import { AdminRequestsPage } from './AdminRequestsPage'

const holen = vi.mocked(api.get)

function zeile(id: number, status: string, fassung: string | null, title: string) {
  return {
    id, media_type: 'movie', fassung, tier: 'standard', tmdb_id: 600 + id, title,
    poster_path: null, release_date: null, status, quality_profile_id: null,
    root_folder_path: null, season: null, episodes: null, from_watchlist: false,
    arr_linked: false, requested_at: '2026-09-20T10:00:00', approved_at: null,
    completed_at: null, approved_by_name: null, last_checked_at: null,
    laedt_fortschritt: null, laedt_seit: null, rejection_reason: null, regel_name: null,
    darf_trotzdem_fragen: false, trotzdem_gefragt: false, error_message: null,
    error_detail: null, rating: null, feedback: null, rated_at: null, rating_outdated: false,
    feedback_reply: null, replied_at: null, user_id: 2, username: 'kim', display_name: 'kim',
    avatar_url: null, storage: { used_bytes: 0, limit_bytes: null, exhausted: false },
    requester_subscriptions: [], for_child_name: null, import_haengt: null,
  }
}

function nexBetriebMit(anfragen: ReturnType<typeof zeile>[]) {
  holen.mockImplementation((pfad: string) => {
    if (pfad.startsWith('/api/config')) {
      return Promise.resolve({
        beschaffung: 'nex',
        beschaffung_kann: { warum: true, papierkorb: true, anime: true, kalender: true, wertungen: [] },
        beschaffung_sprung: {},
        fassungen: [
          { kennung: 'v_beispiel', media_type: 'movie', name: 'Movies', klasse: 'hd', quelle: 'nex', haupt: true },
        ],
      })
    }
    if (pfad.startsWith('/api/admin/requests')) return Promise.resolve(anfragen)
    return Promise.resolve({})
  })
}

/** Abzeichen in den Zeilen, ohne den gleichnamigen Filterknopf. */
function abzeichenIn(titel: string): HTMLElement[] {
  const zeileMitTitel = screen.getByText(titel).closest('p') as HTMLElement
  expect(zeileMitTitel).not.toBeNull()
  return Array.from(zeileMitTitel.querySelectorAll('span')).filter(
    (e) => e.textContent === 'Fassung gibt es nicht',
  ) as HTMLElement[]
}

describe('AdminRequestsPage: Abzeichen für eine fremde Fassung', () => {
  it('trägt es nur an laufenden Anfragen, nicht an erledigten aus der Arr-Zeit', async () => {
    // Nach dem Umstieg trägt jede alte Anfrage eine Arr-Kennung. Geladen oder
    // abgebrochen erwartet sie nichts mehr vom Weg; eine Warnung daran wäre
    // falscher Alarm an jeder Zeile der Historie.
    nexBetriebMit([
      zeile(1, 'downloaded', 'radarr-standard', 'Erfundener geladener Film'),
      zeile(2, 'cancelled', 'radarr-uhd', 'Erfundener abgebrochener Film'),
      zeile(3, 'approved', 'radarr-uhd', 'Erfundener freigegebener Film'),
      zeile(4, 'searching', 'radarr-standard', 'Erfundener suchender Film'),
      zeile(5, 'approved', 'v_beispiel', 'Erfundener bekannter Film'),
    ])
    rendern(<AdminRequestsPage />, { pfad: '/admin/requests?filter=all' })
    await screen.findByText('Erfundener geladener Film')

    expect(abzeichenIn('Erfundener geladener Film')).toHaveLength(0)
    expect(abzeichenIn('Erfundener abgebrochener Film')).toHaveLength(0)
    expect(abzeichenIn('Erfundener freigegebener Film')).toHaveLength(1)
    expect(abzeichenIn('Erfundener suchender Film')).toHaveLength(1)
    expect(abzeichenIn('Erfundener bekannter Film')).toHaveLength(0)
  })
})
