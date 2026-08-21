import { useTranslation } from 'react-i18next'

import type { StorageShare } from '../api/types'
import { formatSize } from '../lib/format'

/**
 * Wer belegt wie viel - eine Liste aller Beteiligten und ein Kuchendiagramm.
 *
 * **Der Hausbestand ist im Kuchen nicht dabei.** Er ist kein Mitbewerber,
 * sondern der Boden, auf dem alle stehen: Gemessen an der echten Bibliothek
 * halten die Nutzer 66 GB und das Haus 38.428 GB - der Kuchen waere ein
 * einfarbiger Kreis. Die Frage, die sich hier stellt, ist "wer von meinen
 * Leuten zieht am meisten", und die beantwortet er ohne das Haus.
 *
 * **Genau drei Farben.** Mehr besteht die Pruefung auf Farbfehlsichtigkeit
 * nicht, sobald - wie im Kuchen unvermeidlich - *alle* Stuecke miteinander
 * verglichen werden und nicht nur benachbarte. Alles darueber faellt in
 * "Andere"; in der Liste darunter steht ohnehin jeder einzeln.
 */

const FARBEN = ['var(--color-viz-1)', 'var(--color-viz-2)', 'var(--color-viz-3)']
const ANDERE = 'var(--color-ink-600)'
const MAX_STUECKE = 3

export function StorageDistribution({
  shares,
  houseBytes,
}: {
  shares: StorageShare[]
  houseBytes: number
}) {
  const { t } = useTranslation()

  const personen = shares.filter((a) => a.user_id !== null)
  const belegt = personen.filter((a) => a.used_bytes > 0)
  const groesster = Math.max(1, ...personen.map((a) => a.used_bytes))

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_auto]">
      <div className="flex flex-col gap-1.5">
        {personen.map((anteil, i) => (
          <Zeile
            key={anteil.user_id}
            name={anteil.display_name ?? anteil.username ?? ''}
            bytes={anteil.used_bytes}
            anteilVom={groesster}
            farbe={i < MAX_STUECKE && anteil.used_bytes > 0 ? FARBEN[i] : ANDERE}
          />
        ))}
        {/* Das Haus steht darunter und abgesetzt: gleiche Zahl, andere Art. */}
        <div className="mt-2 border-t border-ink-700 pt-2">
          <Zeile
            name={t('storage.houseLabel')}
            bytes={houseBytes}
            anteilVom={Math.max(groesster, houseBytes)}
            farbe={ANDERE}
            gedimmt
          />
        </div>
      </div>

      <Kuchen belegt={belegt} />
    </div>
  )
}

function Zeile({
  name,
  bytes,
  anteilVom,
  farbe,
  gedimmt = false,
}: {
  name: string
  bytes: number
  anteilVom: number
  farbe: string
  gedimmt?: boolean
}) {
  const { i18n } = useTranslation()

  return (
    <div className="flex items-center gap-3">
      <span
        className={'w-32 shrink-0 truncate text-sm ' + (gedimmt ? 'text-mist-500' : '')}
      >
        {name}
      </span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
        {/* Auch die Null bekommt einen sichtbaren Stummel - sonst fragt man
            sich, warum jemand in der Liste fehlt, obwohl er dasteht. */}
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.max(1.5, (bytes / anteilVom) * 100)}%`,
            background: farbe,
          }}
        />
      </div>
      <span
        className={
          'w-20 shrink-0 text-right text-sm tabular-nums ' +
          (gedimmt ? 'text-mist-500' : 'text-mist-400')
        }
      >
        {formatSize(bytes, i18n.language)}
      </span>
    </div>
  )
}

/** Anteile der Personen untereinander - ohne Hausbestand. */
function Kuchen({ belegt }: { belegt: StorageShare[] }) {
  const { t, i18n } = useTranslation()

  // Ein Kuchen mit einem einzigen Stueck ist ein Kreis und sagt nichts. Dann
  // ist die Zahl selbst die bessere Darstellung - und die steht links schon.
  if (belegt.length < 2) return null

  const sortiert = [...belegt].sort((a, b) => b.used_bytes - a.used_bytes)
  const vorne = sortiert.slice(0, MAX_STUECKE)
  const rest = sortiert.slice(MAX_STUECKE)
  const restSumme = rest.reduce((summe, a) => summe + a.used_bytes, 0)

  const stuecke = [
    ...vorne.map((a, i) => ({
      name: a.display_name ?? a.username ?? '',
      bytes: a.used_bytes,
      farbe: FARBEN[i],
    })),
    ...(restSumme > 0
      ? [{ name: t('storage.others', { count: rest.length }), bytes: restSumme, farbe: ANDERE }]
      : []),
  ]

  const gesamt = stuecke.reduce((summe, s) => summe + s.bytes, 0)

  // Ring statt Vollkreis: In die Mitte passt die Summe, und die will man hier
  // ohnehin wissen.
  const R = 60
  const BREITE = 26
  const umfang = 2 * Math.PI * R
  // 2px Abstand zwischen den Stuecken, damit zwei Farben nie aneinanderstossen.
  const LUECKE = 2

  let gelaufen = 0

  return (
    <figure className="m-0 flex items-center gap-5">
      <svg viewBox="0 0 160 160" className="h-40 w-40 shrink-0" role="img">
        <title>{t('storage.pieTitle')}</title>
        <g transform="rotate(-90 80 80)">
          {stuecke.map((stueck) => {
            const laenge = (stueck.bytes / gesamt) * umfang
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
                strokeDasharray={`${strich} ${umfang - strich}`}
                strokeDashoffset={-gelaufen}
              />
            )
            gelaufen += laenge
            return kreis
          })}
        </g>
        <text
          x="80"
          y="76"
          textAnchor="middle"
          className="fill-mist-100 text-[15px] font-semibold"
        >
          {formatSize(gesamt, i18n.language)}
        </text>
        <text x="80" y="92" textAnchor="middle" className="fill-mist-600 text-[9px]">
          {t('storage.pieCenter')}
        </text>
      </svg>

      {/* Beschriftung neben dem Ring, nicht darin: Die Farbe traegt nie allein
          die Aussage, wer gemeint ist. */}
      <figcaption className="flex flex-col gap-1.5">
        {stuecke.map((stueck) => (
          <span key={stueck.name} className="flex items-center gap-2 text-sm">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
              style={{ background: stueck.farbe }}
              aria-hidden
            />
            <span className="truncate text-mist-300">{stueck.name}</span>
            <span className="tabular-nums text-mist-500">
              {Math.round((stueck.bytes / gesamt) * 100)}%
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  )
}
