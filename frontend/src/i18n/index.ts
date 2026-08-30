/**
 * Die Sprachen — und zwar nur die, die gerade gebraucht wird.
 *
 * ⚠️ **Vorher lagen beide im Grundpaket.** Deutsch und Englisch zusammen sind
 * 437 kB, also fast ein Drittel von allem, was ein Besucher beim Öffnen
 * herunterlädt — und die Hälfte davon liest er nie. Jetzt kommt eine Sprache
 * mit, die andere erst beim Umschalten.
 *
 * ⚠️ **Deshalb gibt es keinen Rückfall mehr auf eine andere Sprache.** i18next
 * konnte bisher bei einem fehlenden englischen Text den deutschen einsetzen;
 * das ginge nur, wenn Deutsch geladen wäre — und genau das soll es nicht mehr
 * sein. Fehlt ein Text, erscheint sein Schlüssel. Damit das nie passiert,
 * prüft `i18n-vollstaendig.test.ts`, dass beide Dateien exakt dieselben
 * Einträge haben; heute sind das 2.667 auf beiden Seiten.
 *
 * Gestartet wird von `main.tsx` mit `i18nStarten()`, **bevor** gerendert wird:
 * Ohne das Warten stünde einen Wimpernschlag lang die rohe Schlüsselliste auf
 * dem Bildschirm.
 */

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

export const SUPPORTED_LANGUAGES = ['de', 'en'] as const
export type Language = (typeof SUPPORTED_LANGUAGES)[number]

const STORAGE_KEY = 'nexview.language'

/**
 * Die Sprachdateien als Nachschub.
 *
 * ⚠️ Der `import(...)` muss hier **wörtlich** stehen und darf nicht über eine
 * Variable laufen. Nur so erkennt der Bau beim Zusammenstellen, dass daraus
 * zwei getrennte Dateien werden sollen — mit einem berechneten Pfad packt er
 * vorsichtshalber wieder alles zusammen, und der ganze Gewinn wäre weg.
 */
const TEXTE: Record<Language, () => Promise<{ default: Record<string, unknown> }>> = {
  de: () => import('./de.json'),
  en: () => import('./en.json'),
}

function initialLanguage(): Language {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'de' || stored === 'en') return stored
  return navigator.language.toLowerCase().startsWith('de') ? 'de' : 'en'
}

/** Eine Sprache holen, falls sie noch nicht dasteht. */
async function nachladen(sprache: Language): Promise<void> {
  if (i18n.hasResourceBundle(sprache, 'translation')) return
  const { default: texte } = await TEXTE[sprache]()
  i18n.addResourceBundle(sprache, 'translation', texte)
}

/**
 * Die Texte holen und i18next damit starten.
 *
 * Der Parameter ist für die Tests: Sie sollen nicht davon abhängen, welche
 * Sprache der Browser meldet, in dem sie gerade laufen.
 */
export async function i18nStarten(sprache: Language = initialLanguage()): Promise<void> {
  const { default: texte } = await TEXTE[sprache]()
  await i18n.use(initReactI18next).init({
    resources: { [sprache]: { translation: texte } },
    lng: sprache,
    // Siehe oben: Eine Rückfallsprache, die nicht geladen ist, hilft nicht.
    fallbackLng: false,
    interpolation: { escapeValue: false },
  })
  document.documentElement.lang = sprache
}

export async function changeLanguage(language: Language): Promise<void> {
  localStorage.setItem(STORAGE_KEY, language)
  await nachladen(language)
  document.documentElement.lang = language
  await i18n.changeLanguage(language)
}

export default i18n
