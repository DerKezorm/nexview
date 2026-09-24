/**
 * Kinderwünsche freigeben im NEX-Betrieb (Rundgang-Befund 6).
 *
 * ⚠️ Die Zielwahl holte immer `/api/arr/{art}/options`. Im NEX-Betrieb
 * antwortet der Server dort `409 not_in_this_mode`; die Freigabe zeigte nur
 * diese Meldung, und der Knopf blieb gesperrt. Die Attrappe antwortet wie der
 * echte Server.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  }
})

import { ApiError, api } from '../../api/client'
import { rendern } from '../../test/rendern'
import { Kinderwuensche } from './Kinderwuensche'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

const WUNSCH = {
  id: 5,
  child_id: 9,
  child_name: 'Kind',
  media_type: 'movie',
  tmdb_id: 862,
  title: 'Erfundener Wunschfilm',
  poster_path: null,
  release_date: null,
  created_at: '2026-09-20T10:00:00',
}

beforeEach(() => {
  vi.clearAllMocks()
  schicken.mockResolvedValue({})
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') {
      return {
        beschaffung: 'nex',
        beschaffung_kann: {
          warum: true, papierkorb: true, anime: true, kalender: true, wertungen: [],
          zielwahl: false,
        },
        fassungen: [
          {
            kennung: 'v_film', media_type: 'movie', name: 'Full-HD', klasse: 'hd',
            quelle: 'nex', haupt: true, bereit: true, offen_fuer_alle: true,
            approver_picks_target: false, darf_anfragen: true,
          },
        ],
      }
    }
    if (pfad === '/api/children/wishes') return [WUNSCH]
    if (pfad.startsWith('/api/arr/')) {
      throw new ApiError(
        409,
        'Dieses Werkzeug gehört zur anderen Betriebsart der Beschaffung.',
        'not_in_this_mode',
      )
    }
    throw new Error(`Unerwartet: ${pfad}`)
  })
})

describe('Kinderwunsch im NEX-Betrieb', () => {
  it('gibt frei, ohne nach Ordner und Profil zu fragen', async () => {
    rendern(<Kinderwuensche />)
    const nutzer = userEvent.setup()
    await nutzer.click(await screen.findByRole('button', { name: 'Freigeben' }))

    const knoepfe = await screen.findAllByRole('button', { name: 'Freigeben' })
    const bestaetigen = knoepfe[knoepfe.length - 1]
    await waitFor(() => expect(bestaetigen).toBeEnabled())
    await nutzer.click(bestaetigen)

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(1))
    expect(schicken.mock.calls[0]).toEqual([
      '/api/children/wishes/5/release',
      {
        fassung: 'v_film',
        quality_profile_id: null,
        root_folder_path: null,
        season: null,
        episodes: null,
      },
    ])
    expect(screen.queryByText(/anderen Betriebsart/)).not.toBeInTheDocument()
    expect(holen.mock.calls.some(([p]) => String(p).startsWith('/api/arr/'))).toBe(false)
  })
})
