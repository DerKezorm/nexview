/**
 * Namen der Medienserver, wie ein Mensch sie liest.
 *
 * Steht bewusst hier und nicht in der Logo-Komponente: Eine Datei, die eine
 * Komponente *und* Konstanten ausliefert, bricht das schnelle Neuladen im
 * Entwicklungsmodus – und die Namen werden ohnehin an Stellen gebraucht, an
 * denen kein Logo steht.
 */

export const PROVIDER_NAMES: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  emby: 'Emby',
}

/** Der Anzeigename – unbekannte Anbieter behalten ihren technischen Namen. */
export function providerName(provider: string): string {
  return PROVIDER_NAMES[provider] ?? provider
}
