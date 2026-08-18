import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { MediaItem, RecentItem } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { DetailModal } from '../components/media/DetailModal'
import { FavoriteButton, useFavorites } from '../components/media/FavoriteButton'
import { CartIcon } from '../components/media/MediaCard'
import { Slider } from '../components/Slider'
import { Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { formatDate, formatRuntime } from '../lib/format'
import { titlePath } from '../lib/routes'

/** Abstand zwischen zwei Kacheln beim Aufblenden. */
const STAGGER_MS = 90

/** Eine Karte im Slider der zuletzt geladenen Titel. */
function RecentCard({ item, index }: { item: RecentItem; index: number }) {
  const { t, i18n } = useTranslation()

  return (
    <Link
      to={titlePath(item.media_type, item.tmdb_id)}
      style={{ animationDelay: `${index * STAGGER_MS}ms` }}
      className="animate-nv-rise group w-40 shrink-0 snap-start sm:w-48"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-2xl border border-ink-700 bg-ink-850 transition-colors group-hover:border-accent-600">
        {item.poster_url ? (
          <img
            src={item.poster_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center px-3 text-center text-sm text-mist-600">
            <span className="w-full break-words">{item.title}</span>
          </div>
        )}

        <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-transparent to-transparent" />

        {/* Wer den Titel geholt hat, erscheint erst beim Überfahren. */}
        <div className="absolute inset-x-0 bottom-0 flex items-center gap-1.5 p-2.5 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
          <Avatar url={item.requester_avatar} name={item.requested_by} className="h-5 w-5" />
          <span className="truncate text-xs text-mist-300">{item.requested_by}</span>
        </div>
      </div>

      <p className="mt-2 line-clamp-2 text-sm leading-snug font-semibold">{item.title}</p>
      <p className="truncate text-xs text-mist-600">
        {t(item.media_type === 'movie' ? 'common.movies' : 'common.series')}
        {item.completed_at && ` · ${formatDate(item.completed_at.slice(0, 10), i18n.language)}`}
      </p>
    </Link>
  )
}

/**
 * Eine große Karte im Aufmacher-Slider.
 *
 * Hintergrundbild für die Stimmung, Cover daneben für den Wiedererkennungswert
 * - im Regal sucht man das Poster, nicht das Standbild. Die Karte ist etwas
 * schmaler als die Seite, damit die nächste hervorlugt: sonst sähe der Slider
 * aus wie ein einzelnes Bild.
 */
function Spotlight({
  item,
  index,
  onQuickAdd,
}: {
  item: MediaItem
  index: number
  onQuickAdd: () => void
}) {
  const { t, i18n } = useTranslation()
  const laufzeit = formatRuntime(item.runtime_minutes, i18n.language)

  /**
   * Läuft der Film erst noch an?
   *
   * Dann ersetzt das Startdatum den Beliebtheits-Hinweis: dass man ihn noch
   * nicht sehen kann, ist die wichtigste Information über diesen Titel - sie
   * gehört nach vorn und nicht in die Zeile mit den Eckdaten.
   */
  const kommtNoch = Boolean(item.release_date) && item.release_date! > new Date().toISOString().slice(0, 10)

  const eckdaten = [
    item.release_date?.slice(0, 4),
    laufzeit,
    item.vote_average > 0 ? `★ ${item.vote_average.toFixed(1)}` : null,
    item.genres.slice(0, 3).join(', ') || null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div
      style={{ animationDelay: `${index * STAGGER_MS}ms` }}
      className="animate-nv-fade group relative w-[88%] shrink-0 snap-center overflow-hidden rounded-3xl border border-ink-700 sm:w-[92%]"
    >
      <Link to={titlePath(item.media_type, item.tmdb_id)} className="block">
        <div className="aspect-[16/10] min-h-80 w-full sm:aspect-[21/9] sm:min-h-[24rem]">
          {item.backdrop_url ? (
            <img
              src={item.backdrop_url}
              alt=""
              className="nv-backdrop h-full w-full object-cover transition-transform duration-[1.2s] group-hover:scale-[1.04]"
            />
          ) : (
            <div className="h-full w-full bg-ink-850" />
          )}
        </div>

        <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/70 to-transparent" />
        <div className="absolute inset-0 bg-gradient-to-r from-ink-950/90 via-ink-950/30 to-transparent" />

        <div className="absolute inset-x-0 bottom-0 flex items-end gap-4 p-5 sm:gap-6 sm:p-8">
          {/* Das Cover erst ab Tablet-Breite - auf dem Handy bliebe für den
              Text sonst nichts übrig. */}
          <div className="hidden aspect-[2/3] w-24 shrink-0 overflow-hidden rounded-xl border border-ink-700 shadow-2xl shadow-black/60 sm:block sm:w-32">
            {item.poster_url ? (
              <img src={item.poster_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="h-full w-full bg-ink-850" />
            )}
          </div>

          <div className="flex min-w-0 flex-col items-start gap-2">
            {kommtNoch ? (
              <span className="rounded-full bg-warn-500 px-3 py-1 text-xs font-semibold tracking-wide text-ink-950 uppercase">
                {t('home.comingOn', {
                  date: formatDate(item.release_date, i18n.language),
                })}
              </span>
            ) : (
              <span className="rounded-full bg-accent-500 px-3 py-1 text-xs font-semibold tracking-wide uppercase">
                {t('home.trendingBadge')}
              </span>
            )}

            <h3 className="text-2xl font-bold tracking-tight sm:text-4xl">{item.title}</h3>
            <p className="text-xs text-mist-400 sm:text-sm">{eckdaten}</p>

            {item.overview && (
              <p className="line-clamp-2 max-w-2xl text-sm leading-relaxed text-mist-300 sm:line-clamp-3">
                {item.overview}
              </p>
            )}
          </div>
        </div>
      </Link>

      {/* Außerhalb des Links: sonst führte der Wagen auf die Detailseite. */}
      <button
        type="button"
        onClick={onQuickAdd}
        className="absolute top-4 right-4 flex items-center gap-2 rounded-full bg-accent-500 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-accent-700/30 transition-colors hover:bg-accent-400"
      >
        <CartIcon className="h-5 w-5" />
        <span className="hidden sm:inline">{t('media.quickAdd')}</span>
      </button>
    </div>
  )
}

/** Ein weiterer Vorschlag als kleine Kachel unter dem Slider. */
function TrendingTile({
  item,
  index,
  onQuickAdd,
}: {
  item: MediaItem
  index: number
  onQuickAdd: () => void
}) {
  const { t, i18n } = useTranslation()
  const kommtNoch =
    Boolean(item.release_date) && item.release_date! > new Date().toISOString().slice(0, 10)

  return (
    <div
      style={{ animationDelay: `${index * STAGGER_MS}ms` }}
      className="animate-nv-rise group relative"
    >
      <Link to={titlePath(item.media_type, item.tmdb_id)} className="block">
        <div className="relative aspect-[2/3] overflow-hidden rounded-xl border border-ink-700 bg-ink-850 transition-colors group-hover:border-accent-600">
          {item.poster_url ? (
            <img
              src={item.poster_url}
              alt=""
              loading="lazy"
              className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center px-3 text-center text-sm text-mist-600">
              <span className="w-full break-words">{item.title}</span>
            </div>
          )}

          {kommtNoch ? (
            <span className="absolute top-2 right-2 rounded-full bg-warn-500 px-2 py-0.5 text-xs font-semibold text-ink-950">
              {formatDate(item.release_date, i18n.language)}
            </span>
          ) : (
            item.vote_average > 0 && (
              <span className="absolute top-2 right-2 rounded-full bg-ink-950/85 px-2 py-0.5 text-xs font-semibold text-mist-200 ring-1 ring-ink-700">
                ★ {item.vote_average.toFixed(1)}
              </span>
            )
          )}

          {/* Der Anriss liegt auf dem Poster und erscheint beim Überfahren -
              so bleibt das Raster ruhig und die Handlung trotzdem erreichbar. */}
          <div className="pointer-events-none absolute inset-0 flex items-end bg-gradient-to-t from-ink-950 via-ink-950/70 to-transparent p-3 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
            <p className="line-clamp-5 text-xs leading-relaxed text-mist-300">
              {item.overview || t('media.noOverview')}
            </p>
          </div>
        </div>

        <p className="mt-2 line-clamp-2 text-sm leading-snug font-semibold">{item.title}</p>
        <p className="truncate text-xs text-mist-600">
          {item.release_date?.slice(0, 4)}
          {item.genres.length > 0 && ` · ${item.genres.slice(0, 2).join(', ')}`}
        </p>
      </Link>

      <button
        type="button"
        onClick={onQuickAdd}
        title={t('media.quickAdd')}
        aria-label={`${item.title} – ${t('media.quickAdd')}`}
        className="absolute top-2 left-2 rounded-full border border-ink-700 bg-ink-950/85 p-1.5 text-mist-200 backdrop-blur transition-colors hover:border-accent-500 hover:bg-accent-500 hover:text-white"
      >
        <CartIcon />
      </button>
    </div>
  )
}

/**
 * Ein kuratierter Vorschlag: Cover links, Handlung rechts.
 *
 * Bewusst mit Cover statt Hintergrundbild - hier stehen viele Titel
 * nebeneinander, und Poster lassen sich viel schneller erfassen.
 */
function CuratedCard({
  item,
  index,
  favorit,
  onQuickAdd,
}: {
  item: MediaItem
  index: number
  favorit: boolean
  onQuickAdd: () => void
}) {
  const { t, i18n } = useTranslation()

  return (
    <div
      style={{ animationDelay: `${index * STAGGER_MS}ms` }}
      className="animate-nv-rise group flex gap-4 rounded-2xl border border-ink-700 bg-ink-850/60 p-3 transition-colors hover:border-accent-600/60"
    >
      <Link
        to={titlePath(item.media_type, item.tmdb_id)}
        className="aspect-[2/3] w-20 shrink-0 self-start overflow-hidden rounded-xl bg-ink-900 sm:w-24"
      >
        {item.poster_url ? (
          <img
            src={item.poster_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center px-2 text-center text-xs text-mist-600">
            <span className="w-full break-words">{item.title}</span>
          </span>
        )}
      </Link>

      <div className="flex min-w-0 flex-1 flex-col">
        <Link to={titlePath(item.media_type, item.tmdb_id)} className="min-w-0">
          <p className="line-clamp-2 font-semibold">{item.title}</p>
          <p className="mt-0.5 text-xs text-mist-600">
            {[
              item.release_date?.slice(0, 4),
              formatRuntime(item.runtime_minutes, i18n.language),
              item.vote_average > 0 ? `★ ${item.vote_average.toFixed(1)}` : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
          <p className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-mist-500">
            {item.overview || t('media.noOverview')}
          </p>
        </Link>

        <div className="mt-2 flex items-center gap-2">
          <FavoriteButton item={item} markiert={favorit} />
          <button
            type="button"
            onClick={onQuickAdd}
            title={t('media.quickAdd')}
            aria-label={`${item.title} – ${t('media.quickAdd')}`}
            className="rounded-full border border-ink-700 bg-ink-900 p-1.5 text-mist-300 transition-colors hover:border-accent-500 hover:bg-accent-500 hover:text-white"
          >
            <CartIcon />
          </button>
        </div>
      </div>
    </div>
  )
}

export function HomePage() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: config } = useConfig()
  const [offen, setOffen] = useState<RecentItem | null>(null)
  const [schnellAnfrage, setSchnellAnfrage] = useState<MediaItem | null>(null)

  const recentQuery = useQuery({
    queryKey: ['home-recent'],
    queryFn: () => api.get<RecentItem[]>('/api/home/recent'),
  })

  const curatedQuery = useQuery({
    queryKey: ['home-curated'],
    queryFn: () =>
      api.get<{ has_favorites: boolean; items: MediaItem[] }>('/api/home/curated'),
    staleTime: 30 * 60 * 1000,
  })

  const trendingQuery = useQuery({
    queryKey: ['home-trending'],
    queryFn: () => api.get<MediaItem[]>('/api/home/trending'),
    // Die Liste ändert sich höchstens täglich.
    staleTime: 60 * 60 * 1000,
  })

  // Erst beim Anklicken werden die vollen Angaben geholt - inklusive Status.
  const detailQuery = useQuery({
    queryKey: ['media-detail', offen?.media_type, offen?.tmdb_id],
    queryFn: () => api.get<MediaItem>(`/api/media/${offen!.media_type}/${offen!.tmdb_id}`),
    enabled: offen !== null,
  })

  const zuletzt = recentQuery.data ?? []
  const { markiert } = useFavorites()
  const kuratiert = curatedQuery.data?.items ?? []
  const hatFavoriten = curatedQuery.data?.has_favorites ?? false

  const alleVorschlaege = trendingQuery.data ?? []
  // Die ersten paar gross im Slider, der Rest als Kachelreihe darunter.
  const vorschlaege = alleVorschlaege.slice(0, 5)
  const weitere = alleVorschlaege.slice(5)

  return (
    <div className="flex flex-col gap-10">
      <header className="animate-nv-fade">
        <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">
          {t('home.greeting', { name: user?.display_name ?? user?.username ?? '' })}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-2 text-mist-500">{t('home.intro')}</p>
      </header>

      {(recentQuery.isPending || trendingQuery.isPending) && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}

      {vorschlaege.length > 0 && (
        <section className="flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold tracking-wide text-mist-500 uppercase">
              {t('home.trending')}
            </h2>
            <p className="mt-1 text-sm text-mist-600">{t('home.trendingIntro')}</p>
          </div>

          <Slider>
            {vorschlaege.map((item, index) => (
              <Spotlight
                key={item.tmdb_id}
                item={item}
                index={index}
                onQuickAdd={() => setSchnellAnfrage(item)}
              />
            ))}
          </Slider>

          {weitere.length > 0 && (
            <div className="mt-2 grid grid-cols-3 gap-4 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-7">
              {weitere.map((item, index) => (
                <TrendingTile
                  key={item.tmdb_id}
                  item={item}
                  index={index}
                  onQuickAdd={() => setSchnellAnfrage(item)}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {!curatedQuery.isPending && (
        <section className="flex flex-col gap-4">
          <div>
            <h2 className="text-sm font-semibold tracking-wide text-mist-500 uppercase">
              {t('home.curated')}
            </h2>
            <p className="mt-1 text-sm text-mist-600">{t('home.curatedIntro')}</p>
          </div>

          {!hatFavoriten ? (
            /* Ohne Favoriten gibt es nichts zu kuratieren - dann steht hier,
               was zu tun ist, statt einer leeren Flaeche. */
            <div className="animate-nv-fade rounded-2xl border border-dashed border-ink-700 px-6 py-10 text-center">
              <p className="text-lg font-semibold">{t('home.curatedEmptyTitle')}</p>
              <p className="mx-auto mt-1 max-w-lg text-sm leading-relaxed text-mist-500">
                {t('home.curatedEmptyText')}
              </p>
            </div>
          ) : kuratiert.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-8 text-center text-sm text-mist-500">
              {t('home.curatedNothingLeft')}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {kuratiert.map((item, index) => (
                <CuratedCard
                  key={item.tmdb_id}
                  item={item}
                  index={index}
                  favorit={markiert.has(`${item.media_type}-${item.tmdb_id}`)}
                  onQuickAdd={() => setSchnellAnfrage(item)}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {zuletzt.length > 0 && (
        <section>
          <h2 className="mb-4 text-sm font-semibold tracking-wide text-mist-500 uppercase">
            {t('home.justArrived')}
          </h2>
          <Slider>
            {zuletzt.map((item, index) => (
              <RecentCard key={`${item.media_type}-${item.tmdb_id}`} item={item} index={index} />
            ))}
          </Slider>
        </section>
      )}

      {!recentQuery.isPending && zuletzt.length === 0 && (
        <div className="animate-nv-fade rounded-3xl border border-dashed border-ink-700 px-6 py-16 text-center">
          <p className="text-lg font-semibold">{t('home.emptyTitle')}</p>
          <p className="mt-1 text-sm text-mist-500">{t('home.emptyText')}</p>
        </div>
      )}

      <p className="text-center">
        <Link
          to="/filme"
          className="text-sm text-mist-500 underline-offset-4 transition-colors hover:text-mist-100 hover:underline"
        >
          {t('home.discoverMore')}
        </Link>
      </p>

      {/* Zwei Wege ins selbe Fenster: aus dem Slider oben (dort fehlt der
          Status, der wird nachgeladen) und aus den Vorschlägen. */}
      <DetailModal
        item={offen ? (detailQuery.data ?? null) : schnellAnfrage}
        onClose={() => {
          setOffen(null)
          setSchnellAnfrage(null)
        }}
        arrConfigured={
          ((offen ?? schnellAnfrage)?.media_type === 'movie'
            ? config?.radarr_configured
            : config?.sonarr_configured) ?? false
        }
      />
    </div>
  )
}
