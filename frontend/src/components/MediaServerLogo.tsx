/**
 * Das Zeichen eines Medienservers – Plex, Jellyfin oder Emby.
 *
 * Bewusst **inline** und nicht als Bilddatei wie die Logos der
 * Benachrichtigungsdienste unter `assets/`: Ein `<img>` lässt sich nicht
 * einfärben. Hier hängt aber genau daran die Aussage – grün heißt verbunden,
 * gedämpft heißt nicht verbunden. Deshalb `currentColor` und die Farbe von
 * außen.
 *
 * Beide Formen sind einfarbige Silhouetten, so wie die Projekte sie selbst
 * verwenden. Die Aussparungen entstehen über `fill-rule="evenodd"`: Was
 * innerhalb einer ungeraden Zahl von Umrissen liegt, wird gefüllt – beim
 * Jellyfin-Zeichen ergibt das den Ring **und** das Dreieck darin.
 */

import { providerName } from '../lib/mediaserver'

export function MediaServerLogo({
  provider,
  className = 'h-4 w-4',
  title,
}: {
  provider: string
  className?: string
  title?: string
}) {
  // Ein unbekannter Anbieter bekommt kein erfundenes Zeichen – lieber nichts
  // als ein falsches. Der Name daneben trägt die Aussage ohnehin.
  if (provider !== 'plex' && provider !== 'jellyfin' && provider !== 'emby') return null

  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="currentColor"
      fillRule="evenodd"
      role="img"
      aria-label={title ?? providerName(provider)}
    >
      {title && <title>{title}</title>}
      {provider === 'emby' ? (
        // ⚠️ **Nachgezeichnet, nicht das offizielle Zeichen.**
        //
        // Embys Marke ist ein gerundetes Viereck auf der Spitze mit einem
        // ausgesparten Winkel. Das hier ist eine geometrische Annäherung
        // daran – erkennbar, aber nicht maßgetreu. Wer das offizielle
        // Pfad-Datum hat, sollte es ersetzen; es steht unter einer eigenen
        // Marke, und eine schiefe Nachzeichnung ist kein Ersatz.
        <path d="M12 1.2 22.8 12 12 22.8 1.2 12 12 1.2Zm0 3.9L5.1 12l6.9 6.9L18.9 12 12 5.1Zm-1.9 3.2 5.4 3.7-5.4 3.7V8.3Z" />
      ) : provider === 'plex' ? (
        // Kreis mit ausgespartem Winkel.
        <path d="M12 1a11 11 0 1 0 0 22 11 11 0 0 0 0-22Zm-3.1 5h5.2l4.1 6-4.1 6H8.9l4.1-6-4.1-6Z" />
      ) : (
        // Gerundetes Dreieck als Ring, mit einem kleineren darin.
        <path
          d="M12 1.6c1.6 0 4.9 4.4 7.6 9.1 2.7 4.7 3.9 8.1 3.1 9.5-.8 1.4-5.2 2.2-10.7 2.2s-9.9-.8-10.7-2.2C.5 18.8 1.7 15.4 4.4
             10.7 7.1 6 10.4 1.6 12 1.6Zm0 4.8c-.9 0-2.8 2.5-4.4 5.2-1.6 2.7-2.3 4.6-1.8 5.4.5.8 3 1.3 6.2 1.3s5.7-.5
             6.2-1.3c.5-.8-.2-2.7-1.8-5.4-1.6-2.7-3.5-5.2-4.4-5.2Zm0 4c.5 0 1.5 1.3 2.4 2.8.9 1.5 1.2 2.5 1 2.9-.3.4-1.6.7-3.4.7s-3.1-.3-3.4-.7c-.2-.4.1-1.4 1-2.9.9-1.5 1.9-2.8 2.4-2.8Z"
        />
      )}
    </svg>
  )
}
