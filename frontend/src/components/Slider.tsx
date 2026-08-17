import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * Waagerechte Leiste mit Pfeilen an den Rändern.
 *
 * Scrollen geht auch ohne die Pfeile - mit Finger, Rad oder Trackpad. Sie sind
 * für die Maus da: ohne sichtbares Bedienelement merkt man am Schreibtisch oft
 * gar nicht, dass rechts noch etwas kommt.
 *
 * Die Pfeile erscheinen nur, wenn es in die jeweilige Richtung etwas zu holen
 * gibt - ein Pfeil, der nichts tut, ist schlimmer als keiner.
 */
export function Slider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const leiste = useRef<HTMLDivElement>(null)
  const [links, setLinks] = useState(false)
  const [rechts, setRechts] = useState(false)

  function pruefen() {
    const el = leiste.current
    if (!el) return
    setLinks(el.scrollLeft > 8)
    // Ein Pixel Reserve: Nachkommastellen bei der Breite sorgen sonst dafür,
    // dass der rechte Pfeil am Ende hängenbleibt.
    setRechts(el.scrollLeft + el.clientWidth < el.scrollWidth - 8)
  }

  function schieben(richtung: -1 | 1) {
    const el = leiste.current
    if (!el) return
    el.scrollBy({ left: richtung * el.clientWidth * 0.85, behavior: 'smooth' })
  }

  return (
    <div className="group/slider relative">
      <div
        ref={(el) => {
          leiste.current = el
          // Einmal messen, sobald das Element steht: sonst wüsste niemand,
          // dass rechts überhaupt etwas kommt, bevor man scrollt.
          if (el) requestAnimationFrame(pruefen)
        }}
        onScroll={pruefen}
        className="-mx-1 flex snap-x snap-mandatory gap-4 overflow-x-auto scroll-smooth px-1 pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {children}
      </div>

      {links && (
        <button
          type="button"
          onClick={() => schieben(-1)}
          aria-label={t('common.scrollLeft')}
          className="absolute top-1/2 -left-2 hidden -translate-y-1/2 rounded-full border border-ink-700 bg-ink-950/90 p-2.5 text-mist-200 opacity-0 backdrop-blur transition-opacity group-hover/slider:opacity-100 hover:border-accent-600 sm:block"
        >
          ‹
        </button>
      )}

      {rechts && (
        <button
          type="button"
          onClick={() => schieben(1)}
          aria-label={t('common.scrollRight')}
          className="absolute top-1/2 -right-2 hidden -translate-y-1/2 rounded-full border border-ink-700 bg-ink-950/90 p-2.5 text-mist-200 opacity-0 backdrop-blur transition-opacity group-hover/slider:opacity-100 hover:border-accent-600 sm:block"
        >
          ›
        </button>
      )}
    </div>
  )
}
