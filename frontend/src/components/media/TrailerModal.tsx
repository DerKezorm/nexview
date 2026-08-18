import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import type { Trailer } from '../../api/types'

/**
 * Trailer-Fenster.
 *
 * Das Video liegt bei YouTube - TMDB speichert nur die Kennung. Eingebunden
 * wird über youtube-nocookie.com: dort wird erst beim tatsächlichen Abspielen
 * etwas gesetzt, nicht schon beim Öffnen.
 *
 * Der Rahmen entsteht erst, wenn das Fenster offen ist. Wäre er dauerhaft in
 * der Seite, nähme der Browser schon beim Aufruf der Detailseite Verbindung
 * zu YouTube auf - auch wenn niemand den Trailer sehen will.
 */
export function TrailerModal({ trailer, onClose }: { trailer: Trailer | null; onClose: () => void }) {
  const { t } = useTranslation()
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!trailer) return

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', onKeyDown)
    const vorher = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = vorher
    }
  }, [trailer, onClose])

  if (!trailer) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={trailer.name || t('detail.trailer')}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="relative w-full max-w-4xl">
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label={t('media.close')}
          className="absolute -top-11 right-0 rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-300 transition-colors hover:border-accent-600 hover:text-accent-400"
        >
          ✕
        </button>

        <div className="aspect-video overflow-hidden rounded-2xl border border-ink-700 bg-black shadow-2xl">
          <iframe
            src={`https://www.youtube-nocookie.com/embed/${trailer.key}?autoplay=1&rel=0`}
            title={trailer.name || t('detail.trailer')}
            allow="accelerometer; autoplay; encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
            className="h-full w-full"
          />
        </div>

        {trailer.name && <p className="mt-3 text-center text-sm text-mist-500">{trailer.name}</p>}
      </div>
    </div>
  )
}

/** Dreieck im Kreis - der übliche Abspielknopf. */
export function PlayIcon({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm-1.2 6.1 5 3.5a.5.5 0 0 1 0 .8l-5 3.5a.5.5 0 0 1-.8-.4V8.5a.5.5 0 0 1 .8-.4Z" />
    </svg>
  )
}
