/**
 * Der Satz über einer Befundliste, getrennt von der Liste selbst.
 *
 * Er wohnt hier, weil das Admin-Dashboard ihn zeigt, ohne die Liste zu bauen,
 * und weil `Befundliste.tsx` dadurch eine Datei bleibt, die nichts als
 * Bauteile ausliefert. Nur solche Dateien tauscht Vite im Entwicklungsbetrieb
 * im laufenden Bild aus; bei gemischten Ausfuhren lädt es die ganze Seite neu
 * und der Zustand ist weg.
 */

import type { TFunction } from 'i18next'

import type { BefundSchwere } from '../api/types'

/**
 * „3 Fehler · 8 Warnungen · 1 Hinweis“, aus drei Bausteinen, nicht aus einem Satz.
 *
 * ⚠️ **Ein Satz mit drei Zahlen lässt sich nicht beugen.** i18next entscheidet
 * über Einzahl und Mehrzahl anhand von genau einem `count`; bei drei Zahlen in
 * einer Zeile stand deshalb „1 Hinweise“. Jeder Teil wird einzeln übersetzt und
 * erst danach zusammengesetzt.
 */
export function befundZusammenfassung(
  zaehler: Record<BefundSchwere, number>,
  t: TFunction,
): string {
  const teile: string[] = []
  for (const schwere of ['fehler', 'warnung', 'hinweis'] as BefundSchwere[]) {
    const anzahl = zaehler[schwere] ?? 0
    // Nullen weglassen: „0 Hinweise“ ist keine Auskunft, nur Länge.
    if (anzahl > 0) teile.push(t(`befund.anzahl.${schwere}`, { count: anzahl }))
  }
  return teile.join(' · ')
}
