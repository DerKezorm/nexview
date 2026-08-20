import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

type SearchInputProps = {
  value: string
  onChange: (value: string) => void
  /** Ersetzt „Titel suchen …" – die Personen-Seite sucht eben keine Titel. */
  placeholder?: string
}

/** Suchfeld mit kurzer Verzögerung, damit nicht bei jedem Tastendruck geladen wird. */
export function SearchInput({ value, onChange, placeholder }: SearchInputProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(value)

  useEffect(() => setDraft(value), [value])

  useEffect(() => {
    if (draft === value) return
    const timer = setTimeout(() => onChange(draft), 350)
    return () => clearTimeout(timer)
  }, [draft, value, onChange])

  return (
    <div className="relative">
      <svg
        viewBox="0 0 20 20"
        className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <circle cx="9" cy="9" r="6" />
        <path d="m13.5 13.5 4 4" strokeLinecap="round" />
      </svg>

      <input
        type="search"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={placeholder ?? t('discover.searchPlaceholder')}
        aria-label={placeholder ?? t('discover.searchPlaceholder')}
        className="w-full rounded-full border border-ink-700 bg-ink-900 py-2.5 pr-10 pl-10 text-sm text-mist-100 placeholder:text-mist-600 transition-colors focus:border-accent-500 focus:outline-none"
      />

      {draft && (
        <button
          type="button"
          onClick={() => {
            setDraft('')
            onChange('')
          }}
          aria-label={t('discover.clearSearch')}
          className="absolute top-1/2 right-3 -translate-y-1/2 rounded-full px-1.5 text-mist-600 transition-colors hover:text-mist-100"
        >
          ✕
        </button>
      )}
    </div>
  )
}
