import { NavLink, Outlet, useSearchParams } from 'react-router-dom'
import { useState } from 'react'
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
import { WasNeuBanner } from './WasNeuBanner'
import { UserMenu } from './UserMenu'
import type { MediaItem, MediaType } from '../api/types'
import { DetailModal } from './media/DetailModal'
import { Filmabend } from './stoebern/Filmabend'
import { useConfig } from '../hooks/useConfig'

type NavItem = { to: string; labelKey: string }

/**
 * Hauptmenü - bewusst nur das Entdecken.
 *
 * Alles Persönliche und Verwaltende liegt im Benutzermenü oben rechts.
 */
/**
 * Adresszusatz, der den Filmabend-Assistenten öffnet.
 *
 * ⚠️ Bewusst ein **Parameter** und keine eigene Adresse. Als eigene Route
 * (`/filmabend`, die dieselbe Stöber-Seite rendert) war es zweifach kaputt:
 * Von Stöbern aus passierte gar nichts, weil React dieselbe Komponente
 * wiederverwendet und der Anfangswert von `useState` nicht neu gelesen wird —
 * und von jeder anderen Seite aus sprang der Hintergrund auf Stöbern.
 *
 * Als Parameter legt sich das Fenster über **die Seite, auf der man gerade
 * ist**, und Schließen lässt einen dort stehen.
 */
const FILMABEND = 'filmabend'

const NAV_ITEMS: NavItem[] = [
  // Je ein Eintrag fuer Filme UND Serien - die Medienart wird auf der Seite
  // selbst umgeschaltet. Zwei Eintraege ergaeben ein siebenteiliges
  // Hauptmenue, und das ist auf dem Handy keine Navigation mehr.
  { to: '/stoebern', labelKey: 'nav.browse' },
  { to: '/personen', labelKey: 'nav.people' },
  { to: '/kalender', labelKey: 'nav.calendar' },
  { to: '/suche', labelKey: 'nav.search' },
]

// "Filme entdecken" und "Serien entdecken" sind hier bewusst **nicht** mehr
// aufgefuehrt, die Routen aber absichtlich stehengeblieben (siehe App.tsx):
// Nach dem Umbau beantwortete die Stoeber-Seite jede ihrer Fragen besser, bis
// auf "was ist gerade erschienen?" - und das ist jetzt ein Regal dort. Alte
// Lesezeichen laufen weiter, statt auf eine 404 zu treffen.

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
  const [params, setParams] = useSearchParams()
  const { data: config } = useConfig()
  const [schnellAnfrage, setSchnellAnfrage] = useState<MediaItem | null>(null)

  const filmabendWert = params.get(FILMABEND)
  const filmabendOffen = filmabendWert !== null
  const filmabendArt: MediaType = filmabendWert === 'tv' ? 'tv' : 'movie'

  /** Öffnen, umschalten oder schließen - ohne die Seite darunter zu wechseln. */
  function setzeFilmabend(art: MediaType | null) {
    const neu = new URLSearchParams(params)
    if (art) neu.set(FILMABEND, art)
    else neu.delete(FILMABEND)
    setParams(neu, { replace: true })
  }

  const oeffneFilmabend = () => setzeFilmabend(filmabendOffen ? null : 'movie')

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
            <button
              type="button"
              onClick={oeffneFilmabend}
              aria-pressed={filmabendOffen}
              className={
                'rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                (filmabendOffen
                  ? 'bg-accent-500/15 text-accent-400'
                  : 'text-mist-500 hover:bg-ink-850 hover:text-mist-100')
              }
            >
              {t('nav.whatToWatch')}
            </button>
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
          <button
            type="button"
            onClick={oeffneFilmabend}
            aria-pressed={filmabendOffen}
            className={
              'shrink-0 rounded-full px-3 py-1.5 text-sm font-medium transition-colors ' +
              (filmabendOffen
                ? 'bg-accent-500/15 text-accent-400'
                : 'text-mist-500 hover:bg-ink-850 hover:text-mist-100')
            }
          >
            {t('nav.whatToWatch')}
          </button>
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
      <WasNeuBanner />
      <WatchlistExpiredBanner />

      {/* flex-1 schiebt die Fußzeile auch auf kurzen Seiten nach unten. */}
      <main className="relative z-10 mx-auto w-full max-w-7xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>

      {filmabendOffen && (
        <Filmabend
          key={filmabendArt}
          mediaType={filmabendArt}
          onMediaTypeChange={(neu) => setzeFilmabend(neu)}
          onQuickAdd={setSchnellAnfrage}
          onClose={() => setzeFilmabend(null)}
        />
      )}

      <DetailModal
        item={schnellAnfrage}
        onClose={() => setSchnellAnfrage(null)}
        arrConfigured={
          schnellAnfrage?.media_type === 'tv'
            ? (config?.sonarr_configured ?? false)
            : (config?.radarr_configured ?? false)
        }
      />

      <Footer />
    </div>
  )
}
