/**
 * Wann eine Staffel vergeben ist, und wie das ehrlich heißt.
 *
 * Die Regel steht hier und nicht beim Wähler, weil das Anfrage-Formular und
 * die Kinderwünsche sie ebenso brauchen. Nebenbei liefert
 * `StaffelFolgenWaehler.tsx` damit nur noch Bauteile aus, und nur solche
 * Dateien tauscht Vite im Entwicklungsbetrieb im laufenden Bild aus.
 */

import type { QualityTier, SeasonInfo } from '../../api/types'

/**
 * Ist diese Staffel schon **ganz** vergeben, vorhanden oder komplett
 * angefragt?
 *
 * Je Stufe eine eigene Antwort: Staffel 3 in 1080p anzufragen ist etwas
 * anderes als Staffel 3 in 4K, zwei Instanzen, zwei Dateien. Fehlende
 * 4K-Felder heißen „unbekannt“, nicht „belegt“, wie bei `status_uhd`.
 *
 * Laufende Folgen-Pakete zählen hier **nicht**: Eine Staffel mit zwei
 * vergebenen Folgen bleibt wählbar, der Rest gehört noch niemandem. Was
 * ein Paket belegt, steht in `requested_episodes`.
 */
export function staffelBelegt(staffel: SeasonInfo, tier: QualityTier): boolean {
  if (tier === 'uhd') {
    const gesamt = staffel.episodes_total_arr_uhd ?? staffel.episode_count
    return (
      Boolean(staffel.requested_uhd) ||
      (gesamt > 0 && (staffel.episodes_available_uhd ?? 0) >= gesamt)
    )
  }
  // ⚠️ Der Nenner kommt von **Sonarr**, nicht von TMDB - die beiden zaehlen
  // Folgen gern verschieden (Baywatch S1: 22 gegen 21), und mit der
  // TMDB-Zahl galt eine komplette Staffel ewig als unvollstaendig.
  const gesamt = staffel.episodes_total_arr ?? staffel.episode_count
  return (
    Boolean(staffel.requested) ||
    (gesamt > 0 && staffel.episodes_available >= gesamt)
  )
}

/**
 * Das ehrliche Wort zu einer belegten Staffel oder Folge.
 *
 * „läuft“ stand früher für jeden aktiven Zustand, auch fürs Warten auf
 * Freigabe, die noch abgelehnt werden kann, und für längst Geladenes. Wer
 * daneben liest, plant mit etwas, das es so nicht gibt. Deshalb entscheidet
 * jetzt der Status der belegenden Anfrage, nicht die Zahlen-Arithmetik.
 */
export function belegungsWort(status: string | null | undefined, vorhanden: boolean): string {
  if (vorhanden || status === 'downloaded') return 'request.seasonHere'
  if (status === 'pending_approval') return 'request.seasonPending'
  return 'request.seasonRunning'
}
