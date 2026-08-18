import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { TicketDetail, TicketMessage, TicketStatus } from '../api/tickets'
import { TICKET_STATUSES } from '../api/tickets'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { TicketStatusBadge } from '../components/TicketStatusBadge'
import { Button, Card, ErrorBanner, Spinner } from '../components/ui'
import { formatDateTime } from '../lib/format'

/** Der Verlauf eines Tickets. */
export function TicketPage() {
  const { t, i18n } = useTranslation()
  const { ticketId } = useParams()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const istAdmin = user?.role === 'admin'

  const [antwort, setAntwort] = useState('')
  /** Welche Nachricht wird gerade nachgebessert? */
  const [bearbeite, setBearbeite] = useState<number | null>(null)
  const [entwurf, setEntwurf] = useState('')

  const ticketQuery = useQuery({
    queryKey: ['ticket', ticketId],
    queryFn: () => api.get<TicketDetail>(`/api/tickets/${ticketId}`),
  })

  function aktualisieren(neu: TicketDetail) {
    queryClient.setQueryData(['ticket', ticketId], neu)
    void queryClient.invalidateQueries({ queryKey: ['tickets'] })
    void queryClient.invalidateQueries({ queryKey: ['tickets-open'] })
    void queryClient.invalidateQueries({ queryKey: ['notifications'] })
  }

  const senden = useMutation({
    mutationFn: () =>
      api.post<TicketDetail>(`/api/tickets/${ticketId}/messages`, { body: antwort.trim() }),
    onSuccess: (neu) => {
      setAntwort('')
      aktualisieren(neu)
    },
  })

  const speichern = useMutation({
    mutationFn: (id: number) =>
      api.patch<TicketDetail>(`/api/tickets/messages/${id}`, { body: entwurf.trim() }),
    onSuccess: (neu) => {
      setBearbeite(null)
      setEntwurf('')
      aktualisieren(neu)
    },
  })

  const statusSetzen = useMutation({
    mutationFn: (status: TicketStatus) =>
      api.patch<TicketDetail>(`/api/tickets/${ticketId}`, { status }),
    onSuccess: aktualisieren,
  })

  if (ticketQuery.isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  }

  if (ticketQuery.error || !ticketQuery.data) {
    return (
      <div className="flex flex-col gap-4">
        <ErrorBanner message={t('tickets.notFound')} />
        <Link to="/tickets" className="text-sm text-accent-400 hover:underline">
          {t('tickets.backToList')}
        </Link>
      </div>
    )
  }

  const ticket = ticketQuery.data
  const geschlossen = ticket.status === 'closed'

  function beginneBearbeiten(nachricht: TicketMessage) {
    setBearbeite(nachricht.id)
    setEntwurf(nachricht.body)
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-3">
        <Link to="/tickets" className="text-sm text-mist-500 hover:text-mist-100">
          ← {t('tickets.backToList')}
        </Link>

        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{ticket.subject}</h1>
            {/* Wer eröffnet hat, steht an der ersten Nachricht - nicht am
                Ticket. Schreibt der Administrator jemanden an, gehört das
                Ticket dem Empfänger, verfasst hat es aber der Administrator.
                Deshalb wird zusätzlich gesagt, mit wem es geht. */}
            <p className="mt-1 text-sm text-mist-600">
              {t('tickets.openedBy', {
                name: ticket.opened_by_name ?? ticket.display_name ?? ticket.username,
                date: formatDateTime(ticket.created_at, i18n.language),
              })}
              {ticket.opened_by !== null && ticket.opened_by !== ticket.user_id && (
                <>
                  {' · '}
                  {t('tickets.withUser', {
                    name: ticket.display_name ?? ticket.username,
                  })}
                </>
              )}
            </p>
            {ticket.media_title && ticket.tmdb_id && (
              <Link
                to={`/titel/${ticket.media_type}/${ticket.tmdb_id}`}
                className="mt-1 inline-block text-sm text-accent-400 hover:underline"
              >
                {t('tickets.about', { title: ticket.media_title })}
              </Link>
            )}
          </div>

          {/* Den Zustand setzt ausschließlich der Administrator - der Server
              lehnt es für alle anderen ohnehin ab. */}
          {istAdmin ? (
            <select
              value={ticket.status}
              onChange={(event) => statusSetzen.mutate(event.target.value as TicketStatus)}
              disabled={statusSetzen.isPending}
              aria-label={t('tickets.status')}
              className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:opacity-50"
            >
              {TICKET_STATUSES.map((wert) => (
                <option key={wert} value={wert}>
                  {t(`ticketStatus.${wert}`)}
                </option>
              ))}
            </select>
          ) : (
            <TicketStatusBadge status={ticket.status} />
          )}
        </div>
      </header>

      <ul className="flex flex-col gap-3">
        {ticket.messages.map((nachricht) => {
          const vonMir = nachricht.user_id === user?.id
          return (
            <li key={nachricht.id} className={'flex gap-3 ' + (vonMir ? 'flex-row-reverse' : '')}>
              <Avatar
                url={nachricht.avatar_url}
                name={nachricht.display_name ?? nachricht.username ?? '?'}
                className="h-9 w-9 shrink-0"
              />

              <div
                className={
                  'min-w-0 max-w-[46rem] flex-1 rounded-2xl border px-4 py-3 ' +
                  (vonMir
                    ? 'border-accent-500/30 bg-accent-500/10'
                    : 'border-ink-700 bg-ink-900/60')
                }
              >
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-sm font-medium text-mist-100">
                    {nachricht.display_name ?? nachricht.username ?? t('tickets.deletedAccount')}
                  </span>
                  <span className="text-xs text-mist-600">
                    {formatDateTime(nachricht.created_at, i18n.language)}
                  </span>
                  {/* Nachgebessert wird sichtbar - sonst ließe sich der
                      Verlauf still umschreiben. */}
                  {nachricht.edited_at && (
                    <span className="text-xs text-mist-600 italic">
                      {t('tickets.edited', {
                        date: formatDateTime(nachricht.edited_at, i18n.language),
                      })}
                    </span>
                  )}
                </div>

                {bearbeite === nachricht.id ? (
                  <div className="mt-2 flex flex-col gap-2">
                    <textarea
                      value={entwurf}
                      onChange={(event) => setEntwurf(event.target.value)}
                      rows={4}
                      maxLength={5000}
                      className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                    />
                    <div className="flex flex-wrap gap-2">
                      <Button
                        onClick={() => speichern.mutate(nachricht.id)}
                        loading={speichern.isPending}
                        disabled={!entwurf.trim()}
                      >
                        {t('common.save')}
                      </Button>
                      <Button variant="ghost" onClick={() => setBearbeite(null)}>
                        {t('common.cancel')}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    {/* whitespace-pre-wrap: Absätze bleiben erhalten, ohne
                        dass irgendetwas als HTML ausgelegt wird. */}
                    <p className="mt-1 text-sm whitespace-pre-wrap text-mist-300">
                      {nachricht.body}
                    </p>
                    {vonMir && (
                      <button
                        type="button"
                        onClick={() => beginneBearbeiten(nachricht)}
                        className="mt-2 text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
                      >
                        {t('common.edit')}
                      </button>
                    )}
                  </>
                )}
              </div>
            </li>
          )
        })}
      </ul>

      {/* Geschlossen heißt für den Eigentümer zu: kein Antwortfeld mehr.
          Der Verlauf bleibt sichtbar. Der Administrator darf weiterhin einen
          Nachtrag hinterlassen, ohne das Ticket dafür aufmachen zu müssen. */}
      {geschlossen && !istAdmin ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-8 text-center text-sm text-mist-500">
          {t('tickets.closedNoReply')}
        </p>
      ) : (
        <Card className="flex flex-col gap-2">
          {geschlossen && <p className="text-sm text-mist-500">{t('tickets.closedHintAdmin')}</p>}

          <textarea
            value={antwort}
            onChange={(event) => setAntwort(event.target.value)}
            rows={4}
            maxLength={5000}
            placeholder={t('tickets.replyPlaceholder')}
            aria-label={t('tickets.replyPlaceholder')}
            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />

          {senden.error && (
            <ErrorBanner
              message={
                senden.error instanceof ApiError ? senden.error.message : t('errors.generic')
              }
            />
          )}

          <div>
            <Button
              onClick={() => senden.mutate()}
              loading={senden.isPending}
              disabled={!antwort.trim()}
            >
              {t('tickets.send')}
            </Button>
          </div>
        </Card>
      )}
    </div>
  )
}
