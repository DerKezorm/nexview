import { useTranslation } from 'react-i18next'

import { KIDS } from './kidsTheme'

/**
 * Filme oder Serien – zwei große Knöpfe.
 *
 * Ein Umschalter statt doppelt so vieler Kategorien: Jede Rubrik kostet eine
 * eigene Abfrage, und sechzehn Kacheln untereinander findet niemand mehr.
 */
export function MediaSwitch({
  value,
  onChange,
}: {
  value: 'movie' | 'tv'
  onChange: (wert: 'movie' | 'tv') => void
}) {
  const { t } = useTranslation()

  return (
    <div className="flex gap-3">
      {(['movie', 'tv'] as const).map((wert) => (
        <button
          key={wert}
          type="button"
          onClick={() => onChange(wert)}
          aria-pressed={value === wert}
          className={
            'flex-1 rounded-3xl px-4 py-4 text-lg font-extrabold transition-transform active:scale-95 ' +
            (value === wert ? 'shadow-lg' : '')
          }
          style={{
            backgroundColor: value === wert ? KIDS.primaer : KIDS.flaeche,
            color: value === wert ? '#ffffff' : KIDS.textLeise,
          }}
        >
          {t(wert === 'movie' ? 'kids.movies' : 'kids.series')}
        </button>
      ))}
    </div>
  )
}
