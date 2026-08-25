import { useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation } from '@tanstack/react-query'

import { ApiError, api } from '../api/client'
import type { User } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { Fenster } from '../components/Fenster'
import { MediaServerLogo } from '../components/MediaServerLogo'
import { Button, Card, ErrorBanner, Field } from '../components/ui'
import { providerName } from '../lib/mediaserver'
import { useConfig } from '../hooks/useConfig'
import { changeLanguage as spracheAnwenden } from '../i18n'
import type { Language } from '../i18n'
import { istTheme, themeAnwenden } from '../lib/theme'
import type { Theme } from '../lib/theme'
import { SpracheUndRegion } from './profile/SpracheUndRegion'
import { StreamingDienste } from './profile/StreamingDienste'
import { Kinder } from './profile/Kinder'
import { KontoLoeschen } from './profile/KontoLoeschen'
import { MediaServerLink } from './profile/MediaServerLink'
import { WatchlistPlex } from './profile/WatchlistPlex'
import { NotificationSettings } from './profile/NotificationSettings'
import { StorageMine } from './profile/StorageMine'

/**
 * Reiter des eigenen Profils.
 *
 * Untereinander waren das sechs Karten - eine Liste, in der man das Gesuchte
 * nur noch durch Scrollen findet. Aufgeteilt wie die Einstellungen des
 * Administrators, damit beide Seiten sich gleich anfühlen.
 */
type Tab =
  | 'account'
  | 'notifications'
  | 'streaming'
  | 'mediaserver'
  | 'watchlist'
  | 'storage'
  | 'children'

export function ProfilePage() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const { data: config } = useConfig()
  const minPassword = config?.min_password_length ?? 4
  // `?reiter=kinder` öffnet den Reiter direkt - die Glocke springt so aus
  // einem Kinderwunsch an die Stelle, an der er entschieden wird.
  //
  // Die Adresse spricht deutsch, der Zustand englisch - deshalb die Tabelle.
  // Ohne sie zeigte der Link auf einen Reiter, den es nicht gibt, und die
  // Seite blieb kommentarlos auf „Konto" stehen.
  const [suchparameter, setSuchparameter] = useSearchParams()
  const REITER_AUS_ADRESSE: Record<string, Tab> = {
    kinder: 'children',
    konto: 'account',
    benachrichtigungen: 'notifications',
    // „Sprache & Region" und „Sicherheit" sind in „Konto" aufgegangen. Die
    // alten Adressen bleiben gültig, damit Links aus Mails und der Glocke
    // nicht ins Leere zeigen.
    sprache: 'account',
    sicherheit: 'account',
    streaming: 'streaming',
    merkliste: 'watchlist',
    speicher: 'storage',
  }
  const gewuenschterReiter = REITER_AUS_ADRESSE[suchparameter.get('reiter') ?? '']
  const [tab, setTab] = useState<Tab>(gewuenschterReiter ?? 'account')

  const fileRef = useRef<HTMLInputElement>(null)
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [repeatPassword, setRepeatPassword] = useState('')
  const [passwortOffen, setPasswortOffen] = useState(false)

  // Sprache, Region und Darstellung liegen jetzt hier, nicht mehr in einem
  // eigenen Reiter mit eigenem Knopf: Die ganze Kontoseite wird mit **einem**
  // Speichern gesichert.
  const [region, setRegion] = useState(user?.discover_region ?? '')
  const [sprache, setSprache] = useState<Language>(
    (user?.language as Language) ?? ('de' as Language),
  )
  const [darstellung, setDarstellung] = useState<Theme>(
    istTheme(user?.theme) ? (user.theme as Theme) : 'dark',
  )

  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setMessage(null)
    setError(null)
  }

  function fail(caught: unknown) {
    setMessage(null)
    setError(caught instanceof ApiError ? caught.message : t('errors.generic'))
  }

  const avatarMutation = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      // Kein JSON: der Browser setzt die passende Kopfzeile für den Upload selbst.
      return api.upload<User>('/api/auth/me/avatar', body)
    },
    onMutate: reset,
    onSuccess: (updated) => {
      updateUser(updated)
      setMessage(t('profile.avatarSaved'))
    },
    onError: fail,
  })

  const removeAvatarMutation = useMutation({
    mutationFn: () => api.delete<User>('/api/auth/me/avatar'),
    onMutate: reset,
    onSuccess: (updated) => {
      updateUser(updated)
      setMessage(t('profile.avatarRemoved'))
    },
    onError: fail,
  })

  /**
   * **Ein** Speichern für die ganze Kontoseite.
   *
   * Vorher hatte jede Karte ihren eigenen Knopf - nach dem Zusammenlegen von
   * „Sprache & Region" und „Sicherheit" standen fünf davon untereinander, und
   * keiner sagte, wie weit er reicht.
   *
   * Zwei Aufrufe, weil die Adresse einen eigenen Endpunkt hat: Sie löst eine
   * Bestätigungsmail aus und ist damit kein Feld wie die anderen. Für den
   * Menschen bleibt es trotzdem eine Entscheidung.
   *
   * Bewusst **nicht** hier: Profilbild (wirkt sofort beim Auswählen),
   * Passwort (eigenes Formular mit eigener Prüfung) und Kontolöschung. Das
   * sind Handlungen, keine Einstellungen.
   */
  const speichern = useMutation({
    mutationFn: async () => {
      let aktuell: User | null = null
      const nameGeaendert = displayName.trim() !== (user?.display_name ?? '')
      const restGeaendert =
        region !== (user?.discover_region ?? '') ||
        sprache !== user?.language ||
        darstellung !== user?.theme

      if (nameGeaendert || restGeaendert) {
        aktuell = await api.patch<User>('/api/auth/me', {
          display_name: displayName.trim(),
          discover_region: region,
          language: sprache,
          theme: darstellung,
        })
      }
      // Die Adresse zuletzt: Schlägt sie fehl (schon vergeben), ist der Rest
      // trotzdem gesichert.
      if (email.trim() !== '' && email.trim() !== (user?.email ?? '')) {
        aktuell = await api.put<User>('/api/auth/me/email', { email: email.trim() })
      }
      return aktuell
    },
    onMutate: reset,
    onSuccess: (aktuell) => {
      if (!aktuell) return
      updateUser(aktuell)
      setRegion(aktuell.discover_region ?? '')
      // Erst jetzt umschalten: Die Auswahl ist ein Vorschlag, bis gespeichert
      // wird - sonst spränge die Oberfläche schon beim Aufklappen der Liste um.
      spracheAnwenden(aktuell.language as Language)
      if (istTheme(aktuell.theme)) themeAnwenden(aktuell.theme)
      setMessage(t('profile.accountSaved'))
    },
    onError: fail,
  })

  const resendMutation = useMutation({
    mutationFn: () =>
      api.post<{ sent: boolean; error: string | null }>('/api/auth/me/resend-verification'),
    onMutate: reset,
    onSuccess: (ergebnis) => {
      if (ergebnis.sent) setMessage(t('profile.verificationSent'))
      else setError(ergebnis.error ?? t('errors.generic'))
    },
    onError: fail,
  })

  const passwordMutation = useMutation({
    mutationFn: () =>
      api.post<void>('/api/auth/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      }),
    onMutate: reset,
    onSuccess: () => {
      setCurrentPassword('')
      setNewPassword('')
      setRepeatPassword('')
      setPasswortOffen(false)
      setMessage(t('profile.passwordSaved'))
    },
    onError: fail,
  })

  function handlePassword(event: FormEvent) {
    event.preventDefault()
    reset()
    if (newPassword !== repeatPassword) {
      setError(t('setup.mismatch'))
      return
    }
    if (newPassword.length < minPassword) {
      setError(t('adminUsers.passwordTooShort', { count: minPassword }))
      return
    }
    passwordMutation.mutate()
  }

  if (!user) return null
  const name = user.display_name ?? user.username

  // Gesperrt, solange nichts geändert wurde: Ein Knopf, der nichts bewirkt,
  // sieht aus wie ein Knopf, der nicht funktioniert.
  const kontoGeaendert =
    displayName.trim() !== (user.display_name ?? '') ||
    (email.trim() !== '' && email.trim() !== (user.email ?? '')) ||
    region !== (user.discover_region ?? '') ||
    sprache !== user.language ||
    darstellung !== user.theme

  const tabs: { value: Tab; labelKey: string }[] = [
    { value: 'account', labelKey: 'profile.tabAccount' },
    { value: 'notifications', labelKey: 'profile.tabNotifications' },
  ]
  // Kein Schalter beim Betreiber und keine Bedingung: Wer nichts anhakt,
  // bekommt nie einen Hinweis, und dann kostet der Reiter auch nichts. Ein
  // Kinderkonto hat keine eigenen Abos - es guckt ueber die seiner Eltern,
  // und dort erscheint der Hinweis auch.
  if (user.role !== 'child') {
    tabs.splice(2, 0, { value: 'streaming', labelKey: 'profile.tabStreaming' })
  }
  // ⚠️ Ein eigener Reiter, nicht mehr unten unter „Sicherheit".
  //
  // Dort war er nicht zu finden: Wer sein Plex- oder Jellyfin-Konto verbinden
  // will, sucht nicht hinter „Passwort ändern". Der Reiter erscheint nur,
  // wenn es überhaupt etwas zu verbinden gibt - entweder steht ein Server
  // bereit, oder das Konto hängt noch an einem, der gerade nicht verbunden
  // ist (den muss man lösen können).
  if (
    (config?.mediaserver_providers ?? []).length > 0 ||
    (user.mediaserver_accounts ?? []).length > 0
  ) {
    tabs.push({ value: 'mediaserver', labelKey: 'profile.tabMediaServer' })
  }
  // Nur wenn der Administrator die Merkliste freigeschaltet hat - sonst
  // stünde dort ein Reiter, hinter dem es nichts geben kann.
  if (config?.watchlist_enabled) {
    tabs.push({ value: 'watchlist', labelKey: 'profile.tabWatchlist' })
  }
  // Ohne eingeschaltete Speicher-Kontingente gibt es hier nichts zu sehen.
  if (config?.storage_enabled) {
    tabs.push({ value: 'storage', labelKey: 'profile.tabStorage' })
  }
  // Der Reiter erscheint **auch ohne Freigabe**. Wer nicht weiß, dass es
  // Kinderkonten gibt, fragt auch nicht danach; statt einer leeren Seite steht
  // dort dann, was die Funktion kann - und ein Knopf, der sie beantragt.
  // Nur für Kinderkonten selbst gibt es ihn nicht.
  if (user.role !== 'child') {
    tabs.push({ value: 'children', labelKey: 'profile.tabChildren' })
  }

  // Ueberschrift und Reiterreihe bekommen **immer** die volle Breite, nur der
  // Inhalt darunter wird eingeschnuert. Vorher hing beides an derselben
  // Breite - und weil breite Reiter (Merkliste, Speicher) einen breiteren
  // Inhalt haben, brach die Reiterreihe je nach gewaehltem Reiter um und bei
  // anderen nicht. Ein Menue darf sich nicht danach richten, was darunter steht.
  const schmal =
    tab !== 'watchlist' &&
    tab !== 'storage' &&
    tab !== 'children' &&
    tab !== 'streaming' &&
    tab !== 'account'

  return (
    <div className="flex max-w-6xl flex-col gap-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {t('nav.profile')}
          <span className="text-accent-500">.</span>
        </h1>
        <p className="mt-1.5 text-mist-500">{t('profile.intro')}</p>
      </header>

      <div className="flex flex-wrap gap-2" role="tablist">
        {tabs.map((entry) => (
          <button
            key={entry.value}
            type="button"
            role="tab"
            aria-selected={tab === entry.value}
            onClick={() => {
              setTab(entry.value)
              // Die Adresse nicht stehen lassen - sonst spränge ein Neuladen
              // zurück auf den Reiter aus dem Link.
              if (suchparameter.has('reiter')) setSuchparameter({}, { replace: true })
              // Eine Erfolgsmeldung vom vorherigen Reiter hätte hier keinen
              // Bezug mehr - sie würde nur verwirren.
              reset()
            }}
            className={
              'rounded-full border px-4 py-2 text-sm font-medium transition-colors ' +
              (tab === entry.value
                ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {t(entry.labelKey)}
          </button>
        ))}
      </div>

      <div className={'flex flex-col gap-6 ' + (schmal ? 'max-w-2xl' : '')}>
      {error && <ErrorBanner message={error} />}
      {message && !error && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {message}
        </p>
      )}

      {tab === 'account' && (
        <div className="flex flex-col gap-4">
          {/* Zwei echte Spalten statt eines Rasters: Ein Raster richtet
              **zeilenweise** aus, und die kurze Karte links erbte dann die
              Höhe der langen rechts - zwischen Profilbild und Sprache klaffte
              eine Lücke, für die es keinen Grund gab. */}
          <div className="grid items-start gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-4">
          <Card className="flex flex-col gap-4">
            <h2 className="text-lg font-semibold">{t('profile.picture')}</h2>
            <div className="flex flex-wrap items-center gap-4">
              <Avatar url={user.avatar_url} name={name} className="h-20 w-20" />
              <div className="flex flex-wrap gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/webp"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) avatarMutation.mutate(file)
                    event.target.value = ''
                  }}
                />
                <Button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  loading={avatarMutation.isPending}
                >
                  {t('profile.chooseImage')}
                </Button>
                {user.avatar_url && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => removeAvatarMutation.mutate()}
                    loading={removeAvatarMutation.isPending}
                  >
                    {t('profile.removeImage')}
                  </Button>
                )}
              </div>
            </div>
            <p className="text-xs text-mist-600">{t('profile.pictureHint')}</p>
          </Card>

          <Card className="flex flex-col gap-4">
            <h2 className="text-lg font-semibold">{t('profile.name')}</h2>

            {/* Nur zur Ansicht: der Benutzername steht in Anfragen, Freigaben und
            in den Radarr-/Sonarr-Etiketten. Ihn nachträglich zu ändern würde
            diese Spuren auseinanderreißen. */}
            <Field
              label={t('profile.username')}
              value={user.username}
              readOnly
              disabled
              hint={t('profile.usernameHint')}
            />

            <Field
              label={t('adminUsers.displayName')}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              hint={t('profile.nameHint')}
              autoComplete="nickname"
            />
          </Card>

          <Card className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">{t('profile.email')}</h2>
              {user.email_verified ? (
                <span className="rounded-full border border-ok-500/40 bg-ok-500/10 px-2.5 py-0.5 text-xs text-ok-500">
                  {t('profile.emailVerified')}
                </span>
              ) : (
                <span className="rounded-full border border-warn-500/40 bg-warn-500/10 px-2.5 py-0.5 text-xs text-warn-500">
                  {t('profile.emailUnverified')}
                </span>
              )}
            </div>

            <Field
              label={t('adminUsers.email')}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              hint={t('profile.emailHint')}
              autoComplete="email"
            />

            <div className="flex flex-wrap items-center gap-3">
              {/* Nur anbieten, wenn es auch etwas zu bestätigen gibt. */}
              {!user.email_verified && user.email && (
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => resendMutation.mutate()}
                  loading={resendMutation.isPending}
                >
                  {t('profile.resendVerification')}
                </Button>
              )}
            </div>
          </Card>
            </div>

            <div className="flex flex-col gap-4">
          <Card className="flex flex-col gap-4">
            <SpracheUndRegion
              region={region}
              setRegion={setRegion}
              sprache={sprache}
              setSprache={setSprache}
              darstellung={darstellung}
              setDarstellung={setDarstellung}
              alter={user.age}
              disabled={speichern.isPending}
            />
          </Card>

            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={() => speichern.mutate()}
              loading={speichern.isPending}
              disabled={!kontoGeaendert}
            >
              {t('common.save')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                reset()
                setCurrentPassword('')
                setNewPassword('')
                setRepeatPassword('')
                setPasswortOffen(true)
              }}
            >
              {t('profile.changePassword')}
            </Button>
            {kontoGeaendert && !speichern.isPending && (
              <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
            )}
          </div>

          {/* Der Antrag, das eigene Konto zu löschen - nicht für
              Administratoren: die löschen direkt in der Benutzerverwaltung. */}
          {user.role !== 'admin' && (

            <KontoLoeschen />
          )}

          {/* Das Fenster trägt seinen Knopf in der Fußzeile - dort sucht man
              Entscheidungen. Einen zweiten Ausgang oben gibt es deshalb
              nicht; Escape und ein Klick daneben schließen ohnehin. */}
          <Fenster
            offen={passwortOffen}
            titel={t('profile.password')}
            onSchliessen={() => setPasswortOffen(false)}
            fuss={
              <>
                {/* Der Ausgang gehört in die Fußzeile, sobald es eine gibt:
                    ``Fenster`` blendet den Schließen-Knopf oben dann aus, um
                    nicht zwei Wege mit derselben Wirkung anzubieten. Ohne
                    „Abbrechen" hier blieben gar keiner - außer Escape, und den
                    kennt nicht jeder. */}
                <Button
                  variant="ghost"
                  onClick={() => setPasswortOffen(false)}
                  disabled={passwordMutation.isPending}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  type="submit"
                  form="passwort-formular"
                  loading={passwordMutation.isPending}
                >
                  {t('profile.changePassword')}
                </Button>
              </>
            }
          >
            <form
              id="passwort-formular"
              onSubmit={handlePassword}
              className="flex flex-col gap-4"
            >
            <Field
              label={t('profile.currentPassword')}
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
            <Field
              label={t('profile.newPassword')}
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              hint={t('adminUsers.passwordHint', { count: minPassword })}
              autoComplete="new-password"
              required
              minLength={minPassword}
            />
            <Field
              label={t('setup.passwordRepeatLabel')}
              type="password"
              value={repeatPassword}
              onChange={(event) => setRepeatPassword(event.target.value)}
              autoComplete="new-password"
              required
            />
            </form>
          </Fenster>
        </div>
      )}

      {tab === 'streaming' && (
        <StreamingDienste aufSpracheUndRegion={() => setTab('account')} />
      )}
      {tab === 'notifications' && <NotificationSettings />}
      {tab === 'storage' && <StorageMine />}
      {tab === 'children' && <Kinder />}

      {/* Die Verknüpfung gehört zur Anmeldung und damit neben das Passwort. */}
      {tab === 'mediaserver' && <MediaServerLink />}

      {/* Eine Pille je Quelle. Heute nur Plex - Jellyfin und Emby haben
          keine Merkliste -, später kommen weitere dazu (Trakt etwa).

          ⚠️ **Eine Quelle steht auch dann da, wenn sie nicht verbunden ist.**
          Sie wegzulassen hieße, den ganzen Bereich verschwinden zu lassen,
          ohne dass jemand erfährt, warum. Stattdessen steht sie blass da und
          sagt es. */}
      {tab === 'watchlist' && (
        <>
          <div className="flex flex-wrap gap-2">
            {(config?.mediaserver_watchlist_available ?? []).map((anbieter) => {
              const verbunden = (
                config?.mediaserver_watchlist_connected ?? []
              ).includes(anbieter)
              return (
                <span
                  key={anbieter}
                  className={
                    'flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm font-medium ' +
                    (verbunden
                      ? 'border-accent-500/60 bg-accent-500/15 text-accent-400'
                      : 'border-ink-700 bg-ink-950/40 text-mist-600')
                  }
                >
                  <MediaServerLogo provider={anbieter} className="h-3.5 w-3.5" />
                  {providerName(anbieter)}
                  {!verbunden && (
                    <span className="text-xs font-normal">
                      · {t('watchlist.sourceNotConnected')}
                    </span>
                  )}
                </span>
              )
            })}
          </div>

          {(config?.mediaserver_watchlist_connected ?? []).length > 0 ? (
            <WatchlistPlex />
          ) : (
            <Card>
              <p className="text-sm text-mist-500">
                {t('watchlist.sourceNotConnectedHint', {
                  name: (config?.mediaserver_watchlist_available ?? [])
                    .map(providerName)
                    .join(', '),
                })}
              </p>
            </Card>
          )}
        </>
      )}
      </div>
    </div>
  )
}
