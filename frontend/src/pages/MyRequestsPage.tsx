import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { MediaRequest, QuotaInfo, QuotaOverview, StorageMine } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { useConfig } from '../hooks/useConfig'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Pagination, useSeiten } from '../components/Pagination'
import { StarRating } from '../components/StarRating'
import { StatusBadge } from '../components/media/StatusBadge'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDate, formatSize } from '../lib/format'
import { anfragenStandNeuLaden } from '../lib/refresh'

/**
 * Belegter Platz - noch ohne Grenze.
 *
 * Bewusst neben den Kontingent-Karten und in derselben Form: Es ist dieselbe
 * Art Auskunft. Nur steht hier keine Grenze, weil es noch keine gibt - und
 * genau das sagt die Karte auch, statt eine zu suggerieren.
 */
function StorageCard({ daten }: { daten: StorageMine }) {
  const { t, i18n } = useTranslation()

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-850/60 px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {t('storage.usedLabel')}
      </p>
      <p className="mt-1 text-sm text-mist-300 tabular-nums">
        {formatSize(daten.used_bytes, i18n.language)}
      </p>
      <Link to="/profil" className="mt-0.5 block text-xs text-mist-600 hover:text-accent-500">
        {t('storage.itemCount', { count: daten.items })}
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

/** Sterne und Kommentar zu einem geladenen Titel - plus die Antwort der Entscheider. */
function FeedbackBlock({ request, onSaved }: { request: MediaRequest; onSaved: () => void }) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(request.rating === null)
  const [sterne, setSterne] = useState(request.rating ?? 0)
  const [kommentar, setKommentar] = useState(request.feedback ?? '')

  const speichern = useMutation({
    mutationFn: () =>
      api.post(`/api/requests/${request.id}/feedback`, {
        rating: sterne,
        comment: kommentar.trim() || null,
      }),
    onSuccess: () => {
      setOffen(false)
      onSaved()
    },
  })

  return (
    <div className="w-full border-t border-ink-700 pt-3">
      {offen ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs text-mist-500">{t('feedback.question')}</span>
            <StarRating value={sterne} onChange={setSterne} />
          </div>
          {sterne > 0 && (
            <>
              <textarea
                value={kommentar}
                onChange={(event) => setKommentar(event.target.value)}
                maxLength={1000}
                rows={2}
                placeholder={t('feedback.commentPlaceholder')}
                className="w-full rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-200 outline-none focus:border-accent-500"
              />
              <div className="flex items-center gap-2">
                <Button onClick={() => speichern.mutate()} loading={speichern.isPending}>
                  {t('feedback.submit')}
                </Button>
                {request.rating !== null && (
                  <Button variant="ghost" onClick={() => setOffen(false)}>
                    {t('common.cancel')}
                  </Button>
                )}
              </div>
            </>
          )}
          {speichern.isError && (
            <p className="text-xs text-bad-500">
              {speichern.error instanceof ApiError
                ? speichern.error.message
                : t('feedback.failed')}
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs text-mist-500">{t('feedback.yourRating')}</span>
            <StarRating value={request.rating ?? 0} size="sm" />
            <button
              type="button"
              onClick={() => setOffen(true)}
              className="text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
            >
              {t('feedback.change')}
            </button>
          </div>
          {request.feedback && <p className="text-sm text-mist-400">{request.feedback}</p>}
          {request.feedback_reply && (
            <div className="mt-1 rounded-xl border border-ink-700 bg-ink-850/60 px-3 py-2">
              <p className="text-xs font-medium text-accent-500">{t('feedback.replyTitle')}</p>
              <p className="mt-0.5 text-sm text-mist-300">{request.feedback_reply}</p>
            </div>
          )}
        </div>
      )}
    </div>
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
  const [cancelling, setCancelling] = useState<MediaRequest | null>(null)
  const [filter, setFilter] = useState<Filter>('all')

  const requestsQuery = useQuery({
    queryKey: ['my-requests'],
    queryFn: () => api.get<MediaRequest[]>('/api/requests/mine'),
  })

  // Die Belegung laeuft unabhaengig vom Kontingent - gemessen wird immer,
  // begrenzt (spaeter) nur auf Wunsch.
  const { data: config } = useConfig()
  const storageQuery = useQuery({
    queryKey: ['storage-mine'],
    queryFn: () => api.get<StorageMine>('/api/storage/me'),
    // Ist der Schalter aus, gibt es den Endpunkt nicht - dann gar nicht erst
    // fragen, statt einen 404 zu erzeugen und zu verstecken.
    enabled: !!config?.storage_enabled,
  })

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

  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/requests/${id}/cancel`),
    onSuccess: () => setCancelling(null),
    onSettled: refresh,
  })

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

      {quotaQuery.data && (
        <section>
          <h2 className="mb-2 text-sm font-semibold text-mist-300">{t('myRequests.quota')}</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <QuotaCard label={t('common.movies')} quota={quotaQuery.data.movie} />
            <QuotaCard label={t('common.series')} quota={quotaQuery.data.tv} />
            {storageQuery.data && <StorageCard daten={storageQuery.data} />}
          </div>
          <p className="mt-2 text-xs text-mist-600">
            {quotaQuery.data.auto_approve
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
              className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 p-3"
            >
              {/* Auf dem Telefon nimmt der Titel die ganze Zeile ein, Etikett
                  und Knopf rutschen darunter. Ohne das w-full teilen sich alle
                  drei eine Zeile: flex-1 hat die Grundbreite 0, wehrt sich also
                  nicht gegen das Schrumpfen - der Titel schnurrt dann auf "P."
                  zusammen, waehrend Etikett und Knopf ihre volle Breite
                  behalten. Lieber umbrechen als kuerzen. */}
              <div className="w-full min-w-0 sm:w-auto sm:flex-1">
                <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="min-w-0 font-semibold break-words">{request.title}</span>
                  {request.season !== null && (
                    <span className="shrink-0 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs font-medium text-mist-400">
                      {t('request.seasonShort', { number: request.season })}
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
                  <p className="mt-1 text-xs text-bad-500">{request.error_message}</p>
                )}
                {request.rejection_reason && (
                  <p className="mt-1 text-xs text-mist-500">{request.rejection_reason}</p>
                )}
              </div>

              <StatusBadge status={request.status} />

              {request.status === 'pending_approval' && (
                <Button
                  variant="ghost"
                  onClick={() => withdrawMutation.mutate(request.id)}
                  loading={withdrawMutation.isPending && withdrawMutation.variables === request.id}
                >
                  {t('myRequests.withdraw')}
                </Button>
              )}

              {/* Hängt seit Tagen in der Warteschlange? Abbrechen gibt den
                  Platz im eigenen Kontingent wieder frei. */}
              {(request.status === 'searching' || request.status === 'requested') && (
                <Button
                  variant="ghost"
                  onClick={() => setCancelling(request)}
                  loading={cancelMutation.isPending && cancelMutation.variables === request.id}
                >
                  {t('requests.cancel')}
                </Button>
              )}

              {/* Administratoren bewerten nicht - sie beantworten die
                  Rückmeldungen der anderen. */}
              {request.status === 'downloaded' && !istAdmin && (
                <FeedbackBlock request={request} onSaved={refresh} />
              )}
            </div>
          ))}
        </Card>
      )}

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
        confirmLabel={t('requests.cancelConfirm')}
        loading={cancelMutation.isPending}
        onCancel={() => setCancelling(null)}
        onConfirm={() => cancelling && cancelMutation.mutate(cancelling.id)}
      />
    </div>
  )
}
