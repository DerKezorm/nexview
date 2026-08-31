import { useRef, useState } from 'react'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useSearchParams } from 'react-router-dom'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery } from '@tanstack/react-query'

import { ApiError, api, setTokens } from '../api/client'
import type { TokenPair } from '../api/client'
import type { OidcAnbieter, User } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { Avatar } from '../components/Avatar'
import { Fenster } from '../components/Fenster'
import { MediaServerLogo } from '../components/MediaServerLogo'
import { Reiterreihe, type Reiter } from '../components/Reiterreihe'
import { Button, Card, ErrorBanner, Field, Section } from '../components/ui'
import { providerName } from '../lib/mediaserver'
import { useConfig } from '../hooks/useConfig'
import { changeLanguage as spracheAnwenden } from '../i18n'
import type { Language } from '../i18n'
import { istTheme, themeAnwenden } from '../lib/theme'
import type { Theme } from '../lib/theme'
import { Darstellung } from './profile/Darstellung'
import { SpracheUndRegion } from './profile/SpracheUndRegion'
import { StreamingDienste } from './profile/StreamingDienste'
import { Kinder } from './profile/Kinder'
import { ApiSchluessel } from './profile/ApiSchluessel'
import { BetreiberUebergeben } from './profile/BetreiberUebergeben'
import { KontoLoeschen } from './profile/KontoLoeschen'
import { MediaServerLink } from './profile/MediaServerLink'
import { OidcLinks } from './profile/OidcLinks'
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
/**
 * Das Untermenü unter „Konto".
 *
 * ⚠️ **Drei Reiter statt einer langen Seite.** „Konto" trug zuletzt sechs
 * Blöcke untereinander - Profilbild, Name, E-Mail, Sprache, Passwort und ganz
 * unten die API-Token. Der wichtigste Neuzugang lag damit am weitesten unten,
 * hinter allem, was man selten anfasst.
 *
 * Aufgeteilt nach der Frage, warum man herkommt: **wer bin ich** (Profil),
 * **wer kommt an mein Konto** (Sicherheit), **was verstehe ich** (Sprache).
 *
 * Sprache und Region hatten früher schon einmal einen eigenen Reiter und sind
 * damals in „Konto" aufgegangen, weil sie allein zu dünn waren. Das ist kein
 * Rückschritt dorthin: Damals wären es *obere* Reiter gewesen, gleichrangig
 * mit „Benachrichtigungen"; hier hängen sie eine Ebene tiefer unter „Konto"
 * und kosten die Hauptreihe keinen Platz.
 */
type KontoReiter = 'profil' | 'sicherheit' | 'sprache'

type Tab =
  | 'account'
  | 'notifications'
  | 'streaming'
  | 'mediaserver'
  | 'oidc'
  | 'watchlist'
  | 'storage'
  | 'children'

export function ProfilePage() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuth()
  const { data: config } = useConfig()
  // Ob der Anmeldungs-Reiter erscheint, entscheidet dieselbe Liste, die auch
  // die Anmeldeseite zeichnet - derselbe Abfrage-Schlüssel, also kein
  // zweiter Abruf.
  const { data: oidcListe } = useQuery({
    queryKey: ['oidc-anbieter'],
    queryFn: () => api.get<OidcAnbieter[]>('/api/auth/oidc', { auth: false }),
  })
  const oidcAnbieter = oidcListe ?? []
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
    // Die Rueckkehr vom OIDC-Anbieter landet hier - der Browser war weg und
    // soll direkt wieder vor der richtigen Karte stehen.
    anmeldung: 'oidc',
  }
  const gewuenschterReiter = REITER_AUS_ADRESSE[suchparameter.get('reiter') ?? '']
  const [tab, setTab] = useState<Tab>(gewuenschterReiter ?? 'account')

  // Die alten Adressen ``?reiter=sprache`` und ``?reiter=sicherheit`` zeigten
  // auf „Konto", seit die beiden dort aufgegangen waren. Jetzt gibt es sie
  // wieder - also landen sie auch wieder dort, statt oben auf „Profil".
  const UNTERREITER_AUS_ADRESSE: Record<string, KontoReiter> = {
    sprache: 'sprache',
    sicherheit: 'sicherheit',
    token: 'sicherheit',
  }
  const [kontoReiter, setKontoReiter] = useState<KontoReiter>(
    UNTERREITER_AUS_ADRESSE[suchparameter.get('reiter') ?? ''] ?? 'profil',
  )

  const fileRef = useRef<HTMLInputElement>(null)
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [repeatPassword, setRepeatPassword] = useState('')
  const [passwortOffen, setPasswortOffen] = useState(false)
  const [abmeldenOffen, setAbmeldenOffen] = useState(false)

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
      void spracheAnwenden(aktuell.language as Language)
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
      api.post<TokenPair>('/api/auth/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      }),
    onMutate: reset,
    /**
     * ⚠️ Das frische Token muss uebernommen werden.
     *
     * Seit 0.21 beendet ein Passwortwechsel alle Sitzungen dieses Kontos -
     * die eigene eingeschlossen, denn der Server kann sie nicht von den
     * anderen unterscheiden. Deshalb gibt er ein frisches Paar zurueck. Ohne
     * diese Zeile faende die naechste Anfrage ein Token vor, das der Server
     * gerade selbst fuer ungueltig erklaert hat.
     */
    onSuccess: (tokens) => {
      setTokens(tokens)
      setCurrentPassword('')
      setNewPassword('')
      setRepeatPassword('')
      setPasswortOffen(false)
      setMessage(t('profile.passwordSaved'))
    },
    onError: fail,
  })

  /**
   * Alle anderen Geräte abmelden - ohne das Passwort zu ändern.
   *
   * ⚠️ **Der Ausweg, den es bis 0.22 nicht gab.** Gewöhnliches Abmelden nimmt
   * nur das Cookie aus *diesem* Browser; wer eine Kopie hat, kommt damit
   * weiter herein, bis es abläuft. Der einzige Riegel war bis dahin ein
   * Passwortwechsel - man musste also sein Passwort ändern, obwohl mit dem
   * Passwort nichts war.
   */
  const ueberallAbmelden = useMutation({
    mutationFn: () => api.post<TokenPair>('/api/auth/me/ueberall-abmelden', {}),
    onMutate: reset,
    // Dasselbe wie beim Passwortwechsel: Ohne das frische Token fände die
    // nächste Anfrage eines vor, das der Server gerade selbst verworfen hat.
    onSuccess: (tokens) => {
      setTokens(tokens)
      setAbmeldenOffen(false)
      setMessage(t('profile.signedOutEverywhere'))
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

  // ⚠️ Dieselbe Reiterreihe wie unter „System" - bis eben zeichnete diese
  // Seite ihre Reihe selbst, ohne Symbole. Die Klassen stimmten zwar zufaellig
  // ueberein, aber die Symbole fehlten, und ein Untermenue mit Symbolen unter
  // einer Reihe ohne haette den Unterschied erst recht sichtbar gemacht.
  const tabs: Reiter<Tab>[] = [
    { value: 'account', label: t('profile.tabAccount'), symbol: 'benutzer' },
    { value: 'notifications', label: t('profile.tabNotifications'), symbol: 'glocke' },
  ]
  // Kein Schalter beim Betreiber und keine Bedingung: Wer nichts anhakt,
  // bekommt nie einen Hinweis, und dann kostet der Reiter auch nichts. Ein
  // Kinderkonto hat keine eigenen Abos - es guckt ueber die seiner Eltern,
  // und dort erscheint der Hinweis auch.
  if (user.role !== 'child') {
    tabs.splice(2, 0, {
      value: 'streaming',
      label: t('profile.tabStreaming'),
      symbol: 'dienste',
    })
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
    tabs.push({
      value: 'mediaserver',
      label: t('profile.tabMediaServer'),
      symbol: 'medienserver',
    })
  }
  // Dieselbe Regel für die genormte Anmeldung: nur, wenn ein Anbieter
  // eingerichtet ist - oder eine Verknüpfung übrig, die man lösen können muss.
  if (oidcAnbieter.length > 0 || (user.oidc_links ?? []).length > 0) {
    tabs.push({ value: 'oidc', label: t('profile.tabOidc'), symbol: 'schluessel' })
  }
  // Nur wenn der Administrator die Merkliste freigeschaltet hat - sonst
  // stünde dort ein Reiter, hinter dem es nichts geben kann.
  if (config?.watchlist_enabled) {
    tabs.push({ value: 'watchlist', label: t('profile.tabWatchlist'), symbol: 'merkliste' })
  }
  // Der Reiter steht immer da: Gemessen wird immer, und wer wissen will, was
  // er belegt, soll es auch dann sehen, wenn ihn gerade niemand begrenzt.
  tabs.push({ value: 'storage', label: t('profile.tabStorage'), symbol: 'kontingent' })
  // Der Reiter erscheint **auch ohne Freigabe**. Wer nicht weiß, dass es
  // Kinderkonten gibt, fragt auch nicht danach; statt einer leeren Seite steht
  // dort dann, was die Funktion kann - und ein Knopf, der sie beantragt.
  // Nur für Kinderkonten selbst gibt es ihn nicht.
  if (user.role !== 'child') {
    tabs.push({ value: 'children', label: t('profile.tabChildren'), symbol: 'kind' })
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

      <Reiterreihe
        eintraege={tabs}
        aktiv={tab}
        onWechsel={(wert) => {
          setTab(wert)
          // Die Adresse nicht stehen lassen - sonst spränge ein Neuladen
          // zurück auf den Reiter aus dem Link.
          if (suchparameter.has('reiter')) setSuchparameter({}, { replace: true })
          // Eine Erfolgsmeldung vom vorherigen Reiter hätte hier keinen
          // Bezug mehr - sie würde nur verwirren.
          reset()
        }}
      />

      {/* Das Untermenü erscheint nur unter „Konto". Eine Reihe, die bei jedem
          Reiter dasteht und meistens nichts mit ihm zu tun hat, ist keine
          Navigation, sondern Zierde. */}
      {tab === 'account' && (
        <Reiterreihe
          unter
          label={t('profile.tabAccount')}
          eintraege={[
            { value: 'profil', label: t('profile.tabProfile'), symbol: 'benutzer' },
            { value: 'sicherheit', label: t('profile.tabSecurity'), symbol: 'schluessel' },
            { value: 'sprache', label: t('profile.tabDiscover'), symbol: 'sprache' },
          ]}
          aktiv={kontoReiter}
          onWechsel={(wert) => {
            setKontoReiter(wert)
            if (suchparameter.has('reiter')) setSuchparameter({}, { replace: true })
            reset()
          }}
        />
      )}

      <div className={'flex flex-col gap-6 ' + (schmal ? 'max-w-2xl' : '')}>
      {error && <ErrorBanner message={error} />}
      {message && !error && (
        <p className="rounded-xl border border-ok-500/40 bg-ok-500/10 px-4 py-3 text-sm text-ok-500">
          {message}
        </p>
      )}

      {tab === 'account' && (
        <div className="flex flex-col gap-4">
          {/* --- Profil: wer bin ich ------------------------------------- */}
          {/* Zwei echte Spalten statt eines Rasters: Ein Raster richtet
              **zeilenweise** aus, und die kurze Karte links erbte dann die
              Höhe der langen rechts - zwischen den Karten klaffte eine Lücke,
              für die es keinen Grund gab.

              ⚠️ Die E-Mail steht **rechts**, nicht unter dem Namen. Seit die
              Sprache einen eigenen Reiter hat, trüge die rechte Spalte sonst
              nur ein einziges Auswahlfeld gegen drei hohe Karten links. */}
          {kontoReiter === 'profil' && (
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

            </div>

            <div className="flex flex-col gap-4">
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

              <Card className="flex flex-col gap-4">
                <Darstellung
                  darstellung={darstellung}
                  setDarstellung={setDarstellung}
                  disabled={speichern.isPending}
                />
              </Card>
            </div>
          </div>
          )}

          {/* --- Sprache: was verstehe ich ------------------------------- */}
          {kontoReiter === 'sprache' && (
            <Card className="flex flex-col gap-4">
              <SpracheUndRegion
                region={region}
                setRegion={setRegion}
                sprache={sprache}
                setSprache={setSprache}
                alter={user.age}
                disabled={speichern.isPending}
              />
            </Card>
          )}

          {/* ⚠️ **Ein Speichern-Knopf für beide Reiter, nicht zwei.**
              Gespeichert wird alles zusammen - Name, E-Mail, Darstellung,
              Sprache und Region gehen in denselben Aufruf. Die Eingaben
              überleben den Reiterwechsel, weil sie im Zustand dieser Seite
              stehen und nicht im Reiter: Wer die Sprache ändert, zu „Profil"
              wechselt und dort speichert, verliert nichts.

              Deshalb steht der Hinweis „nicht gespeichert" auch auf beiden
              Reitern - er meint die ganze Seite, nicht den sichtbaren Teil. */}
          {kontoReiter !== 'sicherheit' && (
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                onClick={() => speichern.mutate()}
                loading={speichern.isPending}
                disabled={!kontoGeaendert}
              >
                {t('common.save')}
              </Button>
              {kontoGeaendert && !speichern.isPending && (
                <span className="text-sm text-mist-600">{t('common.unsaved')}</span>
              )}
            </div>
          )}

          {/* --- Sicherheit: wer kommt an mein Konto --------------------- */}
          {kontoReiter === 'sicherheit' && (
            <>
              {/* ⚠️ **Dasselbe Bauteil wie die API-Token darunter**, nicht eine
                  eigene Karte mit eigener Breite. Vorher war diese hier
                  ``max-w-2xl`` und die Token-Sektion ging über die volle
                  Breite - zwei Blöcke untereinander, zwei Kanten, und keinen
                  Grund dafür, den man hätte benennen können. */}
              <Section title={t('profile.tabSecurity')} breit>
                <p className="-mt-2 text-sm leading-relaxed text-mist-500">
                  {t('profile.securityIntro')}
                </p>
                <div className="flex flex-wrap items-center gap-3">
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
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      reset()
                      setAbmeldenOffen(true)
                    }}
                  >
                    {t('profile.signOutEverywhere')}
                  </Button>
                </div>
              </Section>

              {/* API-Token für die HTTP-Schnittstelle. Steht hier und nicht in
                  den Einstellungen, weil ein Token **einem Konto** gehört und
                  dessen Rechte erbt - er ist persönlich, kein Instanz-Zugang. */}
              {user.role !== 'child' && <ApiSchluessel />}

              {/* Der Antrag, das eigene Konto zu löschen - nicht für
                  Administratoren: die löschen direkt in der Benutzerverwaltung.
                  Steht unter „Sicherheit", weil es dieselbe Frage beantwortet
                  wie alles andere hier: wer hat Zugang zu diesem Konto - und
                  soll es überhaupt weiter geben. */}
              {user.role !== 'admin' && <KontoLoeschen />}

              {/* „Betreiber übergeben" – nur der Träger sieht es überhaupt.
                  Steht unter „Sicherheit" aus demselben Grund wie das Löschen:
                  Es beantwortet die Frage, wem dieses Haus gehört. */}
              <BetreiberUebergeben me={user} />
            </>
          )}

          <ConfirmDialog
            open={abmeldenOffen}
            title={t('profile.signOutEverywhere')}
            description={t('profile.signOutEverywhereText')}
            warning={t('profile.signOutEverywhereWarning')}
            confirmLabel={t('profile.signOutEverywhere')}
            loading={ueberallAbmelden.isPending}
            onConfirm={() => ueberallAbmelden.mutate()}
            onCancel={() => setAbmeldenOffen(false)}
          />

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
            {/* Sagen, was NICHT mitgeht. Ein Passwortwechsel beendet alle
                Sitzungen, laesst die Zugriffs-Schluessel aber leben - sie
                nehmen einen zweiten Weg durch die Anmeldung. Wer wechselt,
                weil ihm etwas gestohlen wurde, haelt sich sonst fuer
                abgesichert und ist es nicht. */}
            <p className="text-xs leading-relaxed text-mist-500">
              {t('profile.passwordKeepsKeys')}
            </p>
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
      {tab === 'oidc' && <OidcLinks />}

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
