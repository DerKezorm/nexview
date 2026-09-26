import { useTranslation } from 'react-i18next'

import { Button } from '../../components/ui'

/**
 * Abschluss des Assistenten.
 *
 * Hier wurde früher die Bestätigungsmail für den Administrator nachgeholt. Das
 * Konto gilt seit 1.0.0 aber schon beim Anlegen als bestätigt, wie es über
 * dem Feld steht: Ohne Mailserver kam die Bestätigung nie an, und der
 * Betreiber kam nach der ersten abgelaufenen Sitzung nicht mehr in die eigene
 * Installation.
 */
export function DoneStep({ onFinish }: { onFinish: () => void }) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold tracking-tight">{t('setup.doneTitle')}</h2>
      <p className="text-sm leading-relaxed text-mist-500">{t('setup.doneText')}</p>

      <div>
        <Button type="button" onClick={onFinish}>
          {t('setup.start')}
        </Button>
      </div>
    </div>
  )
}
