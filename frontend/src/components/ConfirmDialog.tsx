import { useEffect, useRef } from 'react'
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
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
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
  confirmLabel,
  onConfirm,
  onCancel,
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      <div className="w-full max-w-md rounded-2xl border border-ink-700 bg-ink-850 p-6 shadow-2xl shadow-black/60">
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
        <div className="mt-2 text-sm leading-relaxed text-mist-300">{description}</div>

        {warning && (
          <p className="mt-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-500">
            {warning}
          </p>
        )}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {t('common.cancel')}
          </Button>
          <Button ref={confirmRef} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
