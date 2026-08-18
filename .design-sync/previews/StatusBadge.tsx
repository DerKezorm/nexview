import { StatusBadge } from 'nexview-ui'

/** Der ganze Lebenslauf einer Anfrage auf einen Blick. */
export const AlleZustaende = () => (
  <div className="bg-ink-950 p-6 flex flex-wrap gap-2">
    <StatusBadge status="not_requested" />
    <StatusBadge status="pending_approval" />
    <StatusBadge status="requested" />
    <StatusBadge status="searching" />
    <StatusBadge status="downloaded" />
    <StatusBadge status="blocked" />
    <StatusBadge status="failed" />
  </div>
)
