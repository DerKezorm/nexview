import { useTranslation } from 'react-i18next'

import type { Datentraeger } from '../api/types'
import { formatSize } from '../lib/format'

/**
 * Wie voll die Platte ist — als Ring.
 *
 * ⚠️ **Drei Stücke, nicht zwei, und das ist die eigentliche Aussage.**
 * „Belegt gegen frei" wäre die naheliegende Aufteilung und eine Halbwahrheit:
 * Auf demselben Träger liegen Sicherungen, Fotos, das Betriebssystem. Ein Ring
 * mit zwei Stücken behauptet stillschweigend, der belegte Platz *sei* die
 * Mediathek — und wer dann aufräumen will, sucht an der falschen Stelle.
 *
 * Deshalb: **Medien** (was Nexview kennt) · **Sonstiges** (der Rest, der auch
 * belegt ist) · **Frei**. Genau drei, und damit noch innerhalb dessen, was die
 * Prüfung auf Farbfehlsichtigkeit hergibt.
 *
 * Bauweise wie `StorageDistribution`: ein Kreis je Stück über `strokeDasharray`,
 * um −90° gedreht, damit oben angefangen wird.
 */

const R = 62
const BREITE = 16
const UMFANG = 2 * Math.PI * R
/** Kleine Lücke zwischen den Stücken — sonst verschwimmen zwei Farben ineinander. */
const LUECKE = 3

export function Datentraegerring({ traeger }: { traeger: Datentraeger }) {
  const { t, i18n } = useTranslation()

  const { gesamt_bytes: gesamt, frei_bytes: frei, medien_bytes: medien } = traeger
  if (gesamt <= 0) return null

  const sonstiges = Math.max(0, gesamt - frei - medien)
  const stuecke = [
    { name: t('dashboard.diskMedia'), bytes: medien, farbe: 'var(--color-viz-1)' },
    { name: t('dashboard.diskOther'), bytes: sonstiges, farbe: 'var(--color-viz-2)' },
    { name: t('dashboard.diskFree'), bytes: frei, farbe: 'var(--color-ink-600)' },
  ].filter((s) => s.bytes > 0)

  let gelaufen = 0
  const belegtAnteil = Math.round(((gesamt - frei) / gesamt) * 100)

  return (
    <figure className="m-0 flex flex-wrap items-center gap-6">
      <svg viewBox="0 0 160 160" className="h-40 w-40 shrink-0" role="img">
        <title>
          {t('dashboard.diskAlt', {
            prozent: belegtAnteil,
            gesamt: formatSize(gesamt, i18n.language),
          })}
        </title>
        <g transform="rotate(-90 80 80)">
          {stuecke.map((stueck) => {
            const laenge = (stueck.bytes / gesamt) * UMFANG
            const strich = Math.max(0, laenge - LUECKE)
            const kreis = (
              <circle
                key={stueck.name}
                cx="80"
                cy="80"
                r={R}
                fill="none"
                stroke={stueck.farbe}
                strokeWidth={BREITE}
                strokeDasharray={`${strich} ${UMFANG - strich}`}
                strokeDashoffset={-gelaufen}
              />
            )
            gelaufen += laenge
            return kreis
          })}
        </g>
        <text
          x="80"
          y="78"
          textAnchor="middle"
          className="fill-mist-100 text-[19px] font-semibold"
        >
          {belegtAnteil}%
        </text>
        <text x="80" y="94" textAnchor="middle" className="fill-mist-600 text-[9px]">
          {t('dashboard.diskUsedShort')}
        </text>
      </svg>

      {/* Beschriftung neben dem Ring, nicht darin: Die Farbe trägt nie allein
          die Aussage, welches Stück gemeint ist. */}
      <figcaption className="flex min-w-48 flex-col gap-2">
        <p className="text-sm text-mist-500">
          {t('dashboard.diskTotal', { gesamt: formatSize(gesamt, i18n.language) })}
        </p>
        {stuecke.map((stueck) => (
          <span key={stueck.name} className="flex items-center gap-2 text-sm">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: stueck.farbe }}
            />
            <span className="text-mist-300">{stueck.name}</span>
            <span className="ml-auto tabular-nums text-mist-500">
              {formatSize(stueck.bytes, i18n.language)}
            </span>
          </span>
        ))}
        {/* Der Satz, der den Ring erst ehrlich macht. */}
        <p className="mt-1 text-xs leading-relaxed text-mist-600">
          {t('dashboard.diskHint')}
        </p>
      </figcaption>
    </figure>
  )
}
