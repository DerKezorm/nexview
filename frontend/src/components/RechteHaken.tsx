import { useTranslation } from 'react-i18next'

import type { RechteStand } from '../api/types'

/**
 * Ein Recht als Haken, so wie der Server es bewertet (`services/kontorechte.py`).
 *
 * ⚠️ **Hier steht keine Regel.** Ob der Haken frei ist, was gilt und warum,
 * sagt der Server. Einladungsassistent und Kontodialog zeigen damit dieselbe
 * Antwort. Bis zum 12.09.2026 rechnete der Kontodialog selbst und nannte bei 4K
 * mitunter einen Grund, der gar nicht zutraf.
 *
 * Gesperrt zeigt der Haken, was tatsächlich gilt (bei Administratoren also
 * „an“), und darunter den Grund. Ein gesperrter Haken ohne Grund sähe aus wie
 * ein Fehler.
 */
export function RechteHaken({
  label,
  stand,
  wert,
  onChange,
}: {
  label: string
  stand: RechteStand
  wert: boolean
  onChange: (neu: boolean) => void
}) {
  const { t } = useTranslation()
  return (
    <label
      className={'flex items-start gap-2 text-sm ' + (stand.frei ? 'text-mist-300' : 'text-mist-600')}
    >
      <input
        type="checkbox"
        checked={stand.frei ? wert : stand.wirkt}
        disabled={!stand.frei}
        onChange={(ev) => onChange(ev.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500 disabled:opacity-60"
      />
      <span>
        {label}
        {stand.grund && (
          <span className="block text-xs text-mist-600">{t(`rechte.grund.${stand.grund}`)}</span>
        )}
      </span>
    </label>
  )
}
