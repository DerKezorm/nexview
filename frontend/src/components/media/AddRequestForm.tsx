import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { ArrOptions, MediaItem, QualityTier } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { useConfig } from '../../hooks/useConfig'
import { anfragenStandNeuLaden } from '../../lib/refresh'
import { Fenster } from '../Fenster'
import { Button, ErrorBanner, Spinner } from '../ui'

type AddRequestFormProps = {
  item: MediaItem
  onDone: () => void
  /**
   * Kam der Klick von der Merklisten-Seite? Reine Herkunftsangabe – am
   * Ablauf ändert sie nichts, sie macht die Anfrage nur nachträglich
   * zuordenbar (Abzeichen und Filter „Über Merkliste angefragt").
   */
  fromWatchlist?: boolean
}

type CreatedRequest = { id: number; status: string; title: string }

/**
 * Auswahl von Qualitätsprofil und Zielordner, dann Anfrage abschicken.
 *
 * Die Auswahlmöglichkeiten kommen direkt aus Radarr bzw. Sonarr - es gibt
 * also nichts zu tippen und nichts, was dort nicht existiert.
 */
export function AddRequestForm({
  item,
  onDone,
  fromWatchlist = false,
}: AddRequestFormProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { data: config } = useConfig()

  // Sortiert der Betreiber seine Mediathek in mehrere Ordner, waehlt erst der
  // Entscheider bei der Freigabe. Dann gibt es hier nichts zu entscheiden -
  // und die Listen aus Radarr braucht dieser Benutzer gar nicht erst zu laden.
  //
  // Wer selbst freigeben darf, ist ausgenommen: Er waere es, der spaeter
  // waehlt, also waehlt er gleich jetzt.
  const zielSpaeter =
    Boolean(
      item.media_type === 'movie'
        ? config?.approver_picks_target_movie
        : config?.approver_picks_target_tv,
    ) && !user?.can_approve

  // Gibt es fuer diese Medienart ueberhaupt eine 4K-Instanz, und darf dieser
  // Benutzer sie nutzen? Nur dann erscheint der Umschalter.
  const uhdEingerichtet =
    item.media_type === 'movie'
      ? Boolean(config?.radarr_uhd_configured)
      : Boolean(config?.sonarr_uhd_configured)
  const darfUhd =
    user?.role === 'admin' ||
    (item.media_type === 'movie'
      ? Boolean(user?.can_request_uhd_movies)
      : Boolean(user?.can_request_uhd_series))
  const uhdMoeglich = uhdEingerichtet && darfUhd

  // Welche Stufen sind ueberhaupt noch offen? Ein Film, der in 1080p schon
  // liegt, laesst sich nur noch in 4K holen - und umgekehrt. Genau dafuer gibt
  // es die zweite Instanz.
  const istSerie = item.media_type === 'tv'

  /**
   * Ist diese Staffel schon vergeben – vorhanden oder angefragt?
   *
   * Von Liste **und** Sperre gemeinsam benutzt: Was man nicht anhaken kann,
   * darf auch nicht mitzählen, wenn entschieden wird, ob überhaupt noch etwas
   * zu holen ist.
   */
  const belegt = (staffel: (typeof item.seasons)[number]) =>
    Boolean(staffel.requested) ||
    (staffel.episode_count > 0 && staffel.episodes_available >= staffel.episode_count)

  const standardOffen = item.status === 'not_requested'
  // ⚠️ Ein **fehlendes** `status_uhd` heißt „unbekannt", nicht „belegt". Nicht
  // jede Kachel trägt die zweite Achse mit – aus dem Kalender und von der
  // Merkliste kommt sie gar nicht mit. Als „liegt schon in 4K vor" gelesen,
  // sperrte das eine Anfrage, die es geben darf, und behauptete im
  // Sprechblasentext obendrein etwas Falsches. Großzügig zu sein ist hier
  // gefahrlos: Eine echte Doppelanfrage weist der Server ohnehin ab.
  const uhdOffen =
    uhdMoeglich && (item.status_uhd == null || item.status_uhd === 'not_requested')
  const [tier, setTier] = useState<QualityTier>(
    standardOffen || !uhdOffen ? 'standard' : 'uhd',
  )

  const [profileId, setProfileId] = useState<number | null>(null)
  const [folder, setFolder] = useState('')
  /**
   * Welche Staffeln angefragt werden – **eine Menge, kein einzelner Wert.**
   *
   * Wer die Staffeln 1, 4 und 7 will, soll das in einem Zug sagen können;
   * daraus werden drei Anfragen, eine je Staffel. Das entspricht dem
   * Datenmodell (`MediaRequest.season` ist eine Zahl) und der Doppel-Prüfung,
   * die ohnehin je Staffel greift.
   *
   * ⚠️ **Nichts ist vorausgewählt.** Hier stand einmal „die neueste Staffel",
   * gedacht für das Nachfordern bei einer laufenden Serie. Seit weitere
   * Staffeln immer anfragbar sind, trifft dieser Fall auf **jede** Serie zu –
   * und dann steht plötzlich Staffel 11 angehakt da, ohne dass jemand sie
   * gewählt hätte. Eine Vorauswahl, die Speicher kostet, muss von der Person
   * kommen.
   */
  const [staffeln, setStaffeln] = useState<Set<number>>(new Set())
  // Ob das Auswahlfenster offen ist.
  const [waehlt, setWaehlt] = useState(false)
  /**
   * Sollen künftige Staffeln automatisch mitkommen?
   *
   * ⚠️ Standardmäßig **aus**, und das ist der Punkt: Vorher hieß „ganze Serie"
   * in Sonarr `monitor: "all"` – also auch alles, was es noch gar nicht gibt.
   * Mit Kontingenten ist das ein Blankoscheck über Speicher, den niemand
   * beziffern kann.
   */
  const [kuenftige, setKuenftige] = useState(false)

  const optionsQuery = useQuery({
    queryKey: ['arr-options', item.media_type, tier],
    queryFn: () =>
      api.get<ArrOptions>(`/api/arr/${item.media_type}/options?tier=${tier}`),
    staleTime: 5 * 60 * 1000,
    retry: false,
    enabled: !zielSpaeter,
  })

  // Vorauswahl treffen, sobald die Listen da sind - meist gibt es ohnehin
  // nur einen Zielordner. Welches Profil vorausgewählt wird, entscheidet der
  // Server: das vom Admin gesetzte Standardprofil, oder das erste erlaubte,
  // falls der Standard für diesen Benutzer gesperrt ist.
  useEffect(() => {
    const data = optionsQuery.data
    if (!data) return
    setProfileId(
      (current) =>
        current ?? data.default_quality_profile_id ?? data.quality_profiles[0]?.id ?? null,
    )
    setFolder((current) => current || data.default_root_folder || data.root_folders[0]?.path || '')
  }, [optionsQuery.data])

  // Stufenwechsel setzt die Auswahl zurueck. Die Profil-Kennungen der beiden
  // Instanzen kollidieren: Profil 1 der 1080p-Instanz ist ein voellig anderes
  // als Profil 1 der 4K-Instanz. Bliebe die alte Wahl stehen, ginge sie an die
  // falsche Instanz - und Radarr nimmt eine unbekannte Kennung je nach Fassung
  // kommentarlos an.
  function stufeWechseln(neu: QualityTier) {
    if (neu === tier) return
    setTier(neu)
    setProfileId(null)
    setFolder('')
  }

  const createMutation = useMutation({
    mutationFn: async () => {
      const gemeinsam = {
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
        tier,
        quality_profile_id:
          zielSpaeter || !options?.quality_profile_choice ? null : profileId,
        // Ohne Auswahlrecht bewusst nichts mitschicken: welcher Ordner gilt,
        // entscheidet dann allein der Server.
        root_folder_path: !zielSpaeter && options?.root_folder_choice ? folder : null,
        from_watchlist: fromWatchlist,
        monitor_future: istSerie ? kuenftige : false,
      }

      // Filme haben keine Staffel; bei Serien ist mindestens eine gewählt -
      // dafür sorgt ``staffelGewaehlt``.
      if (!istSerie) {
        return api.post<CreatedRequest>('/api/requests', {
          ...gemeinsam,
          season: null,
        })
      }

      // Nacheinander, nicht parallel: SQLite lässt genau einen Schreiber zu,
      // und bei einer Handvoll Staffeln ist der Unterschied nicht messbar.
      let letzte: CreatedRequest | null = null
      let uebersprungen = 0
      for (const nummer of [...staffeln].sort((a, b) => a - b)) {
        try {
          letzte = await api.post<CreatedRequest>('/api/requests', {
            ...gemeinsam,
            season: nummer,
          })
        } catch (fehler) {
          // ⚠️ Eine bereits laufende Staffel darf den Stapel nicht abbrechen.
          // Wer 1, 4 und 7 anhakt und 4 ist schon unterwegs, will 1 und 7
          // trotzdem haben – und „ist schon angefragt" ist ohnehin das
          // Ergebnis, das er wollte.
          if (fehler instanceof ApiError && fehler.status === 409) {
            uebersprungen += 1
            continue
          }
          throw fehler
        }
      }
      if (letzte === null && uebersprungen > 0) {
        throw new ApiError(409, t('request.allSeasonsAlready'))
      }
      return letzte as CreatedRequest
    },
    onSuccess: () => {
      // Badges und Kontingent neu laden - auch auf der Seite, die hinter
      // diesem Fenster liegt und gleich wieder sichtbar wird.
      anfragenStandNeuLaden(queryClient)
      onDone()
    },
  })

  // Die Abfrage ist abgeschaltet, wenn spaeter gewaehlt wird - eine
  // abgeschaltete Abfrage bleibt dauerhaft "pending", deshalb hier zuerst.
  if (!zielSpaeter && optionsQuery.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-mist-500">
        <Spinner /> {t('common.loading')}
      </p>
    )
  }

  if (!zielSpaeter && optionsQuery.isError) {
    return (
      <ErrorBanner
        message={
          optionsQuery.error instanceof ApiError
            ? optionsQuery.error.message
            : t('errors.generic')
        }
      />
    )
  }

  const options = optionsQuery.data ?? null
  /**
   * Gibt es auf dieser Stufe überhaupt noch etwas zu holen?
   *
   * ⚠️ **Bei Serien entscheidet die Staffel, nicht der Titel.** Der Titel steht
   * schon auf „angefragt", sobald *irgendeine* Staffel läuft – und dann war
   * der Knopf aus, obwohl drei andere Staffeln angehakt waren. Der Server
   * hätte sie anstandslos angenommen; es war allein die Maske, die zumachte.
   */
  const stufeOffen = istSerie
    ? item.seasons.some((staffel) => !belegt(staffel))
    : tier === 'standard'
      ? standardOffen
      : uhdOffen
  // ⚠️ Bei Serien **muss** eine Staffel gewählt sein.
  //
  // Ohne diese Bedingung fiel das Absenden auf „ganze Serie" zurück – also
  // auf genau das, was hier abgeschafft werden sollte: eine Anfrage, die auch
  // alle künftigen Staffeln einschließt, ohne dass jemand das gesagt hat. Wer
  // alles will, hat dafür „Alle inkl. künftige".
  const staffelGewaehlt = !istSerie || staffeln.size > 0
  const ready = !stufeOffen || !staffelGewaehlt
    ? false
    : zielSpaeter
    ? true
    : options !== null &&
      (!options.quality_profile_choice || profileId !== null) &&
      (!options.root_folder_choice || folder !== '')

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <h3 className="text-sm font-semibold">{t('request.chooseOptions')}</h3>

      {/* Nur wenn es beide Stufen gibt und der Benutzer beide darf. Sonst
          bleibt der Dialog genau so, wie er immer war. */}
      {uhdMoeglich && (
        <div
          className="mt-3 flex rounded-full border border-ink-700 bg-ink-900 p-0.5"
          role="group"
          aria-label={t('uhd.tier')}
        >
          {(['standard', 'uhd'] as const).map((stufe) => {
            const offen = stufe === 'standard' ? standardOffen : uhdOffen
            return (
              <button
                key={stufe}
                type="button"
                onClick={() => stufeWechseln(stufe)}
                aria-pressed={tier === stufe}
                disabled={!offen}
                title={offen ? undefined : t('uhd.tierBelegt')}
                className={
                  'flex-1 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ' +
                  (tier === stufe
                    ? 'bg-accent-500 text-white'
                    : offen
                      ? 'text-mist-500 hover:text-mist-100'
                      : 'cursor-not-allowed text-mist-700')
                }
              >
                {t(stufe === 'standard' ? 'uhd.tierStandard' : 'uhd.tierUhd')}
              </button>
            )
          })}
        </div>
      )}

      {/* Warnen, nicht sperren. Der Titel darf in die 4K-Instanz - vielleicht
          soll sie ihn ja übernehmen. Nur soll niemand versehentlich eine
          zweite 4K-Datei anlegen, ohne von der ersten zu wissen. Steht direkt
          unter dem Umschalter, weil dort die Entscheidung fällt, und in Gelb
          statt Rot: Es ist kein Fehler. */}
      {tier === 'uhd' && item.uhd_in_standard && (
        <p className="mt-3 rounded-xl border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs leading-relaxed text-warn-500">
          {t('uhd.alreadyStandardUhd')}
        </p>
      )}

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Bei **jeder** Serie, auch bei einstaffligen. Vorher erschien die
            Auswahl erst ab zwei Staffeln – und ohne sie hieß die Anfrage
            „ganze Serie", was in Sonarr auch alle künftigen einschließt. Wer
            eine einstaffelige Serie anfragte, unterschrieb damit unbemerkt für
            Staffel 2, 3 und alles Weitere.

            Als Fenster und nicht als Liste im Formular: Eine Serie mit zwanzig
            Staffeln macht das Formular sonst unbenutzbar, und die Auswahl ist
            eine eigene Entscheidung – nicht ein Feld unter vielen. */}
        {istSerie && item.seasons.length > 0 && (
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
              {t('request.season')}
            </span>
            <button
              type="button"
              onClick={() => setWaehlt(true)}
              className="flex items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-left text-sm text-mist-100 hover:border-accent-600"
            >
              <span className="min-w-0 truncate">
                {staffeln.size === 0
                  ? t('request.seasonNonePicked')
                  : t('request.seasonPicked', {
                      list: [...staffeln].sort((a, b) => a - b).join(', '),
                      count: staffeln.size,
                    })}
                {kuenftige && ` · ${t('request.futureShort')}`}
              </span>
              <span className="shrink-0 text-xs text-mist-500">
                {t('request.seasonChoose')}
              </span>
            </button>
          </div>
        )}

        {/* Darf der Benutzer das Profil gar nicht waehlen, gibt es hier nichts
            zu entscheiden - dann wird das Feld weggelassen wie beim Ordner. */}
        {!zielSpaeter && options !== null && options.quality_profile_choice && (
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
            {t('request.qualityProfile')}
          </span>
          <select
            value={profileId ?? ''}
            onChange={(event) => setProfileId(Number(event.target.value))}
            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
          >
            {options.quality_profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
        )}

        {/* Hat der Administrator die Auswahl abgeschaltet, gibt es hier nichts
            zu entscheiden - dann wird das Feld gar nicht erst gezeigt. Welcher
            Ordner gilt, setzt der Server ohnehin selbst. */}
        {!zielSpaeter && options !== null && options.root_folder_choice && (
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium tracking-wide text-mist-600 uppercase">
              {t('request.rootFolder')}
            </span>
            <select
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              {options.root_folders.map((root) => (
                <option key={root.path} value={root.path}>
                  {root.path}
                  {root.free_space ? ` (${formatSpace(root.free_space)})` : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        {zielSpaeter && (
          <p className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-xs leading-relaxed text-mist-500 sm:col-span-2">
            {t('request.targetLater')}
          </p>
        )}
      </div>

      {createMutation.isError && (
        <div className="mt-3">
          <ErrorBanner
            message={
              createMutation.error instanceof ApiError
                ? createMutation.error.message
                : t('errors.generic')
            }
          />
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => createMutation.mutate()}
          loading={createMutation.isPending}
          disabled={!ready}
        >
          {t('request.submit')}
        </Button>
        <p className="text-xs text-mist-600">
          {stufeOffen && !staffelGewaehlt
            ? t('request.seasonRequired')
            : t('request.hint')}
        </p>
      </div>

      <Fenster
        offen={waehlt}
        titel={t('request.seasonChooseFor', { title: item.title })}
        onSchliessen={() => setWaehlt(false)}
        fuss={<Button onClick={() => setWaehlt(false)}>{t('common.done')}</Button>}
      >
        <div className="flex flex-col gap-3">
          {item.seasons.length > 1 && (
            <button
              type="button"
              onClick={() => {
                // Nur das Wählbare: Eine vorhandene oder laufende Staffel
                // anzuhaken hieße, sie gleich darauf mit 409 abgelehnt zu
                // bekommen.
                const waehlbar = item.seasons
                  .filter((s) => !belegt(s))
                  .map((s) => s.season_number)
                const alleDa = staffeln.size === waehlbar.length && kuenftige
                setStaffeln(alleDa ? new Set() : new Set(waehlbar))
                setKuenftige(!alleDa)
              }}
              className="self-start text-sm text-mist-400 underline-offset-2 hover:text-accent-500 hover:underline"
            >
              {t(
                staffeln.size > 0 && kuenftige
                  ? 'request.seasonNone'
                  : 'request.seasonAll',
              )}
            </button>
          )}

          {/* Einspaltig. Zweispaltig lief die Lesereihenfolge über Kreuz –
              links 1, rechts 2, darunter 3 – und bei zwanzig Staffeln sucht
              man die gewünschte, statt sie zu finden. */}
          <ul className="flex flex-col">
            {item.seasons.map((staffel) => {
              /* Ausgegraut statt versteckt: Eine Staffel, die kommentarlos
                 fehlt, wirft die Frage auf, wo sie geblieben ist. So steht
                 daneben, warum sie nicht zu haben ist – und dass sie ohnehin
                 unterwegs oder schon da ist, ist ja eine gute Nachricht. */
              const vorhanden =
                staffel.episode_count > 0 &&
                staffel.episodes_available >= staffel.episode_count
              const vergeben = belegt(staffel)
              return (
                <li key={staffel.season_number}>
                  <label
                    className={
                      'flex items-center gap-3 rounded-lg px-2 py-2 ' +
                      (vergeben ? 'opacity-50' : 'cursor-pointer hover:bg-ink-800')
                    }
                  >
                    <input
                      type="checkbox"
                      checked={staffeln.has(staffel.season_number)}
                      disabled={vergeben}
                      onChange={() =>
                        setStaffeln((alt) => {
                          const neu = new Set(alt)
                          if (neu.has(staffel.season_number))
                            neu.delete(staffel.season_number)
                          else neu.add(staffel.season_number)
                          return neu
                        })
                      }
                      className="h-4 w-4 shrink-0 accent-accent-500"
                    />
                    <span className="min-w-0 flex-1 truncate text-sm">{staffel.name}</span>
                    <span className="shrink-0 text-xs text-mist-600">
                      {vergeben
                        ? t(vorhanden ? 'request.seasonHere' : 'request.seasonRunning')
                        : t('request.episodes', { count: staffel.episode_count })}
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>

          {/* ⚠️ Ein eigener Haken, standardmäßig aus. Früher steckte das
              stillschweigend in „ganze Serie": Sonarr überwacht dann auch jede
              künftige Staffel, und mit Kontingenten ist das ein Blankoscheck
              über Speicher, den niemand beziffern kann. */}
          <label className="flex cursor-pointer items-start gap-3 border-t border-ink-800 px-2 pt-3">
            <input
              type="checkbox"
              checked={kuenftige}
              onChange={(event) => setKuenftige(event.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500"
            />
            <span>
              <span className="text-sm">{t('request.future')}</span>
              <span className="mt-0.5 block text-xs text-mist-600">
                {t('request.futureHint')}
              </span>
            </span>
          </label>
        </div>
      </Fenster>
    </div>
  )
}

/** Freier Speicherplatz lesbar machen: 1234567890 -> "1,1 TB" */
function formatSpace(bytes: number): string {
  const terabytes = bytes / 1024 ** 4
  if (terabytes >= 1) return `${terabytes.toFixed(1)} TB`
  return `${Math.round(bytes / 1024 ** 3)} GB`
}
