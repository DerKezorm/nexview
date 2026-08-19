import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { MediaItem, MovieRatings } from '../../api/types'
import { formatRuntime, formatYear } from '../../lib/format'
import { titlePath } from '../../lib/routes'
import type { CardItem } from './cardItem'
import { fromMediaItem } from './cardItem'
import { FavoriteButton } from './FavoriteButton'
import { RatingBadges } from './RatingBadges'
import { Poster, RatingBadge } from './Poster'
import { StatusBadge } from './StatusBadge'
import { WatchedBadge } from './WatchedBadge'

/**
 * Wie gedrängt die Kachel sein darf.
 *
 * `voll`    – Raster auf Entdecken, Suche, Stöbern, Filmografie.
 * `kompakt` – enge Reihen auf der Startseite: kleinere Schrift, keine Genres.
 */
export type CardVariant = 'voll' | 'kompakt'

/**
 * Der klickbare Teil der Kachel – oder nur die Hülle, wenn es nichts zu öffnen
 * gibt.
 *
 * Ohne TMDB-Kennung existiert keine Detailseite. Das kommt bei Sonarr-Folgen
 * vor, deren Serie sich keiner Kennung zuordnen ließ; ein Link auf
 * `/titel/tv/0` wäre eine Sackgasse.
 *
 * Bewusst hier außen und nicht innerhalb von `MediaCard`: Eine dort definierte
 * Komponente wäre bei jedem Rendern eine neue, React würde den ganzen Inhalt
 * verwerfen und neu aufbauen – das Poster lüde sichtbar noch einmal.
 */
function Rahmen({
  verlinkbar,
  to,
  label,
  children,
}: {
  verlinkbar: boolean
  to: string
  label: string
  children: React.ReactNode
}) {
  const klassen = 'flex flex-1 flex-col text-left'
  if (!verlinkbar) return <div className={klassen}>{children}</div>
  return (
    <Link to={to} aria-label={label} className={klassen}>
      {children}
    </Link>
  )
}

type MediaCardProps = {
  item: CardItem
  /** Öffnet das Schnell-Popup zum Anfragen. Fehlt er, gibt es keinen Wagen. */
  onQuickAdd?: (item: MediaItem) => void
  /** Wertungen von IMDb, sobald sie da sind. */
  ratings?: MovieRatings
  /** Ist dieser Titel als Favorit markiert? */
  favorit?: boolean
  variant?: CardVariant
  /**
   * Ersetzt Herz und Wagen – für Listen, in denen etwas anderes zu tun ist
   * (auf der Favoriten-Seite etwa das Entfernen).
   */
  actions?: React.ReactNode
  /**
   * Zusätzliches Schild neben dem Zustand – im Kalender das „fehlt noch“.
   *
   * Bewusst kein weiterer Wert für `status`: Der ist eine geschlossene Liste
   * mit fester Farbtabelle, und „fehlt noch“ ist eine andere Achse als
   * „vorhanden“ oder „angefragt“ – es würde sie verdecken statt ergänzen.
   */
  badge?: React.ReactNode
}

/**
 * Die eine Kachel für alles.
 *
 * Vorher rendered jede Seite ihre eigene: Entdecken mit Leiste unten, die
 * Filmografie mit Warenkorb oben und ohne Wertungen, die Startseite gleich in
 * drei weiteren Varianten – teils ganz ohne Zustand, sodass ein längst
 * vorhandener Titel aussah wie einer, den man noch anfragen kann.
 *
 * Der Aufbau ist überall gleich und deshalb überall wiederzuerkennen:
 *
 *   Poster · Zustand oben links · TMDB-Stern oben rechts
 *   Titel, Jahr, Laufzeit
 *   Leiste: IMDb · Auge (gesehen) · Herz · Wagen
 *
 * Die Leiste ist immer da, auch wenn nichts anzufragen ist – mögen kann man
 * einen Titel schließlich auch dann, und eine Kachel, der die Leiste fehlt,
 * wäre um genau diese Höhe kürzer als ihre Nachbarn.
 */
export function MediaCard({
  item,
  onQuickAdd,
  ratings,
  favorit = false,
  variant = 'voll',
  actions,
  badge,
}: MediaCardProps) {
  const { t, i18n } = useTranslation()
  const runtime = formatRuntime(item.runtime_minutes, i18n.language)
  const anfragbar = item.status === 'not_requested'
  const kompakt = variant === 'kompakt'

  /* Ohne TMDB-Kennung gibt es keine Detailseite. Das kommt bei Sonarr-Folgen
     vor, deren Serie sich keiner Kennung zuordnen ließ. Dann bleibt die Kachel
     stehen, aber ohne Link – ein Link auf /titel/tv/0 wäre eine Sackgasse. */
  const verlinkbar = item.tmdb_id > 0

  return (
    /* h-full: Im Raster wird das Feld auf die Zeilenhoehe gezogen, die Kachel
       darin aber nicht - dann sitzen die Leisten benachbarter Kacheln auf
       verschiedenen Hoehen, sobald ein Titel zweizeilig umbricht. */
    <div className="group relative flex h-full flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850 transition-all hover:-translate-y-1 hover:border-accent-600/60 hover:shadow-2xl hover:shadow-accent-700/20">
      <Rahmen
        verlinkbar={verlinkbar}
        to={titlePath(item.media_type, item.tmdb_id)}
        label={`${item.title} – ${t('media.openDetails')}`}
      >
        <div className="relative aspect-2/3 overflow-hidden bg-ink-900">
          <Poster
            url={item.poster_url}
            title={item.title}
            className="h-full w-full transition-transform duration-300 group-hover:scale-105"
          />

          {/* Oben teilen sich nur zwei Dinge die Zeile. Vorher drängte sich
              der Wagen dazwischen, und "Nicht angefragt" brach um. */}
          <div className="absolute inset-x-2 top-2 flex flex-wrap items-start justify-between gap-1.5">
            <span className="flex flex-wrap items-start gap-1.5">
              <StatusBadge status={item.status} />
              {badge}
            </span>
            <RatingBadge vote={item.vote_average} count={item.vote_count} />
          </div>

          {item.certification && (
            <span className="absolute bottom-2 left-2 rounded-md bg-ink-950/85 px-1.5 py-0.5 text-[11px] font-bold text-mist-300 ring-1 ring-ink-700">
              {item.certification}
            </span>
          )}

          {/* Beschreibung erscheint erst beim Überfahren - hält das Raster ruhig. */}
          {item.overview && (
            <div className="pointer-events-none absolute inset-0 flex items-end bg-linear-to-t from-ink-950 via-ink-950/60 to-transparent p-3 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
              <p className="line-clamp-5 text-xs leading-relaxed text-mist-300">
                {item.overview}
              </p>
            </div>
          )}
        </div>

        <div className="flex flex-1 flex-col gap-1 p-3">
          <h3
            className={
              'line-clamp-2 leading-snug font-semibold ' + (kompakt ? 'text-xs' : 'text-sm')
            }
          >
            {item.title}
          </h3>

          {/* In einer Filmografie ist die Rolle die eigentliche Auskunft. */}
          {item.character && (
            <p className="line-clamp-1 text-xs text-mist-500">{item.character}</p>
          )}

          <p className="mt-auto text-xs text-mist-500">
            {formatYear(item.release_date)}
            {runtime && !kompakt && <span> · {runtime}</span>}
          </p>

          {!kompakt && item.genres.length > 0 && (
            <p className="line-clamp-1 text-xs text-mist-600">{item.genres.join(', ')}</p>
          )}
        </div>
      </Rahmen>

      {/* Ein Balken unter der Kachel: links die Wertung, rechts die Knöpfe.
          Auf dem Poster wurde es zu eng - "Nicht angefragt" brach um, und die
          Abzeichen standen gequetscht in der Ecke. Hier ist Platz, und die
          verlinkten Abzeichen liegen außerhalb des Kachel-Links, wo sie
          hingehören: ein Link im Link ist nicht erlaubt. */}
      <div className="flex items-center justify-between gap-2 border-t border-ink-700/60 px-3 py-2">
        {/* Auf der Kachel nur IMDb - drei Wertungen sprengen die Leiste. */}
        <RatingBadges ratings={ratings} title={item.title} nurImdb />

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {/* Das Auge sitzt hier und nicht auf dem Poster: als zweites Schild
              neben "Bereits geladen" verdeckte es das halbe Bild. */}
          {item.watched && <WatchedBadge />}

          {actions ?? (
            <>
              <FavoriteButton item={item} markiert={favorit} />

              {anfragbar && onQuickAdd && (
                <button
                  type="button"
                  onClick={() => onQuickAdd(toMediaItem(item))}
                  title={t('media.quickAdd')}
                  aria-label={`${item.title} – ${t('media.quickAdd')}`}
                  className="rounded-full border border-ink-700 bg-ink-900 p-1.5 text-mist-300 transition-colors hover:border-accent-500 hover:bg-accent-500 hover:text-white"
                >
                  <CartIcon />
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * Zurück in die Form, die das Anfrage-Popup erwartet.
 *
 * Die Kachel arbeitet mit dem gemeinsamen Nenner; das Popup braucht einen
 * vollständigen `MediaItem`. Die fehlenden Felder sind für das Anfragen
 * bedeutungslos - es zählen Art, Kennung und Titel.
 */
function toMediaItem(item: CardItem): MediaItem {
  return {
    media_type: item.media_type,
    tmdb_id: item.tmdb_id,
    tvdb_id: null,
    title: item.title,
    original_title: null,
    overview: item.overview,
    poster_url: item.poster_url,
    backdrop_url: null,
    release_date: item.release_date,
    vote_average: item.vote_average,
    // Das Popup braucht eine Zahl; "unbekannt" ist dort bedeutungslos.
    vote_count: item.vote_count ?? 0,
    genres: item.genres,
    runtime_minutes: item.runtime_minutes,
    certification: item.certification,
    original_language: null,
    seasons: [],
    status: item.status,
    watched: item.watched,
  }
}

/** Bequemlichkeit für die vielen Stellen, die einen echten `MediaItem` haben. */
export function MediaItemCard({
  item,
  ...rest
}: Omit<MediaCardProps, 'item'> & { item: MediaItem }) {
  return <MediaCard item={fromMediaItem(item)} {...rest} />
}

/** Einkaufswagen - steht für "schnell anfragen". */
export function CartIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="9" cy="20" r="1.5" />
      <circle cx="18" cy="20" r="1.5" />
      <path d="M2 3h2.5l2.4 12.2a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.6L21 7H6" />
    </svg>
  )
}
