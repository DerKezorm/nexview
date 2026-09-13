/**
 * Die Texte der Seite Downloads, nachgeliefert.
 *
 * ⚠️ **Warum sie nicht im Grundpaket stehen.** Es sind rund zehn Kilobyte je
 * Sprache, fast alles Erklärungen zu Gründen, warum ein Download hängt. Die
 * Seite öffnen nur Administratoren, und auch die nicht jeden Tag. Im
 * Grundpaket trüge sie trotzdem jeder Besucher bei jedem Öffnen mit, und die
 * Waage (`tools/gewicht-pruefen.mjs`) zählt genau das.
 *
 * Was außerhalb der Seite gebraucht wird, bleibt im Grundpaket: der Menüpunkt,
 * das Wort auf der Pille in der Anfrageliste, die Meldung in der Glocke.
 *
 * Dasselbe Muster wie `wasneu.ts`: eingehängt **tief in denselben Namensraum**,
 * damit `t('downloads.title')` ohne Vorsilbe funktioniert.
 */

import i18n from 'i18next'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

// ⚠️ Nur der Typ aus './index', nicht der Wert: sonst ein Ring (index lädt
// downloads, downloads lädt index), und der Bau könnte die Texte nicht mehr in
// ein eigenes Stück legen. Siehe `wasneu.ts`.
import type { Language } from './index'

/** ⚠️ Der `import(...)` muss wörtlich stehen, sonst packt der Bau alles zusammen. */
const TEXTE: Record<Language, () => Promise<{ default: Record<string, unknown> }>> = {
  de: () => import('./de.downloads.json'),
  en: () => import('./en.downloads.json'),
}

const geladen = new Set<Language>()

export async function downloadsLaden(sprache: Language): Promise<void> {
  if (geladen.has(sprache)) return
  const { default: texte } = await TEXTE[sprache]()
  i18n.addResourceBundle(sprache, 'translation', texte, true, true)
  geladen.add(sprache)
}

/** Beim Sprachwechsel mitziehen, aber nur, wenn die Texte schon jemand gebraucht hat. */
export async function downloadsNachziehen(sprache: Language): Promise<void> {
  if (geladen.size === 0) return
  await downloadsLaden(sprache)
}

function alsSprache(wert: string | undefined): Language {
  return wert?.startsWith('en') ? 'en' : 'de'
}

/** Holt die Texte für die eingestellte Sprache und sagt, ob sie schon da sind. */
export function useDownloadsTexte(): boolean {
  const { i18n: instanz } = useTranslation()
  const sprache = alsSprache(instanz.language)
  const [, neuZeichnen] = useState(0)

  useEffect(() => {
    if (geladen.has(sprache)) return
    let aktiv = true
    void downloadsLaden(sprache).then(() => {
      if (aktiv) neuZeichnen((zaehler) => zaehler + 1)
    })
    return () => {
      aktiv = false
    }
  }, [sprache])

  return geladen.has(sprache)
}
