/**
 * nexview-ui - die wiederverwendbaren Bausteine aus Nexview.
 *
 * Dieses Paket enthaelt **keinen eigenen Code**: es gibt die Komponenten der
 * Anwendung unveraendert nach aussen weiter. Sie bleiben dort liegen, wo sie
 * sind, und Nexview selbst merkt von diesem Paket nichts.
 *
 * Aufgenommen ist nur, was ohne Serveranbindung vollstaendig funktioniert.
 * Alles, was Daten nachlaedt - Kacheln, Glocke, Benutzermenue, Detailfenster -
 * bleibt bewusst draussen: es wuerde hier zwar rendern, aber halb tot.
 */

import './styles.css'

// --- Grundbausteine (ohne jede Abhaengigkeit) -------------------------------
export { Button, Spinner, Field, ErrorBanner, Card } from '../../../src/components/ui'
export { Avatar } from '../../../src/components/Avatar'
export { LoadingBar } from '../../../src/components/LoadingBar'
export { Logo } from '../../../src/components/Logo'

// --- Brauchen nur Sprache bzw. Navigation ----------------------------------
export { ConfirmDialog } from '../../../src/components/ConfirmDialog'
export { SearchInput } from '../../../src/components/SearchInput'
export { Slider } from '../../../src/components/Slider'
export { StarRating } from '../../../src/components/StarRating'
export { TicketStatusBadge } from '../../../src/components/TicketStatusBadge'

// --- Filmspezifisch, aber datenfrei ----------------------------------------
export { StatusBadge } from '../../../src/components/media/StatusBadge'
export { FilterBar } from '../../../src/components/media/FilterBar'
export { CastStrip } from '../../../src/components/media/CastStrip'
export { Poster, RatingBadge } from '../../../src/components/media/Poster'
export { TrailerModal, PlayIcon } from '../../../src/components/media/TrailerModal'

// --- Der Rahmen, den die Komponenten um sich brauchen ----------------------
export { NexviewProvider } from './NexviewProvider'
