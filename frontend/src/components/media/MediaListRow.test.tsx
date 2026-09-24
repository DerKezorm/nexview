/**
 * Die Listenzeile zeigt von den Wertungen nur IMDb, wie die Kachel.
 *
 * Seit nexcrate Rotten Tomatoes und Metacritic im Stapel mitschickt, tragen
 * die Wertungen einer Zeile echte Werte für beide. OMDb verlangt neben diesen
 * Werten einen Satz, den die Zeile nicht hat; die volle Auswahl samt Satz
 * steht auf der Titelseite.
 *
 * ⚠️ Ohne diesen Test fiel es nicht auf, wenn `nurImdb` an der Zeile fehlte:
 * `RatingBadges` selbst ist geprüft, sein Aufruf hier war es nicht.
 */

import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import type { MediaItem, MovieRatings } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import { MediaListRow } from './MediaListRow'

const OMDB = 'Rotten Tomatoes and Metacritic through the OMDb API (https://www.example.com).'

const WERTUNG: MovieRatings = {
  imdb_id: 'tt0000603',
  imdb: 7.5,
  imdb_votes: 1200,
  rotten_tomatoes: 88,
  metacritic: 71,
  attribution: [OMDB],
}

const TITEL: MediaItem = {
  media_type: 'movie',
  tmdb_id: 603,
  tvdb_id: null,
  title: 'Beispielfilm',
  original_title: null,
  overview: '',
  poster_url: null,
  backdrop_url: null,
  release_date: '1999-03-31',
  vote_average: 8.2,
  vote_count: 100,
  genres: [],
  runtime_minutes: 136,
  certification: null,
  original_language: null,
  seasons: [],
  status: 'not_requested',
}

describe('MediaListRow', () => {
  it('zeigt nur IMDb, auch wenn Rotten Tomatoes und Metacritic da sind', () => {
    rendernSchlicht(<MediaListRow item={TITEL} ratings={WERTUNG} onQuickAdd={vi.fn()} />)

    expect(screen.getByTitle('IMDb 7.5')).toBeInTheDocument()
    expect(screen.queryByTitle('Rotten Tomatoes 88%')).not.toBeInTheDocument()
    expect(screen.queryByTitle('Metacritic 71')).not.toBeInTheDocument()
    expect(screen.queryByText(/OMDb/)).not.toBeInTheDocument()
  })
})
