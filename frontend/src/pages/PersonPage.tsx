import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { CreditKind, MediaItem, PersonCredit, PersonDetail } from '../api/types'
import { DetailModal } from '../components/media/DetailModal'
import { FavoritePersonButton } from '../components/media/FavoriteButton'
import { usePersonFavorites } from '../components/media/useFavorites'
import { fromPersonCredit } from '../components/media/cardItem'
import { MediaCard } from '../components/media/MediaCard'
import { PersonPhoto } from '../components/media/PersonPhoto'
import { useCardData } from '../components/media/useCardData'
import { Card, ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { formatDate } from '../lib/format'

const CREDIT_KINDS = ['movie', 'series', 'appearance'] as const

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
  const [werk, setWerk] = useState<CreditKind>('movie')
  const { markiert: personMarkiert } = usePersonFavorites()

  const query = useQuery({
    queryKey: ['person', personId],
    queryFn: () => api.get<PersonDetail>(`/api/person/${personId}`),
    enabled: Boolean(personId),
    staleTime: 60 * 60 * 1000,
  })

  // Ohne das blieben in der Filmografie die IMDb-Abzeichen leer und jedes Herz
  // ungefuellt - genau der Unterschied, der wie Absicht aussah statt wie Fehler.
  const kachelDaten = useCardData(query.data?.credits ?? [])

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

  // Filmografie nach Art aufteilen: Filme, Serien, TV-Auftritte ("Self").
  const credits: Record<CreditKind, PersonCredit[]> = {
    movie: person.credits.filter((c) => c.kind === 'movie'),
    series: person.credits.filter((c) => c.kind === 'series'),
    appearance: person.credits.filter((c) => c.kind === 'appearance'),
  }
  // Hat die Person keine Filme (reine TV-Person), aufs erste gefüllte Fach.
  const werkEffektiv: CreditKind =
    credits[werk].length > 0
      ? werk
      : (CREDIT_KINDS.find((art) => credits[art].length > 0) ?? 'movie')

  return (
    <div className="flex flex-col gap-8">
      <Card className="flex flex-col gap-6 sm:flex-row">
        <div className="aspect-2/3 w-36 shrink-0 self-start overflow-hidden rounded-xl border border-ink-700 bg-ink-900 sm:w-44">
          <PersonPhoto url={person.photo_url} name={person.name} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{person.name}</h1>
            <FavoritePersonButton
              person={{
                person_id: person.person_id,
                name: person.name,
                photo_url: person.photo_url,
                department: person.known_for_department,
              }}
              markiert={personMarkiert.has(person.person_id)}
              gross
            />
          </div>
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
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold">{t('detail.knownFor')}</h2>
            {/* Nur die Fächer zeigen, zu denen es auch etwas gibt - ein leerer
                Knopf verspricht sonst mehr, als da ist. */}
            <div className="flex flex-wrap gap-2" role="group" aria-label={t('people.department')}>
              {CREDIT_KINDS.filter((art) => credits[art].length > 0).map((art) => {
                const aktiv = werkEffektiv === art
                return (
                  <button
                    key={art}
                    type="button"
                    onClick={() => setWerk(art)}
                    aria-pressed={aktiv}
                    className={
                      'rounded-full border px-3 py-1 text-xs font-semibold transition-colors ' +
                      (aktiv
                        ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                        : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
                    }
                  >
                    {t(`person.works.${art}`)}
                    <span className="ml-1.5 tabular-nums opacity-70">{credits[art].length}</span>
                  </button>
                )
              })}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {credits[werkEffektiv].map((credit) => (
              <MediaCard
                key={`${credit.media_type}-${credit.tmdb_id}`}
                item={fromPersonCredit(credit)}
                onQuickAdd={setSchnellAnfrage}
                ratings={kachelDaten.ratingsFor(credit)}
                favorit={kachelDaten.istFavorit(credit)}
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
