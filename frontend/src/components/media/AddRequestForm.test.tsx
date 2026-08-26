/**
 * Weg 2 von dreien: eine Anfrage stellen.
 *
 * Der Weg, um den es bei Nexview überhaupt geht. Geprüft wird vor allem der
 * Serienfall, denn dort steckt eine Entscheidung, die man beim Umbauen leicht
 * wieder herausreißt: **Eine bereits laufende Staffel darf den Stapel nicht
 * abbrechen.** Wer 1, 4 und 7 anhakt und 4 ist schon unterwegs, will 1 und 7
 * trotzdem haben.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
    setTokens: vi.fn(),
    clearTokens: vi.fn(),
    logout: vi.fn(),
    restoreSession: vi.fn(async () => false),
    setSessionLostHandler: vi.fn(),
  }
})

import { ApiError, api } from '../../api/client'
import type { MediaItem } from '../../api/types'
import { rendern } from '../../test/rendern'
import { AddRequestForm } from './AddRequestForm'

const holen = vi.mocked(api.get)
const schicken = vi.mocked(api.post)

const FILM = {
  media_type: 'movie',
  tmdb_id: 603,
  title: 'Matrix',
  status: 'not_requested',
  // ⚠️ Auch bei Filmen. Das Backend schickt ``seasons: []`` immer mit; ein
  // Fixture ohne das Feld lässt das Bauteil an ``item.seasons.length``
  // scheitern - ein Zustand, den es in der Anwendung gar nicht gibt.
  seasons: [],
} as unknown as MediaItem

const SERIE = {
  media_type: 'tv',
  tmdb_id: 1399,
  title: 'Eine Serie',
  status: 'not_requested',
  seasons: [
    { season_number: 1, name: 'Staffel 1', episode_count: 10 },
    { season_number: 2, name: 'Staffel 2', episode_count: 10 },
    { season_number: 3, name: 'Staffel 3', episode_count: 10 },
  ],
} as unknown as MediaItem

function serverAntwortet() {
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') {
      return { radarr_configured: true, sonarr_configured: true }
    }
    if (pfad.startsWith('/api/arr/')) {
      // ⚠️ **Vollständig, auch was das Formular nicht anzeigt.** Ohne
      // Auswahlrecht sind Profil und Ordner unsichtbar - gelesen werden sie
      // trotzdem, und eine unvollständige Attrappe lässt das Bauteil beim
      // Erscheinen scheitern. Genau daran ist der erste Anlauf gestorben.
      return {
        quality_profiles: [{ id: 1, name: 'HD-1080p' }],
        root_folders: [{ path: '/data/Movies', free_space: 1_000_000_000 }],
        default_root_folder: '/data/Movies',
        default_quality_profile_id: 1,
        quality_profile_choice: false,
        root_folder_choice: false,
      }
    }
    throw new Error(`Unerwartet: ${pfad}`)
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  serverAntwortet()
  schicken.mockResolvedValue({ id: 1 })
})

async function abschicken() {
  const nutzer = userEvent.setup()
  const knopf = await screen.findByRole('button', { name: /jetzt anfragen/i })
  await waitFor(() => expect(knopf).toBeEnabled())
  await nutzer.click(knopf)
  return nutzer
}

describe('Einen Film anfragen', () => {
  it('schickt genau eine Anfrage, ohne Staffel', async () => {
    rendern(<AddRequestForm item={FILM} onDone={() => {}} />)
    await abschicken()

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(1))
    const [pfad, koerper] = schicken.mock.calls[0]
    expect(pfad).toBe('/api/requests')
    expect(koerper).toMatchObject({ media_type: 'movie', tmdb_id: 603, season: null })
  })

  it('merkt sich die Herkunft von der Merkliste', async () => {
    rendern(<AddRequestForm item={FILM} onDone={() => {}} fromWatchlist />)
    await abschicken()

    await waitFor(() =>
      expect(schicken.mock.calls[0][1]).toMatchObject({ from_watchlist: true }),
    )
  })

  it('meldet dem Aufrufer, dass es fertig ist', async () => {
    const fertig = vi.fn()
    rendern(<AddRequestForm item={FILM} onDone={fertig} />)
    await abschicken()

    await waitFor(() => expect(fertig).toHaveBeenCalled())
  })
})

describe('Eine Serie anfragen', () => {
  async function staffelnWaehlen(nummern: number[]) {
    const nutzer = userEvent.setup()
    // Erst das Staffelfenster öffnen.
    await nutzer.click(await screen.findByRole('button', { name: /auswählen/i }))
    // ⚠️ Über den Text des Labels, nicht über den berechneten Namen des
    // Kästchens. Der setzt sich aus Staffelname **und** Folgenzahl zusammen
    // („Staffel 1 10 Folgen"), und ein Suchmuster darauf wäre beim ersten
    // geänderten Zusatz hinfällig — der Test ginge kaputt, ohne dass sich am
    // Verhalten etwas geändert hätte.
    const kaestchenZu = async (nummer: number) => {
      const beschriftung = await screen.findByText(`Staffel ${nummer}`)
      const kaestchen = beschriftung
        .closest('label')
        ?.querySelector<HTMLInputElement>('input[type="checkbox"]')
      if (!kaestchen) throw new Error(`Kein Kästchen zu Staffel ${nummer}`)
      return kaestchen
    }

    for (const nummer of nummern) {
      await nutzer.click(await kaestchenZu(nummer))
    }
    await nutzer.click(screen.getByRole('button', { name: /^fertig$/i }))
    return nutzer
  }

  it('schickt je Staffel eine Anfrage, aufsteigend', async () => {
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([3, 1])
    await abschicken()

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(2))
    // ⚠️ Aufsteigend, auch wenn anders angehakt: So kommen die Anfragen in
    // der Reihenfolge an, in der man sie liest.
    expect(schicken.mock.calls.map(([, k]) => (k as { season: number }).season)).toEqual([1, 3])
  })

  it('⚠️ eine schon laufende Staffel bricht den Stapel nicht ab', async () => {
    // Der Fall: 1, 2 und 3 angehakt, 2 läuft schon. 1 und 3 müssen trotzdem
    // durchgehen - "ist schon angefragt" ist ja das Ergebnis, das man wollte.
    schicken.mockImplementation(async (_pfad, koerper) => {
      if ((koerper as { season: number }).season === 2) {
        throw new ApiError(409, 'Staffel 2 wurde bereits angefragt.')
      }
      return { id: 1 }
    })

    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1, 2, 3])
    await abschicken()

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(3))
    expect(schicken.mock.calls.map(([, k]) => (k as { season: number }).season)).toEqual([1, 2, 3])
  })

  it('ein echter Fehler bricht dagegen ab', async () => {
    // 409 heißt "hast du schon". Alles andere heißt, dass etwas nicht
    // stimmt - dann weiterzumachen hieße, den Fehler zu vervielfachen.
    schicken.mockImplementation(async (_pfad, koerper) => {
      if ((koerper as { season: number }).season === 1) {
        throw new ApiError(500, 'Serverfehler.')
      }
      return { id: 1 }
    })

    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1, 2, 3])
    await abschicken()

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(1))
  })

  it('ohne gewählte Staffel geht nichts hinaus', async () => {
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    const knopf = await screen.findByRole('button', { name: /jetzt anfragen/i })

    await userEvent.setup().click(knopf)
    expect(schicken).not.toHaveBeenCalled()
  })
})
