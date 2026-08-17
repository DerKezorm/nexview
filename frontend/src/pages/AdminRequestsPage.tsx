import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { MediaRequestWithUser, MediaStatus } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { StarRating } from '../components/StarRating'
import { StatusBadge } from '../components/media/StatusBadge'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDate } from '../lib/format'

type Filter = 'pending_approval' | 'all' | 'feedback' | MediaStatus

const FILTERS: Filter[] = [
  'pending_approval',
  'feedback',
  'all',
  'searching',
  'downloaded',
  'rejected',
  'cancelled',
  'failed',
]

/** Adresse für den gewählten Filter. */
function urlFuer(filter: Filter): string {
  if (filter === 'all') return '/api/admin/requests'
  if (filter === 'feedback') return '/api/admin/requests?feedback=true'
  return `/api/admin/requests?status=${filter}`
}

/** Zustände, in denen ein Abbruch möglich ist (Titel liegt in Radarr/Sonarr). */
const CANCELLABLE: ReadonlySet<string> = new Set(['approved', 'searching'])

type Gruppe = {
  userId: number
  username: string
  displayName: string | null
  avatar: string | null
  requests: MediaRequestWithUser[]
}

/** Ab hier (und darunter) gilt eine Rückmeldung als Beschwerde. */
const POOR_RATING = 2

/** Rückmeldung des Anfragenden lesen und beantworten. */
function FeedbackReview({
  request,
  darfAntworten,
  onSaved,
}: {
  request: MediaRequestWithUser
  /** Antworten ist dem Administrator vorbehalten. */
  darfAntworten: boolean
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [text, setText] = useState(request.feedback_reply ?? '')
  const [offen, setOffen] = useState(false)

  const antworten = useMutation({
    mutationFn: () => api.post(`/api/admin/requests/${request.id}/reply`, { reply: text.trim() }),
    onSuccess: () => {
      setOffen(false)
      onSaved()
    },
  })

  return (
    <div className="mt-3 border-t border-ink-700 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <StarRating value={request.rating ?? 0} size="sm" />
        <span className="text-xs text-mist-600">
          {t('feedback.from', { name: request.display_name ?? request.username })}
        </span>
      </div>
      {request.feedback && <p className="mt-1 text-sm text-mist-300">{request.feedback}</p>}

      {request.feedback_reply && !offen && (
        <div className="mt-2 rounded-xl border border-ink-700 bg-ink-850/60 px-3 py-2">
          <p className="text-xs font-medium text-accent-500">{t('feedback.replyTitle')}</p>
          <p className="mt-0.5 text-sm text-mist-300">{request.feedback_reply}</p>
        </div>
      )}

      {!darfAntworten ? null : offen ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={1000}
            placeholder={t('feedback.replyPlaceholder')}
            aria-label={t('feedback.replyPlaceholder')}
            className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
          <Button
            onClick={() => antworten.mutate()}
            loading={antworten.isPending}
            disabled={!text.trim()}
          >
            {t('feedback.sendReply')}
          </Button>
          <Button variant="ghost" onClick={() => setOffen(false)}>
            {t('common.cancel')}
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOffen(true)}
          className="mt-2 text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
        >
          {request.feedback_reply ? t('feedback.changeReply') : t('feedback.reply')}
        </button>
      )}

      {antworten.isError && (
        <p className="mt-1 text-xs text-bad-500">
          {antworten.error instanceof ApiError ? antworten.error.message : t('feedback.failed')}
        </p>
      )}
    </div>
  )
}

export function AdminRequestsPage() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  // Antworten auf Rückmeldungen ist dem Administrator vorbehalten.
  const istAdmin = user?.role === 'admin'

  // Aus der Adresse vorbelegen, damit ein Klick in der Glocke direkt in der
  // richtigen Ansicht landet.
  const [suche, setSuche] = useSearchParams()
  const [filter, setFilterState] = useState<Filter>(() =>
    suche.get('filter') === 'feedback' ? 'feedback' : 'pending_approval',
  )

  function setFilter(wert: Filter) {
    setFilterState(wert)
    // Der Parameter hat seinen Zweck erfüllt - sonst spränge ein Neuladen
    // wieder zurück.
    if (suche.has('filter')) setSuche({}, { replace: true })
  }
  const [rejecting, setRejecting] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  const [cancelling, setCancelling] = useState<MediaRequestWithUser | null>(null)

  const requestsQuery = useQuery({
    queryKey: ['admin-requests', filter],
    queryFn: () => api.get<MediaRequestWithUser[]>(urlFuer(filter)),
  })

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['admin-requests'] })
    void queryClient.invalidateQueries({ queryKey: ['pending-count'] })
    void queryClient.invalidateQueries({ queryKey: ['discover'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }

  const approveMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/admin/requests/${id}/approve`),
    onSettled: refresh,
  })

  const approveAllMutation = useMutation({
    mutationFn: (userId: number) => api.post(`/api/admin/requests/approve-all/${userId}`),
    onSettled: refresh,
  })

  const rejectMutation = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) =>
      api.post(`/api/admin/requests/${id}/reject`, { reason: text || undefined }),
    onSuccess: () => {
      setRejecting(null)
      setReason('')
      refresh()
    },
  })

  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.post(`/api/admin/requests/${id}/cancel`),
    onSuccess: () => setCancelling(null),
    onSettled: refresh,
  })

  const requests = requestsQuery.data ?? []
  const failure =
    approveMutation.error ?? rejectMutation.error ?? cancelMutation.error ?? approveAllMutation.error

  // Nach Benutzer gruppieren, damit man nicht jeden Titel einzeln freigeben muss.
  const gruppen: Gruppe[] = []
  for (const request of requests) {
    let gruppe = gruppen.find((eintrag) => eintrag.userId === request.user_id)
    if (!gruppe) {
      gruppe = {
        userId: request.user_id,
        username: request.username,
        displayName: request.display_name,
        avatar: request.avatar_url,
        requests: [],
      }
      gruppen.push(gruppe)
    }
    gruppe.requests.push(request)
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {t('nav.allRequests')}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-1.5 text-mist-500">{t('adminRequests.intro')}</p>
        </div>
        <Link
          to="/admin/stats"
          className="rounded-full border border-ink-700 px-4 py-2 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100"
        >
          {t('nav.stats')}
        </Link>
      </header>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setFilter(value)}
            aria-pressed={filter === value}
            className={
              'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
              (filter === value
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {value === 'all'
              ? t('adminRequests.filterAll')
              : value === 'feedback'
                ? t('adminRequests.filterFeedback')
                : t(`status.${value}`)}
          </button>
        ))}
      </div>

      {failure && (
        <ErrorBanner
          message={failure instanceof ApiError ? failure.message : t('errors.generic')}
        />
      )}

      {requestsQuery.isPending && (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner /> {t('common.loading')}
        </p>
      )}

      {!requestsQuery.isPending && requests.length === 0 && (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {filter === 'pending_approval'
            ? t('adminRequests.noPending')
            : filter === 'feedback'
              ? t('adminRequests.emptyFeedback')
              : t('adminRequests.empty')}
        </p>
      )}

      {gruppen.map((gruppe) => {
        const offene = gruppe.requests.filter((r) => r.status === 'pending_approval')
        return (
          <Card key={gruppe.username} className="flex flex-col gap-3 p-4">
            <div className="flex flex-wrap items-center gap-3 border-b border-ink-700 pb-3">
              <Avatar
                url={gruppe.avatar}
                name={gruppe.displayName ?? gruppe.username}
                className="h-10 w-10"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold">{gruppe.displayName ?? gruppe.username}</p>
                <p className="text-xs text-mist-600">
                  {t('adminRequests.countForUser', { count: gruppe.requests.length })}
                </p>
              </div>

              {offene.length > 1 && (
                <Button
                  onClick={() => approveAllMutation.mutate(gruppe.userId)}
                  loading={
                    approveAllMutation.isPending && approveAllMutation.variables === gruppe.userId
                  }
                >
                  {t('adminRequests.approveAll', { count: offene.length })}
                </Button>
              )}
            </div>

            {gruppe.requests.map((request) => (
              <div key={request.id} className="rounded-xl border border-ink-700 bg-ink-900/50 p-3">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="flex min-w-0 items-center gap-2">
                      <span className="truncate font-semibold">{request.title}</span>
                      {request.season !== null && (
                        <span className="shrink-0 rounded-full border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs font-medium text-mist-400">
                          {t('request.seasonShort', { number: request.season })}
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
                      <p className="mt-1 text-xs text-mist-500">
                        {t('adminRequests.reason')}: {request.rejection_reason}
                      </p>
                    )}
                  </div>

                  {/* Schwache Bewertungen sollen ohne Aufklappen auffallen. */}
                  {request.rating !== null && (
                    <span
                      title={t('feedback.stars', { count: request.rating })}
                      className={
                        'rounded-full border px-2 py-1 leading-none ' +
                        (request.rating <= POOR_RATING
                          ? 'border-bad-500/50 bg-bad-500/10'
                          : 'border-ink-700 bg-ink-850')
                      }
                    >
                      <StarRating value={request.rating} size="sm" />
                    </span>
                  )}

                  <StatusBadge status={request.status} />

                  {request.status === 'pending_approval' && (
                    <div className="flex gap-2">
                      <Button
                        onClick={() => approveMutation.mutate(request.id)}
                        loading={
                          approveMutation.isPending && approveMutation.variables === request.id
                        }
                      >
                        {t('adminRequests.approve')}
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setRejecting(rejecting === request.id ? null : request.id)
                          setReason('')
                        }}
                      >
                        {t('adminRequests.reject')}
                      </Button>
                    </div>
                  )}

                  {CANCELLABLE.has(request.status) && (
                    <Button
                      variant="ghost"
                      onClick={() => setCancelling(request)}
                      loading={cancelMutation.isPending && cancelMutation.variables === request.id}
                    >
                      {t('requests.cancel')}
                    </Button>
                  )}
                </div>

                {rejecting === request.id && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-ink-700 pt-3">
                    <input
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      placeholder={t('adminRequests.reasonPlaceholder')}
                      aria-label={t('adminRequests.reasonPlaceholder')}
                      className="min-w-0 flex-1 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                    />
                    <Button
                      onClick={() => rejectMutation.mutate({ id: request.id, text: reason })}
                      loading={rejectMutation.isPending}
                    >
                      {t('adminRequests.confirmReject')}
                    </Button>
                    <Button variant="ghost" onClick={() => setRejecting(null)}>
                      {t('common.cancel')}
                    </Button>
                  </div>
                )}

                {request.rating !== null && (
                  <FeedbackReview
                    request={request}
                    darfAntworten={istAdmin}
                    onSaved={refresh}
                  />
                )}
              </div>
            ))}
          </Card>
        )
      })}

      <ConfirmDialog
        open={cancelling !== null}
        title={t('requests.cancelTitle')}
        description={t('requests.cancelTextAdmin', {
          title: cancelling?.title ?? '',
          name: cancelling?.display_name ?? cancelling?.username ?? '',
        })}
        warning={t('requests.cancelWarning')}
        confirmLabel={t('requests.cancelConfirm')}
        loading={cancelMutation.isPending}
        onCancel={() => setCancelling(null)}
        onConfirm={() => cancelling && cancelMutation.mutate(cancelling.id)}
      />
    </div>
  )
}
