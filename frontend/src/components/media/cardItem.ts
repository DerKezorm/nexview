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

import type {
  CalendarEntry,
  Favorite,
  MediaItem,
  MediaStatus,
  MediaType,
  PersonCredit,
} from "../../api/types";

export type CardItem = {
  media_type: MediaType;
  tmdb_id: number;
  title: string;
  poster_url: string | null;
  release_date: string | null;
  /** TMDB-Wertung; 0 heißt "noch keine". */
  vote_average: number;
  /**
   * Wie viele Stimmen dahinterstehen – oder `undefined`, wenn die Quelle das
   * nicht mitliefert (Filmografien etwa). Eine 0 bedeutet dagegen wirklich
   * "noch niemand hat bewertet", und nur dann zeigt die Kachel einen Strich.
   */
  vote_count?: number;
  status: MediaStatus;
  /** Zustand in der 4K-Instanz; `null`/fehlt = keine zweite Instanz. */
  status_uhd?: MediaStatus | null;
  /** Hat der angemeldete Benutzer das schon gesehen? */
  watched: boolean;
  genres: string[];
  runtime_minutes: number | null;
  certification: string | null;
  overview: string;
  /** Nur bei Filmografien: die Rolle bzw. "Regie"/"Drehbuch". */
  character?: string;
};

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
    status_uhd: item.status_uhd,
    watched: item.watched ?? false,
    genres: item.genres,
    runtime_minutes: item.runtime_minutes,
    certification: item.certification,
    overview: item.overview,
  };
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
    // Wie `watched`: Der Server liefert die zweite Achse auch auf einem
    // Filmografie-Eintrag. Wird sie hier nicht weitergereicht, steht auf der
    // Personenseite "Nicht angefragt" an einem Film, den es in 4K gibt.
    status_uhd: credit.status_uhd,
    watched: credit.watched ?? false,
    genres: [],
    runtime_minutes: null,
    certification: null,
    overview: "",
    character: credit.character,
  };
}

/**
 * Ein Eintrag aus dem Kalender.
 *
 * Die Folgennummer geht in `character` – das Feld ist der freie Untertitel der
 * Kachel und trägt in einer Filmografie die Rolle. Genau dafür ist es da, und
 * so braucht der Kalender keine eigene Kachel.
 *
 * `tmdb_id` darf im Kalender `null` sein; die Kachel zeigt einen solchen
 * Eintrag dann ohne Link. Die 0 hier ist bewusst kein gültiger Wert, sondern
 * das Signal dafür.
 */
export function fromCalendarEntry(eintrag: CalendarEntry): CardItem {
  return {
    media_type: eintrag.media_type,
    tmdb_id: eintrag.tmdb_id ?? 0,
    title: eintrag.title,
    poster_url: eintrag.poster_url,
    release_date: eintrag.date,
    vote_average: eintrag.vote_average,
    vote_count: eintrag.vote_count,
    status: eintrag.status,
    // Wie bei den Filmografie-Einträgen: Der Server liefert die zweite Achse
    // inzwischen auch für Kalender-Einträge. Sie hier nicht weiterzureichen
    // wäre genau die Lücke, die den Kalender vorher als einzige Liste ohne
    // 4K-Abzeichen dastehen ließ.
    status_uhd: eintrag.status_uhd,
    watched: eintrag.watched,
    genres: eintrag.genres,
    runtime_minutes: eintrag.runtime_minutes,
    certification: eintrag.certification,
    overview: eintrag.overview,
    character: eintrag.episode_label ?? undefined,
  };
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
    status: "not_requested",
    watched: false,
    genres: [],
    runtime_minutes: null,
    certification: null,
    overview: "",
  };
}
