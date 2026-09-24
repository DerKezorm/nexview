/**
 * Die Kachel zeigt von den Wertungen nur IMDb.
 *
 * Seit nexcrate Rotten Tomatoes und Metacritic im Stapel mitschickt, tragen
 * die Wertungen einer Kachel echte Werte für beide. Die Leiste unter dem
 * Poster ist für drei Abzeichen zu schmal, und OMDb verlangt neben diesen
 * Werten einen Satz, den die Kachel nicht hat. Also bleibt es bei IMDb.
 *
 * ⚠️ Ohne diesen Test fiel es nicht auf, wenn `nurImdb` an der Kachel fehlte:
 * `RatingBadges` selbst ist geprüft, sein Aufruf hier war es nicht.
 */

import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import type { MovieRatings } from '../../api/types'
import { rendernSchlicht } from '../../test/rendern'
import type { CardItem } from './cardItem'
import { MediaCard } from './MediaCard'

const OMDB = 'Rotten Tomatoes and Metacritic through the OMDb API (https://www.example.com).'

const WERTUNG: MovieRatings = {
  imdb_id: 'tt0000603',
  imdb: 7.5,
  imdb_votes: 1200,
  rotten_tomatoes: 88,
  metacritic: 71,
  attribution: [OMDB],
}

const TITEL: CardItem = {
  media_type: 'movie',
  tmdb_id: 603,
  title: 'Beispielfilm',
  poster_url: null,
  release_date: '1999-03-31',
  vote_average: 8.2,
  vote_count: 100,
  status: 'not_requested',
  watched: false,
  genres: [],
  runtime_minutes: 136,
  certification: null,
  overview: '',
}

describe('MediaCard', () => {
  it('zeigt nur IMDb, auch wenn Rotten Tomatoes und Metacritic da sind', () => {
    rendernSchlicht(<MediaCard item={TITEL} ratings={WERTUNG} />)

    expect(screen.getByTitle('IMDb 7.5')).toBeInTheDocument()
    expect(screen.queryByTitle('Rotten Tomatoes 88%')).not.toBeInTheDocument()
    expect(screen.queryByTitle('Metacritic 71')).not.toBeInTheDocument()
    expect(screen.queryByText(/OMDb/)).not.toBeInTheDocument()
  })
})
