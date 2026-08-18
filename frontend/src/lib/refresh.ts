/**
 * Welche Abfragen den Zustand einer Anfrage zeigen - an einer Stelle.
 *
 * Der Zustand "angefragt / genehmigt / vorhanden" steckt nicht nur in den
 * Kacheln, sondern genauso im Detailfenster, auf der Detailseite, in
 * Filmografien, Kategorielisten und auf der Startseite. Wer nach einer
 * Anfrage nur die Kacheln neu lädt, sieht auf der Seite davor weiterhin
 * "Zu Radarr hinzufügen".
 *
 * Ohne diese gemeinsame Stelle wird die Liste an jeder neuen Aufrufstelle
 * aufs Neue - und jedes Mal etwas anders - abgeschrieben.
 */

import type { QueryClient } from '@tanstack/react-query'

/** Alles, was den Anfrage- bzw. Bibliothekszustand eines Titels anzeigt. */
const ABFRAGEN = [
  'discover', // Kacheln und Listen unter Filme/Serien/Suche
  'media-detail', // Detailfenster (Popup)
  'title-detail', // Detailseite
  'browse', // Kategorieliste zu Schlagwort/Studio
  'person', // Filmografie
  'home-recent',
  'home-curated',
  'home-trending',
  'favorites',
  'my-requests',
  'quota',
]

/**
 * Nach jeder Änderung an einer Anfrage aufrufen: anlegen, zurückziehen,
 * freigeben, ablehnen, abbrechen.
 */
export function anfragenStandNeuLaden(queryClient: QueryClient): void {
  for (const schluessel of ABFRAGEN) {
    void queryClient.invalidateQueries({ queryKey: [schluessel] })
  }
}
