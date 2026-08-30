import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import type { LaufendeZeile, LaufendStand } from '../api/types'
import { Avatar } from './Avatar'
import { Card } from './ui'

/**
 * Wer schaut gerade was — die einzige lebende Ansicht der ganzen Auswertung.
 *
 * ⚠️ **Die Umrechnung hat drei Zustände, nicht zwei.** „Rechnet der Server um"
 * klingt nach ja/nein und ist es nicht: An echten Servern gemessen meldete Plex
 * `videoDecision: "copy"` und Emby `PlayMethod: "Transcode"` **mit**
 * `IsVideoDirect: true` — beide reichen das Bild durch und rechnen nur den Ton
 * um. Das kostet fast nichts. Rot ist deshalb allein die Bild-Umrechnung; sie
 * ist die, die die CPU frisst.
 *
 * ⚠️ **Steht der Name des Anbieters da statt eines Nexview-Kontos**, heißt das
 * nicht „unbekannte Person", sondern „zu diesem Medienserver-Konto gibt es
 * keine Verknüpfung". Das ist die ehrliche Auskunft — eine geratene Zuordnung
 * wäre schlimmer, dann stünde an einer Wiedergabe der Name von jemand anderem.
 */

const TAKT_MS = 15_000

const TON = {
  direkt: 'border-ok-500/40 bg-ok-500/10 text-ok-500',
  ton: 'border-warn-500/40 bg-warn-500/10 text-warn-500',
  bild: 'border-bad-500/40 bg-bad-500/10 text-bad-500',
} as const

export function LaufendeWiedergaben({ kompakt = false }: { kompakt?: boolean }) {
  const { t } = useTranslation()

  const query = useQuery({
    queryKey: ['laufende-wiedergaben'],
    queryFn: () => api.get<LaufendStand>('/api/admin/analyse/laufend'),
    // ⚠️ Häufiger als die übrigen Abfragen, und das ist hier richtig: „gerade"
    // veraltet in Sekunden. Der Server fragt dabei live bei den Anbietern nach,
    // deshalb nicht noch häufiger.
    refetchInterval: TAKT_MS,
  })

  const stand = query.data
  const laufen = stand?.wiedergaben ?? []

  // Auf dem Dashboard verschwindet der Kasten, wenn nichts läuft — dort zählt
  // jede Zeile. Auf dem eigenen Reiter bleibt er stehen und sagt es.
  if (kompakt && laufen.length === 0) return null

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{t('laufend.title')}</h2>
        {stand && stand.bild_umrechnungen > 0 && (
          <span className="text-sm text-bad-500">
            {t('laufend.transcodingNow', { count: stand.bild_umrechnungen })}
          </span>
        )}
      </div>

      {laufen.length === 0 ? (
        <p className="text-sm text-mist-500">{t('laufend.nothing')}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {laufen.map((zeile, index) => (
            <Zeile key={`${zeile.provider}-${zeile.konto}-${index}`} zeile={zeile} />
          ))}
        </ul>
      )}
    </Card>
  )
}

function Zeile({ zeile }: { zeile: LaufendeZeile }) {
  const { t } = useTranslation()
  const prozent = Math.round((zeile.fortschritt ?? 0) * 100)

  return (
    <li className="flex flex-col gap-2 rounded-2xl border border-ink-700 bg-ink-900/50 px-3 py-2.5">
      <div className="flex items-center gap-3">
        <Avatar url={zeile.avatar_url} name={zeile.konto} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-mist-100">
            {zeile.serie ? `${zeile.serie} · ${zeile.titel}` : zeile.titel}
          </p>
          <p className="truncate text-xs text-mist-600">
            {zeile.konto}
            {zeile.geraet && ` · ${zeile.geraet}`}
            {zeile.anwendung && ` · ${zeile.anwendung}`}
            {` · ${zeile.provider}`}
          </p>
        </div>
        <span
          className={
            'shrink-0 rounded-full border px-2.5 py-1 text-xs font-medium ' +
            TON[zeile.umrechnung as keyof typeof TON]
          }
          title={zeile.grund || undefined}
        >
          {t(`laufend.mode.${zeile.umrechnung}`)}
        </span>
      </div>

      {/* Ein Fortschrittsbalken statt einer Prozentzahl: Man sieht auf einen
          Blick, ob jemand gerade anfängt oder gleich fertig ist. */}
      <div className="flex items-center gap-2">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-ink-700">
          <div
            className="h-full rounded-full bg-accent-500"
            style={{ width: `${prozent}%` }}
          />
        </div>
        <span className="shrink-0 text-xs tabular-nums text-mist-600">
          {zeile.pausiert ? t('laufend.paused') : `${prozent} %`}
        </span>
      </div>

      {/* Nur wenn es etwas zu sagen gibt: Bei einer Direktwiedergabe gibt es
          weder Grund noch Bandbreite, und eine leere Zeile wäre nur Platz. */}
      {(zeile.grund || zeile.bandbreite || zeile.beschleunigung) && (
        <p className="text-xs text-mist-600">
          {[
            zeile.bandbreite ? t('laufend.bandwidth', { kbit: zeile.bandbreite }) : '',
            zeile.beschleunigung
              ? t('laufend.hardware', { art: zeile.beschleunigung })
              : '',
            zeile.grund,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      )}
    </li>
  )
}
