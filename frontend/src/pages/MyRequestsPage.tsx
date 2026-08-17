import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { MediaRequest, QuotaInfo, QuotaOverview } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { StarRating } from '../components/StarRating'
import { StatusBadge } from '../components/media/StatusBadge'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDate } from '../lib/format'

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

type Filter = 'all' | MediaRequest['status']

/** Reihenfolge der Filterknöpfe. Zustände ohne Einträge werden ausgeblendet. */
const FILTERS: Filter[] = [
  'all',
  'pending_approval',
  'searching',
  'downloaded',
  'rejected',
  'cancelled',
  'failed',
]

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

  const quotaQuery = useQuery({
    queryKey: ['quota'],
    queryFn: () => api.get<QuotaOverview>('/api/requests/quota'),
  })

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['my-requests'] })
    void queryClient.invalidateQueries({ queryKey: ['quota'] })
    void queryClient.invalidateQueries({ queryKey: ['discover'] })
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
  const vorhanden = FILTERS.filter(
    (wert) => wert === 'all' || alle.some((eintrag) => eintrag.status === wert),
  )
  const requests = filter === 'all' ? alle : alle.filter((e) => e.status === filter)

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
          <div className="grid gap-3 sm:grid-cols-2">
            <QuotaCard label={t('common.movies')} quota={quotaQuery.data.movie} />
            <QuotaCard label={t('common.series')} quota={quotaQuery.data.tv} />
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
              {wert === 'all' ? t('adminRequests.filterAll') : t(`status.${wert}`)}
              <span className="ml-1.5 text-xs tabular-nums opacity-70">
                {wert === 'all' ? alle.length : alle.filter((e) => e.status === wert).length}
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
          {requests.map((request) => (
            <div
              key={request.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/50 p-3"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold">{request.title}</p>
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
