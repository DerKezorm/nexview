/**
 * Holt die redaktionellen „Alles, was neu ist“-Texte und sagt, ob sie dastehen.
 *
 * Zwei Stellen brauchen sie: der Balken, der entscheiden muss, ob es zu dieser
 * Fassung überhaupt etwas zu lesen gibt, und das Fenster selbst. Beide rufen
 * diesen Haken; das Laden passiert trotzdem nur einmal, dafür sorgt die Merkliste
 * in `i18n/wasneu.ts`.
 *
 * ⚠️ **Der Rückgabewert ist wichtiger, als er aussieht.** Solange `false`
 * zurückkommt, dürfen die Texte nicht gelesen werden: `t('whatsNew.entries')`
 * lieferte dann ein leeres Verzeichnis, der Balken hielte das für „zu dieser
 * Fassung gibt es nichts“ und verschwände für immer. Der Aufrufer muss also
 * warten, nicht raten.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Language } from '../i18n'
import { wasNeuDa, wasNeuLaden } from '../i18n/wasneu'

export function useWasNeuTexte(gebraucht = true): boolean {
  const { i18n } = useTranslation()
  const sprache = i18n.language as Language
  const [bereit, setBereit] = useState(() => wasNeuDa(sprache))

  useEffect(() => {
    if (!gebraucht) return
    if (wasNeuDa(sprache)) {
      setBereit(true)
      return
    }
    // Nach dem Laden kann die Komponente längst weg sein, etwa weil jemand die
    // Seite gewechselt hat. Dann darf kein setState mehr kommen.
    let lebt = true
    setBereit(false)
    void wasNeuLaden(sprache).then(() => {
      if (lebt) setBereit(true)
    })
    return () => {
      lebt = false
    }
  }, [gebraucht, sprache])

  return bereit
}
