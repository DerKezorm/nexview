import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { Child, User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Button, Card } from '../../components/ui'

import { SCHALTER } from './schalter'
import type { MailFeld } from './schalter'

type Entwurf = Record<MailFeld, boolean>

function ausUser(user: User): Entwurf {
  return {
    mail_download_complete: user.mail_download_complete,
    mail_request_decided: user.mail_request_decided,
    mail_request_pending: user.mail_request_pending,
    mail_feedback: user.mail_feedback,
    mail_ticket: user.mail_ticket,
    mail_watch: user.mail_watch,
    mail_user_imported: user.mail_user_imported,
    mail_mediaserver_reconnect: user.mail_mediaserver_reconnect,
    mail_storage: user.mail_storage,
    mail_child_wish: user.mail_child_wish,
    mail_cleanup: user.mail_cleanup,
  }
}

/**
 * E-Mail-Benachrichtigungen - jede einzeln, standardmäßig alle aus.
 *
 * Bewusst mit Speichern-Knopf statt sofortigem Sichern bei jedem Haken: ohne
 * ihn passiert nach dem Klick sichtbar nichts, und man weiß nicht, ob die
 * Einstellung angekommen ist.
 *
 * Die Glocke in der App ist hiervon nicht betroffen; sie bleibt immer aktiv.
 */
export function NotificationSettings() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()

  const [entwurf, setEntwurf] = useState<Entwurf | null>(null)
  const [gespeichert, setGespeichert] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)

  // Nur einmal vorbelegen. Ein Nachladen im Hintergrund darf nicht
  // überschreiben, was gerade angehakt, aber noch nicht gespeichert wurde.
  const vorbelegt = useRef(false)
  useEffect(() => {
    if (!user || vorbelegt.current) return
    vorbelegt.current = true
    setEntwurf(ausUser(user))
  }, [user])

  const speichern = useMutation({
    mutationFn: (werte: Entwurf) => api.patch<User>('/api/auth/me', werte),
    onMutate: () => {
      setGespeichert(false)
      setFehler(null)
    },
    onSuccess: (aktualisiert) => {
      updateUser(aktualisiert)
      setEntwurf(ausUser(aktualisiert))
      setGespeichert(true)
    },
    onError: (caught) =>
      setFehler(caught instanceof ApiError ? caught.message : t('errors.generic')),
  })

  // Der Schalter für Kinderwünsche erscheint nur, wenn es auch ein aktives
  // Kinderkonto gibt. Die Abfrage läuft nur für Konten, die Kinder führen
  // dürfen - sonst antwortet der Server mit 403.
  const darfKinder = user?.role === 'admin' || Boolean(user?.can_manage_children)
  const kinder = useQuery({
    queryKey: ['children'],
    queryFn: () => api.get<Child[]>('/api/children'),
    enabled: darfKinder,
  })
  const hatAktiveKinder = (kinder.data ?? []).some((kind) => kind.is_active)

  if (!user || !entwurf) return null

  const sichtbar = SCHALTER.filter(
    (s) =>
      (!s.nurEntscheider || user.can_approve) &&
      (!s.nurAdmin || user.role === 'admin') &&
      (!s.nieEntscheider || !user.can_approve) &&
      (!s.nurVerknuepft || user.mediaserver_linked) &&
      (!s.nurMitKindern || hatAktiveKinder),
  )
  // Ohne bestätigte Adresse geht ohnehin nichts raus - das gehört gesagt,
  // statt die Haken wirkungslos setzen zu lassen.
  const zustellbar = Boolean(user.email && user.email_verified)
  const geaendert = sichtbar.some((s) => entwurf[s.feld] !== user[s.feld])

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">{t('profile.notifications')}</h2>
        <p className="mt-1 text-sm text-mist-500">{t('profile.notificationsIntro')}</p>
      </div>

      {!zustellbar && (
        <p className="rounded-xl border border-accent-600/50 bg-accent-700/15 px-4 py-3 text-sm text-accent-400">
          {t('profile.notificationsNeedEmail')}
        </p>
      )}

      <div className="flex flex-col gap-3">
        {sichtbar.map((schalter) => (
          <label
            key={schalter.feld}
            className={
              'flex cursor-pointer items-start gap-3 rounded-xl border border-ink-700 px-4 py-3 transition-colors ' +
              (zustellbar ? 'hover:bg-ink-850' : 'opacity-60')
            }
          >
            <input
              type="checkbox"
              checked={entwurf[schalter.feld]}
              disabled={!zustellbar || speichern.isPending}
              onChange={(event) => {
                setEntwurf({ ...entwurf, [schalter.feld]: event.target.checked })
                // Die alte Erfolgsmeldung gilt für diesen Stand nicht mehr.
                setGespeichert(false)
              }}
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
            />
            <span>
              <span className="text-sm font-medium text-mist-100">{t(schalter.labelKey)}</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-mist-600">
                {t(schalter.hintKey)}
              </span>
            </span>
          </label>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => speichern.mutate(entwurf)}
          loading={speichern.isPending}
          disabled={!geaendert || !zustellbar}
        >
          {t('common.save')}
        </Button>

        {gespeichert && !geaendert && (
          <span className="text-sm text-ok-500">{t('profile.notificationsSaved')}</span>
        )}
        {geaendert && !speichern.isPending && (
          <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
        )}
        {fehler && <span className="text-sm text-accent-400">{fehler}</span>}
      </div>

      <p className="text-xs text-mist-600">{t('profile.notificationsBellHint')}</p>
    </Card>
  )
}
