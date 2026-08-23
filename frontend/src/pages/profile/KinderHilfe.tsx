import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { Button } from '../../components/ui'

/**
 * Was Kinderkonten können – und was nicht.
 *
 * Der zweite Teil ist der wichtigere. „Kinderkonto" klingt nach einer
 * Garantie; Nexview kann aber nur begrenzen, was ein Kind hier *anfragen* und
 * *sehen* kann, und die Altersangaben stammen von TMDB, nicht von uns. Wer das
 * nicht weiß, verlässt sich auf etwas, das so nicht gemeint ist.
 */
export function KinderHilfe({ offen, onSchliessen }: { offen: boolean; onSchliessen: () => void }) {
  const { t } = useTranslation()
  const schliessenRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!offen) return

    function beiTaste(event: KeyboardEvent) {
      if (event.key === 'Escape') onSchliessen()
    }

    document.addEventListener('keydown', beiTaste)
    const vorher = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    schliessenRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', beiTaste)
      document.body.style.overflow = vorher
    }
  }, [offen, onSchliessen])

  if (!offen) return null

  const punkte = t('children.helpCan', { returnObjects: true }) as string[]
  const grenzen = t('children.helpCannot', { returnObjects: true }) as string[]

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={t('children.helpTitle')}
      onClick={onSchliessen}
    >
      <div
        className="max-h-[85dvh] w-full max-w-xl overflow-y-auto rounded-3xl border border-ink-700 bg-ink-850 p-6 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="text-xl font-semibold">{t('children.helpTitle')}</h2>
        <p className="mt-2 text-sm text-mist-400">{t('children.helpIntro')}</p>

        <h3 className="mt-5 text-sm font-semibold text-ok-500">{t('children.helpCanTitle')}</h3>
        <ul className="mt-2 flex flex-col gap-2 text-sm text-mist-300">
          {punkte.map((zeile) => (
            <li key={zeile} className="flex gap-2">
              <span aria-hidden="true" className="text-ok-500">
                ✓
              </span>
              {zeile}
            </li>
          ))}
        </ul>

        <h3 className="mt-5 text-sm font-semibold text-warn-500">
          {t('children.helpCannotTitle')}
        </h3>
        <ul className="mt-2 flex flex-col gap-2 text-sm text-mist-300">
          {grenzen.map((zeile) => (
            <li key={zeile} className="flex gap-2">
              <span aria-hidden="true" className="text-warn-500">
                –
              </span>
              {zeile}
            </li>
          ))}
        </ul>

        {/* Der Haftungshinweis steht bewusst hervorgehoben und nicht als
            Fußnote: Er ist der Punkt, an dem sich die Erwartung und das,
            was die App leisten kann, am weitesten auseinanderbewegen. */}
        <p className="mt-5 rounded-2xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-mist-200">
          {t('children.helpDisclaimer')}
        </p>

        <div className="mt-6 flex justify-end">
          <Button ref={schliessenRef} onClick={onSchliessen}>
            {t('common.close')}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
