import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { MediaItem, PersonCredit, PersonDetail } from '../api/types'
import { DetailModal } from '../components/media/DetailModal'
import { CartIcon } from '../components/media/MediaCard'
import { Poster } from '../components/media/Poster'
import { StatusBadge } from '../components/media/StatusBadge'
import { Card, ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { formatDate, formatYear } from '../lib/format'
import { titlePath } from '../lib/routes'

/** Ein Titel aus der Filmografie - Poster, Rolle, Status, Schnell-Wagen. */
function CreditCard({
  credit,
  onQuickAdd,
}: {
  credit: PersonCredit
  onQuickAdd: (item: MediaItem) => void
}) {
  const { t } = useTranslation()
  const anfragbar = credit.status === 'not_requested'

  return (
    <div className="group relative flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-850 transition-all hover:-translate-y-1 hover:border-accent-600/60">
      <Link to={titlePath(credit.media_type, credit.tmdb_id)} className="flex flex-1 flex-col">
        <div className="relative aspect-2/3 overflow-hidden bg-ink-900">
          <Poster
            url={credit.poster_url}
            title={credit.title}
            className="h-full w-full transition-transform duration-300 group-hover:scale-105"
          />
          <div className="absolute inset-x-2 top-2">
            <StatusBadge status={credit.status} />
          </div>
        </div>
        <div className="flex flex-1 flex-col gap-0.5 p-3">
          <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{credit.title}</h3>
          {credit.character && (
            <p className="line-clamp-1 text-xs text-mist-600">{credit.character}</p>
          )}
          <p className="mt-auto text-xs text-mist-500">{formatYear(credit.release_date)}</p>
        </div>
      </Link>

      {anfragbar && (
        <button
          type="button"
          onClick={() =>
            onQuickAdd({
              media_type: credit.media_type,
              tmdb_id: credit.tmdb_id,
              tvdb_id: null,
              title: credit.title,
              original_title: null,
              overview: '',
              poster_url: credit.poster_url,
              backdrop_url: null,
              release_date: credit.release_date,
              vote_average: credit.vote_average,
              vote_count: 0,
              genres: [],
              runtime_minutes: null,
              certification: null,
              original_language: null,
              seasons: [],
              status: credit.status,
            })
          }
          title={t('media.quickAdd')}
          aria-label={`${credit.title} – ${t('media.quickAdd')}`}
          className="absolute top-2 right-2 z-10 rounded-full border border-ink-700 bg-ink-950/85 p-1.5 text-mist-300 backdrop-blur transition-colors hover:border-accent-500 hover:bg-accent-500 hover:text-white"
        >
          <CartIcon />
        </button>
      )}
    </div>
  )
}

/**
 * Seite zu einer Person: Foto, Biografie und die bekanntesten Titel.
 *
 * Sortiert nach Bekanntheit, nicht chronologisch - sonst stehen oben lauter
 * Kleinstauftritte, die niemand zuordnen kann.
 */
export function PersonPage() {
  const { t, i18n } = useTranslation()
  const { personId } = useParams<{ personId: string }>()
  const { data: config } = useConfig()

  const [schnellAnfrage, setSchnellAnfrage] = useState<MediaItem | null>(null)
  const [ganzeBio, setGanzeBio] = useState(false)

  const query = useQuery({
    queryKey: ['person', personId],
    queryFn: () => api.get<PersonDetail>(`/api/person/${personId}`),
    enabled: Boolean(personId),
    staleTime: 60 * 60 * 1000,
  })

  if (query.isPending) {
    return (
      <p className="flex items-center gap-2 py-16 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  if (query.error) {
    return (
      <ErrorBanner
        message={query.error instanceof ApiError ? query.error.message : t('errors.generic')}
      />
    )
  }

  const person = query.data
  const lang = person.biography.length > 600

  return (
    <div className="flex flex-col gap-8">
      <Card className="flex flex-col gap-6 sm:flex-row">
        <div className="aspect-2/3 w-36 shrink-0 self-start overflow-hidden rounded-xl border border-ink-700 bg-ink-900 sm:w-44">
          {person.photo_url ? (
            <img src={person.photo_url} alt="" className="h-full w-full object-cover" />
          ) : (
            <span className="flex h-full w-full items-center justify-center text-3xl font-semibold text-mist-600">
              {person.name.slice(0, 1)}
            </span>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <h1 className="text-3xl font-bold tracking-tight">{person.name}</h1>
          {person.known_for_department && (
            <p className="mt-1 text-sm text-mist-500">{person.known_for_department}</p>
          )}

          <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
            {person.birthday && (
              <div>
                <dt className="text-[11px] tracking-wide text-mist-600 uppercase">
                  {t('detail.born')}
                </dt>
                <dd className="text-mist-300">{formatDate(person.birthday, i18n.language)}</dd>
              </div>
            )}
            {person.deathday && (
              <div>
                <dt className="text-[11px] tracking-wide text-mist-600 uppercase">
                  {t('detail.died')}
                </dt>
                <dd className="text-mist-300">{formatDate(person.deathday, i18n.language)}</dd>
              </div>
            )}
            {person.place_of_birth && (
              <div className="col-span-2">
                <dt className="text-[11px] tracking-wide text-mist-600 uppercase">
                  {t('detail.birthplace')}
                </dt>
                <dd className="text-mist-300">{person.place_of_birth}</dd>
              </div>
            )}
          </dl>

          {person.biography && (
            <>
              <p
                className={
                  'mt-4 text-sm leading-relaxed whitespace-pre-line text-mist-300 ' +
                  (lang && !ganzeBio ? 'line-clamp-6' : '')
                }
              >
                {person.biography}
              </p>
              {lang && (
                <button
                  type="button"
                  onClick={() => setGanzeBio(!ganzeBio)}
                  className="mt-1 text-xs text-accent-400 hover:text-accent-300"
                >
                  {t(ganzeBio ? 'detail.showLess' : 'detail.showMore')}
                </button>
              )}
            </>
          )}
        </div>
      </Card>

      {person.credits.length > 0 && (
        <section>
          <h2 className="mb-3 text-lg font-semibold">{t('detail.knownFor')}</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {person.credits.map((credit) => (
              <CreditCard
                key={`${credit.media_type}-${credit.tmdb_id}`}
                credit={credit}
                onQuickAdd={setSchnellAnfrage}
              />
            ))}
          </div>
        </section>
      )}

      <DetailModal
        item={schnellAnfrage}
        onClose={() => setSchnellAnfrage(null)}
        arrConfigured={
          schnellAnfrage?.media_type === 'movie'
            ? (config?.radarr_configured ?? false)
            : (config?.sonarr_configured ?? false)
        }
      />
    </div>
  )
}
