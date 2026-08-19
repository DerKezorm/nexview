/**
 * Der gemeinsame Nenner aller Kacheln.
 *
 * Vorher gab es sechs eigene Kachel-Bauweisen – Entdecken, Filmografie,
 * Startseite (gleich dreimal), Favoriten –, weil jede Seite ihre Daten in einer
 * anderen Form vorliegen hat. Das Ergebnis waren vier verschiedene
 * Poster-Platzhalter, drei Eckenradien und ein Warenkorb an fünf Stellen.
 *
 * Statt die Kachel an jede Datenform anzupassen, wird hier umgekehrt jede
 * Datenform auf **eine** Kachel-Form gebracht. Die Übersetzer sind winzig; die
 * Kachel muss dafür nur einmal existieren.
 */

import type { Favorite, MediaItem, MediaStatus, MediaType, PersonCredit } from '../../api/types'

export type CardItem = {
  media_type: MediaType
  tmdb_id: number
  title: string
  poster_url: string | null
  release_date: string | null
  /** TMDB-Wertung; 0 heißt "noch keine". */
  vote_average: number
  /**
   * Wie viele Stimmen dahinterstehen – oder `undefined`, wenn die Quelle das
   * nicht mitliefert (Filmografien etwa). Eine 0 bedeutet dagegen wirklich
   * "noch niemand hat bewertet", und nur dann zeigt die Kachel einen Strich.
   */
  vote_count?: number
  status: MediaStatus
  /** Hat der angemeldete Benutzer das schon gesehen? */
  watched: boolean
  genres: string[]
  runtime_minutes: number | null
  certification: string | null
  overview: string
  /** Nur bei Filmografien: die Rolle bzw. "Regie"/"Drehbuch". */
  character?: string
}

export function fromMediaItem(item: MediaItem): CardItem {
  return {
    media_type: item.media_type,
    tmdb_id: item.tmdb_id,
    title: item.title,
    poster_url: item.poster_url,
    release_date: item.release_date,
    vote_average: item.vote_average,
    vote_count: item.vote_count,
    status: item.status,
    watched: item.watched ?? false,
    genres: item.genres,
    runtime_minutes: item.runtime_minutes,
    certification: item.certification,
    overview: item.overview,
  }
}

/**
 * Ein Eintrag aus einer Filmografie.
 *
 * TMDB liefert dort weder Genres noch Laufzeit – die Kachel kommt ohne aus und
 * zeigt stattdessen die Rolle. Vorher baute die Personenseite sich dafür einen
 * vollständigen `MediaItem` mit erfundenen Nullwerten zusammen.
 */
export function fromPersonCredit(credit: PersonCredit): CardItem {
  return {
    media_type: credit.media_type,
    tmdb_id: credit.tmdb_id,
    title: credit.title,
    poster_url: credit.poster_url,
    release_date: credit.release_date,
    vote_average: credit.vote_average,
    status: credit.status,
    watched: credit.watched ?? false,
    genres: [],
    runtime_minutes: null,
    certification: null,
    overview: '',
    character: credit.character,
  }
}

/**
 * Ein gemerkter Titel.
 *
 * Favoriten führt Nexview mit eigenem Titel und Poster in der Datenbank – das
 * überlebt auch eine spätere Altersbeschränkung. Zustand und Wertung kennt der
 * Eintrag nicht; die Kachel zeigt sie dann einfach nicht.
 */
export function fromFavorite(eintrag: Favorite): CardItem {
  return {
    media_type: eintrag.media_type,
    tmdb_id: eintrag.tmdb_id,
    title: eintrag.title,
    poster_url: eintrag.poster_url,
    release_date: null,
    vote_average: 0,
    vote_count: 0,
    status: 'not_requested',
    watched: false,
    genres: [],
    runtime_minutes: null,
    certification: null,
    overview: '',
  }
}
