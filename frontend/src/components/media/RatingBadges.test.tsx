/**
 * Die Namensnennung unter den Wertungen, und wer die Einzelansicht fragt.
 *
 * Im NEX-Betrieb kommen Rotten Tomatoes und Metacritic über OMDb, und OMDb
 * verlangt einen Satz dort, wo die Werte stehen. nexcrate schickt ihn mit;
 * die Oberfläche zeigt ihn wörtlich und sonst nichts.
 *
 * ⚠️ **Nur die Titelseite fragt die Einzelansicht.** Jeder Aufruf kostet
 * nexcrate eine OMDb-Abfrage aus einem Tageskontingent; eine Liste mit
 * vierzig Kacheln darf das nicht auslösen.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../../api/client', async () => {
  const echt = await vi.importActual<typeof import('../../api/client')>('../../api/client')
  return {
    ...echt,
    api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  }
})

import { api } from '../../api/client'
import type { MovieRatings } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { RatingCredit, WertungsNennung } from './RatingBadges'
import { useMovieRatings } from './useMovieRatings'

const holen = vi.mocked(api.get)

const IMDB = 'Information courtesy of IMDb (https://www.example.com). Used with permission.'
const OMDB = 'Rotten Tomatoes and Metacritic through the OMDb API (https://www.example.com).'

function wertung(attribution: string[]): MovieRatings {
  return {
    imdb_id: 'tt0000603',
    imdb: 7.5,
    imdb_votes: 1200,
    rotten_tomatoes: 88,
    metacritic: 71,
    attribution,
  }
}

describe('RatingCredit', () => {
  it('zeigt die Sätze der Quelle wörtlich', () => {
    rendernSchlicht(<RatingCredit ratings={wertung([IMDB, OMDB])} />)
    expect(screen.getByText(`${IMDB} ${OMDB}`)).toBeInTheDocument()
  })

  it('zeigt nichts, wenn keine Nennung verlangt ist', () => {
    const { container } = rendernSchlicht(<RatingCredit ratings={wertung([])} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('zeigt nichts ohne Wertungen', () => {
    const { container } = rendernSchlicht(<RatingCredit ratings={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })
})

function Frager({ einzeln }: { einzeln?: boolean }) {
  useMovieRatings(
    [
      { media_type: 'movie', tmdb_id: 604 },
      { media_type: 'movie', tmdb_id: 603 },
    ],
    einzeln === undefined ? undefined : { einzeln },
  )
  return null
}

describe('useMovieRatings', () => {
  it('fragt für Listen nur den Stapel', async () => {
    holen.mockReset()
    holen.mockResolvedValue({})
    rendernSchlicht(<Frager />)
    await waitFor(() => expect(holen).toHaveBeenCalled())
    expect(holen).toHaveBeenCalledWith('/api/ratings/movie?ids=603,604')
  })

  it('fragt für die Titelseite die Einzelansicht', async () => {
    holen.mockReset()
    holen.mockResolvedValue({})
    rendernSchlicht(<Frager einzeln />)
    await waitFor(() => expect(holen).toHaveBeenCalled())
    expect(holen).toHaveBeenCalledWith('/api/ratings/movie?ids=603,604&detail=true')
  })
})

/**
 * Die Fußzeile: Karten zeigen IMDb-Werte ohne Platz für den Satz, den IMDb
 * dazu verlangt. Er steht einmal unten, sobald geladene Wertungen ihn tragen.
 */
describe('WertungsNennung', () => {
  it('nennt jeden Satz der geladenen Wertungen einmal', async () => {
    holen.mockReset()
    holen.mockResolvedValue({ 603: wertung([IMDB]), 604: wertung([IMDB, OMDB]) })
    rendernSchlicht(
      <>
        <Frager />
        <WertungsNennung />
      </>,
    )
    expect(await screen.findByText(`${IMDB} ${OMDB}`)).toBeInTheDocument()
  })

  it('zeigt nichts, solange keine Wertung einen Satz trägt (ARR-Betrieb)', async () => {
    holen.mockReset()
    holen.mockResolvedValue({ 603: wertung([]), 604: wertung([]) })
    const { container } = rendernSchlicht(
      <>
        <Frager />
        <WertungsNennung />
      </>,
    )
    await waitFor(() => expect(holen).toHaveBeenCalled())
    await new Promise((fertig) => setTimeout(fertig, 20))
    expect(container).toBeEmptyDOMElement()
  })

  it('zeigt nichts ohne geladene Wertungen', () => {
    const { container } = rendernSchlicht(<WertungsNennung />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lässt weg, was die Titelseite schon unter ihren Werten nennt', async () => {
    // Titelseite eines Films: oben die Einzelansicht mit IMDb, unten Kacheln
    // mit IMDb und OMDb. Unten bleibt nur, was oben fehlt (Prüfer, 24.09.2026).
    holen.mockReset()
    holen.mockImplementation(async (pfad: string) =>
      pfad.includes('detail=true') ? { 603: wertung([IMDB]) } : { 604: wertung([IMDB, OMDB]) },
    )
    rendernSchlicht(
      <>
        <Titel />
        <Kachel />
        <WertungsNennung />
      </>,
    )
    expect(await screen.findByText(OMDB)).toBeInTheDocument()
    expect(screen.queryByText(IMDB, { exact: false })).not.toBeInTheDocument()
  })

  it('vergisst eine verlassene Seite, auch wenn ihre Wertungen noch im Speicher liegen', async () => {
    holen.mockReset()
    holen.mockResolvedValue({ 604: wertung([IMDB]) })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const seite = (mitKachel: boolean) => (
      <QueryClientProvider client={client}>
        {mitKachel && <Kachel />}
        <WertungsNennung />
      </QueryClientProvider>
    )
    const { rerender } = render(seite(true))
    expect(await screen.findByText(IMDB)).toBeInTheDocument()

    rerender(seite(false))
    await waitFor(() => expect(screen.queryByText(IMDB)).not.toBeInTheDocument())
    expect(client.getQueryCache().findAll({ queryKey: ['movie-ratings'] })).toHaveLength(1)
  })
})

function Titel() {
  useMovieRatings([{ media_type: 'movie', tmdb_id: 603 }], { einzeln: true })
  return null
}

function Kachel() {
  useMovieRatings([{ media_type: 'movie', tmdb_id: 604 }])
  return null
}
