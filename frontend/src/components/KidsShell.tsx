import { NavLink, Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useAuth } from '../auth/useAuth'
import { KidsHintergrund } from '../pages/kids/KidsHintergrund'
import { KIDS } from '../pages/kids/kidsTheme'
import { LoadingBar } from './LoadingBar'

/**
 * Der Rahmen der Kinderansicht.
 *
 * Bewusst nicht die `AppShell` mit ausgeblendeten Punkten, sondern ein eigener
 * Rahmen mit eigenem Seitenbaum: Was hier nicht steht, existiert für ein Kind
 * nicht – auch nicht über die Adresszeile oder den Zurück-Knopf.
 *
 * Und bewusst eine **eigene Farbwelt** (siehe `kidsTheme`): hell und warm
 * statt Nexviews Dunkelgrau mit rotem Akzent. Drei große Ziele unten am Rand,
 * weil Kinder am Tablet sitzen und ein Menü in der Kopfzeile mit dem Daumen
 * nicht zu treffen ist. Keine Glocke, kein Benutzermenü, keine Fußzeile mit
 * Version und Update-Hinweis.
 */
const ZIELE = [
  {
    to: '/',
    labelKey: 'kids.navDiscover',
    icon: 'M12 3l2.4 5.6 6.1.5-4.6 4 1.4 5.9L12 16l-5.3 3 1.4-5.9-4.6-4 6.1-.5L12 3z',
  },
  {
    to: '/suchen',
    labelKey: 'kids.navSearch',
    icon: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4.2-4.2',
  },
  {
    to: '/wuensche',
    labelKey: 'kids.navWishes',
    icon: 'M12 20s-7-4.4-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.6-7 9-7 9z',
  },
]

export function KidsShell() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const name = user?.display_name ?? user?.username ?? ''
  const initiale = name.slice(0, 1).toUpperCase()

  return (
    <div className="relative flex min-h-dvh flex-col">
      <KidsHintergrund />
      <LoadingBar />

      <header className="relative mx-auto flex w-full max-w-5xl items-center gap-3 px-4 pt-4">
        <div
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl text-xl font-extrabold text-white"
          style={{ backgroundColor: KIDS.primaer }}
          aria-hidden="true"
        >
          {initiale}
        </div>
        <p className="flex-1 truncate text-2xl font-extrabold" style={{ color: KIDS.text }}>
          {t('kids.greeting', { name })}
        </p>
        <button
          type="button"
          onClick={logout}
          className="rounded-2xl px-4 py-2.5 text-sm font-bold transition-transform active:scale-95"
          style={{ backgroundColor: KIDS.flaeche, color: KIDS.textLeise }}
        >
          {t('kids.logout')}
        </button>
      </header>

      {/* Platz unten für die Leiste, damit sie nichts verdeckt. */}
      <main className="relative mx-auto w-full max-w-5xl flex-1 px-4 pt-5 pb-32">
        <Outlet />
      </main>

      <nav
        className="fixed inset-x-0 bottom-0 z-20 px-4 pb-4"
        aria-label={t('kids.navLabel')}
      >
        <div
          className="mx-auto flex max-w-md overflow-hidden rounded-3xl shadow-xl"
          style={{ backgroundColor: KIDS.flaeche }}
        >
          {ZIELE.map((ziel) => (
            <NavLink
              key={ziel.to}
              to={ziel.to}
              end={ziel.to === '/'}
              className="flex flex-1 flex-col items-center gap-1 py-3 text-xs font-bold transition-colors"
              style={({ isActive }) => ({
                color: isActive ? KIDS.primaer : KIDS.textLeise,
                backgroundColor: isActive ? KIDS.flaecheSanft : 'transparent',
              })}
            >
              <svg
                viewBox="0 0 24 24"
                className="h-7 w-7"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d={ziel.icon} />
              </svg>
              {t(ziel.labelKey)}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  )
}
