import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { api } from '../../api/client'
import { useAuth } from '../../auth/useAuth'
import { Button, Spinner } from '../../components/ui'
import { useConfig } from '../../hooks/useConfig'

type Versand = { sent: boolean; error: string | null }

/**
 * Abschluss des Assistenten.
 *
 * Hier wird die Bestätigungsmail für den Administrator nachgeholt. Beim
 * Anlegen des Kontos ging das noch nicht - da war der Mailserver noch nicht
 * eingerichtet. Die Adresse einfach als bestätigt auszugeben wäre bequem, aber
 * unwahr: ein Tippfehler fiele erst auf, wenn er sich damit aussperrt.
 */
export function DoneStep({ onFinish }: { onFinish: () => void }) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { data: config } = useConfig()

  const offen = user !== null && !user.email_verified
  const kannSenden = config?.mail_configured ?? false

  const senden = useMutation({
    mutationFn: () => api.post<Versand>('/api/auth/me/resend-verification'),
  })

  useEffect(() => {
    if (offen && kannSenden && senden.isIdle) senden.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offen, kannSenden])

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold tracking-tight">{t('setup.doneTitle')}</h2>
      <p className="text-sm leading-relaxed text-mist-500">{t('setup.doneText')}</p>

      {offen && (
        <div className="rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3 text-sm">
          {!kannSenden ? (
            /* Ohne Mailserver lässt sich nichts verschicken - dann bleibt die
               Adresse eben unbestätigt, und das steht auch so da. */
            <p className="text-warn-500">{t('setup.verifyImpossible')}</p>
          ) : senden.isPending ? (
            <p className="flex items-center gap-2 text-mist-500">
              <Spinner /> {t('common.loading')}
            </p>
          ) : senden.data?.sent ? (
            <p className="text-ok-500">
              {t('setup.verifySent', { email: user?.email ?? '' })}
            </p>
          ) : (
            <p className="text-warn-500">
              {senden.data?.error ?? t('setup.verifyFailed')}
            </p>
          )}
        </div>
      )}

      <div>
        <Button type="button" onClick={onFinish}>
          {t('setup.start')}
        </Button>
      </div>
    </div>
  )
}
