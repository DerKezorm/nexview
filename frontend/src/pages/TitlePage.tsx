import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { MediaDetail, MediaItem, MediaType, NamedRef, Trailer } from '../api/types'
import { AddRequestForm } from '../components/media/AddRequestForm'
import { CastStrip } from '../components/media/CastStrip'
import { FavoriteButton, useFavorites } from '../components/media/FavoriteButton'
import { MediaCard } from '../components/media/MediaCard'
import { DetailModal } from '../components/media/DetailModal'
import { Poster, RatingBadge } from '../components/media/Poster'
import { RatingBadges, useMovieRatings } from '../components/media/RatingBadges'
import { SeasonList } from '../components/media/SeasonList'
import { StatusBadge } from '../components/media/StatusBadge'
import { PlayIcon, TrailerModal } from '../components/media/TrailerModal'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'
import { formatDate, formatRuntime } from '../lib/format'
import { browsePath, personPath } from '../lib/routes'
import { useAuth } from '../auth/useAuth'

/** Ein Eckdatum: Beschriftung oben, Wert darunter. */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">{label}</dt>
      <dd className="mt-0.5 text-sm text-mist-300">{value}</dd>
    </div>
  )
}

/** Kleine Marke - für Genres, die nicht weiterführen. */
function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-ink-700 bg-ink-850 px-2.5 py-1 text-xs text-mist-400">
      {children}
    </span>
  )
}

/** Dasselbe, aber anklickbar: führt zur Liste aller Titel damit. */
function ChipLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="rounded-full border border-ink-700 bg-ink-850 px-2.5 py-1 text-xs text-mist-400 transition-colors hover:border-accent-600/60 hover:bg-accent-500/10 hover:text-accent-400"
    >
      {children}
    </Link>
  )
}

/** Eine Reihe anklickbarer Marken - für Schlagworte und Studios. */
function ChipRow({
  label,
  eintraege,
  art,
  mediaType,
}: {
  label: string
  eintraege: NamedRef[]
  art: 'schlagwort' | 'studio'
  mediaType: MediaType
}) {
  if (eintraege.length === 0) return null
  return (
    <div className="mt-5">
      <p className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">{label}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {eintraege.map((eintrag) => (
          <ChipLink key={eintrag.id} to={browsePath(mediaType, art, eintrag.id, eintrag.name)}>
            {eintrag.name}
          </ChipLink>
        ))}
      </div>
    </div>
  )
}

function Geld(betrag: number | null, sprache: string): string | null {
  if (!betrag) return null
  return new Intl.NumberFormat(sprache, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(betrag)
}

/**
 * Vollbildseite zu einem Titel.
 *
 * Alles, was TMDB hergibt: Handlung, Eckdaten, Besetzung mit Fotos, Studios,
 * Schlagworte, Empfehlungen - und bei Serien die Staffeln zum Aufklappen samt
 * der Frage, welche Folgen schon vorliegen.
 */
export function TitlePage() {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const navigate = useNavigate()
  const { mediaType, tmdbId } = useParams<{ mediaType: MediaType; tmdbId: string }>()
  const { data: config } = useConfig()

  const [adding, setAdding] = useState(false)
  const [schnellAnfrage, setSchnellAnfrage] = useState<MediaItem | null>(null)
  const [trailer, setTrailer] = useState<Trailer | null>(null)

  // Der Hook muss vor jedem bedingten Rückgabewert stehen: React verlangt,
  // dass bei jedem Durchlauf dieselben Hooks in derselben Reihenfolge laufen.
  const query = useQuery({
    queryKey: ['title-detail', mediaType, tmdbId],
    queryFn: () => api.get<MediaDetail>(`/api/detail/${mediaType}/${tmdbId}`),
    enabled: Boolean(mediaType && tmdbId),
    staleTime: 30 * 60 * 1000,
  })

  const wertungen = useMovieRatings(query.data ? [query.data] : [])
  const { markiert } = useFavorites()

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

  const item = query.data
  const istFilm = item.media_type === 'movie'
  const arrConfigured = istFilm
    ? (config?.radarr_configured ?? false)
    : (config?.sonarr_configured ?? false)

  const laufzeit = formatRuntime(item.runtime_minutes, i18n.language)
  const regie = item.crew.filter((person) => person.job === 'Director' || person.job === 'Creator')
  const drehbuch = item.crew.filter(
    (person) => person.job !== 'Director' && person.job !== 'Creator',
  )

  /**
   * Staffeln, von denen noch Folgen fehlen.
   *
   * Nur die dürfen nachgefordert werden. Eine Serie, die vollständig
   * vorliegt, bekommt gar keinen Knopf - "nachfordern" ohne etwas
   * Fehlendes wäre eine leere Geste.
   */
  const fehlendeStaffeln = item.seasons.filter(
    (staffel) => staffel.episodes_available < staffel.episode_count,
  )
  const nurWeitereStaffel =
    !istFilm &&
    fehlendeStaffeln.length > 0 &&
    (item.status === 'downloaded' || item.status === 'in_library')
  // Gesperrt heißt gesperrt - außer für den Administrator. Die Liste ist
  // seine Entscheidung und soll die anderen bremsen, nicht ihn. Das
  // Backend sieht es genauso, der Knopf ist nur die Bequemlichkeit dazu.
  const istAdmin = user?.role === 'admin'
  const gesperrt = item.status === 'blocked'
  const kannAnfragen =
    item.status === 'not_requested' || nurWeitereStaffel || (gesperrt && istAdmin)

  return (
    <div className="flex flex-col gap-8">
      {/* Kopfbereich mit Hintergrundbild - randlos bis an die Seitenkanten. */}
      <div className="relative -mx-4 -mt-8 sm:-mx-6">
        {item.backdrop_url && (
          <div className="absolute inset-0 h-72 overflow-hidden sm:h-96">
            <img src={item.backdrop_url} alt="" className="h-full w-full object-cover" />
            <div className="absolute inset-0 bg-linear-to-t from-ink-950 via-ink-950/70 to-ink-950/30" />
          </div>
        )}

        <div className="relative px-4 pt-8 sm:px-6">
          <Link
            to={istFilm ? '/filme' : '/serien'}
            className="text-sm text-mist-500 transition-colors hover:text-mist-200"
          >
            ← {t(istFilm ? 'nav.discoverMovies' : 'nav.discoverSeries')}
          </Link>

          <div className="mt-4 flex flex-col gap-6 sm:flex-row">
            <div className="aspect-2/3 w-40 shrink-0 self-start overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-2xl shadow-black/50 sm:w-52">
              <Poster url={item.poster_url} title={item.title} />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={item.status} />
                <RatingBadge vote={item.vote_average} count={item.vote_count} />
                {/* IMDb, Rotten Tomatoes, Metacritic - nur bei Filmen, und nur
                    was Radarr auch kennt. */}
                <RatingBadges ratings={wertungen[item.tmdb_id]} title={item.title} gross />
              </div>

              <h1 className="mt-2 text-3xl leading-tight font-bold tracking-tight sm:text-4xl">
                {item.title}
              </h1>
              {item.original_title && item.original_title !== item.title && (
                <p className="text-sm text-mist-600">{item.original_title}</p>
              )}
              {item.tagline && (
                <p className="mt-2 text-sm text-mist-400 italic">„{item.tagline}“</p>
              )}

              {item.genres.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {item.genres.map((genre) => (
                    <Chip key={genre}>{genre}</Chip>
                  ))}
                </div>
              )}

              <p className="mt-4 max-w-2xl text-sm leading-relaxed text-mist-300">
                {item.overview || t('media.noOverview')}
              </p>

              <div className="mt-5 flex flex-wrap items-center gap-3">
                {/* Das Herz steht bei jedem Titel - auch bei einem, der längst
                    in der Bibliothek liegt. Mögen kann man ihn trotzdem, und
                    genau das treibt die Empfehlungen. */}
                <FavoriteButton
                  item={item}
                  markiert={markiert.has(`${item.media_type}-${item.tmdb_id}`)}
                  gross
                />

                {item.trailer && (
                  <Button type="button" variant="ghost" onClick={() => setTrailer(item.trailer)}>
                    <PlayIcon />
                    {t('detail.trailer')}
                  </Button>
                )}

                {/* Etwas stimmt nicht mit diesem Titel? Führt ins
                    Ticketcenter, mit vorbelegtem Bezug - so muss niemand den
                    Namen abtippen und der Administrator weiß sofort, worum es
                    geht. */}
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    navigate(
                      `/tickets?media_type=${item.media_type}&tmdb_id=${item.tmdb_id}` +
                        `&title=${encodeURIComponent(item.title)}`,
                    )
                  }
                >
                  {t('tickets.report')}
                </Button>

                {kannAnfragen ? (
                  adding ? (
                    <Card className="max-w-xl">
                      {/* Beim Nachfordern stehen nur die Staffeln zur Wahl,
                          von denen wirklich etwas fehlt. */}
                      <AddRequestForm
                        item={nurWeitereStaffel ? { ...item, seasons: fehlendeStaffeln } : item}
                        onDone={() => setAdding(false)}
                        seasonOnly={nurWeitereStaffel}
                      />
                    </Card>
                  ) : (
                    <Button
                      type="button"
                      onClick={() => setAdding(true)}
                      disabled={!arrConfigured}
                      title={arrConfigured ? undefined : t('request.arrMissing')}
                    >
                      {nurWeitereStaffel
                        ? t('request.addSeason')
                        : !istFilm && item.seasons.length > 1
                          ? t('request.chooseSeason')
                          : t(istFilm ? 'request.addMovie' : 'request.addSeries')}
                    </Button>
                  )
                ) : (
                  <p className="text-sm text-mist-500">
                    {t(
                      gesperrt && istAdmin
                        ? 'request.state.blockedAdmin'
                        : `request.state.${item.status}`,
                    )}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <Card>
        <dl className="grid grid-cols-2 gap-5 sm:grid-cols-4">
          <Fact label={t('media.released')} value={formatDate(item.release_date, i18n.language)} />
          {laufzeit && (
            <Fact label={t(istFilm ? 'media.runtime' : 'media.episodeLength')} value={laufzeit} />
          )}
          {item.certification && (
            <Fact label={t('media.certification')} value={item.certification} />
          )}
          {item.status_text && <Fact label={t('detail.status')} value={item.status_text} />}

          {!istFilm && item.seasons_total !== null && (
            <Fact
              label={t('detail.seasons')}
              value={t('detail.seasonCount', { count: item.seasons_total })}
            />
          )}
          {!istFilm && item.episodes_total !== null && (
            <Fact
              label={t('detail.episodes')}
              value={t('detail.episodeCount', { count: item.episodes_total })}
            />
          )}
          {!istFilm && item.networks.length > 0 && (
            <Fact label={t('detail.network')} value={item.networks.map((n) => n.name).join(', ')} />
          )}

          {regie.length > 0 && (
            <Fact
              label={t(istFilm ? 'detail.director' : 'detail.creator')}
              value={regie.map((p) => p.name).join(', ')}
            />
          )}
          {drehbuch.length > 0 && (
            <Fact label={t('detail.writing')} value={drehbuch.map((p) => p.name).join(', ')} />
          )}
          {item.spoken_languages.length > 0 && (
            <Fact label={t('detail.languages')} value={item.spoken_languages.join(', ')} />
          )}
          {Geld(item.budget, i18n.language) && (
            <Fact label={t('detail.budget')} value={Geld(item.budget, i18n.language)!} />
          )}
          {Geld(item.revenue, i18n.language) && (
            <Fact label={t('detail.revenue')} value={Geld(item.revenue, i18n.language)!} />
          )}
        </dl>

        <ChipRow
          label={t('detail.studios')}
          eintraege={item.studios}
          art="studio"
          mediaType={item.media_type}
        />
        <ChipRow
          label={t('detail.keywords')}
          eintraege={item.keywords.slice(0, 20)}
          art="schlagwort"
          mediaType={item.media_type}
        />

        {item.homepage && (
          <p className="mt-5 text-sm">
            <a
              href={item.homepage}
              target="_blank"
              rel="noreferrer noopener"
              className="text-accent-400 underline decoration-accent-400/40 underline-offset-4 hover:text-accent-300"
            >
              {t('detail.homepage')}
            </a>
          </p>
        )}
      </Card>

      {!istFilm && <SeasonList tmdbId={item.tmdb_id} seasons={item.seasons} />}

      <CastStrip cast={item.cast} />

      {item.recommendations.length > 0 && (
        <section>
          <h2 className="mb-3 text-lg font-semibold">{t('detail.recommendations')}</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {item.recommendations.map((vorschlag) => (
              <MediaCard key={vorschlag.tmdb_id} item={vorschlag} onQuickAdd={setSchnellAnfrage} />
            ))}
          </div>
        </section>
      )}

      {/* Das Schnell-Popup aus den Empfehlungen heraus. */}
      <DetailModal
        item={schnellAnfrage}
        onClose={() => setSchnellAnfrage(null)}
        arrConfigured={arrConfigured}
      />

      <TrailerModal trailer={trailer} onClose={() => setTrailer(null)} />

      {/* Bei Personen ohne Foto sieht man sonst nicht, dass sie anklickbar sind. */}
      {item.cast.length === 0 && item.crew.length > 0 && (
        <p className="text-sm text-mist-600">
          {item.crew.map((person, index) => (
            <span key={`${person.person_id}-${person.job}`}>
              {index > 0 && ' · '}
              <Link to={personPath(person.person_id)} className="hover:text-mist-300">
                {person.name}
              </Link>
            </span>
          ))}
        </p>
      )}
    </div>
  )
}
