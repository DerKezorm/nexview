import { MediaServerLogo } from 'nexview-ui'

/**
 * Inline statt Bilddatei, weil genau daran die Aussage haengt: gruen heisst
 * verbunden, gedaempft heisst nicht verbunden. Ein `<img>` liesse sich nicht
 * einfaerben.
 */
export const DreiAnbieter = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-6 text-mist-300" style={{ width: '26rem' }}>
    <span className="flex items-center gap-2"><MediaServerLogo provider="plex" /> Plex</span>
    <span className="flex items-center gap-2"><MediaServerLogo provider="jellyfin" /> Jellyfin</span>
    <span className="flex items-center gap-2"><MediaServerLogo provider="emby" /> Emby</span>
  </div>
)

/** Gruen heisst verbunden, gedaempft heisst nicht verbunden. */
export const Verbindungszustand = () => (
  <div className="bg-ink-950 p-6 flex items-center gap-6 text-mist-300" style={{ width: '26rem' }}>
    <span className="flex items-center gap-2"><MediaServerLogo provider="plex" className="h-5 w-5 text-ok-500" /> verbunden</span>
    <span className="flex items-center gap-2"><MediaServerLogo provider="jellyfin" className="h-5 w-5 text-mist-600" /> nicht verbunden</span>
  </div>
)
