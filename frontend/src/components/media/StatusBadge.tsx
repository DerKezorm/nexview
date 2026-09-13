import { useTranslation } from 'react-i18next'

import type { MediaStatus } from '../../api/types'

/** Farbwelt je Zustand - bewusst dezent, damit die Poster wirken. */
const TONES: Record<MediaStatus, string> = {
  not_requested: 'bg-ink-900/85 text-mist-300 ring-ink-600',
  pending_approval: 'bg-warn-500/20 text-warn-500 ring-warn-500/40',
  // Dieselbe Farbe wie „angefragt": Aus Sicht des Wartenden ist die
  // Freigabe kein eigener Halt, sondern der Beginn des Wartens auf die
  // Suche. Der Unterschied steht im Wort, nicht in der Farbe.
  approved: 'bg-accent-500/20 text-accent-400 ring-accent-500/40',
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
  /** „Lädt gerade“-Prozent aus der Warteschlange – nur bei `searching` gezeigt. */
  fortschritt?: number | null
  /**
   * „Import hängt“: der Grund, solange ein Download zu dieser Anfrage
   * festsitzt. Nur bei `searching`, und nur in der Liste der Entscheider
   * gesetzt.
   */
  importHaengt?: string | null
  className?: string
}

/** Warnfarbe wie „zurückgestellt“: Es wartet auf jemanden. */
const HAENGT = 'bg-warn-500/20 text-warn-500 ring-warn-500/40'

export function StatusBadge({
  status,
  fortschritt = null,
  importHaengt = null,
  className = '',
}: StatusBadgeProps) {
  const { t } = useTranslation()

  // Das Wort wechselt, der Zustand nicht: „lädt“ ist „wird gesucht“ mit
  // sichtbarem Fortschritt. Ein Download kann scheitern und neu anlaufen –
  // deshalb bleibt es dieselbe Farbe und derselbe Status, nur das Wort ist
  // gerade ehrlicher.
  const laedt =
    status === 'searching' && fortschritt !== null && fortschritt !== undefined
  // Ein fertig geladener Download, der nicht importiert wird, stand hier
  // bisher als „Lädt · 100 %“ da. Das war die bequemste Lüge auf der Seite.
  const haengt = status === 'searching' && Boolean(importHaengt)

  return (
    <span
      className={
        'inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-[11px] ' +
        'font-semibold whitespace-nowrap ring-1 backdrop-blur-sm ' +
        (haengt ? HAENGT : TONES[status]) +
        ' ' +
        className
      }
    >
      {haengt
        ? t('status.importHaengt')
        : laedt
          ? t('status.downloading', { prozent: fortschritt })
          : t(`status.${status}`)}
    </span>
  )
}
