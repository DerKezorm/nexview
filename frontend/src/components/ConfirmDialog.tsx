import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './ui'

type ConfirmDialogProps = {
  open: boolean
  title: string
  /** Was genau passiert - ruhig ausführlich, das ist der Sinn der Rückfrage. */
  description: ReactNode
  /** Zusätzlicher Hinweis in Warnfarbe, z. B. "Dateien werden gelöscht". */
  warning?: ReactNode
  /**
   * Was beim letzten Bestätigen schiefging - im Dialog, nicht dahinter.
   *
   * ⚠️ Ein Banner auf der Seite hilft hier nicht: Der Dialog liegt davor und
   * ist modal, die Meldung wäre unsichtbar. Genau so gemeldet - bestätigen,
   * nichts passiert sichtbar, der Dialog bleibt stehen und der einzige Ausweg
   * heißt ausgerechnet "Abbrechen".
   */
  fehler?: ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  /**
   * Weitere Ausgänge, wenn die Frage nicht mit ja/nein zu beantworten ist.
   *
   * Beispiel aus der Freigabe: „Zurückstellen" · „Ablehnen" · „Trotzdem
   * freigeben" – drei verschiedene Entscheidungen. „Abbrechen" bleibt daneben
   * der **Notausgang**: Er tut nichts, und dasselbe passiert bei Escape oder
   * einem Klick daneben. Ein Ausgang, der etwas bewirkt, darf deshalb nie
   * „Abbrechen" heißen.
   *
   * Stehen zwischen Abbrechen und dem Bestätigen-Knopf. `gefahr` färbt einen
   * davon rot – für die ablehnende Wahl.
   */
  weitere?: { label: string; onClick: () => void; gefahr?: boolean }[]
  loading?: boolean
}

/**
 * Rückfrage im Nexview-Stil statt des Browser-Popups.
 *
 * Das Browserfenster sieht auf jedem System anders aus, lässt sich nicht
 * gestalten und erklärt nichts - hier steht dagegen, was wirklich passiert.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  warning,
  fehler,
  confirmLabel,
  onConfirm,
  onCancel,
  weitere,
  loading = false,
}: ConfirmDialogProps) {
  const { t } = useTranslation()
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onCancel()
    }

    document.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    confirmRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onCancel])

  if (!open) return null

  // ⚠️ Portal auf document.body – aus demselben Grund wie bei `Fenster`:
  // Der Dialog wird mitten in einer Karte gerendert, und sobald irgendein
  // Vorfahr einen Stacking-Context aufmacht, klebt das „fixe" Fenster an der
  // Karte statt am Bildschirm. Gemeldet aus der Speicherverwaltung: Die
  // Löschrückfrage hing schief über der Seite, der Bestätigen-Knopf ragte
  // über den Rand.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      {/* `max-h` + eigenes Scrollen: Eine lange Dateiliste darf die Knöpfe
          nicht aus dem Bild schieben – gescrollt wird im Fenster, nicht mit
          der Seite. */}
      <div className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-2xl border border-ink-700 bg-ink-850 p-6 shadow-2xl shadow-black/60">
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
        <div className="mt-2 text-sm leading-relaxed text-mist-300">{description}</div>

        {warning && (
          <p className="mt-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-500">
            {warning}
          </p>
        )}

        {fehler && (
          <p
            className="mt-3 rounded-xl border border-bad-500/40 bg-bad-500/10 px-3 py-2 text-sm text-bad-500"
            role="alert"
          >
            {fehler}
          </p>
        )}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {t('common.cancel')}
          </Button>
          {(weitere ?? []).map((ausgang) => (
            <Button
              key={ausgang.label}
              variant="ghost"
              onClick={ausgang.onClick}
              disabled={loading}
              className={
                ausgang.gefahr
                  ? 'border-bad-500/40 text-bad-500 hover:bg-bad-500/10 hover:text-bad-500'
                  : ''
              }
            >
              {ausgang.label}
            </Button>
          ))}
          <Button ref={confirmRef} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
