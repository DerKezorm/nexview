import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import { Button, Card, ErrorBanner } from '../../components/ui'

/**
 * Der Reiter „Kinder" ohne Freigabe.
 *
 * Er verschwindet bewusst **nicht**: Wer nicht weiß, dass es Kinderkonten
 * gibt, fragt auch nicht danach. Statt einer leeren Seite steht hier, was die
 * Funktion kann, wo ihre Grenzen liegen – und ein Knopf, der die Freigabe
 * beantragt, ohne dass jemand einen Text formulieren muss.
 */
export function KinderGesperrt() {
  const { t } = useTranslation()
  const [gestellt, setGestellt] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)

  const beantragen = useMutation({
    mutationFn: () => api.post('/api/children/request-permission'),
    onSuccess: () => {
      setFehler(null)
      setGestellt(true)
    },
    onError: (error) => setFehler(error instanceof ApiError ? error.message : String(error)),
  })

  const punkte = t('children.helpCan', { returnObjects: true }) as string[]
  const grenzen = t('children.helpCannot', { returnObjects: true }) as string[]

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-lg font-semibold">{t('children.title')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('children.lockedIntro')}</p>
      </div>

      <Card className="flex flex-col gap-5">
        <div>
          <h3 className="text-sm font-semibold text-ok-500">{t('children.helpCanTitle')}</h3>
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
        </div>

        <div>
          <h3 className="text-sm font-semibold text-warn-500">
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
        </div>

        <p className="rounded-2xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-mist-200">
          {t('children.helpDisclaimer')}
        </p>
      </Card>

      {fehler && <ErrorBanner message={fehler} />}

      {gestellt ? (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {t('children.requestSent')}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <Button onClick={() => beantragen.mutate()} loading={beantragen.isPending}>
            {t('children.requestPermission')}
          </Button>
          <p className="text-xs text-mist-600">{t('children.requestHint')}</p>
        </div>
      )}
    </div>
  )
}
