import { useTranslation } from 'react-i18next'

import type { AnalyseStand } from '../../api/types'
import { AufraeumTabelle } from '../../components/AufraeumTabelle'
import { BereichsBefunde } from '../../components/BereichsBefunde'
import { Card, Kennzahl, Section } from '../../components/ui'
import { formatSize } from '../../lib/format'
import { TraegerKacheln } from './AnalyseDienste'

/**
 * Reiter „Bibliothek" — was liegt da, wie viel Platz kostet es, stimmt die
 * Buchführung.
 *
 * ⚠️ **Drei Bereiche auf einem Reiter, und das ist kein Zufall.** Bestand,
 * Speicherplatz und Abgleich beantworten eine einzige Frage — was liegt da und
 * ist die Rechnung sauber. In drei Reiter zerlegt müsste man dieselbe Frage
 * dreimal stellen, um sie einmal beantwortet zu bekommen.
 *
 * ⚠️ **Hier stehen bewusst Zahlen, die im Dashboard keine Befunde sind.**
 * „3505 Titel liegen seit über sechs Monaten unangetastet" ist auf einer
 * gewachsenen Anlage der Normalzustand — als Befund wäre das ein Daueralarm,
 * der die echten Befunde daneben entwertet. Als Kennzahl auf dieser Seite ist
 * es eine Auskunft.
 */
export function AnalyseBibliothek({ stand }: { stand: AnalyseStand }) {
  const { t, i18n } = useTranslation()
  const { bibliothek, abgleich } = stand

  return (
    <div className="flex flex-col gap-6">
      <BereichsBefunde bereiche={['bibliothek', 'platz', 'abgleich']} />

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kennzahl
          label={t('analyse.items')}
          wert={String(bibliothek.posten)}
          hinweis={formatSize(bibliothek.medien_bytes, i18n.language)}
        />
        <Kennzahl
          label={t('analyse.house')}
          wert={formatSize(bibliothek.hausbestand_bytes, i18n.language)}
          hinweis={t('analyse.houseHint')}
        />
        <Kennzahl
          label={t('analyse.assigned')}
          wert={formatSize(bibliothek.zugerechnet_bytes, i18n.language)}
          hinweis={t('analyse.assignedHint')}
        />
        <Kennzahl
          label={t('analyse.ghosts')}
          wert={String(bibliothek.geisterposten)}
          hinweis={formatSize(bibliothek.geisterposten_bytes, i18n.language)}
          ton={bibliothek.geisterposten > 0 ? 'warn' : 'normal'}
        />
      </section>

      <TraegerKacheln stand={stand} />

      {/* ⚠️ Der ganze Abschnitt fehlt ohne Medienserver — nicht als leere
          Tabelle, nicht als „du könntest einen verbinden". Eine Auswertung,
          die nichts zu vergleichen hat, schweigt. */}
      {abgleich.moeglich && (
        <Section title={t('analyse.reconciliation')} breit>
          <p className="text-sm text-mist-500">{t('analyse.reconciliationHint')}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <Kennzahl
              label={t('analyse.arrOnly')}
              wert={String(abgleich.arr_ohne_server)}
              hinweis={t('analyse.arrOnlyHint')}
              ton={abgleich.arr_ohne_server > 0 ? 'warn' : 'normal'}
            />
            <Kennzahl
              label={t('analyse.serverOnly')}
              wert={String(abgleich.server_ohne_arr)}
              hinweis={t('analyse.serverOnlyHint')}
            />
            <Kennzahl
              label={t('analyse.unmatched')}
              wert={String(abgleich.nicht_erkannt)}
              hinweis={t('analyse.unmatchedHint')}
            />
            <Kennzahl
              label={t('analyse.duplicates')}
              wert={String(abgleich.doppelt)}
              hinweis={t('analyse.duplicatesHint')}
            />
            <Kennzahl
              label={t('analyse.yearConflicts')}
              wert={String(abgleich.jahr_widerspruch)}
              hinweis={t('analyse.yearConflictsHint')}
            />
            {/* Nur bei mehreren Servern — sonst gibt es nichts zu vergleichen. */}
            {Object.keys(abgleich.je_anbieter).length > 1 && (
              <Kennzahl
                label={t('analyse.providerGap')}
                wert={String(abgleich.anbieter_luecke)}
                hinweis={t('analyse.providerGapHint', {
                  count: Object.keys(abgleich.je_anbieter).length,
                })}
              />
            )}
          </div>

          {Object.keys(abgleich.je_anbieter).length > 1 && (
            <Card className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold">{t('analyse.perProvider')}</h3>
              {Object.entries(abgleich.je_anbieter).map(([anbieter, anzahl]) => (
                <p key={anbieter} className="flex justify-between text-sm">
                  <span className="text-mist-300 capitalize">{anbieter}</span>
                  <span className="tabular-nums text-mist-500">{anzahl}</span>
                </p>
              ))}
            </Card>
          )}

          {/* Beispiele statt nur Zahlen: „vier Jahres-Widersprüche" sagt
              niemandem etwas, „Terrifier (2016 / 2018)" schon. */}
          {(abgleich.beispiele.jahr_widerspruch ?? []).length > 0 && (
            <Card className="flex flex-col gap-1.5">
              <h3 className="text-sm font-semibold">{t('analyse.examples')}</h3>
              <ul className="list-disc pl-5 text-sm text-mist-500">
                {abgleich.beispiele.jahr_widerspruch.map((beispiel) => (
                  <li key={beispiel}>{beispiel}</li>
                ))}
              </ul>
            </Card>
          )}
        </Section>
      )}

      <Section title={t('cleanup.title')} breit>
        <AufraeumTabelle
          pfad="/api/admin/stats/aufraeumen"
          schluessel="admin-aufraeumen"
        />
      </Section>
    </div>
  )
}
