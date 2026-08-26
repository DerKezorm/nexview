/**
 * Den Media-Server verbinden und die Vorgaben für neue Konten setzen.
 *
 * Bewusst *ohne* Feld für Adresse und Token: Der Administrator meldet sich bei
 * Plex an und wählt seinen Server aus einer Liste. Das erspart die Sucherei
 * nach dem Token, funktioniert auch hinter einem Reverse Proxy - und verhindert
 * nebenbei, dass beim ersten eigenen Plex-Login ein *zweites* Konto entsteht:
 * Beim Verbinden wird das eigene Konto gleich mitverknüpft.
 */

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type {
  AppSettings,
  MediaServerBlock,
  MediaServerDisconnectImpact,
  MediaServerLibraryState,
  MediaServerOption,
  User,
} from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { MediaServerPasswordForm } from '../../components/MediaServerPasswordForm'
import { MediaServerPrompt } from '../../components/MediaServerPrompt'
import { MediaServerTile } from '../../components/MediaServerTile'
import type { TileState } from '../../components/MediaServerTile'
import { Button, Card, Section, ErrorBanner, Spinner } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { providerName } from '../../lib/mediaserver'
import { useConfig } from '../../hooks/useConfig'
import { useMediaServerChallenge } from '../../lib/useMediaServerChallenge'

type Draft = {
  mediaserver_auto_import: boolean
  mediaserver_default_role: 'user' | 'approver'
}

const EMPTY_DRAFT: Draft = {
  mediaserver_auto_import: true,
  mediaserver_default_role: 'user',
}

/**
 * Ein Abschnitt **innerhalb** der Detailkarte.
 *
 * ⚠️ Vorher war jeder dieser Teile eine eigene `Card`, also vier gestapelte
 * Kästen untereinander. Die Benachrichtigungen machen es anders: **eine** Box,
 * darin die Abschnitte durch eine Haarlinie getrennt. Das ist ruhiger, und es
 * sagt auch das Richtige - die Teile gehören zu *einem* Server, nicht zu vier
 * verschiedenen Dingen.
 *
 * Die Linie fehlt beim ersten Abschnitt; ``first:`` erledigt das, ohne dass
 * hier jemand mitzählen muss.
 */
function Abschnitt({ children }: { children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4 border-t border-ink-700 pt-6 first:border-t-0 first:pt-0">
      {children}
    </section>
  )
}

export function AdminMediaServerSettings() {
  const { t, i18n } = useTranslation()
  const queryClient = useQueryClient()
  const { user: me, updateUser } = useAuth()
  const { data: config } = useConfig()

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [meldung, setMeldung] = useState<{ ok: boolean; text: string } | null>(null)
  const [auswahl, setAuswahl] = useState<MediaServerOption[] | null>(null)
  const [ausgeblendet, setAusgeblendet] = useState(0)
  const [pollToken, setPollToken] = useState<string | null>(null)
  const [trennenOffen, setTrennenOffen] = useState(false)
  /** Welcher Anbieter soll getrennt werden? Gesetzt vom Knopf auf der Kachel. */
  const [trennenFuer, setTrennenFuer] = useState<string | null>(null)
  /**
   * Welche Kachel ist geöffnet? `null` heißt Übersicht.
   *
   * Die Auswahl steht bewusst **nicht** in der Adresszeile: Sie überlebt keinen
   * Seitenwechsel und soll es auch nicht – wer zurückkommt, will den Überblick
   * sehen, nicht den Stand von vorgestern.
   */
  const [offen, setOffen] = useState<string | null>(null)

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<AppSettings>('/api/settings'),
  })
  const settings = settingsQuery.data
  // Steht bewusst hier oben und nicht erst unten bei den anderen Ableitungen:
  // Darunter liegt ein früher `return`, und ein Hook darf nicht dahinter.
  //
  // ⚠️ **Immer auf den geöffneten Anbieter bezogen, nie global.** Vorher stand
  // hier `mediaserver_configured` – ein einzelnes Ja/Nein für „irgendein
  // Server ist verbunden". Solange es nur Plex gab, war das dasselbe. Sobald
  // es zwei gibt, zeigte die Jellyfin-Seite damit die Plex-Verbindung samt
  // deren Einstellungen an, obwohl Jellyfin gar nicht verbunden war.
  const verbindungen = settings?.mediaserver_connections ?? []
  const dieseVerbindung = offen
    ? verbindungen.find((v) => v.provider === offen)
    : undefined
  const verbunden =
    offen !== null ? !!dieseVerbindung : (settings?.mediaserver_configured ?? false)

  const blocksQuery = useQuery({
    queryKey: ['mediaserver-blocks'],
    queryFn: () => api.get<MediaServerBlock[]>('/api/admin/mediaserver/blocks'),
  })

  // ⚠️ Je Anbieter. Vorher stand hier die Gesamtzahl über alle Server - auf
  // *jeder* Server-Seite dieselbe. Auf der Jellyfin-Seite behauptete sie damit
  // etwas über Plex mit.
  const bibliothek = useQuery({
    queryKey: ['mediaserver-library', offen],
    queryFn: () =>
      api.get<MediaServerLibraryState>(
        `/api/admin/mediaserver/library?provider=${encodeURIComponent(offen ?? '')}`,
      ),
    enabled: offen !== null,
  })

  const abgleichen = useMutation({
    // Nur diesen einen Server - der Knopf steht auf dessen Seite. Vorher las
    // ein Klick auf der Jellyfin-Seite auch die Plex-Bibliothek ein.
    mutationFn: () =>
      api.post<MediaServerLibraryState>(
        `/api/admin/mediaserver/library/refresh?provider=${encodeURIComponent(offen ?? '')}`,
        {},
      ),
    onSuccess: (stand) => {
      queryClient.setQueryData(['mediaserver-library', offen], stand)
      // Die Abzeichen hängen daran - ohne das bliebe die Entdecken-Seite auf
      // dem alten Stand, bis der Zwischenspeicher von selbst abläuft.
      void queryClient.invalidateQueries({ queryKey: ['discover'] })
      setMeldung({ ok: true, text: t('mediaserver.librarySyncedNow') })
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('errors.generic'),
      }),
  })

  useEffect(() => {
    if (!settings) return
    setDraft({
      mediaserver_auto_import: settings.mediaserver_auto_import,
      mediaserver_default_role: settings.mediaserver_default_role,
    })
  }, [settings])

  const verbinden = useMediaServerChallenge<{
    status: string
    servers: MediaServerOption[]
    shared_hidden: number
  }>({
    startPfad: '/api/admin/mediaserver/connect/start',
    abfragePfad: '/api/admin/mediaserver/connect/poll',
    onFertig: (ergebnis) => {
      setAuswahl(ergebnis.servers)
      setAusgeblendet(ergebnis.shared_hidden)
    },
  })

  // Der Merkzettel wird für die Auswahl noch einmal gebraucht - der Vorgang
  // bleibt deshalb offen, bis ein Server gewählt ist.
  useEffect(() => {
    if (verbinden.start) setPollToken(verbinden.start.poll_token)
  }, [verbinden.start])

  const waehlen = useMutation({
    mutationFn: (machine_id: string) =>
      api.post<{
        user: User
        server_name: string
        server_url: string
        reachable: boolean
        warning: string | null
      }>('/api/admin/mediaserver/connect/select', {
        poll_token: pollToken,
        machine_id,
      }),
    onSuccess: (ergebnis) => {
      // Das eigene Konto wurde dabei verknüpft - sonst zeigte das Profil
      // weiterhin "nicht verbunden".
      updateUser(ergebnis.user)
      setAuswahl(null)
      setPollToken(null)
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
      // **Auch die Konfiguration**: An ihr hängt, ob es den Merklisten-Reiter
      // und den Plex-Anmeldeknopf überhaupt gibt. Sie liegt fünf Minuten im
      // Zwischenspeicher - ohne diese Zeile ist Plex verbunden, die
      // Oberfläche weiß es nur noch nicht, und es sieht aus, als fehle die
      // Funktion.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      // Ein unerreichbarer Server ist kein Fehler, aber eine Warnung wert.
      setMeldung(
        ergebnis.warning
          ? { ok: false, text: ergebnis.warning }
          : { ok: true, text: t('settings.saved') },
      )
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('settings.saveFailed'),
      }),
  })

  // ⚠️ **Der Anbieter muss mit.** Ohne ihn trennt das Backend *alle*
  // Verbindungen - so gebaut, weil es früher nur eine geben konnte. Genau das
  // ist passiert: Ein Klick auf „Jellyfin trennen" nahm Plex gleich mit.
  //
  // `trennProvider` ist der Anbieter, um den es gerade geht: die geöffnete
  // Kachel, oder die, deren Trennen-Knopf gedrückt wurde.
  const trennProvider = trennenFuer ?? offen

  // Wen ein Trennen träfe. Wird nur geladen, solange überhaupt eine Verbindung
  // besteht - ohne Server gibt es nichts zu trennen und nichts zu warnen.
  const trennFolgen = useQuery({
    queryKey: ['mediaserver-trenn-folgen', trennProvider],
    queryFn: () =>
      api.get<MediaServerDisconnectImpact>(
        `/api/admin/mediaserver/connection/folgen?provider=${encodeURIComponent(trennProvider ?? '')}`,
      ),
    enabled: verbunden && !!trennProvider,
  })

  const trennen = useMutation({
    // `bestaetigt` überstimmt die Sperre im Backend. Die Oberfläche schickt es
    // erst mit, nachdem der Dialog die Namen gezeigt hat - der Klick allein
    // ist keine Bestätigung.
    mutationFn: (bestaetigt: boolean) =>
      api.delete<void>(
        `/api/admin/mediaserver/connection?provider=${encodeURIComponent(trennProvider ?? '')}` +
          (bestaetigt ? '&bestaetigt=true' : ''),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['settings'] })
      // Beim Trennen genauso - sonst bliebe der Merklisten-Reiter stehen.
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      void queryClient.invalidateQueries({ queryKey: ['mediaserver-trenn-folgen'] })
      setTrennenOffen(false)
      setTrennenFuer(null)
      setMeldung({ ok: true, text: t('mediaserver.disconnected') })
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('settings.saveFailed'),
      }),
  })

  const speichern = useMutation({
    mutationFn: (payload: Draft) =>
      api.put<AppSettings>('/api/settings', payload),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data)
      setMeldung({ ok: true, text: t('settings.saved') })
    },
    onError: (caught) =>
      setMeldung({
        ok: false,
        text: caught instanceof ApiError ? caught.message : t('settings.saveFailed'),
      }),
  })

  const sperreLoesen = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/admin/mediaserver/blocks/${id}`),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['mediaserver-blocks'] }),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setMeldung(null)
    speichern.mutate(draft)
  }

  if (settingsQuery.isPending) return <Spinner />

  // Solange die Auskunft noch lädt, gilt "niemand gefährdet" - der Dialog
  // behauptet dann lieber nichts, als eine Zahl zu zeigen, die gleich springt.
  const gefaehrdet = trennFolgen.data?.gefaehrdet ?? []
  const geaendert =
    !!settings &&
    (draft.mediaserver_auto_import !== settings.mediaserver_auto_import ||
      draft.mediaserver_default_role !== settings.mediaserver_default_role)

  // Die drei Arten in fester Reihenfolge. Welche davon *diese Fassung* kann,
  // sagt das Backend – eine zweite Liste hier wäre genau die Doppelung, die
  // sich bis 0.18.0 zwischen PROVIDERS und der Anbieter-Weißliste aufgetan hat.
  const bekannt = settings ? ['plex', 'jellyfin', 'emby'] : []
  const verfuegbar = config?.mediaserver_available ?? []
  const verbundene = config?.mediaserver_providers ?? []
  const mitPasswort = config?.mediaserver_password_login ?? []
  // Sperren gehören zu *einem* Anbieter: Dieselbe Kontonummer kann bei Plex und
  // bei Jellyfin zwei verschiedene Menschen sein. Die Tabelle trennt sie
  // deshalb seit jeher - die Anzeige tat es bisher nicht.
  const gesperrte = (blocksQuery.data ?? []).filter((b) => b.provider === offen)

  const kachel = (anbieter: string): TileState => ({
    provider: anbieter,
    available: verfuegbar.includes(anbieter),
    connected: verbundene.includes(anbieter),
    serverName: verbindungen.find((v) => v.provider === anbieter)?.name,
    // Aus der Liste der Verknüpfungen, nicht aus der Einzelspalte: Die nennt
    // nur die zuletzt hinzugekommene, und die Kachel des anderen Anbieters
    // bliebe ohne Namen, obwohl auch sie verknüpft ist.
    account:
      (me?.mediaserver_accounts ?? []).find((k) => k.provider === anbieter)?.username ??
      null,
    // Stand vorher in einer eigenen Leiste unter der Kachel und wiederholte
    // dabei Name und Zustand. Neu war nur die Adresse selbst.
    url: verbindungen.find((v) => v.provider === anbieter)?.url,
  })

  return (
    <div className="flex flex-col gap-6">
      {/* ⚠️ **Hier lagen die Kacheln einmal frei auf der Seite** - mit der
          Begründung, Kästen in einem Kasten sähen unruhig aus. Das stimmt für
          sich genommen, war aber der Grund, warum ausgerechnet diese Seite
          anders aussah als jede andere Einstellungsseite: Überall sonst sitzt
          das, was man einstellt, in einer grau hinterlegten Sektion. Beim
          Durchklicken fiel genau das auf. Einheitlichkeit wiegt hier schwerer
          als die Sorge vor der Verschachtelung. */}
      <Section title={t('mediaserver.adminTitle')} breit>
        <p className="-mt-2 max-w-3xl text-sm text-mist-500">
          {t('mediaserver.adminIntro')}
        </p>

        {/* Übersicht: alle drei. Geöffnet: nur die gewählte, damit die Seite
            darunter eindeutig zu *einem* Server gehört.

            ⚠️ Das Raster bleibt dabei **dasselbe**. Vorher wechselte es auf eine
            schmale Einzelspalte, und die Kachel änderte beim Anklicken ihre
            Größe - die Seite sprang. Mit gleichem Raster steht die offene Kachel
            genau dort, wo sie vorher stand. */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(offen === null ? bekannt : [offen]).map((anbieter) => (
          <MediaServerTile
            key={anbieter}
            state={kachel(anbieter)}
            selected={offen === anbieter}
            onOpen={() => setOffen(offen === anbieter ? null : anbieter)}
            onDisconnect={() => {
              setTrennenFuer(anbieter)
              setTrennenOffen(true)
            }}
          />
        ))}
        </div>

        {offen !== null && (
          <button
            type="button"
            onClick={() => setOffen(null)}
            className="self-start text-sm text-mist-500 hover:text-mist-300"
          >
            ← {t('mediaserver.back')}
          </button>
        )}
      </Section>

      {/* Die Karte gehört zur **geöffneten** Kachel. Auf der Übersicht gäbe es
          darin nichts zu sagen - eine fast leere Karte wäre nur Rauschen. */}
      {offen !== null && (
      <Card className="flex flex-col gap-6">
        {verbunden ? null : auswahl ? (
          <div className="flex flex-col gap-2">
            {/* ⚠️ Diese Zeilen sind Schaltflächen, sahen aber aus wie eine
                Aufzählung - "man muss unten noch auf den Server klicken, oder?"
                war die Reaktion darauf. Deshalb jetzt: eine Aufforderung statt
                einer Überschrift, ein Pfeil als sichtbarer Hinweis, und
                während des Verbindens steht da, was gerade passiert. */}
            <p className="text-sm font-medium text-mist-300">
              {t('mediaserver.selectServer')}
            </p>
            {auswahl.length === 0 && (
              <p className="text-sm text-mist-600">{t('mediaserver.noServers')}</p>
            )}
            {/* Erklärt eine kürzere Liste als erwartet: Server, auf die nur
                geteilt wurde, stehen bewusst nicht zur Wahl. */}
            {ausgeblendet > 0 && (
              <p className="text-xs text-mist-600">
                {t('mediaserver.sharedHidden', { count: ausgeblendet })}
              </p>
            )}
            {auswahl.map((server) => {
              const laeuft = waehlen.isPending && waehlen.variables === server.machine_id
              return (
                <button
                  key={server.machine_id}
                  type="button"
                  onClick={() => waehlen.mutate(server.machine_id)}
                  disabled={waehlen.isPending}
                  className="flex items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-left text-sm transition hover:border-accent-500 hover:bg-ink-850 disabled:opacity-60"
                >
                  <span className="min-w-0">
                    <span className="font-medium text-mist-200">{server.name}</span>
                    <span className="ml-2 text-xs text-mist-600">{server.url}</span>
                    {laeuft && (
                      <span className="mt-1 block text-xs text-accent-400">
                        {t('mediaserver.connecting')}
                      </span>
                    )}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {server.owned && (
                      <span className="text-xs text-accent-400">
                        {t('mediaserver.owned')}
                      </span>
                    )}
                    {laeuft ? (
                      <Spinner />
                    ) : (
                      <span aria-hidden="true" className="text-mist-600">
                        →
                      </span>
                    )}
                  </span>
                </button>
              )
            })}

            {/* Warum das dauert: Der Verbinden-Endpunkt liest die Bibliothek
                gleich mit ein - bei ein paar tausend Titeln sind das
                Sekunden bis eine Minute. Ohne diesen Satz sieht es aus, als
                hinge etwas. */}
            <p className="text-xs text-mist-600">
              {t('mediaserver.selectServerHint')}
            </p>
          </div>
        ) : mitPasswort.includes(offen) ? (
          /* Anbieter ohne Zwischenstelle: Adresse, Benutzername, Passwort.
             Welche das sind, sagt das Backend – hier steht keine zweite
             Liste, die davon abweichen könnte. */
          <MediaServerPasswordForm
            provider={offen}
            onVerbunden={(ergebnis) => {
              // Das eigene Konto wurde dabei verknüpft. Ohne diese Zeile
              // zeigte die Kachel weiter den Namen von *vorher* - der
              // angemeldete Benutzer steht im React-Zustand, nicht im
              // Abfrage-Zwischenspeicher, und wird von `invalidateQueries`
              // deshalb gar nicht erreicht.
              updateUser(ergebnis.user)
              setMeldung(
                ergebnis.warning
                  ? { ok: false, text: ergebnis.warning }
                  : {
                      ok: true,
                      text: t('mediaserver.connectedTo', { name: ergebnis.server_name }),
                    },
              )
              void queryClient.invalidateQueries({ queryKey: ['settings'] })
              void queryClient.invalidateQueries({ queryKey: ['config'] })
              void queryClient.invalidateQueries({ queryKey: ['mediaserver-library'] })
            }}
          />
        ) : verbinden.start ? (
          <MediaServerPrompt start={verbinden.start} onAbbrechen={verbinden.abbrechen} />
        ) : (
          <div>
            <Button onClick={() => void verbinden.starten()} loading={verbinden.laeuft}>
              {t('mediaserver.connectWith', { name: providerName(offen) })}
            </Button>
          </div>
        )}

        {verbinden.fehler && <ErrorBanner message={verbinden.fehler} />}
      {/* ⚠️ Die Vorgaben gelten für Konten, die beim **Anmelden** entstehen.
          Über einen Anbieter, mit dem man sich gar nicht anmelden kann, kann
          auch keines entstehen - die Karte versprach dort etwas, das nicht
          eintreten kann ("Wer Zugriff auf deine Bibliothek hat, bekommt beim
          ersten Anmelden selbst ein Konto"). Statt sie zu verstecken, steht
          dort jetzt, woran es liegt. */}
        {verbunden && mitPasswort.includes(offen) && (
        <Abschnitt>
          <h2 className="text-lg font-semibold">{t('mediaserver.defaults')}</h2>
          <p className="mt-1.5 text-sm text-mist-500">
            {t('mediaserver.noLoginYet', { name: providerName(offen) })}
          </p>
        </Abschnitt>
        )}

        {verbunden && !mitPasswort.includes(offen) && (
        <Abschnitt>
          <h2 className="text-lg font-semibold">{t('mediaserver.defaults')}</h2>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                checked={draft.mediaserver_auto_import}
                onChange={(event) =>
                  setDraft({ ...draft, mediaserver_auto_import: event.target.checked })
                }
                className="mt-1 h-4 w-4 accent-accent-500"
              />
              <span>
                <span className="text-sm text-mist-200">{t('mediaserver.autoImport')}</span>
                <span className="mt-0.5 block text-xs text-mist-600">
                  {t('mediaserver.autoImportHint')}
                </span>
              </span>
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
                {t('mediaserver.defaultRole')}
              </span>
              <select
                value={draft.mediaserver_default_role}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    mediaserver_default_role: event.target.value as Draft['mediaserver_default_role'],
                  })
                }
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                {/* "Administrator" fehlt hier bewusst - eine automatisch
                    angelegte Anmeldung darf niemals volle Rechte bekommen. */}
                <option value="user">{t('adminUsers.roleUser')}</option>
                <option value="approver">{t('adminUsers.roleApprover')}</option>
              </select>
            </label>

            {/* Kein Eingabefeld, sondern eine Auskunft.

                Frueher standen hier Stueckzahlen fuer neue Konten. Im
                Speicher-Betrieb taten sie nichts - dort zaehlt der belegte
                Platz, und die Pruefung steigt vorher aus. Also trug man Zahlen
                ein, die wirkungslos blieben.

                Statt zwei Vorgaben fuer dasselbe gibt es jetzt eine Auskunft,
                was neue Konten bekommen - und der Weg dorthin steht dabei. */}
            <p className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm text-mist-500">
              {settings?.storage_default_limit_gb
                ? t('mediaserver.newAccountsStorage', {
                    gb: settings.storage_default_limit_gb,
                  })
                : t('mediaserver.newAccountsStorageUnlimited')}
            </p>


        {meldung && (
              <p
                className={
                  'rounded-xl border px-4 py-3 text-sm ' +
                  (meldung.ok
                    ? 'border-ok-500/40 bg-ok-500/10 text-ok-500'
                    : 'border-accent-600/50 bg-accent-700/15 text-accent-400')
                }
              >
                {meldung.text}
              </p>
            )}

            <div>
              {/* Ausdrücklicher Knopf statt Speichern beim Verlassen des
                  Feldes - ohne Rückmeldung weiß niemand, ob es ankam. */}
              <Button type="submit" loading={speichern.isPending} disabled={!geaendert}>
                {t('common.save')}
              </Button>
            </div>
          </form>
        </Abschnitt>
        )}

        {verbunden && (
        <Abschnitt>
          <div>
            <h2 className="text-lg font-semibold">{t('mediaserver.library')}</h2>
            <p className="mt-1.5 text-sm text-mist-500">{t('mediaserver.libraryIntro')}</p>
          </div>

          <p className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm">
            {/* ⚠️ Nur der Zeitpunkt, keine Zahl.
                
                Die Zahl sollte beweisen, dass überhaupt etwas gelesen wurde -
                das leistet der Zeitstempel aber schon: Er stammt aus den
                Titelzeilen selbst, ohne gelesene Titel gibt es kein Datum.
                Die Zahl sagte nur noch *wie viel*, und genau das stimmte
                hier nicht: Sie zählte über alle Server und stand auf jeder
                Server-Seite gleich. */}
            {bibliothek.data?.updated_at ? (
              <>
                <span className="font-medium text-mist-200">
                  {t('mediaserver.librarySynced')}
                </span>
                <span className="ml-2 text-mist-500">
                  {formatDateTime(bibliothek.data.updated_at, i18n.language)}
                </span>
              </>
            ) : (
              <span className="text-mist-500">{t('mediaserver.libraryNever')}</span>
            )}
          </p>

          <div>
            <Button
              variant="ghost"
              onClick={() => abgleichen.mutate()}
              loading={abgleichen.isPending}
            >
              {t('mediaserver.syncNow')}
            </Button>
          </div>
        </Abschnitt>
        )}

        {/* ⚠️ Nur bei Anbietern, über die überhaupt ein Konto entstehen kann.
          Eine Sperre hält genau das auf - bei Jellyfin entsteht ohnehin keines
          (keine E-Mail-Adresse, siehe `knows_email`), die Liste könnte dort
          also nichts bewirken und stünde nur im Weg. */}
        {!mitPasswort.includes(offen) && (
        <Abschnitt>
        <div>
          <h2 className="text-lg font-semibold">{t('mediaserver.blocks')}</h2>
          <p className="mt-1.5 text-sm text-mist-500">
            {t('mediaserver.blocksIntroFor', { name: providerName(offen) })}
          </p>
        </div>

        {gesperrte.length === 0 ? (
          <p className="text-sm text-mist-600">{t('mediaserver.blocksEmpty')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {gesperrte.map((eintrag) => (
              <li
                key={eintrag.id}
                className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 text-sm"
              >
                <span className="text-mist-200">
                  {eintrag.username ?? eintrag.account_id}
                  <span className="ml-2 text-xs text-mist-600">{eintrag.provider}</span>
                </span>
                <Button
                  variant="ghost"
                  onClick={() => sperreLoesen.mutate(eintrag.id)}
                  loading={sperreLoesen.isPending && sperreLoesen.variables === eintrag.id}
                >
                  {t('mediaserver.unblock')}
                </Button>
              </li>
            ))}
          </ul>
        )}
        </Abschnitt>
        )}
      </Card>
      )}

      {/* Ausserhalb jeder Bedingung: Ausgeloest wird er von der Kachel,
          und die steht auch auf der Uebersicht. Lag er im Detailbereich,
          setzte der Klick nur den Zustand - sichtbar wurde nichts. */}
      <ConfirmDialog
      open={trennenOffen}
      title={t('mediaserver.disconnectTitle', {
      name: settings?.mediaserver_name ?? '',
      })}
      description={
      <>
      <p>
      {t('mediaserver.disconnectLinked', {
      count: trennFolgen.data?.verknuepft ?? 0,
      })}
      {gefaehrdet.length === 0 && ` ${t('mediaserver.disconnectAllSafe')}`}
      </p>
      <p className="mt-2">{t('mediaserver.disconnectKeeps')}</p>
      </>
      }
      warning={
      gefaehrdet.length > 0 ? (
      <>
      <p>
      {t('mediaserver.disconnectLocksOut', { count: gefaehrdet.length })}{' '}
      <span className="font-medium">
      {gefaehrdet
      .map((konto) => konto.display_name || konto.username)
      .join(', ')}
      </span>
      </p>
      <p className="mt-1">{t('mediaserver.disconnectFix')}</p>
      </>
      ) : undefined
      }
      confirmLabel={
      gefaehrdet.length > 0
      ? t('mediaserver.disconnectAnyway')
      : t('mediaserver.disconnectServer')
      }
      // Erst hier wird überstimmt - und nur, wenn der Dialog die Namen
      // auch wirklich gezeigt hat.
      onConfirm={() => trennen.mutate(gefaehrdet.length > 0)}
      onCancel={() => setTrennenOffen(false)}
      loading={trennen.isPending}
      />
    </div>
  )
}
