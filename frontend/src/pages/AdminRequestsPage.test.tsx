/** Anfragenliste des Admins: Abzeichen "Fassung gibt es nicht". */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

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

/**
 * ⚠️ **`fassung: null` seit ab4af5b (Backend `str | None`).** Eine Zeile aus
 * der Startmigration kann `fassung_kennung = NULL` tragen. Die Seite darf
 * daran nicht abstürzen und auch nicht das Wort "null" anzeigen - beides wäre
 * schlimmer als die leere Kennung, die das Anfrageformular für denselben Fall
 * benutzt.
 */
describe('AdminRequestsPage: eine Anfrage ohne Fassungskennung', () => {
  it('zeigt sie ohne abzustürzen und ohne das Wort "null"', async () => {
    nexBetriebMit([zeile(6, 'approved', null, 'Erfundener Film ohne Fassung')])
    rendern(<AdminRequestsPage />, { pfad: '/admin/requests?filter=all' })

    await screen.findByText('Erfundener Film ohne Fassung')
    expect(screen.queryByText(/\bnull\b/)).toBeNull()
  })
})

/**
 * ⚠️ Dieselbe `fassung: null`-Zeile, aber wartend: Erst hier öffnet sich
 * der `TargetPicker` (Knopf „Freigeben" ohne Zielordner), und erst hier
 * würde `encodeURIComponent(null)` als `?fassung=null` in einer echten
 * Anfrage landen, wenn die Absicherung in `AdminRequestsPage.tsx`
 * (`beispielFassung`/`request.fassung ?? ""`) fehlte. Der Test oben prüft
 * nur das Abzeichen, nicht diesen Pfad.
 */
describe('AdminRequestsPage: eine wartende Anfrage ohne Fassungskennung', () => {
  it('öffnet den TargetPicker und schickt kein fassung=null', async () => {
    holen.mockImplementation((pfad: string) => {
      if (pfad.startsWith('/api/config')) {
        return Promise.resolve({
          beschaffung: 'nex',
          beschaffung_kann: { warum: true, papierkorb: true, anime: true, kalender: true, wertungen: [] },
          beschaffung_sprung: {},
          fassungen: [],
        })
      }
      if (pfad.startsWith('/api/admin/requests')) {
        return Promise.resolve([
          zeile(7, 'pending_approval', null, 'Erfundener wartender Film ohne Fassung'),
        ])
      }
      if (pfad.startsWith('/api/arr/')) {
        return Promise.resolve({
          quality_profiles: [{ id: 1, name: 'Standard' }],
          root_folders: [{ path: '/filme', free_space: null }],
          default_root_folder: '/filme',
          root_folder_choice: true,
          quality_profile_choice: true,
          default_quality_profile_id: 1,
        })
      }
      return Promise.resolve({})
    })
    rendern(<AdminRequestsPage />, { pfad: '/admin/requests?filter=all' })
    await screen.findByText('Erfundener wartender Film ohne Fassung')

    await userEvent.click(screen.getByRole('button', { name: 'Freigeben' }))
    await screen.findByText('Zielordner')

    const angefragteOptionen = holen.mock.calls
      .map(([pfad]) => pfad as string)
      .filter((pfad) => pfad.startsWith('/api/arr/'))
    expect(angefragteOptionen.length).toBeGreaterThan(0)
    for (const pfad of angefragteOptionen) {
      expect(pfad).not.toContain('null')
    }
  })
})

/**
 * Im NEX-Betrieb freigeben (Rundgang-Befund 6).
 *
 * ⚠️ Dort bleiben Ordner und Profil an jeder Anfrage leer, sie hängen an der
 * Fassung in nexcrate. Die Seite fragte nur „fehlt der Ordner?“ und öffnete
 * für jede wartende Anfrage die Zielwahl, deren Listen-Adresse im
 * NEX-Betrieb `409` antwortet. Der Server gibt ohne Ziel frei
 * (`admin_requests._braucht_ziel`); die Attrappe antwortet wie er.
 */
describe('AdminRequestsPage: Freigabe im NEX-Betrieb', () => {
  it('gibt frei, ohne nach Ordner und Profil zu fragen', async () => {
    // Die Tests davor rufen `/api/arr/` selbst; ohne Leeren zählten sie mit.
    vi.clearAllMocks()
    const schicken = vi.mocked(api.post)
    schicken.mockResolvedValue({})
    holen.mockImplementation((pfad: string) => {
      if (pfad.startsWith('/api/config')) {
        return Promise.resolve({
          beschaffung: 'nex',
          beschaffung_kann: {
            warum: true, papierkorb: true, anime: true, kalender: true, wertungen: [],
            zielwahl: false,
          },
          beschaffung_sprung: {},
          fassungen: [
            { kennung: 'v_beispiel', media_type: 'movie', name: 'Movies', klasse: 'hd', quelle: 'nex', haupt: true },
          ],
        })
      }
      if (pfad.startsWith('/api/admin/requests')) {
        return Promise.resolve([zeile(8, 'pending_approval', 'v_beispiel', 'Erfundener NEX-Film')])
      }
      if (pfad.startsWith('/api/arr/')) {
        return Promise.reject(new Error('409 not_in_this_mode'))
      }
      return Promise.resolve({})
    })
    rendern(<AdminRequestsPage />, { pfad: '/admin/requests?filter=all' })
    await screen.findByText('Erfundener NEX-Film')

    await userEvent.click(screen.getByRole('button', { name: 'Freigeben' }))

    await vi.waitFor(() =>
      expect(schicken).toHaveBeenCalledWith('/api/admin/requests/8/approve', undefined),
    )
    expect(screen.queryByText('Zielordner')).toBeNull()
    expect(holen.mock.calls.some(([p]) => String(p).startsWith('/api/arr/'))).toBe(false)
  })

  it('gibt auch alle einer Person auf einmal frei', async () => {
    vi.clearAllMocks()
    const schicken = vi.mocked(api.post)
    schicken.mockResolvedValue({})
    holen.mockImplementation((pfad: string) => {
      if (pfad.startsWith('/api/config')) {
        return Promise.resolve({
          beschaffung: 'nex',
          beschaffung_kann: {
            warum: true, papierkorb: true, anime: true, kalender: true, wertungen: [],
            zielwahl: false,
          },
          beschaffung_sprung: {},
          fassungen: [
            { kennung: 'v_beispiel', media_type: 'movie', name: 'Movies', klasse: 'hd', quelle: 'nex', haupt: true },
          ],
        })
      }
      if (pfad.startsWith('/api/admin/requests')) {
        return Promise.resolve([
          zeile(9, 'pending_approval', 'v_beispiel', 'Erfundener erster NEX-Film'),
          zeile(10, 'pending_approval', 'v_beispiel', 'Erfundener zweiter NEX-Film'),
        ])
      }
      if (pfad.startsWith('/api/arr/')) {
        return Promise.reject(new Error('409 not_in_this_mode'))
      }
      return Promise.resolve({})
    })
    rendern(<AdminRequestsPage />, { pfad: '/admin/requests?filter=all' })
    await screen.findByText('Erfundener erster NEX-Film')

    await userEvent.click(screen.getByRole('button', { name: 'Alle 2 freigeben' }))

    await vi.waitFor(() => expect(schicken).toHaveBeenCalledTimes(1))
    expect(String(schicken.mock.calls[0][0])).toContain('/api/admin/requests/')
    expect(screen.queryByText('Zielordner')).toBeNull()
    expect(holen.mock.calls.some(([p]) => String(p).startsWith('/api/arr/'))).toBe(false)
  })
})
