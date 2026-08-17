import { useTranslation } from 'react-i18next'

import type { TicketStatus } from '../api/tickets'

/** Eigene Farbwelt, aber dieselbe Form wie der StatusBadge an den Kacheln. */
const TONES: Record<TicketStatus, string> = {
  open: 'bg-accent-500/20 text-accent-400 ring-accent-500/40',
  in_progress: 'bg-warn-500/20 text-warn-500 ring-warn-500/40',
  closed: 'bg-ink-900/85 text-mist-500 ring-ink-600',
}

export function TicketStatusBadge({
  status,
  className = '',
}: {
  status: TicketStatus
  className?: string
}) {
  const { t } = useTranslation()

  return (
    <span
      className={
        'inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-[11px] font-semibold ' +
        'ring-1 ' +
        TONES[status] +
        ' ' +
        className
      }
    >
      {t(`ticketStatus.${status}`)}
    </span>
  )
}
