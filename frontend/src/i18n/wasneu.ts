/**
 * Die redaktionellen Texte des „Alles, was neu ist“-Fensters, nachgeliefert.
 *
 * ⚠️ **Warum sie nicht im Grundpaket stehen.** Es sind 23,6 kB roh auf Deutsch,
 * also der größte einzelne Posten im ganzen Sprachkatalog, und sie stehen für
 * einen Text, den ein Betreiber einmal nach einem Update liest. Ein normaler
 * Nutzer sieht sie nie: Balken und Fenster sind Administratoren vorbehalten.
 * Trotzdem lud sie bisher jeder Besucher bei jedem Öffnen mit, und zwar
 * blockierend vor dem ersten Bild.
 *
 * Geholt werden sie deshalb erst, wenn sie wirklich gebraucht werden: wenn ein
 * Administrator ein ungelesenes Update hat, oder wenn jemand das Fenster über
 * die Über-Seite öffnet.
 *
 * ⚠️ **Die Kleintexte bleiben im Grundpaket.** Titel, Hinweis, Knopfbeschriftung
 * und die beiden Ersatztexte sind zusammen 0,37 kB. Sie hier auszulagern würde
 * nichts sparen, aber das Fenster müsste seine eigene Überschrift nachladen.
 */

import i18n from 'i18next'

// ⚠️ Bewusst direkt von i18next und nicht ueber './index': Sonst entstuende
// ein Ring (index laedt wasneu, wasneu laedt index), und der Bau koennte die
// Texte nicht mehr in ein eigenes Stueck legen. Es ist dieselbe Instanz,
// './index' richtet genau sie ein.
import type { Language } from './index'

/**
 * ⚠️ Der `import(...)` muss hier **wörtlich** stehen, genau wie bei den
 * Sprachdateien selbst: Nur so erkennt der Bau beim Zusammenstellen, dass
 * daraus getrennte Dateien werden sollen. Mit einem berechneten Pfad packt er
 * vorsichtshalber wieder alles zusammen, und der ganze Gewinn wäre weg.
 */
const TEXTE: Record<Language, () => Promise<{ default: Record<string, unknown> }>> = {
  de: () => import('./de.wasneu.json'),
  en: () => import('./en.wasneu.json'),
}

/**
 * Welche Sprachen schon dastehen.
 *
 * Nicht bloß eine Beschleunigung: Ohne diese Menge würde jeder Aufruf des
 * Hakens einen neuen Ladevorgang anstoßen, und der Balken ruft ihn bei jedem
 * Neuzeichnen.
 */
const geladen = new Set<Language>()

/** Steht die Sprache schon? Für Stellen, die ohne Warten entscheiden müssen. */
export function wasNeuDa(sprache: Language): boolean {
  return geladen.has(sprache)
}

/**
 * Die Texte einer Sprache holen, falls sie fehlen.
 *
 * ⚠️ Eingehängt wird **tief in denselben Namensraum**, in dem auch alles andere
 * steht (`addResourceBundle` mit `deep` und `overwrite`). Das ist der Grund,
 * warum keine einzige Aufrufstelle angefasst werden musste: `t('whatsNew.entries')`
 * findet die Einträge hinterher genauso wie vorher. Ein eigener Namensraum
 * hätte jede Stelle auf `t('wasneu:whatsNew.entries')` umgeschrieben.
 */
export async function wasNeuLaden(sprache: Language): Promise<void> {
  if (geladen.has(sprache)) return
  const { default: texte } = await TEXTE[sprache]()
  i18n.addResourceBundle(sprache, 'translation', texte, true, true)
  geladen.add(sprache)
}

/**
 * Beim Sprachwechsel mitziehen.
 *
 * Wer das Fenster auf Deutsch offen hatte und dann auf Englisch schaltet, soll
 * nicht plötzlich vor rohen Schlüsseln stehen. Nachgeladen wird nur, wenn die
 * Texte vorher überhaupt jemand gebraucht hat: Ein normaler Nutzer, der die
 * Sprache umstellt, holt sie weiterhin nicht.
 */
export async function wasNeuNachziehen(sprache: Language): Promise<void> {
  if (geladen.size === 0) return
  await wasNeuLaden(sprache)
}
