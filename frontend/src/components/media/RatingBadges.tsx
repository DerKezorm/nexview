import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { MovieRatings } from '../../api/types'

/**
 * Bewertungen zu einer Liste von Filmen nachladen.
 *
 * Bewusst getrennt vom Laden der Titel selbst: die Werte kommen aus Radarr,
 * und zwanzig Abfragen dorthin würden den Seitenaufbau bremsen. So steht die
 * Liste sofort da und die Zahlen erscheinen kurz darauf.
 *
 * Serien bleiben außen vor - Sonarr liefert keine Aufschlüsselung nach
 * Portalen, sondern nur eine Sammelwertung.
 */
export function useMovieRatings(
  items: { media_type: string; tmdb_id: number }[],
): Record<number, MovieRatings> {
  const ids = items
    .filter((item) => item.media_type === 'movie')
    .map((item) => item.tmdb_id)
    .sort((a, b) => a - b)

  const query = useQuery({
    queryKey: ['movie-ratings', ids.join(',')],
    queryFn: () => api.get<Record<number, MovieRatings>>(`/api/ratings/movie?ids=${ids.join(',')}`),
    enabled: ids.length > 0,
    // Wertungen ändern sich langsam; der Server hält sie ohnehin einen Tag vor.
    staleTime: 60 * 60 * 1000,
    retry: false,
  })

  return query.data ?? {}
}

/** Tomate für frisch, grüner Klecks für verdorben - wie bei Rotten Tomatoes. */
function Tomate({ frisch }: { frisch: boolean }) {
  return <span aria-hidden="true">{frisch ? '🍅' : '🤢'}</span>
}

/**
 * Ein anklickbares Abzeichen.
 *
 * Führt zur jeweiligen Seite, damit man die Kritiken auch lesen kann. Der
 * Klick darf dabei nicht zusätzlich die Kachel öffnen, in der das Abzeichen
 * womöglich sitzt - deshalb wird er hier angehalten.
 */
function Abzeichen({
  href,
  title,
  className,
  children,
}: {
  href: string
  title: string
  className: string
  children: React.ReactNode
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      title={title}
      onClick={(event) => event.stopPropagation()}
      className={`transition-transform hover:scale-105 ${className}`}
    >
      {children}
    </a>
  )
}

/**
 * Die Wertungen als kleine, anklickbare Abzeichen.
 *
 * Nur was vorhanden ist: bei ganz frischen Filmen fehlt Rotten Tomatoes oft
 * ganz, und eine leere Zeile mit Platzhaltern sähe nach einem Fehler aus.
 *
 * Für IMDb kennt Radarr die Kennung, das führt direkt auf die Filmseite. Für
 * Rotten Tomatoes und Metacritic gibt es keine - dort landet man auf der Suche
 * nach dem Titel, was in der Praxis genauso ankommt.
 */
export function RatingBadges({
  ratings,
  title,
  className = '',
  gross = false,
  nurImdb = false,
}: {
  ratings: MovieRatings | undefined
  /** Für die Suche bei Rotten Tomatoes und Metacritic. */
  title: string
  className?: string
  gross?: boolean
  /**
   * Nur IMDb zeigen – für die Kacheln.
   *
   * Drei Wertungen nebeneinander sprengen die schmale Leiste unter dem Poster,
   * und IMDb ist die, die jeder kennt. Auf der Detailseite ist Platz; dort
   * bleiben Rotten Tomatoes und Metacritic erhalten.
   */
  nurImdb?: boolean
}) {
  if (!ratings) return null

  const { imdb, rotten_tomatoes: roheTomaten, metacritic: rohMetacritic } = ratings
  const tomaten = nurImdb ? null : roheTomaten
  const metacritic = nurImdb ? null : rohMetacritic
  if (imdb === null && tomaten === null && metacritic === null) return null

  const groesse = gross ? 'text-sm px-2.5 py-1' : 'text-[11px] px-1.5 py-0.5'
  const suche = encodeURIComponent(title)

  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className}`}>
      {imdb !== null && (
        <Abzeichen
          href={
            ratings.imdb_id
              ? `https://www.imdb.com/title/${ratings.imdb_id}/`
              : `https://www.imdb.com/find/?q=${suche}`
          }
          title={`IMDb ${imdb.toFixed(1)}`}
          className={`rounded-md bg-[#f5c518] font-bold text-black ${groesse}`}
        >
          IMDb {imdb.toFixed(1)}
        </Abzeichen>
      )}

      {tomaten !== null && (
        <Abzeichen
          href={`https://www.rottentomatoes.com/search?search=${suche}`}
          title={`Rotten Tomatoes ${tomaten}%`}
          className={`flex items-center gap-1 rounded-md bg-ink-900 font-semibold ring-1 ring-ink-700 ${groesse} ${
            tomaten >= 60 ? 'text-[#fa320a]' : 'text-[#0ac855]'
          }`}
        >
          <Tomate frisch={tomaten >= 60} />
          {tomaten}%
        </Abzeichen>
      )}

      {metacritic !== null && (
        <Abzeichen
          href={`https://www.metacritic.com/search/${suche}/`}
          title={`Metacritic ${metacritic}`}
          className={`rounded-md font-bold text-white ${groesse} ${
            metacritic >= 61
              ? 'bg-[#00ce7a]'
              : metacritic >= 40
                ? 'bg-[#ffbd3f] text-black'
                : 'bg-[#ff6874]'
          }`}
        >
          {metacritic}
        </Abzeichen>
      )}
    </div>
  )
}
