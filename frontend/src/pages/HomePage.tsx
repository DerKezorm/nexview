import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { MediaItem, RecentItem } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { DetailModal } from '../components/media/DetailModal'
import { FavoriteButton, useFavorites } from '../components/media/FavoriteButton'
import { CartIcon, MediaItemCard } from '../components/media/MediaCard'
import { useCardData } from '../components/media/useCardData'
import { WatchedBadge } from '../components/media/WatchedBadge'
import { Slider } from '../components/Slider'
import { VerschwindetBald } from '../components/VerschwindetBald'
import { Symbol } from '../components/Symbol'
import { Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { formatDate, formatRuntime } from '../lib/format'
import { stoeberPath, titlePath } from '../lib/routes'

/** Abstand zwischen zwei Kacheln beim Aufblenden. */
const STAGGER_MS = 90

/**
 * Was unter dem Titel steht: die Staffel, sonst die Medienart.
 *
 * Eine Serie ist **eine** Kachel, auch wenn vier Staffeln dahinterstecken
 * (Issue #3). Damit die Kachel trotzdem sagt, was angekommen ist, tritt die
 * Staffelangabe an die Stelle des Wortes „Serie" - wer „Staffel 3" liest,
 * weiß ohnehin, dass es keine Film ist, und die schmale Kachel hat für beides
 * keinen Platz.
 *
 * Leere Liste heißt Film oder ganze Serie; dann bleibt es beim alten Wort.
 */
function staffelText(item: RecentItem, t: TFunction): string {
  if (item.seasons.length === 1) return t('home.season', { number: item.seasons[0] })
  if (item.seasons.length > 1) return t('home.seasonCount', { count: item.seasons.length })
  return t(item.media_type === 'movie' ? 'common.movies' : 'common.series')
}

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
        {staffelText(item, t)}
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
          {/* Der Aufbau dieser Kachel bleibt bewusst wie er ist - sie ist
              breit statt hoch und damit ohnehin ein anderes Format. Nur das
              Auge kommt dazu, weil "gesehen" ueberall zu sehen sein soll. */}
          {item.watched && (
            <WatchedBadge on={item.watched_on} notOn={item.watched_not_on} />
          )}
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
  // Die Kachelreihe zeigt jetzt dieselbe Kachel wie ueberall sonst - dazu
  // gehoeren die IMDb-Wertungen und der Favoritenstand.
  const kachelDaten = useCardData(weitere)

  return (
    <div className="flex flex-col gap-10">
      <header className="animate-nv-fade flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-5xl">
            {t('home.greeting', { name: user?.display_name ?? user?.username ?? '' })}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-2 text-mist-500">{t('home.intro')}</p>
        </div>
        {/* ⚠️ **Ein Knopf, kein zweites Dashboard auf dieser Seite.** Die
            Startseite ist die Entdecken-Seite und bleibt es - auch für den
            Betreiber, der hier ja ebenfalls Filme sucht. Der Betriebsteil
            liegt eine Adresse weiter, und wo dieser Knopf steht, lässt sich
            später ohne Umbau ändern. */}
        {user?.role === 'admin' && (
          <Link
            to="/admin/dashboard"
            className="inline-flex items-center gap-2 rounded-full border border-ink-700 px-4 py-2 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100"
          >
            <Symbol name="analyse" />
            {t('nav.dashboard')}
          </Link>
        )}
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
                <div
                  key={item.tmdb_id}
                  style={{ animationDelay: `${index * STAGGER_MS}ms` }}
                  className="animate-nv-rise h-full"
                >
                  <MediaItemCard
                    item={item}
                    variant="kompakt"
                    onQuickAdd={setSchnellAnfrage}
                    ratings={kachelDaten.ratingsFor(item)}
                    favorit={kachelDaten.istFavorit(item)}
                  />
                </div>
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

      {/* ⚠️ **Vor** „Frisch geladen", nicht dahinter. Was verschwindet, hat
          eine Frist; was angekommen ist, läuft nicht weg. Wer noch schauen
          will, muss es zuerst sehen — und Ansehen hebt die Vormerkung auf. */}
      <VerschwindetBald />

      {zuletzt.length > 0 && (
        <section>
          <h2 className="mb-4 text-sm font-semibold tracking-wide text-mist-500 uppercase">
            {t('home.justArrived')}
          </h2>
          <Slider>
            {zuletzt.map((item, index) => (
              // Seit dem Zusammenlegen ist das Paar wieder eindeutig: Vorher
              // hatte eine Serie mit vier Staffeln vier gleiche Schlüssel.
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
          to={stoeberPath('movie')}
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
