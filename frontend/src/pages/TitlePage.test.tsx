/**
 * Die Detailseite: die Filmreihe unter der Besetzung (Issue #9).
 *
 * Die Reihe ist bewusst nichts Eigenes: dieselben Kacheln und derselbe Wagen
 * wie bei den Empfehlungen. Geprüft wird deshalb, dass sie an ihrer Stelle
 * steht, dass ein Teil über das gewohnte Fenster angefragt wird und dass ohne
 * Reihe nichts erscheint.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'

vi.mock('../api/client', async () => {
  const echt = await vi.importActual<typeof import('../api/client')>('../api/client')
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

import { ApiError, api, restoreSession } from '../api/client'
import type { MediaDetail, MediaItem } from '../api/types'
import i18n from '../i18n'
import { NACHFRAGEN_MS } from '../lib/weg'
import { rendern } from '../test/rendern'
import { TitlePage } from './TitlePage'

const holen = vi.mocked(api.get)

function film(tmdbId: number, titel: string): MediaItem {
  return {
    media_type: 'movie',
    tmdb_id: tmdbId,
    tvdb_id: null,
    title: titel,
    original_title: null,
    overview: '',
    poster_url: null,
    backdrop_url: null,
    release_date: '2015-03-10',
    vote_average: 7,
    vote_count: 100,
    genres: [],
    runtime_minutes: null,
    certification: null,
    original_language: null,
    seasons: [],
    status: 'not_requested',
  } as unknown as MediaItem
}

function detail(teil: Partial<MediaDetail> = {}): MediaDetail {
  return {
    ...film(901, 'Erster Teil'),
    tagline: '',
    homepage: null,
    status_text: '',
    original_country: [],
    spoken_languages: [],
    budget: null,
    revenue: null,
    studios: [],
    keywords: [],
    trailer: null,
    watch: null,
    cast: [{ person_id: 1, name: 'Jemand', character: 'Eine Rolle', photo_url: null }],
    crew: [],
    recommendations: [film(950, 'Ein anderer Film')],
    seasons_total: null,
    episodes_total: null,
    series_status: '',
    networks: [],
    collection: {
      id: 4400,
      name: 'Beispielreihe',
      items: [film(902, 'Zweiter Teil'), film(903, 'Dritter Teil')],
    },
    ...teil,
  } as MediaDetail
}

function antworten(
  daten: MediaDetail | (() => MediaDetail),
  config: Record<string, unknown> = { radarr_configured: true, sonarr_configured: true },
) {
  holen.mockImplementation((async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') return config
    if (pfad === '/api/detail/movie/901') return typeof daten === 'function' ? daten() : daten
    if (pfad === '/api/favorites') return []
    if (pfad.startsWith('/api/ratings/movie')) return {}
    throw new Error(`Unerwarteter Aufruf: ${pfad}`)
  }) as never)
}

function seiteOeffnen() {
  return rendern(
    <Routes>
      <Route path="/titel/:mediaType/:tmdbId" element={<TitlePage />} />
    </Routes>,
    { pfad: '/titel/movie/901' },
  )
}

/** Steht `spaeter` im Dokument hinter `frueher`? */
function folgtAuf(frueher: HTMLElement, spaeter: HTMLElement): boolean {
  return Boolean(frueher.compareDocumentPosition(spaeter) & Node.DOCUMENT_POSITION_FOLLOWING)
}

beforeEach(() => {
  holen.mockReset()
})

describe('die Filmreihe', () => {
  it('steht unter der Besetzung und vor den Empfehlungen', async () => {
    antworten(detail())
    seiteOeffnen()

    const reihe = await screen.findByRole('heading', { name: 'Beispielreihe' })
    const besetzung = screen.getByRole('heading', { name: i18n.t('detail.cast') })
    const empfehlungen = screen.getByRole('heading', { name: i18n.t('detail.recommendations') })
    expect(folgtAuf(besetzung, reihe)).toBe(true)
    expect(folgtAuf(reihe, empfehlungen)).toBe(true)

    const oeffnen = i18n.t('media.openDetails')
    expect(screen.getByRole('link', { name: `Zweiter Teil – ${oeffnen}` })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: `Dritter Teil – ${oeffnen}` })).toBeInTheDocument()
  })

  it('fragt einen Teil über das gewohnte Fenster an', async () => {
    antworten(detail())
    const b = userEvent.setup()
    seiteOeffnen()

    await b.click(
      await screen.findByRole('button', { name: `Zweiter Teil – ${i18n.t('media.quickAdd')}` }),
    )

    expect(screen.getByRole('dialog', { name: 'Zweiter Teil' })).toBeInTheDocument()
  })

  it('fehlt, wenn der Film zu keiner Reihe gehört', async () => {
    antworten(detail({ collection: null }))
    seiteOeffnen()

    expect(await screen.findByRole('heading', { name: i18n.t('detail.cast') })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Beispielreihe' })).not.toBeInTheDocument()
    expect(screen.queryByText('Zweiter Teil')).not.toBeInTheDocument()
  })
})

describe('wenn der Weg nicht antwortet', () => {
  /* Prüfgang 26.09.2026: Während nexcrate neu startete, stand ein fertig
     geladener Film als „Nicht angefragt“ da. Jetzt zeigt die Seite den letzten
     bekannten Stand und sagt dazu, dass er nicht bestätigt ist. */
  it('sagt dazu, dass der Stand der zuletzt bekannte ist', async () => {
    antworten(detail({ collection: null, status: 'downloaded', status_unconfirmed: true }))
    seiteOeffnen()

    expect(await screen.findByText(i18n.t('detail.statusUnconfirmed'))).toHaveAttribute(
      'role',
      'status',
    )
  })

  it('nennt im NEX-Betrieb nexcrate', async () => {
    antworten(detail({ collection: null, status: 'downloaded', status_unconfirmed: true }), {
      beschaffung: 'nex',
    })
    seiteOeffnen()

    const satz = i18n.t('detail.statusUnconfirmed', { context: 'nex' })
    expect(satz).toContain('nexcrate')
    expect(await screen.findByText(satz)).toBeInTheDocument()
  })

  it('sagt es anders, wenn der Weg ablehnt statt zu schweigen', async () => {
    antworten(
      detail({
        collection: null,
        status: 'downloaded',
        status_unconfirmed: true,
        status_refused: true,
      }),
      { beschaffung: 'nex' },
    )
    seiteOeffnen()

    expect(
      await screen.findByText(i18n.t('detail.statusRefused', { context: 'nex' })),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(i18n.t('detail.statusUnconfirmed', { context: 'nex' })),
    ).not.toBeInTheDocument()
  })

  it('fragt nach, bis der Stand bestätigt ist, und nimmt den Hinweis dann weg', async () => {
    // Zweite Prüfrunde: Der Hinweis blieb 30 Minuten stehen, auch als nexcrate
    // längst wieder antwortete, und kam nach einem Seitenwechsel zurück.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let aufrufe = 0
      antworten(() => {
        aufrufe += 1
        return detail({ collection: null, status: 'downloaded', status_unconfirmed: aufrufe === 1 })
      })
      seiteOeffnen()
      await screen.findByText(i18n.t('detail.statusUnconfirmed'))

      await vi.advanceTimersByTimeAsync(NACHFRAGEN_MS)

      await waitFor(() =>
        expect(screen.queryByText(i18n.t('detail.statusUnconfirmed'))).not.toBeInTheDocument(),
      )
      expect(aufrufe).toBe(2)
      // Bestätigt: ab jetzt keine weitere Nachfrage.
      await vi.advanceTimersByTimeAsync(3 * NACHFRAGEN_MS)
      expect(aufrufe).toBe(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('schweigt, solange der Weg geantwortet hat', async () => {
    antworten(detail({ collection: null, status: 'downloaded', status_unconfirmed: false }))
    seiteOeffnen()

    await screen.findByRole('heading', { name: i18n.t('detail.cast') })
    expect(screen.queryByText(i18n.t('detail.statusUnconfirmed'))).not.toBeInTheDocument()
  })
})

describe('die Wertungen', () => {
  it('fragt für den Titel die Einzelansicht an', async () => {
    // Im NEX-Betrieb stehen Rotten Tomatoes und Metacritic nur in nexcrates
    // Einzelansicht; ohne `detail=true` fehlten sie auf der Titelseite still.
    antworten(detail({ collection: null }))
    seiteOeffnen()

    await screen.findByRole('heading', { name: i18n.t('detail.cast') })
    const wertungen = holen.mock.calls
      .map(([pfad]) => String(pfad))
      .filter((pfad) => pfad.startsWith('/api/ratings/movie'))
    expect(wertungen).toContain('/api/ratings/movie?ids=901&detail=true')
  })
})

describe('ohne TMDB', () => {
  /* Ohne TMDB-Schlüssel hieß die Titelseite eines echten Films
     "Dieser Demo-Titel ist nicht vorhanden". Jetzt nennt der Server den Grund,
     und wer ihn beheben kann, bekommt den Weg in die Einstellungen. */
  function ohneQuelle(rolle: string, code: string) {
    vi.mocked(restoreSession).mockResolvedValueOnce(true)
    holen.mockImplementation((async (pfad: string) => {
      if (pfad === '/api/setup/status') {
        return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
      }
      if (pfad === '/api/auth/me') {
        return { id: 1, username: 'chef', role: rolle, language: 'de', theme: 'dark' }
      }
      if (pfad === '/api/config') return { radarr_configured: true, sonarr_configured: true }
      if (pfad === '/api/detail/movie/901') {
        throw new ApiError(404, i18n.t(`errors.byCode.${code}`), code)
      }
      return []
    }) as never)
  }

  it('sagt, was fehlt, und zeigt dem Administrator den Weg', async () => {
    ohneQuelle('admin', 'title_needs_tmdb')
    seiteOeffnen()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      i18n.t('errors.byCode.title_needs_tmdb'),
    )
    expect(
      await screen.findByRole('link', { name: i18n.t('discover.demoBannerAdmin') }),
    ).toHaveAttribute('href', '/admin/settings')
  })

  it('zeigt den Weg nur dem, der ihn gehen kann', async () => {
    ohneQuelle('user', 'title_hidden_by_sample_data')
    seiteOeffnen()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      i18n.t('errors.byCode.title_hidden_by_sample_data'),
    )
    expect(
      screen.queryByRole('link', { name: i18n.t('discover.demoBannerAdmin') }),
    ).not.toBeInTheDocument()
  })

  it('bietet bei einem gewöhnlichen Fehler keinen Weg in die Einstellungen an', async () => {
    ohneQuelle('admin', 'sample_title_gone')
    seiteOeffnen()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      i18n.t('errors.byCode.sample_title_gone'),
    )
    expect(
      screen.queryByRole('link', { name: i18n.t('discover.demoBannerAdmin') }),
    ).not.toBeInTheDocument()
  })
})

describe('Wiederholen', () => {
  /* "Gibt es nicht" wird beim zweiten Mal nicht wahrer: ein 404 wird genau
     einmal geholt. Eine Störung (502) bekommt weiter ihren einen zweiten
     Versuch. */
  function detailAntwortet(status: number) {
    holen.mockImplementation((async (pfad: string) => {
      if (pfad === '/api/setup/status') {
        return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
      }
      if (pfad === '/api/config') return { radarr_configured: true, sonarr_configured: true }
      if (pfad === '/api/detail/movie/901') {
        throw new ApiError(status, `Fehler ${status}`, status === 404 ? 'title_needs_tmdb' : null)
      }
      return []
    }) as never)
  }

  function detailAufrufe(): number {
    return holen.mock.calls.filter(([pfad]) => pfad === '/api/detail/movie/901').length
  }

  it('holt einen 404 nur einmal', async () => {
    detailAntwortet(404)
    seiteOeffnen()

    await screen.findByRole('alert')
    expect(detailAufrufe()).toBe(1)
  })

  it('versucht eine Störung ein zweites Mal', async () => {
    detailAntwortet(502)
    seiteOeffnen()

    // Die Wiederholung wartet rund eine Sekunde; länger als findBy von sich aus.
    await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(detailAufrufe()).toBe(2)
  })
})

describe('der Anfrageknopf einer Serie', () => {
  /* Befund: Auf einer Serie, die noch niemand angefragt hatte, hieß der
     einzige Knopf „Staffel nachfordern“. Das klingt, als läge schon etwas
     vor. Nachfordern gibt es erst, wenn von der Serie etwas angefragt oder da
     ist. */
  function staffel(nummer: number, da = 0, angefragt = false) {
    return {
      season_number: nummer,
      name: `Staffel ${nummer}`,
      episode_count: 6,
      air_date: '2019-05-06',
      overview: '',
      poster_url: null,
      episodes_available: da,
      requested: angefragt,
    }
  }

  function serieOeffnen(teil: Partial<MediaDetail>) {
    const daten = detail({
      media_type: 'tv',
      collection: null,
      recommendations: [],
      ...teil,
    } as Partial<MediaDetail>)
    holen.mockImplementation((async (pfad: string) => {
      if (pfad === '/api/setup/status') {
        return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
      }
      if (pfad === '/api/config') return { radarr_configured: true, sonarr_configured: true }
      if (pfad === '/api/detail/tv/901') return daten
      if (pfad === '/api/favorites') return []
      if (pfad.startsWith('/api/ratings/tv')) return {}
      throw new Error(`Unerwarteter Aufruf: ${pfad}`)
    }) as never)
    return rendern(
      <Routes>
        <Route path="/titel/:mediaType/:tmdbId" element={<TitlePage />} />
      </Routes>,
      { pfad: '/titel/tv/901' },
    )
  }

  it('heißt bei einer nie angefragten Serie nicht „nachfordern“', async () => {
    serieOeffnen({ status: 'not_requested', seasons: [staffel(1), staffel(2)] })

    expect(
      await screen.findByRole('button', { name: i18n.t('request.chooseSeason') }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: i18n.t('request.addSeason') }),
    ).not.toBeInTheDocument()
  })

  it('heißt bei einer nie angefragten Serie mit einer Staffel „Anfragen“', async () => {
    serieOeffnen({ status: 'not_requested', seasons: [staffel(1)] })

    expect(
      await screen.findByRole('button', { name: i18n.t('request.addSeries') }),
    ).toBeInTheDocument()
  })

  it('heißt „nachfordern“, sobald von der Serie etwas da ist', async () => {
    serieOeffnen({ status: 'partial', seasons: [staffel(1, 6), staffel(2)] })

    expect(
      await screen.findByRole('button', { name: i18n.t('request.addSeason') }),
    ).toBeInTheDocument()
  })

  it('heißt „nachfordern“, sobald eine Staffel angefragt ist', async () => {
    serieOeffnen({ status: 'not_requested', seasons: [staffel(1, 0, true), staffel(2)] })

    expect(
      await screen.findByRole('button', { name: i18n.t('request.addSeason') }),
    ).toBeInTheDocument()
  })
})
