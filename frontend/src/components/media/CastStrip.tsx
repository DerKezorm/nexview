import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { CastMember } from '../../api/types'
import { personPath } from '../../lib/routes'

/** Anfangsbuchstaben als Ersatz, solange TMDB kein Foto hat. */
function Initialen({ name }: { name: string }) {
  const kuerzel = name
    .split(/\s+/)
    .slice(0, 2)
    .map((teil) => teil[0] ?? '')
    .join('')
    .toUpperCase()

  return (
    <span className="flex h-full w-full items-center justify-center bg-ink-800 text-lg font-semibold text-mist-600">
      {kuerzel || '?'}
    </span>
  )
}

/**
 * Besetzung als waagerechter Streifen.
 *
 * Bewusst scrollbar statt umbrechend: 24 Gesichter untereinander würden die
 * Seite dominieren, obwohl meist nur die ersten paar interessieren.
 */
export function CastStrip({ cast }: { cast: CastMember[] }) {
  const { t } = useTranslation()
  if (cast.length === 0) return null

  return (
    <section>
      <h2 className="mb-3 text-lg font-semibold">{t('detail.cast')}</h2>
      <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-2">
        {cast.map((person) => (
          <Link
            key={`${person.person_id}-${person.character}`}
            to={personPath(person.person_id)}
            className="group w-28 shrink-0"
          >
            <div className="aspect-2/3 overflow-hidden rounded-xl border border-ink-700 bg-ink-900 transition-colors group-hover:border-accent-600/60">
              {person.photo_url ? (
                <img
                  src={person.photo_url}
                  alt=""
                  loading="lazy"
                  className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                />
              ) : (
                <Initialen name={person.name} />
              )}
            </div>
            <p className="mt-1.5 line-clamp-2 text-xs font-medium text-mist-200">{person.name}</p>
            {person.character && (
              <p className="line-clamp-2 text-[11px] text-mist-600">{person.character}</p>
            )}
          </Link>
        ))}
      </div>
    </section>
  )
}
