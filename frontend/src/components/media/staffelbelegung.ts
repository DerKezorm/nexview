/**
 * Wann eine Staffel vergeben ist, und wie das ehrlich heißt.
 *
 * Die Regel steht hier und nicht beim Wähler, weil das Anfrage-Formular und
 * die Kinderwünsche sie ebenso brauchen. Nebenbei liefert
 * `StaffelFolgenWaehler.tsx` damit nur noch Bauteile aus, und nur solche
 * Dateien tauscht Vite im Entwicklungsbetrieb im laufenden Bild aus.
 */

import type { Fassung, SeasonInfo } from '../../api/types'
import { staffelFassung } from '../../lib/fassungen'

/**
 * Ist diese Staffel schon **ganz** vergeben, vorhanden oder komplett
 * angefragt?
 *
 * Je Fassung eine eigene Antwort: Staffel 3 in 1080p anzufragen ist etwas
 * anderes als Staffel 3 in 4K, zwei Instanzen, zwei Dateien. Eine fehlende
 * Fassung heißt „unbekannt“, nicht „belegt“, wie bei den Zuständen der
 * Karten.
 *
 * Laufende Folgen-Pakete zählen hier **nicht**: Eine Staffel mit zwei
 * vergebenen Folgen bleibt wählbar, der Rest gehört noch niemandem. Was
 * ein Paket belegt, steht in `requested_episodes`.
 */
export function staffelBelegt(
  staffel: SeasonInfo,
  fassung: Pick<Fassung, 'kennung' | 'haupt'>,
): boolean {
  const stand = staffelFassung(staffel, fassung)
  if (!stand) return false
  // ⚠️ Der Nenner kommt von **Sonarr**, nicht von TMDB - die beiden zaehlen
  // Folgen gern verschieden (Baywatch S1: 22 gegen 21), und mit der
  // TMDB-Zahl galt eine komplette Staffel ewig als unvollstaendig.
  const gesamt = stand.episodes_total ?? staffel.episode_count
  return stand.requested || (gesamt > 0 && stand.episodes_available >= gesamt)
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
