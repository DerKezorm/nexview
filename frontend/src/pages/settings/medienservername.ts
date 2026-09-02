/**
 * Wie ein Medienserver in der Oberfläche heißt.
 *
 * Getrennt von der Einstellungsseite, weil zwei Testdateien die Regel prüfen
 * und weil `AdminMedienserverVerbindung.tsx` damit nur noch Bauteile
 * ausliefert. Nur solche Dateien tauscht Vite im Entwicklungsbetrieb im
 * laufenden Bild aus.
 */

/** Wie der Anbieter heißt, nicht, wie der Server sich selbst nennt. */
export const ANBIETER: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  emby: 'Emby',
}

/**
 * Trägt dieser Servername etwas bei, oder ist es eine Maschinenkennung?
 *
 * ⚠️ Emby nennt sich hier schon mal „fed014e636a7“, das ist die Kennung der
 * Installation, kein Name. Sie in der Oberfläche zu zeigen hilft niemandem;
 * „Emby“ sagt mehr. Plex dagegen liefert echte Namen wie „Bizzy“, und die
 * sind es wert, genannt zu werden.
 */
export function eigenname(name: string, provider: string): string {
  const sauber = (name || '').trim()
  if (!sauber) return ''
  if (sauber.toLowerCase() === provider.toLowerCase()) return ''
  if (/^[0-9a-f]{8,}$/i.test(sauber)) return ''
  return sauber
}

export function anzeigename(provider: string, name: string): string {
  const anbieter = ANBIETER[provider] ?? provider
  const eigen = eigenname(name, provider)
  return eigen ? `${anbieter} (${eigen})` : anbieter
}
