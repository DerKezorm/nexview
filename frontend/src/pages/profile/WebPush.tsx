import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { Child, PushGeraet, User } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { Symbol } from '../../components/Symbol'
import { Button, Card } from '../../components/ui'
import * as push from '../../lib/push'
import { SCHALTER } from './schalter'
import type { MailFeld } from './schalter'

/** Derselbe Haken wie bei der Mail, mit der Vorsilbe von Web Push. */
type PushFeld =
  Exclude<MailFeld, 'mail_cleanup'> extends `mail_${infer Rest}` ? `push_${Rest}` : never

type Entwurf = Record<PushFeld, boolean>

/**
 * Die Haken der Web-Push-Seite: die der Mail-Seite, ohne den Monatsbericht.
 *
 * Der ist eine Tabelle in einer Mail, keine Meldung für den Sperrbildschirm.
 * Nur die Vormerkungen brauchen einen eigenen Hinweis, weil der Mail-Text von
 * „einer Mail" spricht.
 */
const HAKEN = SCHALTER.filter((s) => s.feld !== 'mail_cleanup').map((s) => ({
  ...s,
  feld: s.feld.replace('mail_', 'push_') as PushFeld,
  hintKey: s.feld === 'mail_watch' ? 'profile.pushWatchHint' : s.hintKey,
}))

function ausUser(user: User): Entwurf {
  return Object.fromEntries(HAKEN.map((h) => [h.feld, user[h.feld]])) as Entwurf
}

/**
 * Web Push: Meldungen aufs Handy, auch wenn Nexview gar nicht offen ist.
 *
 * ⚠️ **Zwei Sorten Einstellung auf einer Seite, und die Trennung steht in der
 * Oberfläche.** Oben: die Erlaubnis, die zu *diesem Browser* gehört, und die
 * Geräte, die sich angemeldet haben. Unten: wobei gemeldet wird, das gilt für
 * das Konto und damit auf allen Geräten gleich. Eine Einstellung, die man am
 * zweiten Gerät nicht wiederfindet und die das nicht sagt, wirkt kaputt.
 *
 * Die Haken werden wie bei der Mail mit einem Knopf gespeichert. Die Geräte
 * dagegen sofort, weil dort der Browser mitredet und ein „Speichern" für eine
 * Erlaubnis, die längst erteilt ist, niemand versteht.
 */
export function WebPush() {
  const { t, i18n } = useTranslation()
  const { user, updateUser } = useAuth()

  /* ⚠️ Die Lage steht erst nach einer Rückfrage beim Service Worker fest,
     deshalb nicht als Anfangswert. Bis dahin gilt „noch nicht erlaubt", das
     ist der harmlose Irrtum von beiden. */
  const [lage, setLage] = useState<push.PushLage>('offen')
  const [eigenerEndpunkt, setEigenerEndpunkt] = useState<string | null>(null)
  /* Drei Zustände, nicht zwei: noch nicht geladen, geladen, ging nicht. Ein
     Fehler, der wie eine leere Liste aussieht, ist keiner, den jemand findet. */
  const [geraete, setGeraete] = useState<PushGeraet[] | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const [probe, setProbe] = useState<'ruhe' | 'unterwegs' | 'fertig'>('ruhe')
  const [vorbelegt, setVorbelegt] = useState(false)

  const [entwurf, setEntwurf] = useState<Entwurf | null>(null)
  const [gespeichert, setGespeichert] = useState(false)
  const [speicherFehler, setSpeicherFehler] = useState<string | null>(null)

  // Nur einmal vorbelegen. Ein Nachladen im Hintergrund darf nicht
  // überschreiben, was gerade angehakt, aber noch nicht gespeichert wurde.
  const vorbelegtAus = useRef(false)
  useEffect(() => {
    if (!user || vorbelegtAus.current) return
    vorbelegtAus.current = true
    setEntwurf(ausUser(user))
  }, [user])

  const meldung = (caught: unknown) =>
    caught instanceof ApiError ? caught.message : t('errors.generic')

  const laden = useCallback(async () => {
    try {
      /* ⚠️ **Erst nachanmelden, dann lesen.** Ein Browser, dessen Erlaubnis
         noch steht, dessen Abonnement der Server aber nicht kennt, heilt sich
         hier von selbst, ohne jemanden zu fragen: Nur die Nachfrage braucht
         einen Klick. Wer sein Gerät bewusst entfernt hat, bleibt draußen. */
      const anmeldung = await push.sicherstellen(i18n.language).catch(() => null)
      if (anmeldung?.geraet.vorbelegt) {
        // Der Server hat gerade alle Haken gesetzt: das Konto neu lesen, sonst
        // zeigt die Liste unten noch den Stand von vorher.
        const frisch = await api.get<User>('/api/auth/me')
        updateUser(frisch)
        setEntwurf(ausUser(frisch))
        setVorbelegt(true)
      }
      const eigen = await push.vorhandene()
      setEigenerEndpunkt(eigen?.endpoint ?? null)
      const liste = await api.get<PushGeraet[]>(
        `/api/push/devices${eigen ? `?endpoint=${encodeURIComponent(eigen.endpoint)}` : ''}`,
      )
      setGeraete(liste)
      setFehler(null)
      setLage(await push.lage())
    } catch (caught) {
      setFehler(meldung(caught))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i18n.language])

  useEffect(() => {
    void laden()
  }, [laden])

  async function erlauben() {
    setLaeuft(true)
    setFehler(null)
    try {
      await push.anmelden(i18n.language)
      await laden()
    } catch (caught) {
      /* Der Browser sagt „denied", sobald jemand die Nachfrage wegklickt, und
         danach fragt er nie wieder. Die Lage wird deshalb neu gelesen, damit
         der Kasten sofort den richtigen Satz zeigt statt weiter zum Klicken
         einzuladen. Ein bewusstes Nein ist kein Fehler und bekommt keine
         Meldung; alles andere sehr wohl. */
      setLage(await push.lage())
      const kennung = caught instanceof Error ? caught.message : String(caught)
      if (kennung === 'push_keine_antwort') setFehler(t('profile.pushNoAnswer'))
      else if (kennung !== 'push_abgelehnt') setFehler(meldung(caught))
    } finally {
      setLaeuft(false)
    }
  }

  async function nachholen() {
    setLaeuft(true)
    setFehler(null)
    try {
      await push.wiederAnmelden(i18n.language)
      await laden()
    } catch (caught) {
      setFehler(meldung(caught))
    } finally {
      setLaeuft(false)
    }
  }

  async function probemeldung() {
    setProbe('unterwegs')
    setFehler(null)
    try {
      const ergebnis = await api.post<{ ok: boolean; message: string }>('/api/push/test', {
        endpoint: eigenerEndpunkt,
      })
      if (!ergebnis.ok) {
        setFehler(ergebnis.message)
        setProbe('ruhe')
        await laden()
        return
      }
      setProbe('fertig')
      await laden()
    } catch (caught) {
      setFehler(meldung(caught))
      setProbe('ruhe')
    }
  }

  async function geraetWeg(geraet: PushGeraet) {
    setFehler(null)
    try {
      if (geraet.this) await push.abmelden(geraet.id)
      else await api.delete(`/api/push/devices/${geraet.id}`)
      await laden()
    } catch (caught) {
      setFehler(meldung(caught))
    }
  }

  const speichern = useMutation({
    mutationFn: (werte: Entwurf) => api.patch<User>('/api/auth/me', werte),
    onMutate: () => {
      setGespeichert(false)
      setSpeicherFehler(null)
    },
    onSuccess: (aktualisiert) => {
      updateUser(aktualisiert)
      setEntwurf(ausUser(aktualisiert))
      setGespeichert(true)
      setVorbelegt(false)
    },
    onError: (caught) => setSpeicherFehler(meldung(caught)),
  })

  // Der Schalter für Kinderwünsche erscheint nur, wenn es auch ein aktives
  // Kinderkonto gibt - dieselbe Regel wie auf der Mail-Seite.
  const darfKinder = user?.role === 'admin' || Boolean(user?.can_manage_children)
  const kinder = useQuery({
    queryKey: ['children'],
    queryFn: () => api.get<Child[]>('/api/children'),
    enabled: darfKinder,
  })
  const hatAktiveKinder = (kinder.data ?? []).some((kind) => kind.is_active)

  if (!user || !entwurf) return null

  const sichtbar = HAKEN.filter(
    (s) =>
      (!s.nurEntscheider || user.can_approve) &&
      (!s.nurAdmin || user.role === 'admin') &&
      (!s.nieEntscheider || !user.can_approve) &&
      (!s.nurVerknuepft || user.mediaserver_linked) &&
      (!s.nurMitKindern || hatAktiveKinder),
  )
  // Ohne angemeldetes Gerät geht ohnehin nichts raus - das gehört gesagt,
  // statt die Haken wirkungslos setzen zu lassen.
  const zustellbar = (geraete?.length ?? 0) > 0
  const geaendert = sichtbar.some((s) => entwurf[s.feld] !== user[s.feld])

  const datum = (wert: string) =>
    new Date(wert).toLocaleDateString(i18n.language, {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    })

  const gesperrt =
    lage === 'unmoeglich' || lage === 'abgelehnt' || lage === 'kein_home' || lage === 'kein_https'

  return (
    /* Zwei echte Spalten statt eines Rasters, wie unter „Konto": Links das
       Gerät und die Geräteliste, rechts die Haken. Ein Raster richtete
       zeilenweise aus, und die kurze Karte links erbte die Höhe der langen
       rechts. */
    <div className="grid items-start gap-4 lg:grid-cols-2">
      <div className="flex flex-col gap-4">
        {/* --- Dieses Gerät ------------------------------------------------ */}
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-semibold">{t('profile.pushTitle')}</h2>
            <p className="mt-1 text-sm text-mist-500">{t('profile.pushIntro')}</p>
          </div>

          {fehler && (
            <p className="rounded-xl border border-accent-600/50 bg-accent-700/15 px-4 py-3 text-sm text-accent-400">
              {fehler}
            </p>
          )}

          <div>
            <h3 className="text-sm font-semibold">{t('profile.pushDeviceTitle')}</h3>
            <p className="mt-0.5 text-xs leading-relaxed text-mist-600">
              {t('profile.pushDeviceHint')}
            </p>
          </div>

          <div
            className={
              'flex flex-wrap items-center gap-4 rounded-xl border px-4 py-3.5 ' +
              (gesperrt ? 'border-ink-700 bg-ink-900' : 'border-accent-500/40 bg-accent-500/[0.07]')
            }
          >
            <div className="min-w-[230px] flex-1">
              <b className="block text-sm font-semibold text-mist-100">
                {t(`profile.pushLage_${lage}`)}
              </b>
              <span className="mt-0.5 block text-xs leading-relaxed text-mist-500">
                {t(`profile.pushLage_${lage}_hint`)}
              </span>
            </div>
            {lage === 'bereit' ? (
              <Button
                type="button"
                variant="ghost"
                onClick={probemeldung}
                disabled={probe === 'unterwegs'}
              >
                {probe === 'unterwegs' ? t('profile.pushTestSent') : t('profile.pushTest')}
              </Button>
            ) : lage === 'erlaubt_ohne_anmeldung' || lage === 'abgemeldet' ? (
              <Button type="button" onClick={nachholen} loading={laeuft}>
                {lage === 'abgemeldet' ? t('profile.pushReenable') : t('profile.pushResubscribe')}
              </Button>
            ) : (
              /* ⚠️ Gesperrt statt weg: Ein Knopf, der nichts bewirkt, schickt
               einen auf die falsche Fährte; kein Knopf lässt einen suchen, ob
               es die Funktion überhaupt gibt. */
              <Button type="button" onClick={erlauben} loading={laeuft} disabled={gesperrt}>
                {t('profile.pushAllow')}
              </Button>
            )}
          </div>
          {probe === 'fertig' && <p className="text-sm text-ok-500">{t('profile.pushTestDone')}</p>}

          {/* ⚠️ Steht immer da, nicht nur auf einem iPhone: Wer am Rechner für
            sein Telefon nachsieht, findet den Schritt sonst nie. */}
          <div>
            <h3 className="text-sm font-medium">{t('profile.pushIosTitle')}</h3>
            <p className="mt-0.5 text-xs leading-relaxed text-mist-600">
              {t('profile.pushIosHint')}
            </p>
          </div>

          <p className="text-xs leading-relaxed text-mist-600">{t('profile.pushRelayHint')}</p>
        </Card>

        {/* --- Geräte ------------------------------------------------------ */}
        <Card className="flex flex-col gap-4">
          <div>
            <h2 className="text-lg font-semibold">{t('profile.pushDevicesTitle')}</h2>
            <p className="mt-1 text-xs leading-relaxed text-mist-600">
              {t('profile.pushDevicesHint')}
            </p>
          </div>

          {geraete === null ? null : geraete.length === 0 ? (
            <p className="text-sm text-mist-600">{t('profile.pushNoDevices')}</p>
          ) : (
            <ul className="flex flex-col">
              {geraete.map((g) => (
                <li
                  key={g.id}
                  className="flex items-center gap-3 border-b border-ink-700 py-2.5 last:border-b-0"
                >
                  <div className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-mist-200">
                      {g.name || t('profile.pushUnknownDevice')}
                      {g.this && (
                        <span className="ml-1 text-accent-400">{t('profile.pushThisDevice')}</span>
                      )}
                    </span>
                    <span className="block text-xs text-mist-600">
                      {t('profile.pushRegisteredAt', {
                        datum: datum(g.created_at),
                      })}
                      {' · '}
                      {g.last_success
                        ? t('profile.pushLastSuccess', {
                            datum: datum(g.last_success),
                          })
                        : t('profile.pushNeverReached')}
                    </span>
                    {g.last_error && g.last_error_at && (
                      <span className="block text-xs text-bad-500">
                        {t('profile.pushLastError', {
                          datum: datum(g.last_error_at),
                          fehler: g.last_error,
                        })}
                      </span>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    className="!px-2.5"
                    aria-label={t('profile.pushRemoveDevice')}
                    title={t('profile.pushRemoveDevice')}
                    onClick={() => geraetWeg(g)}
                  >
                    <Symbol name="loeschen" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* --- Wobei ------------------------------------------------------- */}
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('profile.pushEventsTitle')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('profile.pushEventsIntro')}</p>
        </div>

        {!zustellbar && (
          <p className="rounded-xl border border-accent-600/50 bg-accent-700/15 px-4 py-3 text-sm text-accent-400">
            {t('profile.pushNeedsDevice')}
          </p>
        )}
        {vorbelegt && zustellbar && (
          <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {t('profile.pushPreselected')}
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
                  setEntwurf({
                    ...entwurf,
                    [schalter.feld]: event.target.checked,
                  })
                  setGespeichert(false)
                  setVorbelegt(false)
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
          {speicherFehler && <span className="text-sm text-accent-400">{speicherFehler}</span>}
        </div>

        <p className="text-xs text-mist-600">{t('profile.notificationsBellHint')}</p>
      </Card>
    </div>
  )
}
