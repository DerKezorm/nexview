/**
 * Der Stand der Hausordnung für dieses Konto.
 *
 * Getrennt vom §-Knopf, weil die Hülle der Anwendung ihn ebenso braucht und
 * weil `HausordnungKnopf.tsx` damit nur noch Bauteile ausführt. Nur solche
 * Dateien tauscht Vite im Entwicklungsbetrieb im laufenden Bild aus; bei
 * gemischten Ausfuhren lädt es die ganze Seite neu und der Zustand ist weg.
 */

import { useConfig } from './useConfig'

/** Gibt es etwas zu lesen, das dieses Konto noch nicht quittiert hat? */
export function useHausordnung() {
  const { data: config } = useConfig()
  const vorhanden = config?.hausordnung_vorhanden ?? false
  const quittierbar = config?.hausordnung_quittierbar ?? true
  const nichtQuittiert =
    vorhanden &&
    (config?.hausordnung_gelesen == null ||
      config.hausordnung_gelesen < (config?.hausordnung_fassung ?? 0))

  return {
    vorhanden,
    // ⚠️ **Der Punkt nur, wenn man ihn auch loswerden kann.** Ist das Abhaken
    // abgeschaltet, gibt es nichts zu quittieren - der Punkt bliebe für immer
    // stehen, und einen Hinweis, den man nie loswird, lernt man zu übersehen.
    // Dann ist der Knopf kein Anstupser mehr, sondern schlicht der Zugang.
    ungelesen: nichtQuittiert && quittierbar,
    knopfSichtbar: vorhanden && (nichtQuittiert || !quittierbar),
  }
}
