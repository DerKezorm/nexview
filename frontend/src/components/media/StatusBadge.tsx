import { useTranslation } from 'react-i18next'

import type { MediaStatus } from '../../api/types'

/** Farbwelt je Zustand - bewusst dezent, damit die Poster wirken. */
const TONES: Record<MediaStatus, string> = {
  not_requested: 'bg-ink-900/85 text-mist-300 ring-ink-600',
  pending_approval: 'bg-warn-500/20 text-warn-500 ring-warn-500/40',
  requested: 'bg-accent-500/20 text-accent-400 ring-accent-500/40',
  searching: 'bg-accent-500/25 text-accent-400 ring-accent-500/50',
  downloaded: 'bg-ok-500/20 text-ok-500 ring-ok-500/40',
  // Bewusst dasselbe Grün wie „geladen": Es gibt etwas zu sehen – nur eben
  // nicht alles. Der Unterschied steht im Text, nicht in der Farbe.
  partial: 'bg-ok-500/20 text-ok-500 ring-ok-500/40',
  in_library: 'bg-ok-500/20 text-ok-500 ring-ok-500/40',
  rejected: 'bg-ink-900/85 text-mist-500 ring-ink-600',
  failed: 'bg-bad-500/20 text-bad-500 ring-bad-500/40',
  cancelled: 'bg-ink-900/85 text-mist-500 ring-ink-600',
  deleted: 'bg-ink-900/85 text-mist-500 ring-ink-600',
  // Warnfarbe statt Grau: Zurückgestellt ist kein erledigter Zustand,
  // sondern einer, der auf eine Entscheidung wartet.
  deferred: 'bg-ink-950/85 text-warn-500 ring-warn-500/40',
  // Deutlich, aber nicht alarmierend: gesperrt ist eine Entscheidung,
  // kein Fehler.
  blocked: 'bg-bad-500/20 text-bad-500 ring-bad-500/40',
}

type StatusBadgeProps = {
  status: MediaStatus
  className?: string
}

export function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  const { t } = useTranslation()

  return (
    <span
      className={
        'inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-[11px] ' +
        'font-semibold whitespace-nowrap ring-1 backdrop-blur-sm ' +
        TONES[status] +
        ' ' +
        className
      }
    >
      {t(`status.${status}`)}
    </span>
  )
}
