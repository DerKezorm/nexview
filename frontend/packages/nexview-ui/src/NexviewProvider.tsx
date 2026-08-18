import type { ReactNode } from 'react'
import { I18nextProvider } from 'react-i18next'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Die gemeinsame i18n-Instanz, eingerichtet von der Anwendung selbst: der
// Import unten laeuft nur wegen seines Seiteneffekts. So sind die
// Beschriftungen hier genau die, die Nexview auch zeigt - kein Nachbau, und
// keine zweite Uebersetzungsdatei, die auseinanderlaufen koennte.
import i18n from 'i18next'
import '../../../src/i18n'

/**
 * Der Rahmen, den einige Bausteine um sich brauchen.
 *
 * Zwei Dinge holen sich die Komponenten aus dem Kontext, statt sie als
 * Eigenschaft zu bekommen:
 *
 * - **Sprache.** Abzeichen, Filterleiste und Dialoge beschriften sich selbst
 *   (`useTranslation`). Ohne diesen Rahmen erscheinen statt der Texte die
 *   Schluessel - `status.downloaded` statt "Bereits geladen".
 * - **Datenschicht.** Die Ladeanzeige fragt react-query, ob gerade etwas
 *   laeuft (`useIsFetching`). Ohne Client wirft sie - obwohl sie selbst
 *   nichts nachlaedt.
 * - **Navigation.** Poster und Besetzungsreihe sind Verweise (`Link`).
 *   Ohne Router wirft React beim Rendern.
 *
 * Deshalb: **alles in `NexviewProvider` einschliessen.** Er kostet nichts und
 * ist ausserhalb der Anwendung immer noetig.
 */
// Ein Client ohne Wiederholungen: hier wird nichts geladen, er ist nur da,
// damit useIsFetching eine Antwort bekommt.
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

export function NexviewProvider({
  children,
  language = 'de',
}: {
  children: ReactNode
  /** Sprache der Beschriftungen. Standard Deutsch. */
  language?: 'de' | 'en'
}) {
  if (i18n.language !== language) void i18n.changeLanguage(language)

  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>
  )
}
