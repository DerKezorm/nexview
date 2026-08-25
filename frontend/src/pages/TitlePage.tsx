import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type {
  MediaDetail,
  MediaItem,
  MediaType,
  NamedRef,
  Trailer,
  WatchProvider,
  WatchProviders,
} from '../api/types'
import { AddRequestForm } from '../components/media/AddRequestForm'
import { SagMirBescheid } from '../components/media/SagMirBescheid'
import { CastStrip } from '../components/media/CastStrip'
import { FavoriteButton, useFavorites } from '../components/media/FavoriteButton'
import { MediaItemCard } from '../components/media/MediaCard'
import { useCardData } from '../components/media/useCardData'
import { DetailModal } from '../components/media/DetailModal'
import { Poster, RatingBadge } from '../components/media/Poster'
import { RatingBadges, useMovieRatings } from '../components/media/RatingBadges'
import { SeasonList } from '../components/media/SeasonList'
import { StatusBadge } from '../components/media/StatusBadge'
import { UhdBadge } from '../components/media/UhdBadge'
import { WatchedBadge } from '../components/media/WatchedBadge'
import { PlayIcon, TrailerModal } from '../components/media/TrailerModal'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { darfAnfragen, istUnterwegs } from '../lib/status'
import { useConfig } from '../hooks/useConfig'
import { formatDate, formatRuntime } from '../lib/format'
import { browsePath, personPath, stoeberPath } from '../lib/routes'
import { useAuth } from '../auth/useAuth'

/** Eine Runde Vorschlaege vom Server. */
type Auswahl = {
  items: MediaItem[]
  runde: number
  /** 1 heisst: es gibt nichts zu wechseln. Dann fehlt der Knopf. */
  runden: number
}

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

/** Eine Gruppe Anbieter (Abo, Kostenlos, Leihen, Kaufen) als Logo-Reihe. */
function AnbieterGruppe({ label, anbieter }: { label: string; anbieter: WatchProvider[] }) {
  if (anbieter.length === 0) return null
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
      <span className="w-20 shrink-0 text-[11px] font-medium tracking-wide text-mist-600 uppercase">
        {label}
      </span>
      <div className="flex flex-wrap gap-2">
        {anbieter.map((a) =>
          a.logo_url ? (
            <img
              key={a.id}
              src={a.logo_url}
              alt={a.name}
              title={a.name}
              loading="lazy"
              className="h-9 w-9 rounded-lg border border-ink-700 object-cover"
            />
          ) : (
            <span
              key={a.id}
              className="rounded-full border border-ink-700 bg-ink-850 px-2.5 py-1 text-xs text-mist-400"
            >
              {a.name}
            </span>
          ),
        )}
      </div>
    </div>
  )
}

/**
 * „Wo streambar" - die Streaming-Anbieter zur Region des Nutzers.
 *
 * Die Daten stammen von JustWatch (über TMDB); die Verlinkung auf deren
 * Übersicht ist als Quellenangabe Pflicht - deshalb steht sie fest dabei und
 * ist nicht wegzulassen.
 */
function WoStreambar({ watch }: { watch: WatchProviders }) {
  const { t } = useTranslation()
  return (
    <div className="mt-6 border-t border-ink-700/60 pt-5">
      <p className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">
        {t('watch.title', { region: watch.region })}
      </p>
      <AnbieterGruppe label={t('watch.flatrate')} anbieter={watch.flatrate} />
      <AnbieterGruppe label={t('watch.free')} anbieter={watch.free} />
      <AnbieterGruppe label={t('watch.rent')} anbieter={watch.rent} />
      <AnbieterGruppe label={t('watch.buy')} anbieter={watch.buy} />

      {/* Pflicht-Quellenangabe. TMDB reicht die Daten von JustWatch durch und
          liefert nur einen Link auf die eigene Seite - der wäre hier
          irreführend, also verweisen wir direkt auf JustWatch. */}
      <p className="mt-3 text-xs text-mist-600">
        <a
          href="https://www.justwatch.com"
          target="_blank"
          rel="noreferrer noopener"
          className="hover:text-mist-300 hover:underline"
        >
          {t('watch.justwatch')}
        </a>
      </p>
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
  // Kam der Klick von der Merklisten-Seite? Die Detailseite ist dieselbe wie
  // ueberall, nur die Herkunft der Anfrage soll erhalten bleiben.
  const [suchParameter] = useSearchParams()
  const vonMerkliste = suchParameter.get('von') === 'merkliste'
  const { data: config } = useConfig()

  const [adding, setAdding] = useState(false)
  const [schnellAnfrage, setSchnellAnfrage] = useState<MediaItem | null>(null)
  const [trailer, setTrailer] = useState<Trailer | null>(null)
  /* Welche Runde Vorschlaege ist zu sehen? 0 ist die, die schon in der
     Detailantwort steckt - dafuer wird nichts nachgeladen. */
  const [runde, setRunde] = useState(0)

  // Der Hook muss vor jedem bedingten Rückgabewert stehen: React verlangt,
  // dass bei jedem Durchlauf dieselben Hooks in derselben Reihenfolge laufen.
  const query = useQuery({
    queryKey: ['title-detail', mediaType, tmdbId],
    queryFn: () => api.get<MediaDetail>(`/api/detail/${mediaType}/${tmdbId}`),
    enabled: Boolean(mediaType && tmdbId),
    staleTime: 30 * 60 * 1000,
  })

  /* Erst beim Druck auf "Neue Auswahl" geholt: der Vorrat kostet vier
     Abfragen bei TMDB, und die meisten sehen sich nur die erste Runde an. */
  const auswahl = useQuery({
    queryKey: ['recommendations', mediaType, tmdbId, runde],
    queryFn: () =>
      api.get<Auswahl>(`/api/detail/${mediaType}/${tmdbId}/recommendations?runde=${runde}`),
    enabled: runde > 0,
    staleTime: 30 * 60 * 1000,
  })

  const wertungen = useMovieRatings(query.data ? [query.data] : [])
  const { markiert } = useFavorites()

  /* Runde 0 steckt schon in der Detailantwort. Solange die neue Auswahl laedt,
     bleibt die alte stehen - sonst blitzt eine leere Flaeche auf.

     Steht hier oben und nicht erst weiter unten, weil der Haken darunter vor
     jedem bedingten Rueckgabewert laufen muss - React verlangt bei jedem
     Durchlauf dieselben Haken in derselben Reihenfolge. */
  const vorschlaege =
    runde > 0 && auswahl.data ? auswahl.data.items : (query.data?.recommendations ?? [])
  // Die Empfehlungen sind eigene Titel und brauchen ihre eigenen Wertungen -
  // sonst blieben dort die IMDb-Abzeichen leer und jedes Herz ungefuellt.
  const kachelDaten = useCardData(vorschlaege)

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
  /**
   * ⚠️ Bewusst **ohne** Bedingung an den Zustand.
   *
   * Vorher hieß es „nur wenn die Serie fertig geladen ist" – und damit ließ
   * sich Staffel 2 nicht anfragen, solange Staffel 1 noch lief. Der Server
   * erlaubt das längst (`find_active` prüft die Staffel mit); es war allein
   * die Oberfläche, die zumachte. Für andere Nutzer war die ganze Serie damit
   * blockiert, sobald einer eine einzige Staffel angefragt hatte.
   *
   * Gesperrte Titel bleiben außen vor – dort gilt die Sperre für alles.
   */
  const nurWeitereStaffel =
    !istFilm && fehlendeStaffeln.length > 0 && item.status !== 'blocked'
  // Gesperrt heißt gesperrt - außer für den Administrator. Die Liste ist
  // seine Entscheidung und soll die anderen bremsen, nicht ihn. Das
  // Backend sieht es genauso, der Knopf ist nur die Bequemlichkeit dazu.
  const istAdmin = user?.role === 'admin'
  const gesperrt = item.status === 'blocked'
  /**
   * Die 4K-Fassung ist noch offen - dann muss der Knopf erscheinen, auch wenn
   * die Standard-Fassung längst geladen ist. Genau darum geht es bei zwei
   * Instanzen: derselbe Film einmal in 1080p und einmal in 4K.
   *
   * `status_uhd` liefert der Server nur, wenn es eine 4K-Instanz gibt **und**
   * dieser Benutzer sie nutzen darf - die Prüfung steckt also schon darin.
   */
  const uhdOffen = item.status_uhd === 'not_requested'
  const kannAnfragen =
    darfAnfragen(item.status) || uhdOffen || nurWeitereStaffel || (gesperrt && istAdmin)

  /* Der Knopf erscheint, sobald es etwas zu holen gibt. Vor der ersten
     Abfrage weiss das niemand - dann zaehlt, ob TMDB ueberhaupt Vorschlaege
     kennt, denn der Vorrat ist nie kleiner als das, was hier schon steht. */
  const mehrMoeglich = auswahl.data ? auswahl.data.runden > 1 : item.recommendations.length > 0

  return (
    <div className="flex flex-col gap-8">
      {/* Kopfbereich mit Hintergrundbild - randlos bis an die Seitenkanten. */}
      <div className="relative -mx-4 -mt-8 sm:-mx-6">
        {item.backdrop_url && (
          <div className="absolute inset-0 h-72 overflow-hidden sm:h-96">
            <img src={item.backdrop_url} alt="" className="nv-backdrop h-full w-full object-cover" />
            <div className="absolute inset-0 bg-linear-to-t from-ink-950 via-ink-950/70 to-ink-950/30" />
          </div>
        )}

        <div className="relative px-4 pt-8 sm:px-6">
          {/* Der Rücksprung führt in den Katalog. Früher zeigte er auf
              „Filme entdecken" bzw. „Serien entdecken" — diese Seiten gibt es
              nicht mehr. */}
          <Link
            to={stoeberPath(istFilm ? 'movie' : 'tv')}
            className="text-sm text-mist-500 transition-colors hover:text-mist-200"
          >
            ← {t('stoebern.titel')}
          </Link>

          <div className="mt-4 flex flex-col gap-6 sm:flex-row">
            <div className="aspect-2/3 w-40 shrink-0 self-start overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-2xl shadow-black/50 sm:w-52">
              <Poster url={item.poster_url} title={item.title} />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={item.status} />
                {item.status_uhd && <UhdBadge status={item.status_uhd} />}
                {item.watched && (
                  <WatchedBadge on={item.watched_on} notOn={item.watched_not_on} />
                )}
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
                    geht.

                    Für den Administrator selbst ausgeblendet: er ist ja der,
                    bei dem man sich meldet. */}
                {!istAdmin && (
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
                )}

                {kannAnfragen ? (
                  adding ? (
                    <Card className="max-w-xl">
                      {/* ⚠️ Immer die **volle** Staffelliste – das Fenster
                          graut Vorhandenes selbst aus. Vorher wurden fertige
                          Staffeln hier weggefiltert, und ausgerechnet die
                          einzige komplett geladene fehlte kommentarlos in der
                          Auswahl – gemeldet als „Staffel 4 wird gar nicht
                          angezeigt?". Verstecken beantwortet die Frage nicht,
                          wo sie geblieben ist; Ausgrauen schon. */}
                      <AddRequestForm
                        item={item}
                        onDone={() => setAdding(false)}
                        fromWatchlist={vonMerkliste}
                        imAbo={item.in_my_subscriptions ?? []}
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
                ) : null}

                {/* Bei der Serie dauerhaft, beim Film nur, wenn es etwas zu
                    warten gibt - siehe SagMirBescheid. */}
                {!istFilm && !item.requested_by_me && !gesperrt && (
                  <SagMirBescheid
                    mediaType="tv"
                    tmdbId={item.tmdb_id}
                    title={item.title}
                    posterUrl={item.poster_url}
                    aktiv={item.watching ?? false}
                  />
                )}

                {!kannAnfragen && (
                  <p className="text-sm text-mist-500">
                    {t(
                      gesperrt && istAdmin
                        ? 'request.state.blockedAdmin'
                        : `request.state.${item.status}`,
                    )}
                  </p>
                )}

                {/* Der Film ist angefragt, aber noch nicht da - genau dann
                    gibt es etwas zu warten. */}
                {/* ⚠️ Nicht fuer den, der selbst angefragt hat.
                    Er bekommt die Fertig-Meldung ohnehin; ein zweiter Weg
                    dorthin waere eine zweite Nachricht ueber dasselbe. */}
                {istFilm &&
                  istUnterwegs(item.status) &&
                  !item.requested_by_me &&
                  !gesperrt && (
                  <SagMirBescheid
                    mediaType="movie"
                    tmdbId={item.tmdb_id}
                    title={item.title}
                    posterUrl={item.poster_url}
                    aktiv={item.watching ?? false}
                  />
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

          {/* Der Ablageort. Kommt nur bei Administratoren mit – der Server
              liefert ihn sonst gar nicht erst aus. Über die volle Breite und
              in Schreibmaschinenschrift, weil ein Pfad lang ist und man ihn
              zeichenweise liest.

              Liegt der Titel in beiden Instanzen, stehen **beide** Pfade da –
              es sind zwei Dateien, und welche wo liegt, ist genau die Frage,
              die man sich hier stellt. Markiert wird nur die 4K-Zeile: Die
              andere ist der Normalfall, und ein „1080p" an jedem Film wäre
              Beschriftung ohne Aussage. */}
          {(item.path || item.path_uhd) && (
            <div className="col-span-2 sm:col-span-4">
              <dt className="text-[11px] font-medium tracking-wide text-mist-600 uppercase">
                {t('detail.path')}
              </dt>
              {item.path && (
                <dd className="mt-0.5 break-all font-mono text-xs text-mist-400">
                  {item.path}
                </dd>
              )}
              {item.path_uhd && (
                <dd className="mt-0.5 break-all font-mono text-xs text-mist-400">
                  <span className="mr-1.5 font-sans font-medium text-accent-500">
                    4K
                  </span>
                  {item.path_uhd}
                </dd>
              )}
            </div>
          )}

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

        {item.watch && <WoStreambar watch={item.watch} />}

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

      {vorschlaege.length > 0 && (
        <section>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">{t('detail.recommendations')}</h2>

            {/* Ist unter den zwoelf nichts dabei, holt der Knopf die naechsten
                zwoelf. Er fehlt, solange nicht feststeht, dass es welche gibt -
                ein Knopf, der nichts tut, ist schlimmer als keiner. */}
            {mehrMoeglich && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => setRunde((bisher) => bisher + 1)}
                loading={auswahl.isFetching}
              >
                {t('detail.reroll')}
              </Button>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {vorschlaege.map((vorschlag) => (
              <MediaItemCard
                key={vorschlag.tmdb_id}
                item={vorschlag}
                onQuickAdd={setSchnellAnfrage}
                ratings={kachelDaten.ratingsFor(vorschlag)}
                favorit={kachelDaten.istFavorit(vorschlag)}
              />
            ))}
          </div>

          {auswahl.error && (
            <div className="mt-3">
              <ErrorBanner message={t('detail.rerollFailed')} />
            </div>
          )}
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
