import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { Child, User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { useConfig } from '../../hooks/useConfig'
import { Button, Card } from '../../components/ui'

type MailFeld =
  | 'mail_download_complete'
  | 'mail_request_decided'
  | 'mail_request_pending'
  | 'mail_feedback'
  | 'mail_ticket'
  | 'mail_watch'
  | 'mail_user_imported'
  | 'mail_mediaserver_reconnect'
  | 'mail_storage'
  | 'mail_child_wish'

type Schalter = {
  feld: MailFeld
  labelKey: string
  hintKey: string
  /** Wer die Meldung gar nicht bekommen kann, sieht den Schalter nicht. */
  nurEntscheider?: boolean
  nurAdmin?: boolean
  /** Nur für Konten mit verknüpftem Media-Server-Konto von Belang. */
  nurVerknuepft?: boolean
  /** Nur, wenn im Haus überhaupt nach Speicherplatz gerechnet wird. */
  nurMitSpeicher?: boolean
  /** Nur für Konten, die auch wirklich ein aktives Kinderkonto führen. */
  nurMitKindern?: boolean
  /**
   * Nur für Leute ohne Freigaberecht: Wer selbst freigeben darf, wartet nie
   * auf eine Entscheidung – „freigegeben/abgelehnt" kann ihn nicht erreichen,
   * und ein Schalter ohne mögliche Meldung ist eine Einladung zur Verwirrung.
   */
  nieEntscheider?: boolean
}

/** Reihenfolge nach Häufigkeit, nicht alphabetisch. */
const SCHALTER: Schalter[] = [
  {
    feld: 'mail_download_complete',
    labelKey: 'profile.mailDownloadComplete',
    hintKey: 'profile.mailDownloadCompleteHint',
  },
  {
    // Vorgemerkte Titel. Bewusst getrennt von „deine Anfrage ist fertig": Hier
    // hat der Empfänger nichts angefragt, sondern gewartet, weil ein anderer
    // schneller war. „Ist da" und „neue Folgen" teilen sich einen Haken - die
    // Unterscheidung ist keine, die jemand getrennt abbestellen möchte.
    feld: 'mail_watch',
    labelKey: 'profile.mailWatch',
    hintKey: 'profile.mailWatchHint',
  },
  {
    feld: 'mail_request_decided',
    labelKey: 'profile.mailRequestDecided',
    hintKey: 'profile.mailRequestDecidedHint',
    nieEntscheider: true,
  },
  {
    feld: 'mail_request_pending',
    labelKey: 'profile.mailRequestPending',
    hintKey: 'profile.mailRequestPendingHint',
    nurEntscheider: true,
  },
  {
    feld: 'mail_feedback',
    labelKey: 'profile.mailFeedback',
    hintKey: 'profile.mailFeedbackHint',
    nurEntscheider: true,
  },
  {
    // Für alle sichtbar: Benutzer bekommen Antworten auf ihre Tickets,
    // Administratoren die neuen Anliegen. Ein Schalter für beides.
    feld: 'mail_ticket',
    labelKey: 'profile.mailTicket',
    hintKey: 'profile.mailTicketHint',
  },
  {
    // Nur Administratoren: neue Konten über den Media-Server entstehen sonst
    // lautlos, und niemand merkt, wer dazugekommen ist.
    feld: 'mail_user_imported',
    labelKey: 'profile.mailUserImported',
    hintKey: 'profile.mailUserImportedHint',
    nurAdmin: true,
  },
  {
    // Nur wer ein Plex-Konto verknüpft hat, kann einen abgelaufenen Zugang
    // haben - für alle anderen wäre der Schalter eine Meldung, die nie kommt.
    feld: 'mail_mediaserver_reconnect',
    labelKey: 'profile.mailMediaserverReconnect',
    hintKey: 'profile.mailMediaserverReconnectHint',
    nurVerknuepft: true,
  },
  {
    // Ein Schalter für das ganze Speicher-Thema: abgegeben, entschieden,
    // gewachsen. Was davon einen erreicht, hängt an der Rolle – der
    // Administrator bekommt die Abgaben, alle anderen die Entscheidungen über
    // ihre eigenen. Drei Haken wären drei Zeilen für einen Vorgang.
    //
    // Unsichtbar, solange nicht nach Speicherplatz gerechnet wird: Ein
    // Schalter für eine Meldung, die es nicht geben kann, ist eine Einladung
    // zur Verwirrung.
    feld: 'mail_storage',
    labelKey: 'profile.mailStorage',
    hintKey: 'profile.mailStorageHint',
    nurMitSpeicher: true,
  },
  {
    // Ein Wunsch wartet auf eine Entscheidung, und die Glocke sieht nur, wer
    // die App gerade offen hat. Sichtbar aber erst, wenn es überhaupt ein
    // aktives Kinderkonto gibt - sonst wäre es ein Schalter für eine Meldung,
    // die nicht kommen kann.
    feld: 'mail_child_wish',
    labelKey: 'profile.mailChildWish',
    hintKey: 'profile.mailChildWishHint',
    nurMitKindern: true,
  },
]

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
  const { data: config } = useConfig()
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
      (!s.nurMitSpeicher || Boolean(config?.storage_enabled)) &&
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
