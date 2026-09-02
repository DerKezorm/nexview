/**
 * Der Blätter-Zustand einer langen Liste.
 *
 * Bewusst rein in der Oberfläche geblättert: Die Endpunkte liefern ohnehin die
 * vollständige Liste (sie ist nach Zustand gefiltert und selten größer als ein
 * paar hundert Zeilen), und so bleiben Zählwerte in den Filterknöpfen korrekt,
 * die zählen über *alles*, nicht über die gerade sichtbare Seite.
 *
 * Der Haken wohnt neben der Leiste, nicht in ihr: `Pagination.tsx` liefert
 * damit nur noch Bauteile aus, und nur solche Dateien tauscht Vite im
 * Entwicklungsbetrieb im laufenden Bild aus.
 */

import { useEffect, useState } from 'react'

/**
 * Zwanzig pro Seite ist die Größe, bei der man noch scrollt statt zu suchen.
 */
export const SEITENGROESSE = 20

/**
 * Blätter-Zustand für eine Liste.
 *
 * ``schluessel`` ist alles, was die Liste von Grund auf ändert, der gewählte
 * Filter etwa. Ändert er sich, geht es zurück auf Seite 1: Sonst stünde man
 * nach einem Filterwechsel auf Seite 7 einer Liste mit zwei Seiten und sähe
 * nichts.
 */
export function useSeiten<T>(eintraege: T[], schluessel: string) {
  const [seite, setSeite] = useState(1)

  useEffect(() => {
    setSeite(1)
  }, [schluessel])

  const seiten = Math.max(1, Math.ceil(eintraege.length / SEITENGROESSE))
  // Die letzte Seite kann durch Löschen wegfallen, während man darauf steht.
  const aktuell = Math.min(seite, seiten)
  const start = (aktuell - 1) * SEITENGROESSE

  return {
    seite: aktuell,
    seiten,
    sichtbar: eintraege.slice(start, start + SEITENGROESSE),
    setSeite,
  }
}
