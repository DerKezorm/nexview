/**
 * Was zwischen Nexview und der Instanz auseinandergeht.
 *
 * ⚠️ **Warum das ein eigenes Fenster ist.** „Von dir angepasst“ oder „Konflikt“
 * in einem Abzeichen zu lesen, hilft niemandem: Die einzige nützliche Frage ist
 * *was* anders ist. Ohne diese Ansicht bliebe nur, in Radarr nachzusehen und
 * beide Profile von Hand zu vergleichen — bei sechzig Erkennungsmustern eine
 * Arbeit, die niemand macht.
 *
 * Die Spalten stehen bewusst in dieser Richtung: links, was Nexview vorsieht;
 * rechts, was gerade drüben steht.
 */

import { useTranslation } from 'react-i18next'

import type { QualitaetsprofilAbgleich } from '../../api/types'
import { Fenster } from '../../components/Fenster'
import { Button } from '../../components/ui'

export function AdminQualitaetsUnterschiede({
  profilname,
  instanzname,
  abgleich,
  onSchliessen,
  onUebernehmen,
}: {
  profilname: string
  instanzname: string
  abgleich: QualitaetsprofilAbgleich
  onSchliessen: () => void
  /** Neu schreiben - macht aus jedem Zustand wieder „aktuell“. */
  onUebernehmen: () => void
}) {
  const { t } = useTranslation()

  return (
    <Fenster
      offen
      titel={t('qualityDiff.title')}
      unterzeile={`${profilname} · ${instanzname}`}
      onSchliessen={onSchliessen}
      fuss={
        <>
          <Button type="button" variant="ghost" onClick={onSchliessen}>
            {t('qualityDiff.close')}
          </Button>
          <Button type="button" onClick={onUebernehmen}>
            {t('qualityDiff.rewrite')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-600">
          {t(`qualityDiff.lead_${abgleich.stand}`, {
            defaultValue: t('qualityDiff.lead_konflikt'),
          })}
        </p>

        {abgleich.unterschiede.length === 0 ? (
          <p className="text-sm text-mist-500">{t('qualityDiff.none')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <thead>
                <tr className="border-b border-ink-700 text-left text-xs tracking-wide text-mist-600 uppercase">
                  <th className="py-2 pr-4 font-semibold">{t('qualityDiff.colWhat')}</th>
                  <th className="py-2 pr-4 font-semibold">{t('qualityDiff.colShould')}</th>
                  <th className="py-2 font-semibold">{t('qualityDiff.colIs')}</th>
                </tr>
              </thead>
              <tbody>
                {abgleich.unterschiede.map((u, i) => (
                  <tr key={`${u.art}-${u.was}-${i}`} className="border-b border-ink-800/70">
                    <td className="py-2.5 pr-4 text-mist-300">
                      {u.was || t(`qualityDiff.kind_${u.art}`)}
                      {u.was && (
                        <span className="mt-0.5 block text-xs text-mist-600">
                          {t(`qualityDiff.kind_${u.art}`)}
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs tabular-nums text-ok-500">
                      {u.soll || '—'}
                    </td>
                    <td className="py-2.5 font-mono text-xs tabular-nums text-warn-500">
                      {u.ist || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
          {t('qualityDiff.hint')}
        </p>
      </div>
    </Fenster>
  )
}
