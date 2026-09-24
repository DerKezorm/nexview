/**
 * Die Detailseite: die Filmreihe unter der Besetzung (Issue #9).
 *
 * Die Reihe ist bewusst nichts Eigenes: dieselben Kacheln und derselbe Wagen
 * wie bei den Empfehlungen. Geprüft wird deshalb, dass sie an ihrer Stelle
 * steht, dass ein Teil über das gewohnte Fenster angefragt wird und dass ohne
 * Reihe nichts erscheint.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
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

import { api } from '../api/client'
import type { MediaDetail, MediaItem } from '../api/types'
import i18n from '../i18n'
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

function antworten(daten: MediaDetail) {
  holen.mockImplementation((async (pfad: string) => {
    if (pfad === '/api/setup/status') {
      return { needs_setup: false, mediaserver_login: false, mediaserver_login_ways: [] }
    }
    if (pfad === '/api/config') return { radarr_configured: true, sonarr_configured: true }
    if (pfad === '/api/detail/movie/901') return daten
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
