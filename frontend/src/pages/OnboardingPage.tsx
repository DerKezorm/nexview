import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError, api, logout, uebersetzeFehler } from '../api/client'
import { mitBasis } from '../lib/basis'
import { useMediaServerChallenge } from '../lib/useMediaServerChallenge'
import { Hausordnungstext } from '../components/Hausordnungstext'
import { LanguageSwitcher } from '../components/LanguageSwitcher'
import { Logo } from '../components/Logo'
import { MediaServerLogo } from '../components/MediaServerLogo'
import { MediaServerPrompt } from '../components/MediaServerPrompt'
import { Button, Card, ErrorBanner, Field, Spinner } from '../components/ui'
import { useConfig } from '../hooks/useConfig'

/** Die Hausordnung, wie die Einladung sie mitbringt - ohne Stand eines Kontos. */
type HausordnungSchritt = { titel: string; inhalt: string; quittierbar: boolean }
/** Ein Medienserver der Einladung (`services/einladung_server.py`). */
type ServerFuerPerson = {
  provider: string
  label: string
  /** "konto": Nexview legt dort eines an. "freigabe": Die Person verknüpft ihr eigenes. */
  art: 'konto' | 'freigabe'
  bibliotheken: string[]
  zustand: string
  konto_name: string | null
  fehler: Record<string, unknown> | null
}
type InvitationInfo = {
  email: string
  role: string
  hausordnung: HausordnungSchritt | null
  server?: ServerFuerPerson[]
}
type Eingeloest = { username: string; server?: ServerFuerPerson[] }
type NamenStand = { nexview: boolean; server: Record<string, boolean | null> }
type VerknuepfenStand = { status: string; konto_name: string | null }
type PasswordInfo = { username: string }

/** Rahmen für alle Seiten, die man ohne Anmeldung erreicht. */
function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="nv-glow flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="relative z-10 w-full max-w-md">
        <div className="mb-6 flex items-center justify-between">
          <Logo withWordmark />
          <LanguageSwitcher />
        </div>
        <Card>{children}</Card>
      </div>
    </div>
  )
}

function Laden() {
  const { t } = useTranslation()
  return (
    <p className="flex items-center gap-2 text-sm text-mist-500">
      <Spinner /> {t('common.loading')}
    </p>
  )
}

function Abgelaufen({ nachricht }: { nachricht: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.expiredTitle')}</h1>
      <p className="mt-2 text-sm text-mist-500">{nachricht}</p>
      <Button className="mt-6 w-full" onClick={() => navigate('/')}>
        {t('onboarding.toLogin')}
      </Button>
    </>
  )
}

/** Passwortfelder mit Gleichheitsprüfung - für beide Seiten gleich. */
function PasswordFields({
  password,
  repeat,
  onPassword,
  onRepeat,
  minLength,
}: {
  password: string
  repeat: string
  onPassword: (value: string) => void
  onRepeat: (value: string) => void
  minLength: number
}) {
  const { t } = useTranslation()
  const passtNicht = repeat !== '' && password !== repeat

  return (
    <>
      <Field
        label={t('onboarding.password')}
        type="password"
        value={password}
        onChange={(event) => onPassword(event.target.value)}
        hint={t('adminUsers.passwordHint', { count: minLength })}
        autoComplete="new-password"
        required
      />
      <Field
        label={t('onboarding.passwordRepeat')}
        type="password"
        value={repeat}
        onChange={(event) => onRepeat(event.target.value)}
        hint={passtNicht ? t('onboarding.passwordMismatch') : undefined}
        autoComplete="new-password"
        required
      />
    </>
  )
}

/** Die Balken über den Schritten - wie beim Einrichtungsassistenten. */
function Fortschritt({ namen, aktuell }: { namen: string[]; aktuell: number }) {
  const { t } = useTranslation()
  return (
    <ol className="mb-6 flex items-center gap-2" aria-label={t('setup.progress')}>
      {namen.map((name, i) => (
        <li
          key={name}
          className="flex flex-1 flex-col gap-1.5"
          aria-current={i === aktuell ? 'step' : undefined}
        >
          <span
            className={
              'h-1 rounded-full transition-colors ' +
              (i < aktuell ? 'bg-accent-600' : i === aktuell ? 'bg-accent-500' : 'bg-ink-700')
            }
          />
          <span
            className={'text-[11px] font-medium ' + (i <= aktuell ? 'text-mist-300' : 'text-mist-600')}
          >
            {name}
          </span>
        </li>
      ))}
    </ol>
  )
}

/** Wie die Server dastehen: im letzten Schritt und nach einem gescheiterten Versuch. */
function ServerReihe({
  server,
  mitNexview = false,
}: {
  server: ServerFuerPerson[]
  mitNexview?: boolean
}) {
  const { t } = useTranslation()
  return (
    <ul className="mt-5 flex flex-col gap-2">
      {mitNexview && (
        <li className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm">
          <Logo className="h-5 w-5" />
          <span className="flex-1 text-mist-200">{t('onboarding.rowNexview')}</span>
          <span className="text-xs font-semibold text-ok-500">{t('onboarding.rowDone')}</span>
        </li>
      )}
      {server.map((eintrag) => {
        const fertig = eintrag.zustand === 'fertig'
        const fehlt = eintrag.zustand === 'fehlt'
        return (
          <li
            key={eintrag.provider}
            className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm"
          >
            <MediaServerLogo provider={eintrag.provider} className="h-5 w-5 text-mist-400" />
            <span className="min-w-0 flex-1 text-mist-200">
              {eintrag.art === 'freigabe'
                ? t('onboarding.rowShare', { service: eintrag.label })
                : t('onboarding.rowAccount', { service: eintrag.label })}
              {!fertig && eintrag.fehler && (
                <span className="block text-xs text-warn-500">
                  {uebersetzeFehler(eintrag.fehler, 502)}
                </span>
              )}
            </span>
            <span
              className={
                'text-xs font-semibold ' +
                (fertig ? 'text-ok-500' : fehlt ? 'text-warn-500' : 'text-mist-500')
              }
            >
              {fertig
                ? t('onboarding.rowDone')
                : fehlt
                  ? eintrag.art === 'freigabe'
                    ? t('onboarding.rowLater')
                    : t('onboarding.rowDropped')
                  : t('onboarding.rowOpen')}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * Das eigene Konto beim Anbieter verknüpfen, für eine Freigabe (Plex).
 *
 * Derselbe Ablauf wie beim Anmelden über den Medienserver, nur an die
 * Einladung gebunden und ohne Sitzung. Verknüpft ist hier noch nichts mit
 * Nexview: Das Konto merkt sich die Einladung, freigegeben wird am Ende.
 */
function VerknuepfenSchritt({
  token,
  eintrag,
  onWeiter,
  onZurueck,
}: {
  token: string
  eintrag: ServerFuerPerson
  onWeiter: () => void
  onZurueck: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [verknuepftAls, setVerknuepftAls] = useState<string | null>(eintrag.konto_name)

  const onFertig = useCallback(
    async (antwort: VerknuepfenStand) => {
      setVerknuepftAls(antwort.konto_name ?? '')
      // Damit ein Schritt zurück und wieder vor nicht "nicht verknüpft" zeigt.
      await queryClient.invalidateQueries({ queryKey: ['invitation', token] })
    },
    [queryClient, token],
  )
  const vorgang = useMediaServerChallenge<VerknuepfenStand>({
    startPfad: `/api/onboarding/invitation/${token}/server/${eintrag.provider}/start`,
    abfragePfad: `/api/onboarding/invitation/${token}/server/${eintrag.provider}/poll`,
    auth: false,
    onFertig,
  })

  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">
        {t('onboarding.linkTitle', { service: eintrag.label })}
      </h1>
      <p className="mt-1.5 text-sm text-mist-500">
        {t('onboarding.linkIntro', { service: eintrag.label })}
      </p>
      {verknuepftAls !== null ? (
        <p className="mt-5 flex items-center gap-3 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          <MediaServerLogo provider={eintrag.provider} className="h-5 w-5" />
          {t('onboarding.linkedAs', { name: verknuepftAls })}
        </p>
      ) : vorgang.laeuft && vorgang.start ? (
        <MediaServerPrompt start={vorgang.start} onAbbrechen={vorgang.abbrechen} />
      ) : (
        <Button
          className="mt-5 w-full"
          variant="ghost"
          loading={vorgang.laeuft}
          onClick={() => void vorgang.starten()}
        >
          <MediaServerLogo provider={eintrag.provider} className="h-4 w-4" />
          {t('onboarding.linkButton', { service: eintrag.label })}
        </Button>
      )}
      {vorgang.fehler && (
        <div className="mt-4">
          <ErrorBanner message={vorgang.fehler} />
        </div>
      )}
      <p className="mt-4 rounded-r-xl border-l-2 border-accent-500/60 bg-ink-900/70 px-4 py-3 text-xs leading-relaxed text-mist-400">
        {t('onboarding.linkNote', { service: eintrag.label })}
      </p>
      <div className="mt-6 flex justify-between gap-3">
        <Button variant="ghost" onClick={onZurueck}>
          {t('onboarding.back')}
        </Button>
        <Button disabled={verknuepftAls === null} onClick={onWeiter}>
          {t('onboarding.continue')}
        </Button>
      </div>
    </>
  )
}

/**
 * Einladung einlösen: Willkommen, verknüpfen - falls die Person ein eigenes
 * Konto mitbringt -, Konto, Hausordnung - falls die Einladung sie mitbringt -,
 * fertig.
 *
 * ⚠️ **Angelegt wird erst nach dem letzten Schritt.** Die Entscheidung zur
 * Hausordnung geht mit dem Anlegen an den Server und nicht über
 * `/api/hausordnung/entscheidung`: Diese Seite hat noch kein Konto, und ist im
 * selben Browser ein Administrator angemeldet, landete die Entscheidung sonst
 * an dessen Konto.
 *
 * Mit Medienservern legt der Server dabei zuerst die Konten auf Jellyfin und
 * Emby an. Scheitert eins, steht die Person wieder beim Konto, Passwort und
 * Entscheidung sind noch da, und „Nochmal versuchen“ setzt fort.
 */
export function InvitationPage() {
  const { t, i18n } = useTranslation()
  const { token = '' } = useParams()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4

  const [schritt, setSchritt] = useState('willkommen')
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  /** Wie die Server nach einem gescheiterten Versuch dastehen. */
  const [serverStand, setServerStand] = useState<ServerFuerPerson[] | null>(null)
  const [ergebnis, setErgebnis] = useState<Eingeloest | null>(null)

  const infoQuery = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => api.get<InvitationInfo>(`/api/onboarding/invitation/${token}`),
    retry: false,
  })

  const server = infoQuery.data?.server ?? []
  // Wo Nexview ein Konto anlegt, und wo die Person ihr eigenes mitbringt.
  const neueKonten = server.filter((eintrag) => eintrag.art === 'konto' && eintrag.zustand !== 'fehlt')
  const freigaben = server.filter(
    (eintrag) => eintrag.art === 'freigabe' && eintrag.zustand === 'offen',
  )
  // Ein angefangenes Serverkonto trägt schon einen Namen; ein anderer ginge nicht mehr.
  const festerName = neueKonten.find((eintrag) => eintrag.konto_name)?.konto_name ?? null
  useEffect(() => {
    if (festerName) setUsername((alt) => alt || festerName)
  }, [festerName])

  // Schon beim Tippen zeigen, ob der Name noch frei ist - sonst erfährt man
  // es erst nach dem Absenden.
  const [geprueft, setGeprueft] = useState('')
  useEffect(() => {
    const timer = setTimeout(() => setGeprueft(username.trim()), 400)
    return () => clearTimeout(timer)
  }, [username])

  // Frei heißt: bei Nexview und auf jedem Server, auf dem ein Konto entsteht.
  const verfuegbar = useQuery({
    queryKey: ['invitation-names', token, geprueft],
    queryFn: () =>
      api.get<NamenStand>(
        `/api/onboarding/invitation/${token}/namen?username=${encodeURIComponent(geprueft)}`,
      ),
    enabled: geprueft.length >= 3,
  })

  const annehmen = useMutation({
    mutationFn: (hausordnungAkzeptiert: boolean | null) =>
      api.post<Eingeloest>(`/api/onboarding/invitation/${token}`, {
        username: username.trim(),
        display_name: displayName.trim() || null,
        password,
        hausordnung_akzeptiert: hausordnungAkzeptiert,
      }),
    onSuccess: (antwort) => {
      // Wichtig: Eine eventuell offene fremde Sitzung beenden. Sonst landet
      // der Eingeladene in dem Konto, das im selben Browser noch angemeldet
      // war - typischerweise beim Administrator, der die Einladung gerade
      // verschickt hat.
      //
      // ⚠️ Seit 0.21 muss dafuer der **Server** gefragt werden: Die Sitzung
      // haengt an einem HttpOnly-Cookie, und das kann dieses Skript nicht
      // loeschen. Ein blosses Vergessen im Arbeitsspeicher wuerde den
      // Eingeladenen beim naechsten Seitenaufruf wieder als Administrator
      // hereinlassen.
      void logout()
      setErgebnis(antwort ?? { username: username.trim() })
    },
    onError: (caught) => {
      setError(caught instanceof ApiError ? caught.message : t('errors.network'))
      const stand = caught instanceof ApiError ? caught.data?.server : undefined
      setServerStand(Array.isArray(stand) ? (stand as ServerFuerPerson[]) : null)
      // Scheitert das Anlegen erst nach der Hausordnung - etwa weil der Name
      // inzwischen vergeben ist -, gehört die Meldung dorthin, wo man es beheben kann.
      setSchritt('konto')
    },
  })

  const hausordnung = infoQuery.data?.hausordnung ?? null
  const reihe = [
    'willkommen',
    ...freigaben.map((eintrag) => `verknuepfen:${eintrag.provider}`),
    'konto',
    ...(hausordnung ? ['hausordnung'] : []),
  ]
  const aktuell = Math.max(0, reihe.indexOf(schritt))
  const weiter = () => setSchritt(reihe[Math.min(aktuell + 1, reihe.length - 1)])
  const zurueck = () => setSchritt(reihe[Math.max(aktuell - 1, 0)])
  const verknuepfen = freigaben.find((eintrag) => `verknuepfen:${eintrag.provider}` === schritt)

  function kontoWeiter(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (password !== repeat) {
      setError(t('onboarding.passwordMismatch'))
      return
    }
    // Vor der Hausordnung prüfen, nicht erst danach: Sonst entscheidet jemand
    // über die Regeln und landet dann doch wieder beim Passwort.
    if (password.length < minPassword) {
      setError(t('adminUsers.passwordHint', { count: minPassword }))
      return
    }
    // Nach einem gescheiterten Versuch gilt die Entscheidung von eben weiter.
    if (hausordnung && annehmen.isError && annehmen.variables !== undefined) {
      annehmen.mutate(annehmen.variables)
      return
    }
    if (hausordnung) {
      setSchritt('hausordnung')
      return
    }
    annehmen.mutate(null)
  }

  if (infoQuery.isPending) return <Frame><Laden /></Frame>
  if (infoQuery.isError || !infoQuery.data) {
    return (
      <Frame>
        <Abgelaufen
          nachricht={
            infoQuery.error instanceof ApiError
              ? infoQuery.error.message
              : t('onboarding.expiredText')
          }
        />
      </Frame>
    )
  }

  const schrittNamen = [
    t('onboarding.stepWelcome'),
    ...freigaben.map((eintrag) => eintrag.label),
    t('onboarding.stepAccount'),
    ...(hausordnung ? [t('onboarding.stepHouseRules')] : []),
    t('onboarding.stepDone'),
  ]

  if (ergebnis) {
    const serverDanach = ergebnis.server ?? []
    const teilweise = serverDanach.some((eintrag) => eintrag.zustand === 'fehlt')
    return (
      <Frame>
        <Fortschritt namen={schrittNamen} aktuell={schrittNamen.length - 1} />
        <h1 className="text-2xl font-bold tracking-tight">
          {teilweise ? t('onboarding.donePartlyTitle') : t('onboarding.readyTitle')}
        </h1>
        <p className="mt-2 text-sm text-mist-500">
          {teilweise
            ? t('onboarding.donePartlyText')
            : t('onboarding.readyText', { username: username.trim() })}
        </p>
        {serverDanach.length > 0 && <ServerReihe server={serverDanach} mitNexview />}
        {serverDanach
          .filter((eintrag) => eintrag.art === 'freigabe' && eintrag.zustand === 'fehlt')
          .map((eintrag) => (
            <div
              key={eintrag.provider}
              className="mt-4 rounded-xl border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-xs leading-relaxed text-warn-500"
            >
              {t('onboarding.shareLater', { service: eintrag.label })}
            </div>
          ))}
        <Button
          className="mt-6 w-full"
          onClick={() => {
            // Neu laden, damit die App den alten Anmeldezustand vergisst.
            window.location.href = mitBasis('/')
          }}
        >
          {t('onboarding.toLogin')}
        </Button>
      </Frame>
    )
  }

  const namen = verfuegbar.data
  const nameFrei =
    namen === undefined
      ? undefined
      : namen.nexview && Object.values(namen.server).every((frei) => frei !== false)

  function nameHinweis(): React.ReactNode {
    if (geprueft.length < 3) return t('onboarding.usernameHint')
    if (namen === undefined) return t('onboarding.usernameChecking')
    if (neueKonten.length === 0) {
      return namen.nexview ? t('onboarding.usernameFree') : t('onboarding.usernameTaken')
    }
    const stellen = [
      { key: 'nexview', label: 'Nexview', frei: namen.nexview as boolean | null },
      ...neueKonten.map((eintrag) => ({
        key: eintrag.provider,
        label: eintrag.label,
        frei: namen.server[eintrag.provider] ?? null,
      })),
    ]
    return (
      <span className="flex flex-wrap gap-1.5">
        {stellen.map((stelle) => (
          <span
            key={stelle.key}
            className={
              'rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
              (stelle.frei === false
                ? 'border-accent-600/50 bg-accent-700/15 text-accent-400'
                : stelle.frei === true
                  ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                  : 'border-ink-700 bg-ink-850 text-mist-500')
            }
          >
            {stelle.frei === false
              ? t('onboarding.nameTaken', { service: stelle.label })
              : stelle.frei === true
                ? t('onboarding.nameFree', { service: stelle.label })
                : t('onboarding.nameUnknown', { service: stelle.label })}
          </span>
        ))}
      </span>
    )
  }

  return (
    <Frame>
      <Fortschritt namen={schrittNamen} aktuell={aktuell} />

      {schritt === 'willkommen' && (
        <>
          <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.inviteTitle')}</h1>
          {server.length === 0 ? (
            <p className="mt-1.5 text-sm text-mist-500">{t('onboarding.welcomeInviteIntro')}</p>
          ) : (
            <>
              <p className="mt-1.5 text-sm text-mist-500">{t('onboarding.welcomeListIntro')}</p>
              <ul className="mt-4 flex flex-col gap-2">
                <li className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm text-mist-200">
                  <Logo className="h-5 w-5" />
                  {t('onboarding.welcomeNexview')}
                </li>
                {server
                  .filter((eintrag) => eintrag.zustand !== 'fehlt')
                  .map((eintrag) => (
                    <li
                      key={eintrag.provider}
                      className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm text-mist-200"
                    >
                      <MediaServerLogo provider={eintrag.provider} className="h-5 w-5 text-ok-500" />
                      {eintrag.art === 'freigabe'
                        ? t('onboarding.welcomeShare', { service: eintrag.label })
                        : t('onboarding.welcomeAccount', { service: eintrag.label })}
                    </li>
                  ))}
              </ul>
            </>
          )}
          <Button className="mt-6 w-full" onClick={weiter}>
            {t('onboarding.letsGo')}
          </Button>
        </>
      )}

      {verknuepfen && (
        <VerknuepfenSchritt
          key={verknuepfen.provider}
          token={token}
          eintrag={verknuepfen}
          onWeiter={weiter}
          onZurueck={zurueck}
        />
      )}

      {schritt === 'konto' && (
        <>
          <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.stepAccount')}</h1>
          <p className="mt-1.5 text-sm text-mist-500">
            {t('onboarding.inviteIntro', { email: infoQuery.data.email })}
            {neueKonten.length > 0 &&
              ` ${t('onboarding.accountIntroServers', {
                services: new Intl.ListFormat(i18n.language, { type: 'conjunction' }).format(
                  neueKonten.map((eintrag) => eintrag.label),
                ),
              })}`}
          </p>

          <form onSubmit={kontoWeiter} className="mt-6 flex flex-col gap-4">
            <Field
              label={t('onboarding.username')}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              hint={nameHinweis()}
              autoComplete="username"
              required
              autoFocus
            />
            <Field
              label={t('onboarding.displayName')}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              hint={t('onboarding.displayNameHint')}
              autoComplete="name"
            />
            <PasswordFields
              password={password}
              repeat={repeat}
              onPassword={setPassword}
              onRepeat={setRepeat}
              minLength={minPassword}
            />

            {error && <ErrorBanner message={error} />}
            {serverStand && serverStand.length > 0 && <ServerReihe server={serverStand} />}

            <div className="mt-1 flex gap-3">
              <Button type="button" variant="ghost" onClick={zurueck}>
                {t('onboarding.back')}
              </Button>
              <Button
                type="submit"
                loading={annehmen.isPending}
                disabled={nameFrei === false}
                className="flex-1"
              >
                {serverStand
                  ? t('onboarding.retry')
                  : hausordnung
                    ? t('onboarding.continue')
                    : t('onboarding.createAccount')}
              </Button>
            </div>
          </form>
        </>
      )}

      {schritt === 'hausordnung' && hausordnung && (
        <>
          <h1 className="text-2xl font-bold tracking-tight">
            {hausordnung.titel || t('onboarding.stepHouseRules')}
          </h1>
          <p className="mt-1.5 text-sm text-mist-500">{t('onboarding.houseRulesIntro')}</p>
          <div className="mt-5 max-h-72 overflow-y-auto rounded-xl border border-ink-700 bg-ink-900 p-4 text-sm text-mist-300">
            <Hausordnungstext text={hausordnung.inhalt} />
          </div>
          {hausordnung.quittierbar ? (
            <>
              {/* Wie im Hausordnung-Fenster: Ablehnen links und leiser, aber nicht versteckt. */}
              <div className="mt-5 flex gap-2">
                <Button
                  className="flex-1"
                  variant="ghost"
                  loading={annehmen.isPending && annehmen.variables === false}
                  disabled={annehmen.isPending}
                  onClick={() => annehmen.mutate(false)}
                >
                  {t('hausordnung.ablehnen')}
                </Button>
                <Button
                  className="flex-1"
                  loading={annehmen.isPending && annehmen.variables === true}
                  disabled={annehmen.isPending}
                  onClick={() => annehmen.mutate(true)}
                >
                  {t('hausordnung.akzeptieren')}
                </Button>
              </div>
              <p className="mt-3 text-xs text-mist-600">{t('onboarding.houseRulesNote')}</p>
            </>
          ) : (
            <Button
              className="mt-5 w-full"
              loading={annehmen.isPending}
              onClick={() => annehmen.mutate(null)}
            >
              {t('onboarding.createAccount')}
            </Button>
          )}
          <button
            type="button"
            onClick={() => setSchritt('konto')}
            className="mt-4 text-xs text-mist-500 hover:text-mist-300"
          >
            {t('onboarding.back')}
          </button>
        </>
      )}
    </Frame>
  )
}

/** Passwort setzen - erstes nach dem Anlegen oder ein vergessenes ersetzen. */
export function SetPasswordPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { token = '' } = useParams()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4

  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [fertig, setFertig] = useState(false)

  const infoQuery = useQuery({
    queryKey: ['password-token', token],
    queryFn: () => api.get<PasswordInfo>(`/api/onboarding/password/${token}`),
    retry: false,
  })

  const setzen = useMutation({
    mutationFn: () => api.post(`/api/onboarding/password/${token}`, { password }),
    onSuccess: () => {
      // Derselbe Grund wie beim Einloesen einer Einladung - und hier zaehlt er
      // doppelt: Wer sein Passwort zuruecksetzt, tut das oft, weil jemand
      // anderes an seinem Konto war.
      void logout()
      setFertig(true)
    },
    onError: (caught) =>
      setError(caught instanceof ApiError ? caught.message : t('errors.network')),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (password !== repeat) {
      setError(t('onboarding.passwordMismatch'))
      return
    }
    setzen.mutate()
  }

  if (infoQuery.isPending) return <Frame><Laden /></Frame>
  if (infoQuery.isError || !infoQuery.data) {
    return (
      <Frame>
        <Abgelaufen
          nachricht={
            infoQuery.error instanceof ApiError
              ? infoQuery.error.message
              : t('onboarding.expiredText')
          }
        />
      </Frame>
    )
  }

  if (fertig) {
    return (
      <Frame>
        <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.doneTitle')}</h1>
        <p className="mt-2 text-sm text-mist-500">{t('onboarding.doneText')}</p>
        <Button className="mt-6 w-full" onClick={() => navigate('/', { replace: true })}>
          {t('onboarding.toLogin')}
        </Button>
      </Frame>
    )
  }

  // Konten entstehen nur über Einladungen - dieser Weg ist deshalb immer
  // ein vergessenes Passwort, nie das allererste.
  const { username } = infoQuery.data
  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.resetTitle')}</h1>
      <p className="mt-1.5 text-sm text-mist-500">
        {t('onboarding.resetIntro', { username })}
      </p>

      <form onSubmit={absenden} className="mt-6 flex flex-col gap-4">
        <PasswordFields
          password={password}
          repeat={repeat}
          onPassword={setPassword}
          onRepeat={setRepeat}
          minLength={minPassword}
        />

        {error && <ErrorBanner message={error} />}

        <Button type="submit" loading={setzen.isPending} className="mt-1 w-full">
          {t('onboarding.savePassword')}
        </Button>
      </form>
    </Frame>
  )
}

/** Passwort vergessen: Adresse eingeben, Link anfordern. */
export function ForgotPasswordPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')

  const anfordern = useMutation({
    mutationFn: () =>
      api.post<{ message: string }>('/api/onboarding/forgot-password', {
        email: email.trim(),
      }),
  })

  function absenden(event: FormEvent) {
    event.preventDefault()
    anfordern.mutate()
  }

  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.forgotTitle')}</h1>
      <p className="mt-1.5 text-sm text-mist-500">{t('onboarding.forgotIntro')}</p>

      {anfordern.isSuccess ? (
        <>
          {/* Absichtlich dieselbe Antwort, ob es die Adresse gibt oder nicht -
              sonst könnte hier jeder durchprobieren, wer ein Konto hat. */}
          <p className="mt-6 rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
            {anfordern.data.message}
          </p>
          <Button className="mt-4 w-full" onClick={() => navigate('/', { replace: true })}>
            {t('onboarding.toLogin')}
          </Button>
        </>
      ) : (
        <form onSubmit={absenden} className="mt-6 flex flex-col gap-4">
          <Field
            label={t('onboarding.email')}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            required
            autoFocus
          />
          <Button type="submit" loading={anfordern.isPending} className="mt-1 w-full">
            {t('onboarding.requestLink')}
          </Button>
          <button
            type="button"
            onClick={() => navigate('/')}
            className="text-xs text-mist-600 underline-offset-2 hover:text-mist-300 hover:underline"
          >
            {t('onboarding.toLogin')}
          </button>
        </form>
      )}
    </Frame>
  )
}

/** Adresse bestätigen - ein Klick, dann steht das Ergebnis da. */
export function VerifyEmailPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { token = '' } = useParams()

  const bestaetigen = useMutation({
    mutationFn: () => api.post(`/api/onboarding/verify/${token}`),
  })

  // Der Link soll ohne weiteres Zutun wirken.
  useEffect(() => {
    bestaetigen.mutate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  if (bestaetigen.isPending || bestaetigen.isIdle) return <Frame><Laden /></Frame>

  if (bestaetigen.isError) {
    return (
      <Frame>
        <Abgelaufen
          nachricht={
            bestaetigen.error instanceof ApiError
              ? bestaetigen.error.message
              : t('onboarding.expiredText')
          }
        />
      </Frame>
    )
  }

  return (
    <Frame>
      <h1 className="text-2xl font-bold tracking-tight">{t('onboarding.verifiedTitle')}</h1>
      <p className="mt-2 text-sm text-mist-500">{t('onboarding.verifiedText')}</p>
      <Button className="mt-6 w-full" onClick={() => navigate('/', { replace: true })}>
        {t('onboarding.continue')}
      </Button>
    </Frame>
  )
}
