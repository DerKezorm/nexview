import { TicketStatusBadge } from 'nexview-ui'

export const AlleZustaende = () => (
  <div className="bg-ink-950 p-6 flex flex-wrap gap-2">
    <TicketStatusBadge status="open" />
    <TicketStatusBadge status="in_progress" />
    <TicketStatusBadge status="closed" />
  </div>
)
