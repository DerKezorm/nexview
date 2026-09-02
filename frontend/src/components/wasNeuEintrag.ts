/**
 * Die redaktionellen Texte des „Alles, was neu ist“-Fensters, geprüft.
 *
 * Getrennt vom Fenster, weil der Balken auf jeder Seite wissen muss, ob es zu
 * dieser Fassung überhaupt etwas zu lesen gibt, ohne das Fenster mitzuladen.
 * Nebenbei bleibt `WasNeuFenster.tsx` damit eine Datei, die nur Bauteile
 * ausliefert; nur solche tauscht Vite im Entwicklungsbetrieb im laufenden Bild
 * aus.
 */

import type { TFunction } from 'i18next'

/** Der redaktionelle Text zu einer Fassung, wie er in den Sprachdateien steht. */
export type WasNeuEintrag = {
  lead: string
  sections: { title: string; where: string; body: string }[]
  smallTitle: string
  small: string[]
}

/**
 * Hat der Eintrag die Form, die das Fenster erwartet?
 *
 * Die Texte kommen aus einer Datei, die vor jedem Release von Hand
 * geschrieben wird: ein Tippfehler darin darf nicht die ganze Oberfläche
 * schwarz machen. Passt die Form nicht, zeigt das Fenster den Verweis auf
 * die Release-Seite.
 */
export function istEintrag(wert: unknown): wert is WasNeuEintrag {
  if (!wert || typeof wert !== 'object') return false
  const k = wert as Partial<WasNeuEintrag>
  return typeof k.lead === 'string' && Array.isArray(k.sections) && Array.isArray(k.small)
}

/**
 * Gibt es zu dieser Fassung einen redaktionellen Text?
 *
 * Der Balken hängt daran: Fehlerbehebungen bekommen bewusst keinen eigenen
 * Eintrag, sie sammeln sich für die „Außerdem behoben“-Liste des nächsten
 * größeren Releases. Ohne diese Prüfung poppte nach jedem Hotfix ein Hinweis
 * auf, hinter dem nichts steht.
 */
export function hatEintrag(t: TFunction, version: string): boolean {
  const alle = t('whatsNew.entries', { returnObjects: true, defaultValue: {} }) as Record<
    string,
    unknown
  >
  return Boolean(alle && typeof alle === 'object' && istEintrag(alle[version]))
}
