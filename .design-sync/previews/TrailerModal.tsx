import { TrailerModal } from 'nexview-ui'

/** Das offene Trailerfenster - eingebettet ueber youtube-nocookie. */
export const Offen = () => (
  <TrailerModal
    trailer={{ key: 'dQw4w9WgXcQ', name: 'Offizieller Trailer', site: 'YouTube', language: 'de' }}
    onClose={() => {}}
  />
)
