import { useEffect, useState } from 'react'
import { SerienZuordnung, type Zuordnungsvorschlag } from './SerienZuordnung'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, api } from '../../api/client'
import type { ArrOptions, MediaItem, QualityTier } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { useConfig } from '../../hooks/useConfig'
import { anfragenStandNeuLaden } from '../../lib/refresh'
import { folgenKompakt } from '../../lib/format'
import { Fenster } from '../Fenster'
import { StaffelFolgenWaehler } from './StaffelFolgenWaehler'
import { staffelBelegt } from './staffelbelegung'
import { Button, ErrorBanner, Spinner } from '../ui'
import { darfAnfragen } from '../../lib/status'

type AddRequestFormProps = {
  item: MediaItem
  onDone: () => void
  /**
   * Kam der Klick von der Merklisten-Seite? Reine Herkunftsangabe – am
   * Ablauf ändert sie nichts, sie macht die Anfrage nur nachträglich
   * zuordenbar (Abzeichen und Filter „Über Merkliste angefragt").
   */
  fromWatchlist?: boolean
  /**
   * Namen der eigenen Abos, in denen dieser Titel schon läuft. Kommt vom
   * Server (``in_my_subscriptions``), weil nur dort steht, welche TMDB-Kennung
   * zu welcher Marke gehört - Amazon ist 9 in Deutschland und 119 in der
   * Schweiz, und Netflix hört auch auf 175 und 1796.
   */
  imAbo?: string[]
}

type CreatedRequest = {
  id: number
  status: string
  title: string
  rejection_reason: string | null
  regel_name: string | null
  darf_trotzdem_fragen: boolean
}

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
  imAbo = [],
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
  // Seit dem Kachel-Umbau gilt die Regel je Instanz - der Hinweis und die
  // Felder folgen deshalb der gerade gewaehlten Stufe (Definition weiter
  // unten, nach der tier-Entscheidung).

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

  // Haus-Schalter: Ohne ihn gibt es keine Aufklapp-Pfeile im Wähler - alles
  // sieht aus wie vor dem Umbau, nur ganze Staffeln.
  const folgenErlaubt = Boolean(config?.episode_requests_enabled)

  // Welche Stufen sind ueberhaupt noch offen? Ein Film, der in 1080p schon
  // liegt, laesst sich nur noch in 4K holen - und umgekehrt. Genau dafuer gibt
  // es die zweite Instanz.
  const istSerie = item.media_type === 'tv'

  /**
   * Ist diese Staffel schon **ganz** vergeben – vorhanden oder angefragt?
   *
   * Von Liste **und** Sperre gemeinsam benutzt: Was man nicht anhaken kann,
   * darf auch nicht mitzählen, wenn entschieden wird, ob überhaupt noch etwas
   * zu holen ist. Die Regel selbst wohnt beim Wähler (``staffelBelegt``) –
   * laufende Folgen-Pakete grauen die Staffel dort bewusst nicht aus.
   */
  const belegt = (staffel: (typeof item.seasons)[number]) =>
    staffelBelegt(staffel, tier)

  // Ueber ``darfAnfragen`` statt ueber einen Vergleich mit
  // ``not_requested``: Welche erledigten Zustaende wieder anfragbar sind,
  // steht an **einer** Stelle - sonst haengt es davon ab, welches Fenster
  // gerade offen ist.
  const standardOffen = darfAnfragen(item.status)
  // ⚠️ Ein **fehlendes** `status_uhd` heißt „unbekannt", nicht „belegt". Nicht
  // jede Kachel trägt die zweite Achse mit – aus dem Kalender und von der
  // Merkliste kommt sie gar nicht mit. Als „liegt schon in 4K vor" gelesen,
  // sperrte das eine Anfrage, die es geben darf, und behauptete im
  // Sprechblasentext obendrein etwas Falsches. Großzügig zu sein ist hier
  // gefahrlos: Eine echte Doppelanfrage weist der Server ohnehin ab.
  const uhdOffen =
    uhdMoeglich && (item.status_uhd == null || darfAnfragen(item.status_uhd))
  const [tier, setTier] = useState<QualityTier>(
    standardOffen || !uhdOffen ? 'standard' : 'uhd',
  )

  const [profileId, setProfileId] = useState<number | null>(null)
  const [folder, setFolder] = useState('')

  const zielSpaeter =
    Boolean(
      item.media_type === 'movie'
        ? tier === 'uhd'
          ? config?.approver_picks_target_movie_uhd
          : config?.approver_picks_target_movie
        : tier === 'uhd'
          ? config?.approver_picks_target_tv_uhd
          : config?.approver_picks_target_tv,
    ) && !user?.can_approve
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
  /**
   * Je Staffel die gewählten **einzelnen Folgen** („Folgen-Paket"). Eine
   * Staffel steht entweder hier oder in `staffeln`, nie in beiden: ganz
   * gewählt deckt jede Folge ab. Aus einem Eintrag wird eine Anfrage mit
   * Folgenliste – ein Paket, ein Platz, eine Karte.
   */
  const [folgen, setFolgen] = useState<Map<number, Set<number>>>(new Map())
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

  // „Welche Serie meinst du?" - gefüllt, sobald der Server mit
  // ``tvdb_choice_needed`` antwortet (Issue #5). Solange etwas drinsteht,
  // zeigt dieses Formular nur das Auswahlfenster.
  const [abgelehnt, setAbgelehnt] = useState<CreatedRequest | null>(null)
  const [zuordnung, setZuordnung] = useState<{
    vorschlaege: Zuordnungsvorschlag[]
    frisch: boolean
  } | null>(null)
  // Die getroffene Wahl. Geht als ``tvdb_id`` mit; der Server prüft sie
  // gegen dieselbe Sonarr-Suche, bevor er sie annimmt.
  const [tvdbWahl, setTvdbWahl] = useState<number | null>(null)

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
    // Was in der neuen Stufe vergeben ist, darf nicht angehakt bleiben -
    // sonst geht eine Anfrage raus, die der Server mit 409 ablehnt. Dieselbe
    // Regel wie im Waehler, aus derselben Quelle.
    setStaffeln((alt) => {
      const offen = new Set(
        item.seasons
          .filter((s) => !staffelBelegt(s, neu))
          .map((s) => s.season_number),
      )
      return new Set([...alt].filter((nummer) => offen.has(nummer)))
    })
    // Die Folgen-Auswahl hängt an den Belegt-Daten der alten Stufe - lieber
    // neu wählen als eine Anfrage, die der Server mit 409 ablehnt.
    setFolgen(new Map())
  }

  const createMutation = useMutation({
    mutationFn: async (wahl?: number) => {
      // Die Wahl kommt als Argument, nicht aus dem Zustand: React setzt ihn
      // erst nach dem Rendern, und der Klick löst die Anfrage sofort aus.
      const gewaehlteKennung = wahl ?? tvdbWahl
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
        // Nur gesetzt, nachdem jemand im Auswahlfenster geklickt hat.
        ...(gewaehlteKennung !== null ? { tvdb_id: gewaehlteKennung } : {}),
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
      //
      // Ganze Staffeln und Folgen-Pakete in einem Zug: je Staffel eine
      // Anfrage, ein Paket trägt zusätzlich seine Folgenliste.
      let letzte: CreatedRequest | null = null
      let uebersprungen = 0
      const gewaehlt = [...new Set([...staffeln, ...folgen.keys()])].sort(
        (a, b) => a - b,
      )
      for (const nummer of gewaehlt) {
        const paket = staffeln.has(nummer) ? null : folgen.get(nummer)
        try {
          letzte = await api.post<CreatedRequest>('/api/requests', {
            ...gemeinsam,
            season: nummer,
            ...(paket && paket.size > 0
              ? { episodes: [...paket].sort((a, b) => a - b) }
              : {}),
          })
        } catch (fehler) {
          // ⚠️ Eine bereits laufende Staffel darf den Stapel nicht abbrechen.
          // Wer 1, 4 und 7 anhakt und 4 ist schon unterwegs, will 1 und 7
          // trotzdem haben – und „ist schon angefragt" ist ohnehin das
          // Ergebnis, das er wollte.
          // ⚠️ **Nicht jeder 409 heißt „ist schon angefragt".** Diese Zeile
          // prüfte lange nur den Zahlencode - und verschluckte damit alles,
          // was der Server sonst noch unter 409 meldet. Die Rückfrage nach
          // der richtigen Serie kommt deshalb als 428; hier steht die
          // Bedingung trotzdem enger, damit die nächste Meldung nicht in
          // dieselbe Falle läuft.
          if (
            fehler instanceof ApiError &&
            fehler.status === 409 &&
            fehler.code !== 'tvdb_choice_needed'
          ) {
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
    onSuccess: (angelegt) => {
      // Badges und Kontingent neu laden - auch auf der Seite, die hinter
      // diesem Fenster liegt und gleich wieder sichtbar wird.
      anfragenStandNeuLaden(queryClient)

      // ⚠️ **Eine Regel kann abgelehnt haben, und der Server sagt trotzdem
      // 201.** Die Anfrage *ist* ja entstanden - im Zustand „abgelehnt".
      // Wer das nicht ansieht, schließt hier einfach das Fenster: Der
      // Anfragende klickt, es passiert scheinbar nichts, und er klickt
      // wieder. Der Grund stünde nur unter „Meine Anfragen", wo er ihn nicht
      // sucht.
      if (angelegt?.status === 'rejected') {
        setAbgelehnt(angelegt)
        return
      }
      onDone()
    },
    onError: (fehler) => {
      // Der Server kann die Serie nicht zuordnen und legt Vorschläge vor.
      // Kein Fehler im eigentlichen Sinn: Es fehlt eine Angabe, und die kann
      // nur der geben, der gerade davorsitzt.
      if (fehler instanceof ApiError && fehler.code === 'tvdb_choice_needed') {
        const daten = fehler.data ?? {}
        setZuordnung({
          vorschlaege: (daten.candidates as Zuordnungsvorschlag[] | undefined) ?? [],
          frisch: daten.fresh !== false,
        })
        return
      }
      // Eine abgelaufene Auswahl darf nicht in einer Schleife enden: Der
      // nächste Versuch geht wieder ohne sie los.
      if (fehler instanceof ApiError && fehler.code === 'tvdb_choice_invalid') {
        setTvdbWahl(null)
        setZuordnung(null)
      }
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

  if (abgelehnt) {
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-bad-500/40 bg-bad-500/10 px-4 py-3">
          <div className="text-sm font-semibold text-bad-500">
            {t('request.byRuleDeclined')}
          </div>
          {abgelehnt.rejection_reason && (
            <p className="mt-1 text-sm text-mist-300">{abgelehnt.rejection_reason}</p>
          )}
          <p className="mt-2 text-xs text-mist-500">
            {abgelehnt.darf_trotzdem_fragen
              ? t('request.byRuleMayAsk')
              : t('request.byRuleWhere')}
          </p>
        </div>
        <div className="flex justify-end">
          <Button onClick={onDone}>{t('common.close')}</Button>
        </div>
      </div>
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
  // ⚠️ Bei Serien **muss** eine Staffel gewählt sein - ganz oder als Paket.
  //
  // Ohne diese Bedingung fiel das Absenden auf „ganze Serie" zurück – also
  // auf genau das, was hier abgeschafft werden sollte: eine Anfrage, die auch
  // alle künftigen Staffeln einschließt, ohne dass jemand das gesagt hat. Wer
  // alles will, hat dafür „Alle inkl. künftige".
  const staffelGewaehlt = !istSerie || staffeln.size > 0 || folgen.size > 0
  const ready = !stufeOffen || !staffelGewaehlt
    ? false
    : zielSpaeter
    ? true
    : options !== null &&
      (!options.quality_profile_choice || profileId !== null) &&
      (!options.root_folder_choice || folder !== '')

  // Die Zusammenfassung im Auswahl-Knopf: ganze Staffeln als Nummer,
  // Folgen-Pakete mit ihrer Liste - „Staffeln 1, 2 (F 3–5)".
  const auswahlTeile = [
    ...[...staffeln].sort((a, b) => a - b).map(String),
    ...[...folgen.entries()]
      .sort(([a], [b]) => a - b)
      .map(
        ([nummer, eps]) =>
          `${nummer} (${t('request.episodesShort', { list: folgenKompakt([...eps]) })})`,
      ),
  ]

  // ⚠️ **Ein Fenster, ein Ausgang.** Solange die Zuordnung offen ist, zeigt
  // dieses Formular nichts anderes - Profil und Ordner sind längst gewählt,
  // und daneben noch die Serie zu klären hieße, zwei Fragen gleichzeitig zu
  // stellen.
  if (zuordnung) {
    return (
      <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
        <h3 className="text-sm font-semibold">{t('request.match.title')}</h3>
        <div className="mt-3">
          <SerienZuordnung
            gesucht={{
              title: item.title,
              year: item.release_date?.slice(0, 4) ?? null,
              overview: item.overview,
              poster_url: item.poster_url ?? null,
            }}
            vorschlaege={zuordnung.vorschlaege}
            frisch={zuordnung.frisch}
            laeuft={createMutation.isPending}
            onWaehlen={(tvdbId) => {
              setTvdbWahl(tvdbId)
              setZuordnung(null)
              // Der Zustand ist noch nicht gesetzt, wenn die Mutation läuft -
              // deshalb die Wahl direkt mitgeben statt sie abzuwarten.
              createMutation.mutate(tvdbId)
            }}
            onAbbrechen={() => {
              setZuordnung(null)
              setTvdbWahl(null)
            }}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <h3 className="text-sm font-semibold">{t('request.chooseOptions')}</h3>

      {/* „Läuft schon in deinem Abo" - ein Hinweis, keine Sperre. Der
          Anfrageknopf behält seine Beschriftung; er wird nicht zu „Trotzdem
          anfragen", weil hier nichts zu überwinden ist.

          Bei Serien steht ein anderer Satz: TMDB sagt „läuft auf Netflix" über
          die *Serie*, nicht über die vierte Staffel, die dort fehlt - und
          genau in dem Fall fragt jemand an. */}
      {imAbo.length > 0 && (
        <p className="mt-3 rounded-lg border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-500">
          <span className="font-semibold">
            {t('request.inSubscription', { services: imAbo.join(', ') })}
          </span>{' '}
          {istSerie ? t('request.inSubscriptionSeries') : t('request.inSubscriptionMovie')}
        </p>
      )}

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
                {auswahlTeile.length === 0
                  ? t('request.seasonNonePicked')
                  : t('request.seasonPicked', {
                      list: auswahlTeile.join(', '),
                      count: auswahlTeile.length,
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
          onClick={() => createMutation.mutate(undefined)}
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
        <StaffelFolgenWaehler
          tmdbId={item.tmdb_id}
          seasons={item.seasons}
          tier={tier}
          folgenErlaubt={folgenErlaubt}
          staffeln={staffeln}
          folgen={folgen}
          onAuswahl={(neueStaffeln, neueFolgen) => {
            setStaffeln(neueStaffeln)
            setFolgen(neueFolgen)
          }}
          kuenftige={kuenftige}
          onKuenftige={setKuenftige}
        />
      </Fenster>
    </div>
  )
}

/** Freier Speicherplatz lesbar machen: 1234567890 -> "1,1 TB" */
function formatSpace(bytes: number): string {
  const tebibytes = bytes / 1024 ** 4
  if (tebibytes >= 1) return `${tebibytes.toFixed(1)} TiB`
  return `${Math.round(bytes / 1024 ** 3)} GiB`
}
