/**
 * Die Adresse eines in der Hausordnung hinterlegten Bildes.
 *
 * Nur eigene Bilder, siehe den Parser in `auszeichnung.ts`: Der Name kommt aus
 * dem Betreibertext und wird deshalb kodiert, nicht eingeklebt.
 *
 * Steht hier statt bei der Anzeige, weil die Einstellungsseite die Adresse für
 * ihre Vorschau braucht und `Hausordnungstext.tsx` damit nur noch Bauteile
 * ausliefert. Nur solche Dateien tauscht Vite im Entwicklungsbetrieb im
 * laufenden Bild aus.
 */

import { mitBasis } from './basis'

export function bildAdresse(name: string): string {
  return mitBasis(`/api/hausordnung/bild/${encodeURIComponent(name)}`)
}
