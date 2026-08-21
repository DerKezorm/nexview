import { NavLink, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { AboutInfo } from '../api/types'
import { LanguageSwitcher } from './LanguageSwitcher'
import { ThemeSwitcher } from './ThemeSwitcher'
import { LoadingBar } from './LoadingBar'
import { Logo } from './Logo'
import { NotificationBell } from './NotificationBell'
import { WatchlistExpiredBanner } from './WatchlistExpiredBanner'
import { UserMenu } from './UserMenu'

type NavItem = { to: string; labelKey: string }

/**
 * Hauptmenü - bewusst nur das Entdecken.
 *
 * Alles Persönliche und Verwaltende liegt im Benutzermenü oben rechts.
 */
const NAV_ITEMS: NavItem[] = [
  { to: '/filme', labelKey: 'nav.discoverMovies' },
  { to: '/serien', labelKey: 'nav.discoverSeries' },
  { to: '/personen', labelKey: 'nav.people' },
  { to: '/kalender', labelKey: 'nav.calendar' },
  { to: '/suche', labelKey: 'nav.search' },
]

/**
 * Fußzeile mit Version und Verweis auf die Über-Seite.
 *
 * Liegt ein Update bereit, steht das direkt hier - sonst müsste man die
 * Über-Seite von sich aus aufsuchen, und genau das tut niemand. Den Hinweis
 * bekommt nur, wer auch aktualisieren kann; das entscheidet der Server.
 */
function Footer() {
  const { t } = useTranslation()

  const { data } = useQuery({
    queryKey: ['about'],
    queryFn: () => api.get<AboutInfo>('/api/about'),
    staleTime: 60 * 60 * 1000,
  })

  return (
    <footer className="relative z-10 mt-8 border-t border-ink-700/60">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-4 gap-y-2 px-4 py-5 text-xs text-mist-600 sm:px-6">
        <NavLink to="/ueber" className="transition-colors hover:text-mist-300">
          {t('about.title')}
        </NavLink>

        {data && (
          <>
            <span aria-hidden="true">·</span>
            <span className="tabular-nums">v{data.version}</span>
          </>
        )}

        {data?.update_available && (
          <NavLink
            to="/ueber"
            className="inline-flex items-center gap-1.5 rounded-full bg-accent-500/15 px-2.5 py-1 font-medium text-accent-400 transition-colors hover:bg-accent-500/25"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-accent-400" aria-hidden="true" />
            {t('about.updateShort')}
          </NavLink>
        )}
      </div>
    </footer>
  )
}

/** Rahmen der angemeldeten Ansicht: Kopfzeile, Navigation, Inhalt, Fußzeile. */
export function AppShell() {
  const { t } = useTranslation()
  const items = NAV_ITEMS

  return (
    <div className="nv-glow flex min-h-dvh flex-col">
      <header className="sticky top-0 z-20 border-b border-ink-700/80 bg-ink-950/80 backdrop-blur-xl">
        <LoadingBar />
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 sm:px-6">
          {/* Ein Klick aufs Logo führt zurück zur Startseite. */}
          <NavLink to="/" className="shrink-0" aria-label={t('nav.home')}>
            <Logo withWordmark />
          </NavLink>

          <nav className="hidden flex-1 items-center gap-1 md:flex" aria-label={t('nav.discover')}>
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  'rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                  (isActive
                    ? 'bg-accent-500/15 text-accent-400'
                    : 'text-mist-500 hover:bg-ink-850 hover:text-mist-100')
                }
              >
                {t(item.labelKey)}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <NotificationBell />
            <ThemeSwitcher />
            <LanguageSwitcher />
            <UserMenu />
          </div>
        </div>

        {/* Auf schmalen Bildschirmen wandert die Navigation in eine scrollbare Zeile. */}
        <nav
          className="flex gap-1 overflow-x-auto border-t border-ink-700/60 px-4 py-2 md:hidden"
          aria-label={t('nav.discover')}
        >
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                'shrink-0 rounded-full px-3 py-1.5 text-sm font-medium transition-colors ' +
                (isActive
                  ? 'bg-accent-500/15 text-accent-400'
                  : 'text-mist-500 hover:bg-ink-850 hover:text-mist-100')
              }
            >
              {t(item.labelKey)}
            </NavLink>
          ))}
        </nav>
      </header>

      {/* Über die volle Breite und außerhalb des Inhalts-Containers: Wessen
          Plex-Zugang abgelaufen ist, soll es auf jeder Seite sehen und nicht
          nur dort, wo er zufällig hinklickt. */}
      <WatchlistExpiredBanner />

      {/* flex-1 schiebt die Fußzeile auch auf kurzen Seiten nach unten. */}
      <main className="relative z-10 mx-auto w-full max-w-7xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>

      <Footer />
    </div>
  )
}
