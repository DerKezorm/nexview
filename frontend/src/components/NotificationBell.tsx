import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { AppNotification } from '../api/types'
import { folgenKompakt, formatDate } from '../lib/format'

/** Wie oft im Hintergrund nach Neuem geschaut wird. */
const POLL_MS = 60_000

export function NotificationBell() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const countQuery = useQuery({
    queryKey: ['notifications', 'count'],
    queryFn: () => api.get<{ unread: number }>('/api/notifications/unread/count'),
    refetchInterval: POLL_MS,
  })

  const listQuery = useQuery({
    queryKey: ['notifications', 'list'],
    queryFn: () => api.get<AppNotification[]>('/api/notifications?limit=20'),
    enabled: open,
  })

  const readAllMutation = useMutation({
    mutationFn: () => api.post<void>('/api/notifications/read-all'),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  })

  const clearMutation = useMutation({
    mutationFn: () => api.delete<void>('/api/notifications'),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  })

  /** Wohin eine Benachrichtigung führt. */
  function zielFuer(item: AppNotification): string {
    // Rückmeldungen landen direkt im passenden Filter - sonst käme man auf
    // "wartet auf Freigabe" heraus und müsste erst suchen, worum es ging.
    if (item.type === 'feedback' || item.type === 'feedback_poor') {
      return '/admin/requests?filter=feedback'
    }
    if (item.type === 'request_pending') return '/admin/requests'
    // Tickets führen direkt in den Verlauf - die Liste allein hülfe nicht
    // weiter, wenn mehrere offen sind.
    if (item.type === 'ticket_new' || item.type === 'ticket_reply') {
      return item.ticket_id ? `/tickets/${item.ticket_id}` : '/tickets'
    }
    // Ein neues Konto will man ansehen, nicht suchen.
    if (item.type === 'user_imported') return '/admin/settings'
    // Der abgelaufene Zugang wird auf der Profilseite erneuert - dort sitzt
    // die einmalige Plex-Anmeldung.
    if (item.type === 'mediaserver_reconnect') return '/profil'
    // Ein Kinderwunsch wird unter „Kinder" entschieden - nicht in den eigenen
    // Anfragen. Dort läge er erst, wenn er freigegeben ist.
    if (item.type === 'child_wish') return '/profil?reiter=kinder'
    // Alles andere betrifft die eigenen Anfragen.
    return '/requests'
  }

  function oeffnen(item: AppNotification) {
    setOpen(false)
    if (!item.is_read) {
      void api.post(`/api/notifications/${item.id}/read`).then(() => {
        void queryClient.invalidateQueries({ queryKey: ['notifications'] })
      })
    }
    navigate(zielFuer(item))
  }

  // Klick außerhalb schließt die Liste.
  useEffect(() => {
    if (!open) return
    function onClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  const unread = countQuery.data?.unread ?? 0
  const items = listQuery.data ?? []

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-label={t('notifications.title')}
        aria-expanded={open}
        className="relative rounded-full border border-ink-700 p-2 text-mist-300 transition-colors hover:border-accent-600 hover:text-accent-400"
      >
        <svg
          viewBox="0 0 20 20"
          className="h-4 w-4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          aria-hidden="true"
        >
          <path
            d="M6 8a4 4 0 1 1 8 0c0 3 1 4.5 1.5 5H4.5C5 12.5 6 11 6 8Z"
            strokeLinejoin="round"
          />
          <path d="M8.5 16a1.5 1.5 0 0 0 3 0" strokeLinecap="round" />
        </svg>
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent-500 px-1 text-[10px] font-bold text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        /* Auf dem Smartphone als Leiste über die volle Breite - als Aufklapp-
           Fenster unter der Glocke würde sie seitlich aus dem Bild ragen. */
        <div className="fixed inset-x-3 top-16 z-30 overflow-hidden rounded-xl border border-ink-700 bg-ink-850 shadow-2xl shadow-black/60 sm:absolute sm:inset-auto sm:top-auto sm:right-0 sm:mt-2 sm:w-80">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-700 px-4 py-2.5">
            <span className="text-sm font-semibold">{t('notifications.title')}</span>
            <div className="ml-auto flex items-center gap-3">
              {unread > 0 && (
                <button
                  type="button"
                  onClick={() => readAllMutation.mutate()}
                  className="text-xs text-mist-500 transition-colors hover:text-accent-400"
                >
                  {t('notifications.markAllRead')}
                </button>
              )}
              {items.length > 0 && (
                <button
                  type="button"
                  onClick={() => clearMutation.mutate()}
                  className="text-xs text-mist-500 transition-colors hover:text-accent-400"
                >
                  {t('notifications.clear')}
                </button>
              )}
            </div>
          </div>

          <div className="max-h-80 overflow-y-auto">
            {items.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-mist-600">
                {t('notifications.empty')}
              </p>
            ) : (
              items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => oeffnen(item)}
                  className={
                    'block w-full border-b border-ink-700/60 px-4 py-3 text-left transition-colors ' +
                    'last:border-b-0 hover:bg-ink-800 ' +
                    (item.is_read ? '' : 'bg-accent-500/5')
                  }
                >
                  <p className="text-sm">
                    {!item.is_read && (
                      <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-accent-500 align-middle" />
                    )}
                    {t(item.message_key)}
                  </p>
                  {item.message_title && (
                    <p className="truncate text-sm font-semibold text-mist-300">
                      {item.message_title}
                      {/* ⚠️ Ohne die Staffel sind fünf Meldungen zu einer Serie
                          fünfmal derselbe Text – und keine sagt, worum es geht. */}
                      {item.season !== null && item.season !== undefined && (
                        <span className="ml-1.5 font-normal text-mist-500">
                          {t('storage.season', { number: item.season })}
                          {item.episodes && item.episodes.length > 0 && (
                            <>
                              {' · '}
                              {t('request.episodesShort', {
                                list: folgenKompakt(item.episodes),
                              })}
                            </>
                          )}
                        </span>
                      )}
                    </p>
                  )}
                  <p className="mt-0.5 text-xs text-mist-600">
                    {formatDate(item.created_at.slice(0, 10), i18n.language)}
                  </p>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
