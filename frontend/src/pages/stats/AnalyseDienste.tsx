import { useTranslation } from 'react-i18next'

import type { AnalyseStand, InstanzZeile } from '../../api/types'
import { BereichsBefunde } from '../../components/BereichsBefunde'
import { Card, Kennzahl } from '../../components/ui'
import { formatDate, formatSize } from '../../lib/format'

/**
 * Reiter „Dienste" — je Instanz eine Karte.
 *
 * ⚠️ **Eine Karte je Instanz, keine Tabelle.** Die Angaben sind ungleichartig
 * (ein Zustand, eine Version, zwei Zahlen, eine Liste fremder Meldungen); in
 * Spalten gepresst stünde bei den meisten Instanzen ein Strich, und die eine,
 * die etwas meldet, ginge darin unter.
 *
 * ⚠️ **Die Meldungen der Instanz bleiben in ihrem Wortlaut.** Es ist ihre
 * Aussage, nicht unsere — dieselbe Begründung wie im Warnkasten der
 * Einstellungen.
 */
export function AnalyseDienste({ stand }: { stand: AnalyseStand }) {
  const { t } = useTranslation()

  if (stand.instanzen.length === 0) {
    return <p className="text-sm text-mist-500">{t('analyse.noInstances')}</p>
  }

  return (
    <div className="flex flex-col gap-6">
      <BereichsBefunde bereiche={['dienste']} />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {stand.instanzen.map((instanz) => (
          <InstanzKarte key={instanz.kennung} instanz={instanz} />
        ))}
      </div>
    </div>
  )
}

function InstanzKarte({ instanz }: { instanz: InstanzZeile }) {
  const { t, i18n } = useTranslation()

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">{instanz.name}</h3>
        <span
          className={
            'inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ' +
            (instanz.erreichbar
              ? 'bg-ok-500/10 text-ok-500'
              : 'bg-bad-500/10 text-bad-500')
          }
        >
          <span
            className={
              'h-2 w-2 rounded-full ' +
              (instanz.erreichbar ? 'bg-ok-500' : 'bg-bad-500')
            }
          />
          {instanz.erreichbar ? t('analyse.reachable') : t('analyse.unreachable')}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <Zeile
          name={t('analyse.version')}
          wert={
            instanz.version || '–'
          }
          zusatz={
            instanz.neuere_version
              ? t('analyse.newerAvailable', { version: instanz.neuere_version })
              : undefined
          }
        />
        <Zeile
          name={t('analyse.since')}
          wert={
            instanz.erreichbar_seit
              ? formatDate(instanz.erreichbar_seit.slice(0, 10), i18n.language)
              : '–'
          }
        />
        <Zeile
          name={t('analyse.queue')}
          wert={instanz.warteschlange === null ? '–' : String(instanz.warteschlange)}
          zusatz={
            instanz.warteschlange_haengt
              ? t('analyse.queueStuck', { count: instanz.warteschlange_haengt })
              : undefined
          }
        />
        <Zeile
          name={t('analyse.gaps')}
          wert={instanz.luecken === null ? '–' : String(instanz.luecken)}
          zusatz={
            instanz.luecken_einheit
              ? t(`analyse.gapUnit.${instanz.luecken_einheit}`)
              : undefined
          }
        />
      </dl>

      {/* Der Rückkanal: nur erwähnen, wenn er eingeschaltet ist. Bei jemandem,
          der ihn bewusst abgewählt hat, wäre „aus" eine Meldung ohne Anlass. */}
      {instanz.rueckkanal_aktiv && (
        <p className="text-xs text-mist-600">
          {instanz.rueckkanal_fehler
            ? t('analyse.callbackBroken', { grund: instanz.rueckkanal_fehler })
            : t('analyse.callbackOk')}
        </p>
      )}

      {instanz.meldungen.length > 0 && (
        <div className="rounded-xl border border-warn-500/40 bg-warn-500/5 px-3 py-2">
          <p className="text-xs font-semibold text-warn-500">
            {t('settings.instanceReports')}
          </p>
          <ul className="mt-1 list-disc pl-4 text-xs leading-relaxed text-mist-500">
            {instanz.meldungen.map((meldung) => (
              <li key={meldung.text}>{meldung.text}</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

function Zeile({
  name,
  wert,
  zusatz,
}: {
  name: string
  wert: string
  zusatz?: string
}) {
  return (
    <div>
      <dt className="text-xs tracking-wide text-mist-600 uppercase">{name}</dt>
      <dd className="tabular-nums text-mist-200">
        {wert}
        {zusatz && <span className="ml-1.5 text-xs text-mist-600">{zusatz}</span>}
      </dd>
    </div>
  )
}

/** Wird von „Speicherplatz" mitbenutzt — deshalb hier und nicht privat. */
export function TraegerKacheln({ stand }: { stand: AnalyseStand }) {
  const { t, i18n } = useTranslation()
  if (stand.traeger.length === 0) return null
  return (
    <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {stand.traeger.map((traeger) => (
        <Kennzahl
          key={traeger.gesamt_bytes}
          // ⚠️ **Der Pfad gehört in den Hinweis, nicht in die Beschriftung.**
          // Die Beschriftung einer Kachel steht in Großbuchstaben — was bei
          // „POSTEN" richtig aussieht und bei „/DATA/MOVIES" falsch: Pfade
          // sind auf manchen Dateisystemen groß- und kleinschreibungs-genau,
          // und ein Betreiber liest sie als Vorlage zum Nachsehen.
          label={t('analyse.disk')}
          wert={`${Math.round(traeger.belegt_anteil * 100)} %`}
          // Dieselbe Formatierung wie im Befund daneben. Vorher stand hier
          // „5 TB" und dort „5,02 TB" — dieselbe Zahl, zwei Schreibweisen.
          hinweis={
            t('analyse.diskFree', {
              frei: formatSize(traeger.frei_bytes, i18n.language),
            }) +
            (traeger.ordner.length > 0 ? ` · ${traeger.ordner.join(', ')}` : '')
          }
          ton={traeger.belegt_anteil >= 0.9 ? 'warn' : 'normal'}
        />
      ))}
    </section>
  )
}
