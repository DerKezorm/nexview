import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import de from './de.json'
import en from './en.json'

export const SUPPORTED_LANGUAGES = ['de', 'en'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

const STORAGE_KEY = 'nexview.language'

function initialLanguage(): Language {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'de' || stored === 'en') return stored
  return navigator.language.toLowerCase().startsWith('de') ? 'de' : 'en'
}

void i18n.use(initReactI18next).init({
  resources: {
    de: { translation: de },
    en: { translation: en },
  },
  lng: initialLanguage(),
  fallbackLng: 'de',
  interpolation: { escapeValue: false },
})

export function changeLanguage(language: Language): void {
  localStorage.setItem(STORAGE_KEY, language)
  document.documentElement.lang = language
  void i18n.changeLanguage(language)
}

document.documentElement.lang = i18n.language

export default i18n
