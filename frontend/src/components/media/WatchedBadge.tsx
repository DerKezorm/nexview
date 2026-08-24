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

/**
 * `on` und `notOn` sind entweder **beide** gefüllt oder beide leer – das
 * entscheidet das Backend, weil nur dort bekannt ist, welche Server verbunden
 * sind. Sind sie leer, heißt das Auge schlicht „gesehen“; das ist der
 * Normalfall mit einem Medienserver und bleibt es auch.
 *
 * Sind sie gefüllt, sind sich zwei verbundene Server uneins. Dann bleibt das
 * Auge grün – gesehen ist gesehen –, aber der Hinweistext verschweigt den
 * Widerspruch nicht. Ohne ihn stünde jemand davor, nähme den Haken auf einem
 * Server weg und verstünde nicht, warum das Auge bleibt.
 */
export function WatchedBadge({
  className = '',
  on = [],
  notOn = [],
}: {
  className?: string
  on?: string[]
  notOn?: string[]
}) {
  const { t } = useTranslation()
  const uneins = on.length > 0 && notOn.length > 0
  const text = uneins
    ? t('status.watchedOnlyOn', {
        on: on.join(', '),
        notOn: notOn.join(', '),
      })
    : t('status.watched')

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
