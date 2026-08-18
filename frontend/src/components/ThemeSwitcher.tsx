import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import type { User } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { gespeichertesTheme, istTheme, themeAnwenden } from '../lib/theme'
import type { Theme } from '../lib/theme'

function SonneIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
      <path d="M10 4.2a.9.9 0 0 1-.9-.9V2a.9.9 0 0 1 1.8 0v1.3a.9.9 0 0 1-.9.9Zm0 13.8a.9.9 0 0 1-.9-.9v-1.3a.9.9 0 0 1 1.8 0V17a.9.9 0 0 1-.9.9ZM17.1 10.9a.9.9 0 0 1 0-1.8H18a.9.9 0 0 1 0 1.8h-.9Zm-15.1 0a.9.9 0 0 1 0-1.8h.9a.9.9 0 0 1 0 1.8H2Zm12.6-4.6a.9.9 0 0 1 0-1.3l.7-.7a.9.9 0 1 1 1.3 1.3l-.7.7a.9.9 0 0 1-1.3 0ZM4.3 16.4a.9.9 0 0 1 0-1.3l.7-.7a.9.9 0 0 1 1.3 1.3l-.7.7a.9.9 0 0 1-1.3 0Zm11 0-.7-.7a.9.9 0 0 1 1.3-1.3l.7.7a.9.9 0 0 1-1.3 1.3ZM5 6.3l-.7-.7a.9.9 0 0 1 1.3-1.3l.7.7A.9.9 0 0 1 5 6.3ZM10 14a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z" />
    </svg>
  )
}

function MondIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
      <path d="M17.3 12.9A7.5 7.5 0 0 1 7.1 2.7a.8.8 0 0 0-1-1 9.1 9.1 0 1 0 12.2 12.2.8.8 0 0 0-1-1Z" />
    </svg>
  )
}

const MODI: { wert: Theme; icon: () => React.ReactElement; schluessel: string }[] = [
  { wert: 'dark', icon: MondIcon, schluessel: 'theme.dark' },
  { wert: 'light', icon: SonneIcon, schluessel: 'theme.light' },
]

/**
 * Umschalter Hell/Dunkel - gebaut wie die Sprachumschaltung daneben.
 *
 * Die Wahl wirkt sofort und wird im Profil gespeichert, damit jeder seine
 * eigene Voreinstellung auf jedem Geraet wiederfindet. Der Schalter steht auch
 * schon auf der Anmeldeseite, wo es noch kein Konto gibt - dann wirkt er nur
 * fuer den Moment, ohne zu speichern.
 */
export function ThemeSwitcher() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  // Der angemeldete Stand kommt vom Konto; davor der zuletzt im Browser
  // gemerkte, damit die Anmeldeseite nicht ploetzlich dunkel wird.
  const theme: Theme = istTheme(user?.theme) ? user.theme : gespeichertesTheme()

  function waehlen(neu: Theme) {
    if (neu === theme) return
    themeAnwenden(neu)
    if (user) {
      // Sofort im Konto festhalten. Schlaegt es fehl, gilt die Wahl trotzdem
      // fuer diese Sitzung - nur ueber ein anderes Geraet kaeme sie dann nicht.
      api
        .patch<User>('/api/auth/me', { theme: neu })
        .then(updateUser)
        .catch(() => {})
    }
  }

  return (
    <div
      className="flex items-center rounded-full border border-ink-700 bg-ink-850 p-0.5"
      role="group"
      aria-label={t('theme.label')}
    >
      {MODI.map(({ wert, icon: Icon, schluessel }) => {
        const aktiv = theme === wert
        return (
          <button
            key={wert}
            type="button"
            onClick={() => waehlen(wert)}
            aria-pressed={aktiv}
            title={t(schluessel)}
            aria-label={t(schluessel)}
            className={
              'rounded-full p-1.5 transition-colors ' +
              (aktiv ? 'bg-accent-500 text-white' : 'text-mist-500 hover:text-mist-100')
            }
          >
            <Icon />
          </button>
        )
      })}
    </div>
  )
}
