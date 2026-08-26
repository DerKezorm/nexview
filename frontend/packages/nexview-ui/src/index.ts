/**
 * Die wiederverwendbaren Bausteine aus Nexview.
 *
 * Nichts wird hier nachgebaut: Jede Zeile verweist in die Anwendung unter
 * `frontend/src/components/`. Aufgenommen ist nur, was **ohne Daten** steht -
 * kein `api`, kein `useAuth`, kein Router. Alles andere (Kacheln, Detail-
 * fenster, Benutzermenue) laedt seine Inhalte selbst und waere hier leer.
 */

import './styles.css'

/* --- Grundformen ---------------------------------------------------------- */
export {
  Button,
  Spinner,
  Field,
  ErrorBanner,
  Card,
  PlusKachel,
  RundKnopf,
  Section,
} from '../../../src/components/ui'
export { Avatar } from '../../../src/components/Avatar'
export { Logo } from '../../../src/components/Logo'
export { Symbol } from '../../../src/components/Symbol'
export type { SymbolName } from '../../../src/components/Symbol'

/* --- Text ----------------------------------------------------------------- */
export { Betont, betont } from '../../../src/components/Betont'

/* --- Eingabe und Auswahl -------------------------------------------------- */
export { SearchInput } from '../../../src/components/SearchInput'
export { Slider } from '../../../src/components/Slider'
export { StarRating } from '../../../src/components/StarRating'
export { Umschalter } from '../../../src/components/Umschalter'
export { Reiterreihe } from '../../../src/components/Reiterreihe'
export type { Reiter } from '../../../src/components/Reiterreihe'
export { Pagination, useSeiten, SEITENGROESSE } from '../../../src/components/Pagination'

/* --- Rueckmeldung --------------------------------------------------------- */
export { LoadingBar } from '../../../src/components/LoadingBar'
export { ConfirmDialog } from '../../../src/components/ConfirmDialog'
export { Fenster } from '../../../src/components/Fenster'

/* --- Daten ---------------------------------------------------------------- */
export { StorageDistribution } from '../../../src/components/StorageDistribution'

/* --- Medien --------------------------------------------------------------- */
export { StatusBadge } from '../../../src/components/media/StatusBadge'
export { TicketStatusBadge } from '../../../src/components/TicketStatusBadge'
export { UhdBadge } from '../../../src/components/media/UhdBadge'
export { WatchedBadge } from '../../../src/components/media/WatchedBadge'
export { MediaServerLogo } from '../../../src/components/MediaServerLogo'
export { Poster, RatingBadge } from '../../../src/components/media/Poster'
export { CastStrip } from '../../../src/components/media/CastStrip'
export { PersonPhoto } from '../../../src/components/media/PersonPhoto'
export { TrailerModal, PlayIcon } from '../../../src/components/media/TrailerModal'

/* --- Rahmen --------------------------------------------------------------- */
export { NexviewProvider } from './NexviewProvider'
