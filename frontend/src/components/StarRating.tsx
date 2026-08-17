import { useState } from 'react'
import { useTranslation } from 'react-i18next'

const STERNE = [1, 2, 3, 4, 5]

type Props = {
  value: number
  /** Ohne diese Funktion sind die Sterne nur Anzeige. */
  onChange?: (value: number) => void
  size?: 'sm' | 'md'
}

/** Bewertung von 0 bis 5 Sternen - anklickbar oder nur zum Ansehen. */
export function StarRating({ value, onChange, size = 'md' }: Props) {
  const { t } = useTranslation()
  /** Beim Überfahren die Vorschau zeigen, damit klar ist, was ein Klick bewirkt. */
  const [vorschau, setVorschau] = useState<number | null>(null)

  const anzeige = vorschau ?? value
  const klasse = size === 'sm' ? 'text-base' : 'text-2xl'

  if (!onChange) {
    return (
      <span
        className={`${klasse} leading-none tracking-tight text-accent-500`}
        title={t('feedback.stars', { count: value })}
        aria-label={t('feedback.stars', { count: value })}
      >
        {STERNE.map((stern) => (
          <span key={stern} className={stern <= value ? '' : 'text-ink-600'}>
            ★
          </span>
        ))}
      </span>
    )
  }

  return (
    <span className="flex items-center gap-0.5" onMouseLeave={() => setVorschau(null)}>
      {STERNE.map((stern) => (
        <button
          key={stern}
          type="button"
          aria-label={t('feedback.stars', { count: stern })}
          onMouseEnter={() => setVorschau(stern)}
          onFocus={() => setVorschau(stern)}
          onBlur={() => setVorschau(null)}
          onClick={() => onChange(stern)}
          className={
            `${klasse} leading-none transition hover:scale-110 ` +
            (stern <= anzeige ? 'text-accent-500' : 'text-ink-600')
          }
        >
          ★
        </button>
      ))}
    </span>
  )
}
