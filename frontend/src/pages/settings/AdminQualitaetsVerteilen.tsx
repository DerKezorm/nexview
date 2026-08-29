/**
 * Wohin soll dieses Profil?
 *
 * ⚠️ **Warum das ein eigener Schritt ist und nicht Teil des Assistenten.** Ein
 * Profil liegt in Nexview; auf welchen Instanzen es landet, ist eine Frage, die
 * sich später wieder stellt - wenn eine Instanz dazukommt, wenn eine wegfällt,
 * wenn man es erst einmal nur auf der Testinstanz sehen will. Wäre es Teil des
 * Assistenten, müsste man ihn für jede dieser Änderungen neu durchlaufen.
 *
 * Gezeigt werden **alle** Instanzen des passenden Typs, auch die leeren - sonst
 * sieht man nicht, wohin man es noch schieben könnte.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { QualitaetsprofilFortschritt } from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { Button } from '../../components/ui'
import type { Profil, Stand, Typ } from './qualitaetsprofile-typen'

/** Was hier ausgewählt werden kann - der Zustand entscheidet über die Folgen. */
type Zeile = {
  kennung: string
  name: string
  stand: Stand
  /** Soll es dort (künftig) liegen? */
  gewaehlt: boolean
}

export function AdminQualitaetsVerteilen({
  profil,
  instanzen,
  laeuft = false,
  onSchliessen,
  onSpeichern,
}: {
  profil: Profil
  instanzen: { kennung: string; name: string; typ: Typ }[]
  /** Wird gerade geschrieben? Dann zeigt das Fenster den Fortschritt. */
  laeuft?: boolean
  onSchliessen: () => void
  onSpeichern: (installationen: { instanz: string; stand: Stand }[]) => void
}) {
  const { t } = useTranslation()

  /**
   * Der Stand vom Server, im Sekundentakt.
   *
   * ⚠️ **Warum ueberhaupt gefragt wird.** Das Schreiben haelt eine Verbindung
   * ueber eine Minute offen, weil Radarr jedes Erkennungsmuster einzeln
   * annimmt. Ein Ladepunkt am Knopf sagt in dieser Zeit nur "irgendwas laeuft";
   * gefragt wird deshalb nebenher, wie weit es ist.
   */
  const stand = useQuery({
    queryKey: ['qualitaetsprofil-fortschritt', profil.id],
    queryFn: () =>
      api.get<QualitaetsprofilFortschritt>(
        `/api/settings/qualitaetsprofile/${profil.id}/fortschritt`,
      ),
    enabled: laeuft,
    refetchInterval: 900,
    gcTime: 0,
  })

  const passende = instanzen.filter((i) => i.typ === profil.typ)
  const [zeilen, setZeilen] = useState<Zeile[]>(() =>
    passende.map((i) => {
      const vorhanden = profil.installationen.find((x) => x.instanz === i.kennung)
      const stand = vorhanden?.stand ?? 'nicht-installiert'
      return { kennung: i.kennung, name: i.name, stand, gewaehlt: stand !== 'nicht-installiert' }
    }),
  )

  const umschalten = (kennung: string) =>
    setZeilen((alt) =>
      alt.map((z) => (z.kennung === kennung ? { ...z, gewaehlt: !z.gewaehlt } : z)),
    )

  const neu = zeilen.filter((z) => z.gewaehlt && z.stand === 'nicht-installiert')
  const weg = zeilen.filter((z) => !z.gewaehlt && z.stand !== 'nicht-installiert')
  const etwasZuTun = neu.length > 0 || weg.length > 0

  const speichern = () =>
    onSpeichern(
      zeilen.map((z) => ({
        instanz: z.kennung,
        // Frisch geschoben heißt: deckt sich mit dem, was in Nexview liegt.
        stand: z.gewaehlt
          ? z.stand === 'nicht-installiert'
            ? ('aktuell' as Stand)
            : z.stand
          : ('nicht-installiert' as Stand),
      })),
    )

  return (
    <Fenster
      offen
      titel={t('qualityDistribute.title')}
      unterzeile={profil.name}
      onSchliessen={onSchliessen}
      fuss={
        <>
          <Button
            type="button"
            variant="ghost"
            onClick={onSchliessen}
            disabled={laeuft}
          >
            {t('qualityDistribute.cancel')}
          </Button>
          <Button
            type="button"
            onClick={speichern}
            disabled={!etwasZuTun || laeuft}
            loading={laeuft}
          >
            {t('qualityDistribute.apply')}
          </Button>
        </>
      }
    >
      {laeuft ? (
        <Arbeitsanzeige stand={stand.data} />
      ) : (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-600">{t('qualityDistribute.intro')}</p>

        {passende.length === 0 ? (
          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
            {t('qualityDistribute.noInstances')}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {zeilen.map((z) => (
              <label
                key={z.kennung}
                className={
                  'flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 ' +
                  (z.gewaehlt
                    ? 'border-accent-500/60 bg-accent-500/10'
                    : 'border-ink-700 bg-ink-900')
                }
              >
                <input
                  type="checkbox"
                  checked={z.gewaehlt}
                  onChange={() => umschalten(z.kennung)}
                  className="h-4 w-4 shrink-0 accent-accent-500"
                />
                <span className="flex-1 text-sm text-mist-200">{z.name}</span>
                <span className="text-xs text-mist-600">
                  {t(`qualityProfiles.state.${z.stand}`)}
                </span>
              </label>
            ))}
          </div>
        )}

        {/* Erst sagen, was passiert - dann den Knopf drücken lassen. */}
        {neu.length > 0 && (
          <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
            {t('qualityDistribute.willAdd', {
              anzahl: neu.length,
              instanzen: neu.map((z) => z.name).join(', '),
            })}
          </p>
        )}
        {weg.length > 0 && (
          <p className="rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs leading-relaxed text-warn-500">
            {t('qualityDistribute.willRemove', {
              instanzen: weg.map((z) => z.name).join(', '),
            })}
          </p>
        )}
      </div>
      )}
    </Fenster>
  )
}

/**
 * Was gerade passiert - mit Zahlen statt eines drehenden Rades.
 *
 * ⚠️ **Die Dauer ist kein Fehler und wird deshalb erklärt.** Radarr nimmt
 * Erkennungsmuster einzeln an; sechzig davon dauern rund anderthalb Minuten.
 * Wer das weiß, wartet; wer es nicht weiß, lädt die Seite neu - und genau das
 * darf hier nicht passieren.
 */
function Arbeitsanzeige({ stand }: { stand?: QualitaetsprofilFortschritt }) {
  const { t } = useTranslation()
  const gesamt = stand?.gesamt ?? 0
  const erledigt = stand?.erledigt ?? 0
  const anteil = gesamt > 0 ? Math.round((erledigt / gesamt) * 100) : 0

  return (
    <div className="flex flex-col gap-4 py-2">
      <div>
        <p className="text-sm font-medium text-mist-100">
          {stand?.instanz
            ? t('qualityDistribute.workingOn', { instanz: stand.instanz })
            : t('qualityDistribute.starting')}
        </p>
        {(stand?.von_instanzen ?? 1) > 1 && (
          <p className="mt-0.5 text-xs text-mist-600">
            {t('qualityDistribute.instanceOf', {
              nummer: stand?.instanz_nummer ?? 1,
              gesamt: stand?.von_instanzen ?? 1,
            })}
          </p>
        )}
      </div>

      <div>
        <div className="flex items-baseline justify-between text-xs text-mist-500">
          <span>
            {stand?.schritt === 'profil'
              ? t('qualityDistribute.stepProfile')
              : t('qualityDistribute.stepFormats')}
          </span>
          {gesamt > 0 && stand?.schritt === 'formate' && (
            <span className="font-mono tabular-nums">
              {erledigt} / {gesamt}
            </span>
          )}
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-ink-800">
          <div
            className="h-full rounded-full bg-accent-500 transition-[width] duration-500"
            style={{ width: `${stand?.schritt === 'profil' ? 100 : anteil}%` }}
          />
        </div>
      </div>

      <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
        {t('qualityDistribute.whySlow')}
      </p>
    </div>
  )
}
