import { NavLink, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { LanguageSwitcher } from './LanguageSwitcher'
import { LoadingBar } from './LoadingBar'
import { Logo } from './Logo'
import { NotificationBell } from './NotificationBell'
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
  { to: '/suche', labelKey: 'nav.search' },
]

/** Rahmen der angemeldeten Ansicht: Kopfzeile, Navigation, Inhalt. */
export function AppShell() {
  const { t } = useTranslation()
  const items = NAV_ITEMS

  return (
    <div className="nv-glow min-h-dvh">
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

      <main className="relative z-10 mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
