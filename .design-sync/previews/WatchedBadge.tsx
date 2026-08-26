import { WatchedBadge } from 'nexview-ui'

/**
 * Bewusst **kein** Schild auf dem Poster, sondern ein Auge unten in der
 * Leiste: Ein Poster ist der Grund, warum jemand hinsieht - Zustandsangaben
 * duerfen es nicht zupflastern.
 */
export const Gesehen = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-3 text-mist-500" style={{ width: '30rem' }}>
    <WatchedBadge />
    <span className="text-sm">Ein verbundener Server – der Normalfall.</span>
  </div>
)

/**
 * Zwei Server uneins: Das Auge bleibt gruen - gesehen ist gesehen -, aber der
 * Hinweis verschweigt den Widerspruch nicht.
 */
export const ServerUneins = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-3 text-mist-500" style={{ width: '30rem' }}>
    <WatchedBadge on={['plex']} notOn={['jellyfin']} />
    <span className="text-sm">Gesehen auf Plex, nicht auf Jellyfin.</span>
  </div>
)
