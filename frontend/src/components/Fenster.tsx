import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './ui'

/**
 * Überlagerungsfenster im Nexview-Stil.
 *
 * Eigener kleiner Baustein statt `ConfirmDialog`: Der ist auf eine Frage mit
 * Knöpfen zugeschnitten, hier steht Inhalt drin – eine Ordnerliste, ein Raster
 * aus Plakaten. Gemeinsam bleiben die Dinge, an denen man ein Fenster erkennt:
 * Escape schließt, ein Klick daneben auch, und die Seite dahinter scrollt
 * nicht mit.
 */
export function Fenster({
  offen,
  titel,
  unterzeile,
  onSchliessen,
  fuss,
  breit = false,
  children,
}: {
  offen: boolean
  titel: string
  unterzeile?: string
  onSchliessen: () => void
  /** Die Knöpfe unten rechts – dort sucht man Entscheidungen. */
  fuss?: ReactNode
  /**
   * Platz für eine Tabelle statt für einen Satz.
   *
   * ⚠️ **Ein Schalter und keine freie Breite.** Zwei Maße sind eine
   * Entscheidung, die man beim Bauen trifft; eine Zahl von außen wäre eine,
   * die jede Seite anders trifft — und dann steht dasselbe Fenster an fünf
   * Stellen fünf Finger breit auseinander.
   */
  breit?: boolean
  children: ReactNode
}) {
  const { t } = useTranslation()

  useEffect(() => {
    if (!offen) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onSchliessen()
    }
    document.addEventListener('keydown', onKeyDown)
    const vorher = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = vorher
    }
  }, [offen, onSchliessen])

  if (!offen) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={titel}
      onClick={(event) => {
        if (event.target === event.currentTarget) onSchliessen()
      }}
    >
      <div
        className={
          'flex max-h-[85vh] w-full flex-col rounded-2xl border border-ink-700 ' +
          'bg-ink-850 shadow-2xl shadow-black/60 ' +
          (breit ? 'max-w-5xl' : 'max-w-2xl')
        }
      >
        <div className="flex items-start gap-3 border-b border-ink-700 p-5">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-bold tracking-tight">{titel}</h2>
            {unterzeile && (
              <p className="mt-0.5 truncate font-mono text-xs text-mist-500">{unterzeile}</p>
            )}
          </div>
          {/* ⚠️ Nur **ohne** Fußzeile. Wer unten schon „Abbrechen" oder
              „Fertig" anbietet, bekommt hier sonst einen zweiten Ausgang mit
              derselben Wirkung - gemeldet als „zwei Schließen-Knöpfe". Escape
              und ein Klick daneben schließen ohnehin, in beiden Fällen. */}
          {!fuss && (
            <Button
              variant="ghost"
              onClick={onSchliessen}
              className="shrink-0 px-3 py-1 text-xs"
            >
              {t('common.close')}
            </Button>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
        {fuss && (
          <div className="flex flex-wrap justify-end gap-2 border-t border-ink-700 p-5">
            {fuss}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
