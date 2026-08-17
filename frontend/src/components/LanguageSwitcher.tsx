import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { changeLanguage, SUPPORTED_LANGUAGES } from '../i18n'
import type { Language } from '../i18n'
import type { User } from '../api/types'

/** Umschalter Deutsch/Englisch. Für angemeldete Nutzer wird die Wahl im Profil gespeichert. */
export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const { user, updateUser } = useAuth()

  async function select(language: Language) {
    if (language === i18n.language) return
    changeLanguage(language)
    if (user) {
      try {
        updateUser(await api.patch<User>('/api/auth/me', { language }))
      } catch {
        // Die Sprache ist trotzdem umgestellt - nur das Speichern schlug fehl.
      }
    }
  }

  return (
    <div
      className="flex items-center rounded-full border border-ink-700 bg-ink-850 p-0.5"
      role="group"
      aria-label={t('language.label')}
    >
      {SUPPORTED_LANGUAGES.map((language) => {
        const active = i18n.language === language
        return (
          <button
            key={language}
            type="button"
            onClick={() => void select(language)}
            aria-pressed={active}
            className={
              'rounded-full px-2.5 py-1 text-xs font-semibold uppercase transition-colors ' +
              (active ? 'bg-accent-500 text-white' : 'text-mist-500 hover:text-mist-100')
            }
          >
            {language}
          </button>
        )
      })}
    </div>
  )
}
