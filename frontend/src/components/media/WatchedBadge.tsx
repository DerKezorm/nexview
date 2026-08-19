/**
 * „Schon gesehen" – laut Media-Server.
 *
 * Bewusst **kein** Schild auf dem Poster. Der erste Versuch war eines, und bei
 * einem Titel, der zugleich „Bereits geladen" ist, stapelten sich zwei Kästen
 * übereinander und verdeckten das halbe Bild. Ein Poster ist der Grund, warum
 * jemand hinsieht – Zustandsangaben dürfen es nicht zupflastern.
 *
 * Deshalb ein schlichtes Auge unten in der Leiste, wo Bewertung und Herz schon
 * stehen. Es kostet keine Bildfläche und ist trotzdem auf jeder Kachel an
 * derselben Stelle.
 */

import { useTranslation } from 'react-i18next'

export function WatchedBadge({ className = '' }: { className?: string }) {
  const { t } = useTranslation()
  const text = t('status.watched')

  return (
    <span
      title={text}
      aria-label={text}
      role="img"
      className={
        'inline-flex shrink-0 items-center text-ok-500/80 ' + className
      }
    >
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
        <circle cx="12" cy="12" r="2.6" />
      </svg>
    </span>
  )
}
