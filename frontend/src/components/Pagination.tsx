/**
 * Die Blätterleiste unter langen Listen.
 *
 * Die Anfrage-Listen wachsen mit der Zeit auf hunderte Einträge; alles auf
 * einmal zu zeigen macht die Seite lang und das Nachladen träge. Der Zustand
 * dazu steht in `hooks/useSeiten.ts`.
 */

import { useTranslation } from 'react-i18next'

type Props = {
  seite: number
  seiten: number
  onSeite: (seite: number) => void
}

export function Pagination({ seite, seiten, onSeite }: Props) {
  const { t } = useTranslation()

  // Bei einer einzigen Seite wäre die Leiste eine Zeile ohne Aussage.
  if (seiten <= 1) return null

  const knopf =
    'rounded-full border border-ink-700 bg-ink-900 px-4 py-1.5 text-sm font-medium ' +
    'text-mist-300 transition-colors hover:border-accent-600 hover:text-mist-100 ' +
    'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-ink-700'

  return (
    <nav className="flex flex-wrap items-center justify-center gap-3" aria-label={t('paging.label')}>
      <button
        type="button"
        className={knopf}
        onClick={() => onSeite(seite - 1)}
        disabled={seite <= 1}
      >
        {t('paging.previous')}
      </button>
      <span className="text-sm tabular-nums text-mist-500">
        {t('paging.position', { page: seite, pages: seiten })}
      </span>
      <button
        type="button"
        className={knopf}
        onClick={() => onSeite(seite + 1)}
        disabled={seite >= seiten}
      >
        {t('paging.next')}
      </button>
    </nav>
  )
}
