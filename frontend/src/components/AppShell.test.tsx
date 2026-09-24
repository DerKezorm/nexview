/**
 * Die Fußzeile trägt die Namensnennung der Wertungen.
 *
 * Karten und Listenzeilen zeigen IMDb-Werte ohne Platz für den Satz, den IMDb
 * dazu verlangt (nexcrate reicht die Bedingung weiter: „where the ratings
 * show“). Er steht deshalb einmal unten, wörtlich wie nexcrate ihn schickt.
 */

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
import type { MovieRatings } from '../api/types'
import { rendernSchlicht } from '../test/rendern'
import { Footer } from './AppShell'
import { useMovieRatings } from './media/useMovieRatings'

const holen = vi.mocked(api.get)

const IMDB = 'Information courtesy of IMDb (https://www.example.com). Used with permission.'

function wertung(attribution: string[]): MovieRatings {
  return {
    imdb_id: 'tt0000603',
    imdb: 7.5,
    imdb_votes: 1200,
    rotten_tomatoes: null,
    metacritic: null,
    attribution,
  }
}

/** Eine Kachelreihe, die Wertungen lädt – wie jede Seite mit Filmen. */
function Kacheln() {
  useMovieRatings([{ media_type: 'movie', tmdb_id: 603 }])
  return null
}

function antworten(attribution: string[]) {
  holen.mockReset()
  holen.mockImplementation(async (pfad: string) => {
    if (pfad.startsWith('/api/ratings/')) return { 603: wertung(attribution) }
    if (pfad === '/api/about') return { version: '1.0.0', update_available: false }
    return {}
  })
}

describe('Footer', () => {
  it('nennt IMDb, sobald Wertungen mit dem Satz geladen sind', async () => {
    antworten([IMDB])
    rendernSchlicht(
      <>
        <Kacheln />
        <Footer onHausordnung={() => {}} />
      </>,
    )
    expect(await screen.findByText(IMDB)).toBeInTheDocument()
  })

  it('nennt nichts, wenn die Wertungen keinen Satz tragen (ARR-Betrieb)', async () => {
    antworten([])
    rendernSchlicht(
      <>
        <Kacheln />
        <Footer onHausordnung={() => {}} />
      </>,
    )
    expect(await screen.findByText('v1.0.0')).toBeInTheDocument()
    await vi.waitFor(() =>
      expect(holen).toHaveBeenCalledWith('/api/ratings/movie?ids=603'),
    )
    expect(screen.queryByText(/Information courtesy of IMDb/)).not.toBeInTheDocument()
  })
})
