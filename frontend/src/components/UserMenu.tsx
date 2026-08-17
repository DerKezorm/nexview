import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, useLocation } from 'react-router-dom'

import { useFavorites } from './media/FavoriteButton'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { QuotaOverview, Role } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from './Avatar'

const ROLE_LABELS: Record<Role, string> = {
  admin: 'adminUsers.roleAdmin',
  approver: 'adminUsers.roleApprover',
  user: 'adminUsers.roleUser',
}

type MenuEntry = {
  to: string
  labelKey: string
  /** Nur Administratoren (Einstellungen, Benutzer, Protokoll). */
  adminOnly?: boolean
  /** Administratoren und Entscheider (Freigaben). */
  approverOnly?: boolean
  /** Nur zeigen, wenn diese Bedingung zutrifft. */
  wenn?: boolean
  badge?: number
}

/**
 * Aufklappmenü am eigenen Namen.
 *
 * Hier liegt alles, was den eigenen Zugang oder die Verwaltung betrifft -
 * das Hauptmenü bleibt dadurch auf das Entdecken beschränkt.
 */
export function UserMenu() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const location = useLocation()

  const isAdmin = user?.role === 'admin'
  // Entscheider dürfen freigeben - sie brauchen den Menüpunkt genauso.
  const canApprove = user?.can_approve ?? false

  const pendingQuery = useQuery({
    queryKey: ['pending-count'],
    queryFn: () => api.get<{ pending: number }>('/api/admin/requests/pending/count'),
    enabled: canApprove,
    refetchInterval: 60_000,
  })

  // Erst laden, wenn das Menü aufgeklappt wird - vorher sieht es ja niemand.
  const quotaQuery = useQuery({
    queryKey: ['quota'],
    queryFn: () => api.get<QuotaOverview>('/api/requests/quota'),
    enabled: open,
  })

  // Nach einem Seitenwechsel schließen.
  useEffect(() => setOpen(false), [location.pathname])

  useEffect(() => {
    if (!open) return
    function onClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const { favorites } = useFavorites()

  if (!user) return null

  const name = user.display_name ?? user.username
  const entries: MenuEntry[] = (
    [
      { to: '/profil', labelKey: 'nav.profile' },
      { to: '/requests', labelKey: 'nav.myRequests' },
      // Erscheint erst, wenn es etwas zu sehen gibt - ein leerer Punkt
      // waere nur ein Versprechen ohne Inhalt.
      { to: '/mag-ich', labelKey: 'nav.favorites', wenn: favorites.length > 0 },
      {
        to: '/admin/requests',
        labelKey: 'nav.allRequests',
        approverOnly: true,
        badge: pendingQuery.data?.pending,
      },
      { to: '/admin/stats', labelKey: 'nav.stats', approverOnly: true },
      { to: '/admin/settings', labelKey: 'nav.settings', adminOnly: true },
    ] as MenuEntry[]
  ).filter((entry) => {
    if (entry.adminOnly) return isAdmin
    if (entry.approverOnly) return canApprove
    if (entry.wenn !== undefined) return entry.wenn
    return true
  })

  const offene = canApprove ? (pendingQuery.data?.pending ?? 0) : 0

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex items-center gap-2 rounded-full border border-ink-700 py-1 pr-3 pl-1 transition-colors hover:border-accent-600"
      >
        <span className="relative">
          <Avatar url={user.avatar_url} name={name} />
          {offene > 0 && (
            <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-accent-500 ring-2 ring-ink-950" />
          )}
        </span>
        <span className="hidden max-w-32 truncate text-sm text-mist-300 sm:inline">{name}</span>
        <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 text-mist-500" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="m6 8 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-ink-700 bg-ink-850 py-1 shadow-2xl shadow-black/60"
        >
          <div className="border-b border-ink-700 px-4 py-3">
            <div className="flex items-center gap-3">
              <Avatar url={user.avatar_url} name={name} className="h-9 w-9" />
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{name}</p>
                <p className="truncate text-xs text-mist-600">{t(ROLE_LABELS[user.role])}</p>
              </div>
            </div>

            {/* Eigener Kontingent-Stand - so muss man dafür nicht erst auf
                "Meine Anfragen" wechseln. */}
            {quotaQuery.data && (
              <dl className="mt-3 grid grid-cols-2 gap-2 border-t border-ink-700/60 pt-3">
                {(['movie', 'tv'] as const).map((art) => {
                  const stand = quotaQuery.data[art]
                  return (
                    <div key={art}>
                      <dt className="text-[10px] font-medium tracking-wide text-mist-600 uppercase">
                        {t(art === 'movie' ? 'common.movies' : 'common.series')}
                      </dt>
                      <dd
                        className={
                          'text-sm ' + (stand.exhausted ? 'text-bad-500' : 'text-mist-300')
                        }
                      >
                        {stand.unlimited
                          ? t('myRequests.quotaUnlimited')
                          : `${stand.used} / ${stand.limit}`}
                      </dd>
                    </div>
                  )
                })}
              </dl>
            )}
          </div>

          {entries.map((entry) => (
            <NavLink
              key={entry.to}
              to={entry.to}
              role="menuitem"
              className={({ isActive }) =>
                'flex items-center gap-2 px-4 py-2.5 text-sm transition-colors ' +
                (isActive
                  ? 'bg-accent-500/15 text-accent-400'
                  : 'text-mist-300 hover:bg-ink-800 hover:text-mist-100')
              }
            >
              {t(entry.labelKey)}
              {entry.badge ? (
                <span className="ml-auto rounded-full bg-accent-500 px-1.5 text-[10px] font-bold text-white">
                  {entry.badge}
                </span>
              ) : null}
            </NavLink>
          ))}

          <button
            type="button"
            role="menuitem"
            onClick={logout}
            className="w-full border-t border-ink-700 px-4 py-2.5 text-left text-sm text-mist-300 transition-colors hover:bg-ink-800 hover:text-accent-400"
          >
            {t('nav.logout')}
          </button>
        </div>
      )}
    </div>
  )
}
