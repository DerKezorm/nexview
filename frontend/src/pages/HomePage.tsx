import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { MediaItem, RecentItem } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { DetailModal } from '../components/media/DetailModal'
import { Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { formatDate, formatRuntime } from '../lib/format'

/** Abstand zwischen zwei Kacheln beim Aufblenden. */
const STAGGER_MS = 90

function Meta({ item }: { item: RecentItem }) {
  const { i18n } = useTranslation()
  const teile = [
    item.release_date?.slice(0, 4),
    formatRuntime(item.runtime_minutes, i18n.language),
    item.vote_average > 0 ? `★ ${item.vote_average.toFixed(1)}` : null,
    item.genres.slice(0, 2).join(', ') || null,
  ].filter(Boolean)

  return <>{teile.join(' · ')}</>
}

/** Der große Titel ganz oben - Hintergrundbild über die volle Breite. */
function Hero({ item, onOpen }: { item: RecentItem; onOpen: () => void }) {
  const { t, i18n } = useTranslation()

  return (
    <button
      type="button"
      onClick={onOpen}
      className="animate-nv-fade group relative block w-full overflow-hidden rounded-3xl border border-ink-700 text-left"
    >
      {/* Mindesthöhe: auf schmalen Bildschirmen wäre 16:9 sonst niedriger als
          der Text, der darauf liegt - der obere Rand würde abgeschnitten. */}
      <div className="aspect-[16/9] min-h-80 w-full sm:aspect-[21/9] sm:min-h-96">
        {item.backdrop_url ? (
          <img
            src={item.backdrop_url}
            alt=""
            className="h-full w-full object-cover transition-transform duration-700 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="h-full w-full bg-ink-850" />
        )}
      </div>

      {/* Dunkler Verlauf, damit die Schrift auf jedem Bild lesbar bleibt. */}
      <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/70 to-transparent" />
      <div className="absolute inset-0 bg-gradient-to-r from-ink-950/80 to-transparent" />

      <div className="absolute inset-x-0 bottom-0 flex flex-col gap-2 p-5 sm:p-8">
        <span className="w-fit rounded-full bg-accent-500 px-3 py-1 text-xs font-semibold tracking-wide uppercase">
          {t('home.justArrived')}
        </span>
        <h2 className="max-w-2xl text-2xl font-bold tracking-tight sm:text-4xl">{item.title}</h2>
        <p className="text-xs text-mist-400 sm:text-sm">
          <Meta item={item} />
        </p>
        {item.overview && (
          <p className="line-clamp-2 max-w-2xl text-sm text-mist-300 sm:line-clamp-3">
            {item.overview}
          </p>
        )}
        <p className="mt-1 flex items-center gap-2 text-xs text-mist-500">
          <Avatar url={item.requester_avatar} name={item.requested_by} className="h-6 w-6" />
          {t('home.requestedBy', { name: item.requested_by })}
          {item.completed_at &&
            ` · ${formatDate(item.completed_at.slice(0, 10), i18n.language)}`}
        </p>
      </div>
    </button>
  )
}

/** Eine Poster-Kachel im versetzten Raster. */
function Tile({
  item,
  index,
  onOpen,
}: {
  item: RecentItem
  index: number
  onOpen: () => void
}) {
  const { t } = useTranslation()
  /** Jede zweite Kachel sitzt etwas tiefer - das nimmt dem Raster die Strenge. */
  const versetzt = index % 2 === 1

  return (
    <button
      type="button"
      onClick={onOpen}
      style={{ animationDelay: `${index * STAGGER_MS}ms` }}
      className={
        'animate-nv-rise group relative block overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 text-left transition-transform duration-300 hover:-translate-y-1.5 hover:border-accent-600 ' +
        (versetzt ? 'sm:mt-8' : '')
      }
    >
      <div className="aspect-[2/3] w-full">
        {item.poster_url ? (
          <img
            src={item.poster_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center px-3 text-center text-sm text-mist-600">
            {item.title}
          </div>
        )}
      </div>

      <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/20 to-transparent opacity-90" />

      <div className="absolute inset-x-0 bottom-0 p-3">
        <p className="truncate text-sm font-semibold">{item.title}</p>
        <p className="truncate text-xs text-mist-500">
          {t(item.media_type === 'movie' ? 'common.movies' : 'common.series')}
          {item.release_date && ` · ${item.release_date.slice(0, 4)}`}
        </p>
        {/* Wer den Titel geholt hat, erscheint erst beim Überfahren. */}
        <p className="mt-1 flex items-center gap-1.5 text-xs text-mist-500 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
          <Avatar url={item.requester_avatar} name={item.requested_by} className="h-4 w-4" />
          <span className="truncate">{item.requested_by}</span>
        </p>
      </div>
    </button>
  )
}

export function HomePage() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: config } = useConfig()
  const [offen, setOffen] = useState<RecentItem | null>(null)

  const recentQuery = useQuery({
    queryKey: ['home-recent'],
    queryFn: () => api.get<RecentItem[]>('/api/home/recent'),
  })

  // Erst beim Anklicken werden die vollen Angaben geholt - inklusive Status.
  const detailQuery = useQuery({
    queryKey: ['media-detail', offen?.media_type, offen?.tmdb_id],
    queryFn: () => api.get<MediaItem>(`/api/media/${offen!.media_type}/${offen!.tmdb_id}`),
    enabled: offen !== null,
  })

  const titel = recentQuery.data ?? []
  const [neuester, ...weitere] = titel

  return (
    <div className="flex flex-col gap-8">
      <header className="animate-nv-fade">
        <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">
          {t('home.greeting', { name: user?.display_name ?? user?.username ?? '' })}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-2 text-mist-500">{t('home.intro')}</p>
      </header>

      {recentQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}

      {!recentQuery.isPending && titel.length === 0 && (
        <div className="animate-nv-fade rounded-3xl border border-dashed border-ink-700 px-6 py-20 text-center">
          <p className="text-lg font-semibold">{t('home.emptyTitle')}</p>
          <p className="mt-1 text-sm text-mist-500">{t('home.emptyText')}</p>
          <div className="mt-6 flex flex-wrap justify-center gap-2">
            <Link
              to="/filme"
              className="rounded-full bg-accent-500 px-5 py-2.5 text-sm font-semibold transition-colors hover:bg-accent-400"
            >
              {t('nav.discoverMovies')}
            </Link>
            <Link
              to="/serien"
              className="rounded-full border border-ink-700 px-5 py-2.5 text-sm transition-colors hover:border-accent-600"
            >
              {t('nav.discoverSeries')}
            </Link>
          </div>
        </div>
      )}

      {neuester && <Hero item={neuester} onOpen={() => setOffen(neuester)} />}

      {weitere.length > 0 && (
        <section>
          <h2 className="mb-4 text-sm font-semibold tracking-wide text-mist-500 uppercase">
            {t('home.alsoNew')}
          </h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
            {weitere.map((item, index) => (
              <Tile
                key={`${item.media_type}-${item.tmdb_id}`}
                item={item}
                index={index}
                onOpen={() => setOffen(item)}
              />
            ))}
          </div>
        </section>
      )}

      {titel.length > 0 && (
        <p className="text-center">
          <Link
            to="/filme"
            className="text-sm text-mist-500 underline-offset-4 transition-colors hover:text-mist-100 hover:underline"
          >
            {t('home.discoverMore')}
          </Link>
        </p>
      )}

      <DetailModal
        item={offen ? (detailQuery.data ?? null) : null}
        onClose={() => setOffen(null)}
        arrConfigured={
          (offen?.media_type === 'movie'
            ? config?.radarr_configured
            : config?.sonarr_configured) ?? false
        }
      />
    </div>
  )
}
