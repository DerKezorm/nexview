import { useTranslation } from 'react-i18next'

import { formatSize } from '../lib/format'

/**
 * Wie viel Platz noch übrig ist — als Kurve über die Zeit.
 *
 * ⚠️ **Gezeichnet wird der FREIE Platz, nicht der belegte.** Der erste Bau
 * zeichnete den belegten auf einer Achse von null bis Gesamtgröße. Das war
 * ehrlich und vollkommen nutzlos: Bei 94 % belegt ist es ein ausgefüllter
 * Kasten mit einer waagerechten Linie obendrauf, und der ganze Zuwachs
 * verschwindet im obersten Zwanzigstel.
 *
 * Der Betreiber fragt nicht „wie viel liegt da", sondern „wie viel habe ich
 * noch und wie lange reicht das". Der freie Platz beantwortet beides in einem
 * Bild: Die Kurve läuft auf die Nulllinie zu, und wie steil sie das tut, ist
 * die Antwort.
 *
 * ⚠️ **Die Nulllinie bleibt die Nulllinie.** Null heißt hier „Platte voll" und
 * ist damit die einzig richtige Untergrenze. Die Achse am kleinsten Messwert
 * beginnen zu lassen wäre die klassische Art, mit einem ehrlichen Diagramm zu
 * lügen — aus zwei Prozent würde eine Rampe.
 *
 * ⚠️ **Handgezeichnetes SVG, keine Diagramm-Bibliothek.** Das Projekt hat
 * bewusst keine; eine Kurve ist ein Pfad und eine Skala. Dasselbe gilt schon
 * für `StorageDistribution` und die Balken der Statistik.
 */

export type VerlaufsPunkt = {
  tag: string
  belegt_bytes: number
  frei_bytes: number
}

/** Ohne diese Mindestzahl wäre die „Kurve" ein Strich zwischen zwei Punkten. */
const MINDESTPUNKTE = 3

const BREITE = 600
const HOEHE = 120

export function Verlaufsdiagramm({ punkte }: { punkte: VerlaufsPunkt[] }) {
  const { t, i18n } = useTranslation()

  // ⚠️ **Kein `null`, sondern ein Satz.** Vorher gab die Kurve bei zu wenigen
  // Punkten nichts zurück — die Karte drumherum wurde aber trotzdem gezeichnet,
  // und auf einer frischen Installation stand dort eine Überschrift über einer
  // leeren Fläche. Wer das sieht, hält es für kaputt; tatsächlich fehlt nur
  // noch Zeit.
  if (punkte.length < MINDESTPUNKTE) {
    return (
      <p className="py-6 text-sm text-mist-500">
        {t('dashboard.trendTooEarly', { tage: MINDESTPUNKTE - punkte.length })}
      </p>
    )
  }

  const hoechste = Math.max(...punkte.map((p) => p.frei_bytes), 1)
  const x = (index: number) => (index / (punkte.length - 1)) * BREITE
  const y = (wert: number) => HOEHE - (wert / hoechste) * HOEHE

  const linie = punkte
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.frei_bytes).toFixed(1)}`)
    .join(' ')
  const flaeche = `${linie} L ${BREITE} ${HOEHE} L 0 ${HOEHE} Z`

  const erster = punkte[0]
  const letzter = punkte[punkte.length - 1]
  const veraenderung = letzter.frei_bytes - erster.frei_bytes

  return (
    <figure className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-2xl font-bold tabular-nums text-mist-100">
          {formatSize(letzter.frei_bytes, i18n.language)}
        </span>
        <span className="text-xs text-mist-600">{t('dashboard.trendFreeLabel')}</span>
      </div>
      <svg
        viewBox={`0 0 ${BREITE} ${HOEHE}`}
        className="h-28 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={t('dashboard.trendAlt', {
          von: erster.tag,
          bis: letzter.tag,
          frei: formatSize(letzter.frei_bytes, i18n.language),
        })}
      >
        <path d={flaeche} fill="var(--color-viz-1)" opacity="0.16" />
        <path
          d={linie}
          fill="none"
          stroke="var(--color-viz-1)"
          strokeWidth="2"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        {/* Die Nulllinie ausdrücklich zeichnen: Sie ist nicht bloß der
            Bildrand, sondern die Aussage „hier ist die Platte voll". */}
        <line
          x1="0"
          y1={HOEHE}
          x2={BREITE}
          y2={HOEHE}
          stroke="var(--color-bad-500)"
          strokeWidth="1"
          strokeDasharray="4 4"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <figcaption className="flex flex-wrap justify-between gap-2 text-xs text-mist-600">
        <span>{erster.tag}</span>
        <span className={veraenderung < 0 ? 'text-warn-500' : undefined}>
          {t(
            veraenderung < 0
              ? 'dashboard.trendShrinking'
              : 'dashboard.trendGrowing',
            { menge: formatSize(Math.abs(veraenderung), i18n.language) },
          )}
        </span>
        <span>{letzter.tag}</span>
      </figcaption>
    </figure>
  )
}
