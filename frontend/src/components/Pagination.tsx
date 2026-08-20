/**
 * Blättern in langen Listen.
 *
 * Die Anfrage-Listen wachsen mit der Zeit auf hunderte Einträge; alles auf
 * einmal zu zeigen macht die Seite lang und das Nachladen träge. Zwanzig pro
 * Seite ist die Größe, bei der man noch scrollt statt zu suchen.
 *
 * Bewusst rein in der Oberfläche geblättert: Die Endpunkte liefern ohnehin
 * die vollständige Liste (sie ist nach Zustand gefiltert und selten größer
 * als ein paar hundert Zeilen), und so bleiben Zählwerte in den Filterknöpfen
 * korrekt — die zählen über *alles*, nicht über die gerade sichtbare Seite.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

export const SEITENGROESSE = 20

/**
 * Blätter-Zustand für eine Liste.
 *
 * ``schluessel`` ist alles, was die Liste von Grund auf ändert — der gewählte
 * Filter etwa. Ändert er sich, geht es zurück auf Seite 1: Sonst stünde man
 * nach einem Filterwechsel auf Seite 7 einer Liste mit zwei Seiten und sähe
 * nichts.
 */
export function useSeiten<T>(eintraege: T[], schluessel: string) {
  const [seite, setSeite] = useState(1)

  useEffect(() => {
    setSeite(1)
  }, [schluessel])

  const seiten = Math.max(1, Math.ceil(eintraege.length / SEITENGROESSE))
  // Die letzte Seite kann durch Löschen wegfallen, während man darauf steht.
  const aktuell = Math.min(seite, seiten)
  const start = (aktuell - 1) * SEITENGROESSE

  return {
    seite: aktuell,
    seiten,
    sichtbar: eintraege.slice(start, start + SEITENGROESSE),
    setSeite,
  }
}

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
