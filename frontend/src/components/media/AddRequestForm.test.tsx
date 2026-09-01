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

// ⚠️ **Vollständig, auch was das Formular nicht anzeigt.** Ohne Auswahlrecht
// sind Profil und Ordner unsichtbar - gelesen werden sie trotzdem, und eine
// unvollständige Attrappe lässt das Bauteil beim Erscheinen scheitern. Genau
// daran ist der erste Anlauf gestorben.
const ARR_OPTIONEN = {
  quality_profiles: [{ id: 1, name: 'HD-1080p' }],
  root_folders: [{ path: '/data/Movies', free_space: 1_000_000_000 }],
  default_root_folder: '/data/Movies',
  default_quality_profile_id: 1,
  quality_profile_choice: false,
  root_folder_choice: false,
}

function serverAntwortet() {
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') {
      return { radarr_configured: true, sonarr_configured: true }
    }
    if (pfad.startsWith('/api/arr/')) {
      return ARR_OPTIONEN
    }
    throw new Error(`Unerwartet: ${pfad}`)
  })
}

/** Wie ``serverAntwortet``, aber mit Haus-Schalter an und Folgenlisten. */
function serverMitFolgen() {
  holen.mockImplementation(async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') {
      return {
        radarr_configured: true,
        sonarr_configured: true,
        episode_requests_enabled: true,
      }
    }
    if (pfad.startsWith('/api/arr/')) {
      return ARR_OPTIONEN
    }
    if (pfad === '/api/detail/tv/1399/season/2') {
      return {
        season_number: 2,
        name: 'Staffel 2',
        episodes: [
          { episode_number: 1, name: 'Eins', available: false, requested: true },
          { episode_number: 2, name: 'Zwei', available: false },
          { episode_number: 3, name: 'Drei', available: true },
          { episode_number: 4, name: 'Vier', available: false },
        ],
      }
    }
    if (pfad === '/api/detail/tv/1399/season/3') {
      return {
        season_number: 3,
        name: 'Staffel 3',
        episodes: [
          { episode_number: 1, name: 'Eins', available: false },
          { episode_number: 2, name: 'Zwei', available: false },
        ],
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
    // Seit dem Folgen-Wähler trägt jedes Staffel-Kästchen den Staffelnamen
    // als eigenes ``aria-label`` — der berechnete Name ist damit genau der
    // Name, ohne Zusätze wie die Folgenzahl.
    const kaestchenZu = async (nummer: number) =>
      await screen.findByRole('checkbox', { name: `Staffel ${nummer}` })

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

describe('Folgen-Pakete', () => {
  async function fensterOeffnen() {
    const nutzer = userEvent.setup()
    await nutzer.click(await screen.findByRole('button', { name: /auswählen/i }))
    return nutzer
  }

  it('ein Folgen-Paket schickt seine Folgenliste mit', async () => {
    serverMitFolgen()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    const nutzer = await fensterOeffnen()

    // Mischform in einem Zug: Staffel 1 ganz, aus Staffel 2 nur Folge 2 und 4.
    await nutzer.click(await screen.findByRole('checkbox', { name: 'Staffel 1' }))
    await nutzer.click(
      await screen.findByRole('button', { name: /einzelne folgen von staffel 2/i }),
    )
    await nutzer.click(await screen.findByRole('checkbox', { name: /zwei/i }))
    await nutzer.click(await screen.findByRole('checkbox', { name: /vier/i }))
    await nutzer.click(screen.getByRole('button', { name: /^fertig$/i }))
    await abschicken()

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(2))
    expect(schicken.mock.calls[0][1]).toMatchObject({ season: 1 })
    expect(schicken.mock.calls[0][1]).not.toHaveProperty('episodes')
    expect(schicken.mock.calls[1][1]).toMatchObject({ season: 2, episodes: [2, 4] })
  })

  it('belegte Folgen lassen sich nicht anhaken', async () => {
    serverMitFolgen()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    const nutzer = await fensterOeffnen()

    await nutzer.click(
      await screen.findByRole('button', { name: /einzelne folgen von staffel 2/i }),
    )
    // Folge 1 läuft schon (Anfrage), Folge 3 liegt schon da - beides gesperrt.
    expect(await screen.findByRole('checkbox', { name: /eins/i })).toBeDisabled()
    expect(await screen.findByRole('checkbox', { name: /drei/i })).toBeDisabled()
  })

  it('alle Folgen angehakt heißt: die ganze Staffel', async () => {
    serverMitFolgen()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    const nutzer = await fensterOeffnen()

    await nutzer.click(
      await screen.findByRole('button', { name: /einzelne folgen von staffel 3/i }),
    )
    await nutzer.click(await screen.findByRole('checkbox', { name: /eins/i }))
    await nutzer.click(await screen.findByRole('checkbox', { name: /zwei/i }))
    await nutzer.click(screen.getByRole('button', { name: /^fertig$/i }))
    await abschicken()

    // Wer alles anhakt, bestellt die Staffel - samt künftiger Folgen, ohne
    // Folgenliste im Auftrag.
    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(1))
    expect(schicken.mock.calls[0][1]).toMatchObject({ season: 3 })
    expect(schicken.mock.calls[0][1]).not.toHaveProperty('episodes')
  })
})

/**
 * Welche Serie meinst du? (Issue #5)
 *
 * Der Server antwortet mit 428, wenn TMDB keine TVDB-Kennung führt und Sonarr
 * mehrere ähnliche Serien kennt. Was hier hängt, hängt still: Der Server ist
 * gut abgedeckt, aber ob das Fenster überhaupt erscheint und ob die Auswahl
 * zurückgeht, sah bisher nur ein Mensch.
 */
describe('Die Rückfrage, welche Serie gemeint ist', () => {
  /** Wie oben - dort steckt sie in einem anderen ``describe``. */
  async function staffelnWaehlen(nummern: number[]) {
    const nutzer = userEvent.setup()
    await nutzer.click(await screen.findByRole('button', { name: /auswählen/i }))
    for (const nummer of nummern) {
      await nutzer.click(await screen.findByRole('checkbox', { name: `Staffel ${nummer}` }))
    }
    await nutzer.click(screen.getByRole('button', { name: /^fertig$/i }))
    return nutzer
  }

  const VORSCHLAEGE = [
    {
      tvdb_id: 479935,
      title: 'Still Waters',
      year: null,
      overview: 'Wales, 1995.',
      poster_url: null,
    },
    {
      tvdb_id: 85098,
      title: 'Stille Waters',
      year: 2001,
      overview: 'Stan Moereels wird tot aufgefunden.',
      poster_url: null,
    },
  ]

  function serverFragtZurueck(fresh = true) {
    schicken.mockImplementationOnce(async () => {
      throw new ApiError(428, 'Nicht zuzuordnen.', 'tvdb_choice_needed', {
        candidates: VORSCHLAEGE,
        fresh,
      })
    })
  }

  it('zeigt das Fenster mit Sonarrs Vorschlägen', async () => {
    serverFragtZurueck()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    await abschicken()

    expect(await screen.findByText('Still Waters')).toBeInTheDocument()
    expect(screen.getByText('Stille Waters')).toBeInTheDocument()
  })

  it('⚠️ zeigt daneben, was angefragt wurde', async () => {
    // Der Grund, warum das Fenster überhaupt taugt: "Still Waters" und
    // "Still Water" sind einen Buchstaben auseinander. Nebeneinandergelegt
    // fällt der Unterschied auf, sonst nicht.
    serverFragtZurueck()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    await abschicken()

    expect(await screen.findByText(/das hast du angefragt/i)).toBeInTheDocument()
    expect(screen.getByText('Eine Serie')).toBeInTheDocument()
  })

  it('⚠️ wählt nichts vor', async () => {
    // Ein voreingestellter erster Treffer wäre genau das Raten, das der
    // Server bewusst unterlässt.
    serverFragtZurueck()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    await abschicken()

    await screen.findByText('Still Waters')
    for (const knopf of screen.getAllByRole('button', { pressed: false })) {
      expect(knopf).toHaveAttribute('aria-pressed', 'false')
    }
    expect(screen.getByRole('button', { name: /weiter/i })).toBeDisabled()
  })

  it('schickt die Anfrage nach der Auswahl noch einmal, mit der Kennung', async () => {
    serverFragtZurueck()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    const nutzer = await abschicken()

    await nutzer.click(await screen.findByRole('button', { name: /stille waters/i }))
    await nutzer.click(screen.getByRole('button', { name: /weiter/i }))

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(2))
    const [, koerper] = schicken.mock.calls[1]
    expect((koerper as { tvdb_id: number }).tvdb_id).toBe(85098)
    expect((koerper as { season: number }).season).toBe(1)
  })

  it('sagt bei einer neuen Serie, dass Warten hilft', async () => {
    serverFragtZurueck(true)
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    await abschicken()

    expect(await screen.findByText(/ein paar tagen nach/i)).toBeInTheDocument()
  })

  it('⚠️ sagt bei einer alten Serie, dass Warten nicht hilft', async () => {
    // "Versuch es später" wäre bei einem Titel von 1978 eine Vertröstung -
    // es trägt niemand mehr etwas nach.
    serverFragtZurueck(false)
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    await abschicken()

    expect(await screen.findByText(/nichts mehr ändern/i)).toBeInTheDocument()
  })

  it('bricht ohne Anfrage ab, wenn niemand passt', async () => {
    serverFragtZurueck()
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    const nutzer = await abschicken()

    await nutzer.click(await screen.findByRole('button', { name: /abbrechen/i }))

    expect(screen.queryByText('Still Waters')).not.toBeInTheDocument()
    expect(schicken).toHaveBeenCalledTimes(1)
  })

  it('⚠️ ein gewöhnlicher Fehler öffnet kein Fenster', async () => {
    // Sonst erschiene die Rückfrage bei jedem Aussetzer, und niemand wüsste,
    // was er da eigentlich beantwortet.
    schicken.mockImplementationOnce(async () => {
      throw new ApiError(502, 'Sonarr antwortet nicht.')
    })
    rendern(<AddRequestForm item={SERIE} onDone={() => {}} />)
    await staffelnWaehlen([1])
    await abschicken()

    await waitFor(() => expect(schicken).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/welche serie meinst du/i)).not.toBeInTheDocument()
  })
})
