import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { ApiError, api, gespeicherterFehler } from '../api/client'
import type { MediaRequest, QuotaInfo, QuotaOverview } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { TitelVerweis } from '../components/TitelVerweis'
import { useStorageStand, type SpeicherStand } from '../hooks/useStorageStand'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Pagination, useSeiten } from '../components/Pagination'
import { StatusBadge } from '../components/media/StatusBadge'
import { Button, Card, ErrorBanner, RundKnopf, Spinner } from '../components/ui'
import { Fenster } from '../components/Fenster'
import { Anfragebalken, Anfrageverlauf } from '../components/media/Anfrageverlauf'
import { Rueckmeldung } from '../components/media/Rueckmeldung'
import { folgenKompakt, formatDate, formatSize } from '../lib/format'
import { anfragenStandNeuLaden } from '../lib/refresh'

/**
 * Belegter Platz - noch ohne Grenze.
 *
 * Bewusst neben den Kontingent-Karten und in derselben Form: Es ist dieselbe
 * Art Auskunft. Nur steht hier keine Grenze, weil es noch keine gibt - und
 * genau das sagt die Karte auch, statt eine zu suggerieren.
 */
function StorageCard({ stand }: { stand: SpeicherStand }) {
  const { t, i18n } = useTranslation()

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-850/60 px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {t(stand.gesamtsicht ? 'storage.totalLabel' : 'storage.usedLabel')}
      </p>
      <p
        className={
          'mt-1 text-sm tabular-nums ' +
          (stand.ueberzogen ? 'text-bad-500' : 'text-mist-300')
        }
      >
        {/* Ohne Grenze steht nur die Zahl - „91 GB von unbegrenzt" wäre eine
            Formulierung ohne Aussage. */}
        {stand.limitBytes === null
          ? formatSize(stand.bytes, i18n.language)
          : t('storage.usedOfLimit', {
              used: formatSize(stand.bytes, i18n.language),
              limit: formatSize(stand.limitBytes, i18n.language),
            })}
      </p>
      <Link to="/profil" className="mt-0.5 block text-xs text-mist-600 hover:text-accent-500">
        {t(stand.gesamtsicht ? 'storage.houseHint' : 'storage.showDetail')}
      </Link>
    </div>
  )
}


function QuotaCard({ label, quota }: { label: string; quota: QuotaInfo }) {
  const { t, i18n } = useTranslation()

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-850/60 px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">{label}</p>
      {quota.unlimited ? (
        <p className="mt-1 text-sm text-mist-300">{t('myRequests.quotaUnlimited')}</p>
      ) : (
        <>
          <p
            className={
              'mt-1 text-sm ' + (quota.exhausted ? 'text-bad-500' : 'text-mist-300')
            }
          >
            {t('myRequests.quotaUsed', { used: quota.used, limit: quota.limit })}
          </p>
          {quota.resets_at && (
            <p className="mt-0.5 text-xs text-mist-600">
              {t('myRequests.quotaResets', {
                date: formatDate(quota.resets_at.slice(0, 10), i18n.language),
              })}
            </p>
          )}
        </>
      )}
    </div>
  )
}

/**
 * Der Kreis vor dem Titel - Zustand als Zeichen, manchmal auch Knopf.
 *
 * Das Etikett rechts sagt den Zustand in Worten; hier steht er als Form, damit
 * sich eine Liste aus zwanzig Anfragen überfliegen lässt, ohne zu lesen.
 *
 * ⚠️ **Er ist nicht immer anklickbar, und das ist die Schwäche der Idee.**
 * Wo etwas zu tun ist - zurückziehen, abbrechen -, ist der Kreis ein Knopf;
 * sonst nur ein Zeichen. Ein Kreis, der mal reagiert und mal nicht, kann
 * verwirren. Dagegen steht, dass die anklickbaren sich deutlich anders
 * verhalten: Zeiger, Umrandung und Farbe wechseln beim Darüberfahren, und sie
 * tragen einen Tooltip. Die anderen sind ausdrücklich stumm gestellt
 * (``aria-hidden``) - für Screenreader gibt es nur das Etikett, nicht zweimal
 * dieselbe Aussage.
 */
function ZustandsKreis({ status }: { status: MediaRequest['status'] }) {
  const { t } = useTranslation()

  // Form und Farbe je Zustand. Wer nichts davon trifft, bekommt nichts -
  // lieber eine leere Spalte als ein erfundenes Zeichen.
  const zeichen: Partial<Record<MediaRequest['status'], { pfad: string; ton: string }>> = {
    downloaded: { pfad: 'M5 13l4 4L19 7', ton: 'border-ok-500/40 text-ok-500' },
    // Durchgestrichener Kreis: abgebrochen, abgelehnt, wieder verschwunden.
    cancelled: { pfad: 'M5 19L19 5', ton: 'border-ink-700 text-mist-600' },
    rejected: { pfad: 'M5 19L19 5', ton: 'border-bad-500/40 text-bad-500' },
    deleted: { pfad: 'M5 19L19 5', ton: 'border-ink-700 text-mist-600' },
    failed: { pfad: 'M12 7v6M12 17h.01', ton: 'border-bad-500/40 text-bad-500' },
    // Hand: „halt, noch nicht" - offene Handfläche, kein Stoppschild.
    deferred: {
      pfad: 'M9 11V5.5a1.5 1.5 0 0 1 3 0V11m0 0V4.5a1.5 1.5 0 0 1 3 0V11m0 0V6.5a1.5 1.5 0 0 1 3 0V14a6 6 0 0 1-6 6h-1a6 6 0 0 1-6-6v-2a1.5 1.5 0 0 1 3 0',
      ton: 'border-warn-500/40 text-warn-500',
    },
  }

  const treffer = zeichen[status]
  if (!treffer) return null

  return (
    <span
      className={
        'flex h-9 w-9 items-center justify-center rounded-full border bg-ink-850 ' + treffer.ton
      }
      title={t(`status.${status}`)}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d={treffer.pfad} />
      </svg>
    </span>
  )
}

// "watchlist" ist kein Zustand, sondern eine Herkunft - deshalb steht es
// neben den Zuständen und nicht in ihrer Reihe. Der Knopf erscheint nur, wenn
// es überhaupt automatische Anfragen gibt.
type Filter = 'all' | 'watchlist' | MediaRequest['status']

/** Reihenfolge der Filterknöpfe. Zustände ohne Einträge werden ausgeblendet. */
const FILTERS: Filter[] = [
  'all',
  'watchlist',
  'pending_approval',
  'searching',
  'downloaded',
  'rejected',
  'cancelled',
  'deleted',
  'failed',
]

/** Wie viele Einträge fallen unter diesen Knopf? */
function zaehle(alle: MediaRequest[], wert: Filter): number {
  if (wert === 'all') return alle.length
  if (wert === 'watchlist') return alle.filter((e) => e.from_watchlist).length
  return alle.filter((e) => e.status === wert).length
}

export function MyRequestsPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const istAdmin = user?.role === 'admin'
  /** Welche Anfrage soll abgebrochen werden? Steuert die Rückfrage. */
  const [verlauf, setVerlauf] = useState<MediaRequest | null>(null)
  const [cancelling, setCancelling] = useState<MediaRequest | null>(null)
  const [filter, setFilter] = useState<Filter>('all')

  const requestsQuery = useQuery({
    queryKey: ['my-requests'],
    queryFn: () => api.get<MediaRequest[]>('/api/requests/mine'),
  })

  // Die Belegung laeuft unabhaengig vom Kontingent - gemessen wird immer,
  // begrenzt (spaeter) nur auf Wunsch.
  const speicher = useStorageStand()

  const quotaQuery = useQuery({
    queryKey: ['quota'],
    queryFn: () => api.get<QuotaOverview>('/api/requests/quota'),
  })

  function refresh() {
    anfragenStandNeuLaden(queryClient)
  }

  const withdrawMutation = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/requests/${id}`),
    onSuccess: refresh,
  })

  // ⚠️ Ohne ``onError`` verschluckt diese Mutation jeden Fehlschlag: Der
  // Dialog bliebe offen, nichts geschähe sichtbar, und der einzige Ausgang
  // wäre der Knopf "Abbrechen" - der ausgerechnet nichts abbricht. Der Fehler
  // gehört deshalb **in** den Dialog; ``withdrawMutation`` daneben zeigt ihn
  // in einem Banner, weil dort kein Dialog davorliegt.
  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/requests/${id}/cancel`),
    onSuccess: () => setCancelling(null),
    onSettled: refresh,
  })

  // Beim Öffnen einer neuen Rückfrage den alten Fehler vergessen - sonst
  // stünde er über einer Anfrage, die damit nichts zu tun hat.
  const abbruchFehler =
    cancelling !== null && cancelMutation.isError
      ? cancelMutation.error instanceof ApiError
        ? cancelMutation.error.message
        : t('myRequests.cancelFailed')
      : null

  const alle = requestsQuery.data ?? []
  // Nur Filter anbieten, zu denen es auch Einträge gibt - sonst führt eine
  // lange Reihe von Knöpfen ins Leere.
  const vorhanden = FILTERS.filter((wert) => wert === 'all' || zaehle(alle, wert) > 0)
  const requests =
    filter === 'all'
      ? alle
      : filter === 'watchlist'
        ? alle.filter((e) => e.from_watchlist)
        : alle.filter((e) => e.status === filter)
  // Die Zahlen an den Filterknöpfen zählen weiter über *alles* - geblättert
  // wird nur, was man gerade sieht.
  const blaettern = useSeiten(requests, filter)

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('myRequests.title')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('myRequests.intro')}</p>
      </header>

      {(quotaQuery.data || speicher) && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-mist-300">{t('myRequests.quota')}</h2>
          {/* **Nur die Währung, die auch gilt.** Zählt der belegte Platz, wäre
              „Filme: unbegrenzt" daneben keine Auskunft, sondern eine
              Nebelkerze - es gilt immer nur eine der beiden. */}
          <div
            className={
              'grid grid-cols-1 gap-3 ' +
              'sm:grid-cols-2'
            }
          >
            {quotaQuery.data && (
              <>
                <QuotaCard label={t('common.movies')} quota={quotaQuery.data.movie} />
                <QuotaCard
                  label={t('common.seriesPlural')}
                  quota={quotaQuery.data.tv}
                />
              </>
            )}
            {speicher && <StorageCard stand={speicher} />}
          </div>
          {/* Kommt vom angemeldeten Konto und nicht aus der Kontingent-
              Abfrage: Ob eine Anfrage freigegeben werden muss, hat mit der
              Währung nichts zu tun - und im Speicher-Betrieb wird die Abfrage
              gar nicht mehr geladen. */}
          <p className="mt-2 text-xs text-mist-600">
            {user?.effective_auto_approve
              ? t('myRequests.autoApprove')
              : t('myRequests.needsApproval')}
          </p>
        </section>
      )}

      {withdrawMutation.isError && (
        <ErrorBanner
          message={
            withdrawMutation.error instanceof ApiError
              ? withdrawMutation.error.message
              : t('myRequests.withdrawFailed')
          }
        />
      )}

      {requestsQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}

      {/* Erst ab zwei verschiedenen Zuständen lohnt sich die Auswahl - sonst
          stünde neben "Alle" nur derselbe Knopf noch einmal. */}
      {vorhanden.length > 2 && (
        <div className="flex flex-wrap gap-2">
          {vorhanden.map((wert) => (
            <button
              key={wert}
              type="button"
              onClick={() => setFilter(wert)}
              aria-pressed={filter === wert}
              className={
                'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                (filter === wert
                  ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                  : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
              }
            >
              {wert === 'all'
                ? t('adminRequests.filterAll')
                : wert === 'watchlist'
                  ? t('myRequests.fromWatchlistTab')
                  : t(`status.${wert}`)}
              <span className="ml-1.5 text-xs tabular-nums opacity-70">
                {zaehle(alle, wert)}
              </span>
            </button>
          ))}
        </div>
      )}

      {!requestsQuery.isPending && requests.length === 0 && (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {t('myRequests.empty')}
        </p>
      )}

      {requests.length > 0 && (
        <Card className="flex flex-col gap-3 p-4">
          {blaettern.sichtbar.map((request) => (
            <div
              key={request.id}
              className={
                'relative flex flex-wrap items-center gap-3 rounded-xl ' +
                'border border-ink-700 bg-ink-900/50 p-3'
              }
            >
              {/* Die Wegstrecke als Linie an der Unterkante. Sie liegt im
                  Innenabstand der Zeile, ändert also keine Höhe - siehe die
                  Anmerkung zu den festen Spalten weiter unten. */}
              <Anfragebalken status={request.status} />
              {/* ⚠️ Die Handlung steht **vor** dem Titel, in einem Platz mit
                  fester Breite - auch wenn sie fehlt.
                  Als Knopf hinter dem Titel machte sie die Liste unruhig: Je
                  nach Zustand steht dort einer oder keiner, und dadurch sprang
                  jede Zeile an einer anderen Stelle. Vorn und rund kostet sie
                  eine feste Spalte, und die Titel stehen wieder untereinander.
                  Die Beschriftung steckt im Tooltip - ``RundKnopf`` setzt sie
                  zugleich als ``aria-label``, sie ist also nicht nur für die
                  Maus da. */}
              <div className="flex w-9 shrink-0 items-center justify-center">
                {request.status === 'pending_approval' && (
                  <RundKnopf
                    label={t('myRequests.withdraw')}
                    onClick={() => withdrawMutation.mutate(request.id)}
                    gefahr
                  >
                    <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
                  </RundKnopf>
                )}

                {/* Hängt seit Tagen in der Warteschlange? Abbrechen gibt den
                    Platz im eigenen Kontingent wieder frei. */}
                {(request.status === 'searching' || request.status === 'requested') && (
                  <RundKnopf
                    label={t('requests.cancel')}
                    onClick={() => setCancelling(request)}
                    gefahr
                  >
                    <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
                  </RundKnopf>
                )}

                {/* Nichts zu tun? Dann steht hier der Zustand als Zeichen. */}
                {request.status !== 'pending_approval' &&
                  request.status !== 'searching' &&
                  request.status !== 'requested' && (
                    <ZustandsKreis status={request.status} />
                  )}
              </div>

              {/* Auf dem Telefon nimmt der Titel die ganze Zeile ein, Etikett
                  und Knopf rutschen darunter. Ohne das w-full teilen sich alle
                  drei eine Zeile: flex-1 hat die Grundbreite 0, wehrt sich also
                  nicht gegen das Schrumpfen - der Titel schnurrt dann auf "P."
                  zusammen, waehrend Etikett und Knopf ihre volle Breite
                  behalten. Lieber umbrechen als kuerzen. */}
              <div className="w-full min-w-0 sm:w-auto sm:flex-1">
                <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <TitelVerweis
                    mediaType={request.media_type}
                    tmdbId={request.tmdb_id}
                    titel={request.title}
                    erschienen={request.release_date}
                    className="min-w-0 font-semibold break-words"
                  />
                  {request.season !== null && (
                    <span className="shrink-0 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs font-medium text-mist-400">
                      {t('request.seasonShort', { number: request.season })}
                      {/* Ein Folgen-Paket zeigt seine Folgen - sonst wären
                          zwei Pakete derselben Staffel nicht zu unterscheiden. */}
                      {request.episodes && request.episodes.length > 0 && (
                        <>
                          {' · '}
                          {t('request.episodesShort', {
                            list: folgenKompakt(request.episodes),
                          })}
                        </>
                      )}
                    </span>
                  )}
                  {/* Haengt an der Anfrage selbst, nicht an der Einstellung:
                      Nimmt der Admin die 4K-Instanz heraus, waere eine laufende
                      4K-Anfrage sonst nicht mehr als solche zu erkennen. */}
                  {request.tier === 'uhd' && (
                    <span className="shrink-0 rounded-full border border-accent-500/50 bg-accent-500/10 px-2 py-0.5 text-xs font-semibold text-accent-400">
                      4K
                    </span>
                  )}
                  {/* Von der Merkliste statt von einem Klick. Fuer den
                      Entscheider ist das der Unterschied zwischen "jemand
                      wollte genau das" und "es stand auf einer Liste". */}
                  {request.from_watchlist && (
                    <span className="shrink-0 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs font-medium text-mist-400">
                      {t('myRequests.fromWatchlist')}
                    </span>
                  )}
                </p>
                <p className="text-xs text-mist-600">
                  {t(request.media_type === 'movie' ? 'common.movies' : 'common.series')} ·{' '}
                  {t('myRequests.requestedAt')}{' '}
                  {formatDate(request.requested_at.slice(0, 10), i18n.language)}
                </p>
                {request.error_message && (
                  <p className="mt-1 text-xs text-bad-500">
                    {gespeicherterFehler(request.error_detail, request.error_message)}
                  </p>
                )}
                {request.rejection_reason && (
                  <p className="mt-1 text-xs text-mist-500">{request.rejection_reason}</p>
                )}
              </div>

              {/* ⚠️ **Feste Spalten, sonst springt jede Zeile woanders hin.**
                  Die Etiketten sind unterschiedlich lang und je nach Zustand
                  steht ein Knopf da oder keiner. Ohne feste Breiten liegt in
                  einer Liste aus zwanzig Anfragen nichts auf einer Linie, und
                  das liest sich unruhig. Deshalb: Etikett rechtsbündig in
                  fester Breite, danach ein Platz für die situationsabhängige
                  Handlung - **auch wenn sie fehlt** -, und „Verlauf" als
                  letzter. Der steht damit in jeder Zeile an derselben Stelle. */}
              {/* Direkt hinter dem Titel: Die Sterne gehören zum Titel, nicht
                  zu den Handlungen rechts. Administratoren bewerten nicht -
                  sie beantworten die Rückmeldungen der anderen. */}
              {request.status === 'downloaded' && !istAdmin && (
                <Rueckmeldung
                  mediaType={request.media_type}
                  tmdbId={request.tmdb_id}
                  title={request.title}
                  season={request.season}
                  stand={
                    request.rating === null
                      ? null
                      : {
                          rating: request.rating,
                          comment: request.feedback,
                          reply: request.feedback_reply,
                          outdated: request.rating_outdated ?? false,
                        }
                  }
                  onGespeichert={refresh}
                />
              )}

              <div className="flex w-32 shrink-0 justify-end">
                <StatusBadge
                  status={request.status}
                  fortschritt={request.laedt_fortschritt}
                />
              </div>

              {/* „Warum dauert das?" beantwortet sich hier, statt beim
                  Administrator zu landen. Zurückhaltend, weil es die zweite
                  Frage ist - die erste ist der Zustand daneben. */}
              <Button variant="ghost" onClick={() => setVerlauf(request)}>
                {t('verlauf.open')}
              </Button>

            </div>
          ))}
        </Card>
      )}

      {/* Ohne Fußzeile: ``Fenster`` setzt dann selbst einen Schließen-Knopf
          oben hin. Hier gibt es nichts zu entscheiden, nur etwas zu lesen -
          ein „Abbrechen" wäre die falsche Beschriftung dafür. */}
      <Fenster
        offen={verlauf !== null}
        titel={t('verlauf.title')}
        unterzeile={verlauf?.title}
        onSchliessen={() => setVerlauf(null)}
      >
        {verlauf && <Anfrageverlauf request={verlauf} />}
      </Fenster>

      <Pagination
        seite={blaettern.seite}
        seiten={blaettern.seiten}
        onSeite={blaettern.setSeite}
      />

      <ConfirmDialog
        open={cancelling !== null}
        title={t('requests.cancelTitle')}
        description={t('requests.cancelText', { title: cancelling?.title ?? '' })}
        warning={t('requests.cancelWarning')}
        fehler={abbruchFehler}
        confirmLabel={t('requests.cancelConfirm')}
        loading={cancelMutation.isPending}
        onCancel={() => {
          cancelMutation.reset()
          setCancelling(null)
        }}
        onConfirm={() => cancelling && cancelMutation.mutate(cancelling.id)}
      />
    </div>
  )
}
