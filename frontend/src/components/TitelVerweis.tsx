import { Link } from 'react-router-dom'

import type { MediaType } from '../api/types'
import { titlePath } from '../lib/routes'

type Props = {
  mediaType: MediaType
  tmdbId: number | null | undefined
  titel: string
  /** ISO-Datum, aus dem das Jahr für den Tooltip kommt. */
  erschienen?: string | null
  className?: string
}

/**
 * Ein Titel in einer Anfragen-Liste — anklickbar und mit Jahr im Tooltip.
 *
 * In den Listen steht nur der Titel, und der ist oft abgeschnitten oder für
 * sich nicht eindeutig: „Dune" gibt es 1984 und 2021, „Der Gesang der
 * Flusskrebse" passt in keine Zeile. Der Tooltip zeigt beides vollständig,
 * und ein Klick führt dorthin, wo alles steht.
 *
 * ⚠️ Ohne `tmdbId` bleibt es gewöhnlicher Text. Bei alten Anfragen kann die
 * Kennung fehlen; ein Verweis ins Leere wäre schlimmer als keiner — man
 * klickte und landete auf einer Fehlerseite.
 */
export function TitelVerweis({ mediaType, tmdbId, titel, erschienen, className = '' }: Props) {
  const jahr = erschienen?.slice(0, 4)
  const beschriftung = jahr ? `${titel} (${jahr})` : titel

  if (!tmdbId) return <span className={className}>{titel}</span>

  return (
    <Link
      to={titlePath(mediaType, tmdbId)}
      title={beschriftung}
      className={`transition-colors hover:text-accent-400 hover:underline ${className}`}
    >
      {titel}
    </Link>
  )
}
