import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { Ticket, TicketDetail, TicketStatus } from '../api/tickets'
import type { User } from '../api/types'
import type { MediaType } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { TicketStatusBadge } from '../components/TicketStatusBadge'
import { Button, Card, ErrorBanner, Field, Spinner } from '../components/ui'
import { formatDate } from '../lib/format'

type Filter = TicketStatus | 'all'

const FILTERS: Filter[] = ['all', 'open', 'in_progress', 'closed']

/**
 * Übersicht der Tickets.
 *
 * Dieselbe Seite für alle: Der Administrator sieht die Tickets aller Benutzer,
 * jeder andere nur seine eigenen. Was sichtbar ist, entscheidet allein der
 * Server - hier steht keine zweite Regel, die davon abweichen könnte.
 */
export function TicketsPage() {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const istAdmin = user?.role === 'admin'

  const [suche, setSuche] = useSearchParams()
  const [filter, setFilter] = useState<Filter>('all')
  // Aus „Problem melden" kommt ein vorbelegter Titelbezug in der Adresse.
  const bezug = {
    media_type: suche.get('media_type') as MediaType | null,
    tmdb_id: suche.get('tmdb_id'),
    media_title: suche.get('title'),
  }
  const [formularOffen, setFormularOffen] = useState(Boolean(bezug.tmdb_id))
  const [betreff, setBetreff] = useState(
    bezug.media_title ? t('tickets.subjectAbout', { title: bezug.media_title }) : '',
  )
  const [text, setText] = useState('')
  /** Wen der Administrator anschreibt. Leer = eigenes Anliegen. */
  const [empfaenger, setEmpfaenger] = useState('')
  /** Ausgewählte geschlossene Tickets - nur der Administrator sieht das. */
  const [ausgewaehlt, setAusgewaehlt] = useState<number[]>([])
  const [loeschAbfrage, setLoeschAbfrage] = useState(false)

  // Für die Empfängerauswahl. Nur Administratoren dürfen die Liste sehen -
  // deshalb wird sie für alle anderen gar nicht erst geholt.
  const benutzer = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/api/users'),
    enabled: istAdmin,
  })

  const liste = useQuery({
    queryKey: ['tickets', filter],
    queryFn: () =>
      api.get<Ticket[]>(filter === 'all' ? '/api/tickets' : `/api/tickets?status=${filter}`),
  })

  const anlegen = useMutation({
    mutationFn: () =>
      api.post<TicketDetail>('/api/tickets', {
        subject: betreff.trim(),
        body: text.trim(),
        media_type: bezug.media_type ?? undefined,
        tmdb_id: bezug.tmdb_id ? Number(bezug.tmdb_id) : undefined,
        media_title: bezug.media_title ?? undefined,
        user_id: empfaenger ? Number(empfaenger) : undefined,
      }),
    onSuccess: (angelegt) => {
      setBetreff('')
      setText('')
      setEmpfaenger('')
      setFormularOffen(false)
      // Der Titelbezug hat seinen Zweck erfüllt - sonst hinge er beim nächsten
      // Ticket noch daran.
      if (suche.has('tmdb_id')) setSuche({}, { replace: true })
      void queryClient.invalidateQueries({ queryKey: ['tickets'] })
      void queryClient.invalidateQueries({ queryKey: ['tickets-open'] })
      navigate(`/tickets/${angelegt.id}`)
    },
  })

  const loeschen = useMutation({
    mutationFn: () =>
      api.post<{ deleted: number }>('/api/tickets/delete', {
        ticket_ids: ausgewaehlt,
      }),
    onSuccess: () => {
      setAusgewaehlt([])
      setLoeschAbfrage(false)
      void queryClient.invalidateQueries({ queryKey: ['tickets'] })
      void queryClient.invalidateQueries({ queryKey: ['tickets-open'] })
    },
  })

  const tickets = liste.data ?? []
  // Löschen gibt es nur für geschlossene Tickets - ein offenes wegzuräumen
  // hieße, jemandem mitten im Gespräch das Wort abzuschneiden.
  const loeschbar = tickets.filter((eintrag) => eintrag.status === 'closed')

  function umschalten(id: number) {
    setAusgewaehlt((jetzt) =>
      jetzt.includes(id) ? jetzt.filter((eintrag) => eintrag !== id) : [...jetzt, id],
    )
  }
  const kannAbschicken = betreff.trim().length > 0 && text.trim().length > 0

  function absenden(event: FormEvent) {
    event.preventDefault()
    if (kannAbschicken) anlegen.mutate()
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            {t('tickets.title')}
            <span className="text-accent-500">.</span>
          </h1>
          <p className="mt-1.5 text-mist-500">
            {t(istAdmin ? 'tickets.introAdmin' : 'tickets.intro')}
          </p>
        </div>
        {!formularOffen && (
          <Button onClick={() => setFormularOffen(true)}>{t('tickets.new')}</Button>
        )}
      </header>

      {formularOffen && (
        <Card>
          <form onSubmit={absenden} className="flex flex-col gap-3">
            <h2 className="text-lg font-semibold">{t('tickets.new')}</h2>

            {bezug.media_title && (
              <p className="rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2 text-sm text-mist-300">
                {t('tickets.about', { title: bezug.media_title })}
              </p>
            )}

            {/* Nur der Administrator kann jemanden anschreiben. Für alle
                anderen gibt es nur die eine Richtung - zu ihm. */}
            {istAdmin && (
              <label className="flex max-w-md flex-col gap-1.5">
                <span className="text-sm font-medium text-mist-300">{t('tickets.recipient')}</span>
                <select
                  value={empfaenger}
                  onChange={(event) => setEmpfaenger(event.target.value)}
                  className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                >
                  <option value="">{t('tickets.recipientSelf')}</option>
                  {(benutzer.data ?? [])
                    .filter((eintrag) => eintrag.id !== user?.id && eintrag.is_active)
                    .map((eintrag) => (
                      <option key={eintrag.id} value={eintrag.id}>
                        {eintrag.display_name ?? eintrag.username}
                      </option>
                    ))}
                </select>
                <span className="text-xs leading-relaxed text-mist-600">
                  {t('tickets.recipientHint')}
                </span>
              </label>
            )}

            <Field
              label={t('tickets.subject')}
              value={betreff}
              maxLength={200}
              onChange={(event) => setBetreff(event.target.value)}
              placeholder={t('tickets.subjectPlaceholder')}
            />

            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-mist-300">{t('tickets.message')}</span>
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                rows={5}
                maxLength={5000}
                placeholder={t('tickets.messagePlaceholder')}
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
              />
            </label>

            {anlegen.error && (
              <ErrorBanner
                message={
                  anlegen.error instanceof ApiError ? anlegen.error.message : t('errors.generic')
                }
              />
            )}

            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" loading={anlegen.isPending} disabled={!kannAbschicken}>
                {t('tickets.send')}
              </Button>
              <Button variant="ghost" type="button" onClick={() => setFormularOffen(false)}>
                {t('common.cancel')}
              </Button>
            </div>
          </form>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((wert) => (
          <button
            key={wert}
            type="button"
            onClick={() => setFilter(wert)}
            className={
              'rounded-full border px-3 py-1.5 text-sm transition-colors ' +
              (filter === wert
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {wert === 'all' ? t('adminRequests.filterAll') : t(`ticketStatus.${wert}`)}
          </button>
        ))}
      </div>

      {/* Aufräumleiste - nur für den Administrator und nur, wenn es überhaupt
          etwas Geschlossenes gibt. */}
      {istAdmin && loeschbar.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-mist-300">
            <input
              type="checkbox"
              checked={ausgewaehlt.length === loeschbar.length}
              onChange={(event) =>
                setAusgewaehlt(event.target.checked ? loeschbar.map((e) => e.id) : [])
              }
              className="h-4 w-4 accent-accent-500"
            />
            {t('tickets.selectAllClosed', { count: loeschbar.length })}
          </label>

          {ausgewaehlt.length > 0 && (
            <>
              <Button variant="ghost" onClick={() => setLoeschAbfrage(true)}>
                {t('tickets.deleteSelected', { count: ausgewaehlt.length })}
              </Button>
              <button
                type="button"
                onClick={() => setAusgewaehlt([])}
                className="text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
              >
                {t('common.cancel')}
              </button>
            </>
          )}
        </div>
      )}

      <ConfirmDialog
        open={loeschAbfrage}
        title={t('tickets.deleteTitle')}
        description={t('tickets.deleteConfirm', { count: ausgewaehlt.length })}
        warning={t('tickets.deleteWarning')}
        confirmLabel={t('tickets.deleteAction')}
        onConfirm={() => loeschen.mutate()}
        onCancel={() => setLoeschAbfrage(false)}
        loading={loeschen.isPending}
      />

      {liste.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-mist-500">
          <Spinner />
          {t('common.loading')}
        </p>
      ) : tickets.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-16 text-center text-sm text-mist-500">
          {t('tickets.empty')}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {tickets.map((ticket) => {
            // „Wartet auf dich": zuletzt hat die jeweils andere Seite
            // geschrieben. Ohne diesen Hinweis muss man jedes Ticket öffnen,
            // nur um zu sehen, wer am Zug ist.
            const amZug =
              ticket.status !== 'closed' &&
              ticket.last_reply_by !== null &&
              ticket.last_reply_by !== user?.id

            const waehlbar = istAdmin && ticket.status === 'closed'

            return (
              <li key={ticket.id} className="flex items-center gap-2">
                {/* Der Haken steht außerhalb des Links - er darf nicht ins
                    Ticket springen, wenn man nur auswählen will. Nur bei
                    geschlossenen, damit gar nicht erst der Eindruck entsteht,
                    man könne ein laufendes Gespräch wegräumen. */}
                {waehlbar && (
                  <input
                    type="checkbox"
                    checked={ausgewaehlt.includes(ticket.id)}
                    onChange={() => umschalten(ticket.id)}
                    aria-label={t('tickets.select', { subject: ticket.subject })}
                    className="h-4 w-4 shrink-0 accent-accent-500"
                  />
                )}

                <Link
                  to={`/tickets/${ticket.id}`}
                  className="flex flex-1 items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 transition-colors hover:border-ink-600"
                >
                  {istAdmin && (
                    <Avatar
                      url={ticket.avatar_url}
                      name={ticket.display_name ?? ticket.username}
                      className="h-9 w-9 shrink-0"
                    />
                  )}

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-mist-100">{ticket.subject}</span>
                      {amZug && (
                        <span className="rounded-full bg-accent-500/20 px-2 py-0.5 text-[11px] font-semibold text-accent-400">
                          {t('tickets.waitingForYou')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-mist-600">
                      {istAdmin && `${ticket.display_name ?? ticket.username} · `}
                      {t('tickets.messages', { count: ticket.message_count })} ·{' '}
                      {formatDate(ticket.updated_at.slice(0, 10), i18n.language)}
                      {ticket.media_title && ` · ${ticket.media_title}`}
                    </p>
                  </div>

                  <TicketStatusBadge status={ticket.status} />
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
