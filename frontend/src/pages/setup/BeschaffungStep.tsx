import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api } from '../../api/client'
import type { AppSettings, Beschaffung } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { ErrorBanner } from '../../components/ui'

const MODI: Beschaffung[] = ['arr', 'nex']

/**
 * Die Weiche vor den Dienst-Schritten: Radarr und Sonarr, oder nexcrate?
 *
 * ⚠️ **Ein Schalter für Filme und Serien zugleich** – gemischt geht nicht. Er
 * steht hier und nicht später, weil alles danach davon abhängt: Wer nexcrate
 * wählt, sieht weder Profile noch Ordner noch eine Webhook-Adresse (7.1).
 *
 * ⚠️ **Gespeichert wird sofort**, nicht erst am Ende. Die Schritte darunter
 * fragen den Server nach der Betriebsart; eine Wahl, die nur im Browser steht,
 * wäre für sie nicht da.
 */
export function BeschaffungStep({ onDone }: { onDone: (modus: Beschaffung) => void }) {
  const { t } = useTranslation()
  const [fehler, setFehler] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function waehlen(modus: Beschaffung) {
    setBusy(true)
    setFehler(null)
    try {
      await api.put<AppSettings>('/api/settings', { beschaffung: modus })
      onDone(modus)
    } catch (caught) {
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-xl font-bold tracking-tight">{t('setup.beschaffungTitle')}</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-mist-500">
          {t('setup.beschaffungText')}
        </p>
      </div>

      {fehler && <ErrorBanner message={fehler} />}

      {MODI.map((modus) => (
        <button
          key={modus}
          type="button"
          disabled={busy}
          onClick={() => void waehlen(modus)}
          className="flex items-start gap-4 rounded-2xl border border-ink-700 bg-ink-900 p-4 text-left transition-colors hover:border-accent-600 disabled:opacity-60"
        >
          <Symbol
            name={modus === 'arr' ? 'allgemein' : 'herunterladen'}
            className="mt-0.5 h-5 w-5 shrink-0 text-accent-400"
          />
          <span>
            <span className="block font-semibold text-mist-100">{t(`nexcrate.mode.${modus}`)}</span>
            <span className="mt-0.5 block text-sm leading-relaxed text-mist-500">
              {t(`nexcrate.modeDetail.${modus}`)}
            </span>
          </span>
        </button>
      ))}

      <p className="text-xs leading-relaxed text-mist-600">{t('setup.beschaffungNote')}</p>
    </div>
  )
}
