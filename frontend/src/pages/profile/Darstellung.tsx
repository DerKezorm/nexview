import { useTranslation } from 'react-i18next'

import { AUSWAHL } from '../../components/ui'
import type { Theme } from '../../lib/theme'

/**
 * Hell oder dunkel - die Wahl des einzelnen Kontos.
 *
 * ⚠️ **Stand bis eben bei „Sprache & Region" und gehört nicht dorthin.** Sprache
 * und Region sagen, *wo jemand sitzt und was er versteht*; die Darstellung ist
 * eine persönliche Vorliebe wie das Profilbild. Wer sein Konto einrichtet,
 * sucht sie deshalb unter „Profil" — und fand sie hinter einer Ortsangabe.
 *
 * Dieselbe Wahl wie der Schalter oben in der Kopfzeile. Dort wirkt sie sofort,
 * hier erst mit dem Speichern — wie alles andere auf der Seite auch.
 * Gespeichert am Konto, nicht am Browser: Jeder im Haushalt hat so seine
 * eigene Voreinstellung, auch am gemeinsamen Rechner.
 */
export function Darstellung({
  darstellung,
  setDarstellung,
  disabled = false,
}: {
  darstellung: Theme
  setDarstellung: (wert: Theme) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">{t('theme.label')}</h2>

      {/* Kein zweiter Beschriftungstext ueber dem Feld: Er hiesse woertlich
          wie die Ueberschrift darueber. Ein Feld, das seine eigene Ueberschrift
          wiederholt, sieht nach einem Fehler aus. */}
      <label className="flex flex-col gap-1.5">
        <select
          value={darstellung}
          onChange={(event) => setDarstellung(event.target.value as Theme)}
          disabled={disabled}
          className={AUSWAHL}
        >
          <option value="dark">{t('theme.dark')}</option>
          <option value="light">{t('theme.light')}</option>
        </select>
        <span className="text-xs leading-relaxed text-mist-600">{t('profile.themeHint')}</span>
      </label>
    </div>
  )
}
