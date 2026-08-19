import { useTranslation } from 'react-i18next'

import type { Woche } from '../../lib/kalenderwoche'
import {
  heutigeWoche,
  verschobeneWoche,
  wochenImJahr,
  wochenSpanne,
} from '../../lib/kalenderwoche'

type Props = {
  woche: Woche
  onChange: (woche: Woche) => void
}

function Pfeil({ nach }: { nach: 'links' | 'rechts' }) {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={nach === 'links' ? 'M15 18l-6-6 6-6' : 'M9 18l6-6-6-6'} />
    </svg>
  )
}

const knopfKlassen =
  'rounded-lg border border-ink-700 bg-ink-900 p-2 text-mist-300 transition-colors hover:border-accent-500 hover:text-white'

const feldKlassen =
  'rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-200 focus:border-accent-500 focus:outline-none'

/**
 * Jahr und Kalenderwoche wählen, dazu Pfeile für die Nachbarwochen.
 *
 * Die Pfeile rechnen über den Montag der Woche und nicht über „Wochennummer
 * plus eins“ – sonst käme man von KW 52 nicht ins neue Jahr, und in Jahren mit
 * 53 Wochen fiele eine Woche unter den Tisch.
 */
export function WeekPicker({ woche, onChange }: Props) {
  const { t, i18n } = useTranslation()
  const { von, bis } = wochenSpanne(woche)
  const heute = heutigeWoche()
  const istDieseWoche = heute.jahr === woche.jahr && heute.woche === woche.woche

  // Ein Fenster um das laufende Jahr – weiter zurück oder voraus kennt TMDB
  // ohnehin kaum Termine.
  const jahre = [heute.jahr - 1, heute.jahr, heute.jahr + 1]
  const wochen = Array.from({ length: wochenImJahr(woche.jahr) }, (_, i) => i + 1)

  const spanne = (iso: string) =>
    new Date(iso + 'T12:00:00').toLocaleDateString(i18n.language, {
      day: '2-digit',
      month: 'short',
    })

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() => onChange(verschobeneWoche(woche, -1))}
        aria-label={t('calendar.previousWeek')}
        title={t('calendar.previousWeek')}
        className={knopfKlassen}
      >
        <Pfeil nach="links" />
      </button>

      <select
        value={woche.woche}
        onChange={(event) => onChange({ ...woche, woche: Number(event.target.value) })}
        aria-label={t('calendar.week')}
        className={feldKlassen}
      >
        {wochen.map((nummer) => (
          <option key={nummer} value={nummer}>
            {t('calendar.weekShort', { week: nummer })}
          </option>
        ))}
      </select>

      <select
        value={woche.jahr}
        onChange={(event) => {
          const jahr = Number(event.target.value)
          // In einem Jahr mit 52 Wochen gibt es keine KW 53 – dann auf die
          // letzte vorhandene zurückfallen, statt eine leere Woche zu zeigen.
          onChange({ jahr, woche: Math.min(woche.woche, wochenImJahr(jahr)) })
        }}
        aria-label={t('calendar.year')}
        className={feldKlassen}
      >
        {jahre.map((jahr) => (
          <option key={jahr} value={jahr}>
            {jahr}
          </option>
        ))}
      </select>

      <button
        type="button"
        onClick={() => onChange(verschobeneWoche(woche, 1))}
        aria-label={t('calendar.nextWeek')}
        title={t('calendar.nextWeek')}
        className={knopfKlassen}
      >
        <Pfeil nach="rechts" />
      </button>

      <span className="text-sm text-mist-500">
        {spanne(von)} – {spanne(bis)}
      </span>

      {!istDieseWoche && (
        <button
          type="button"
          onClick={() => onChange(heutigeWoche())}
          className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-mist-400 transition-colors hover:border-accent-500 hover:text-white"
        >
          {t('calendar.thisWeek')}
        </button>
      )}
    </div>
  )
}
